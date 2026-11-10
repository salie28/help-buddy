"""Screenshots through the desktop portal (silent, non-interactive)."""

import logging
import os
import re
import shutil
import threading
from collections import deque
from datetime import datetime

import gi
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gst", "1.0")
from gi.repository import GdkPixbuf, Gio, GLib, Gst  # noqa: E402

from helpme import config  # noqa: E402

MIN_SIDE = 16
PORTAL_TIMEOUT_S = 30
SHARE_DIALOG_TIMEOUT_S = 180  # first no-flash capture waits for the "Share screen" dialog
PORTAL = "org.freedesktop.portal.Desktop"
PORTAL_PATH = "/org/freedesktop/portal/desktop"
log = logging.getLogger("helpme")


def backend_for(settings) -> str:
    """"screencast" = silent frame grab (no flash), "screenshot" = Screenshot portal (flash)."""
    return "screenshot" if settings.flash else "screencast"


def area_to_pixels(area, width: int, height: int) -> tuple[int, int, int, int]:
    if area is None:
        return (0, 0, width, height)
    x, y = round(area[0] * width), round(area[1] * height)
    w, h = round(area[2] * width), round(area[3] * height)
    w = min(max(w, MIN_SIDE), width)
    h = min(max(h, MIN_SIDE), height)
    x = min(max(x, 0), width - w)
    y = min(max(y, 0), height - h)
    return (x, y, w, h)


def screenshot_filename(dirpath: str, now: datetime) -> str:
    base = now.strftime("%y%m%d_%H%M%S")
    path = os.path.join(dirpath, base + ".png")
    n = 2
    while os.path.exists(path):
        path = os.path.join(dirpath, f"{base}_{n}.png")
        n += 1
    return path


class Capturer:
    """One portal call at a time: the portal always writes to the same file name."""

    def __init__(self):
        self._queue = deque()
        self._busy = False
        self._counter = 0

    def capture(self, settings, on_done) -> None:
        """on_done(path | None, error | None), called on the main loop."""
        self._queue.append(("save", settings, on_done))
        self._next()

    def capture_pixbuf(self, settings, on_done) -> None:
        """on_done(pixbuf | None, error | None); nothing is saved."""
        self._queue.append(("pixbuf", settings, on_done))
        self._next()

    def _next(self) -> None:
        if self._busy or not self._queue:
            return
        self._busy = True
        kind, settings, cb = self._queue.popleft()

        def shot_done(portal_path, error):
            if error:
                self._finish(cb, None, error)
                return
            threading.Thread(target=self._process, daemon=True,
                             args=(kind, settings, cb, portal_path)).start()

        if backend_for(settings) == "screencast":
            self._shoot_cast(shot_done)
        else:
            self._shoot(shot_done)

    def _shoot_cast(self, done) -> None:
        self._counter += 1
        n = self._counter

        def work():
            try:
                path, error = _grab_screencast_frame(n), None
            except CaptureError as e:
                path, error = None, str(e)
            except Exception as e:  # never leave the queue waiting for a callback
                log.exception("unexpected screen-share capture error")
                path, error = None, f"Capture failed: {getattr(e, 'message', None) or e}"
            GLib.idle_add(lambda: (done(path, error), GLib.SOURCE_REMOVE)[1])
        threading.Thread(target=work, daemon=True).start()

    def _finish(self, cb, value, error) -> None:
        def run():
            self._busy = False
            if error:
                log.error("capture failed: %s", error)
            try:
                cb(value, error)
            except Exception:
                log.exception("capture callback failed")
            finally:
                self._next()
            return GLib.SOURCE_REMOVE
        GLib.idle_add(run)

    def _process(self, kind, settings, cb, portal_path) -> None:
        """Runs in a worker thread (GdkPixbuf only, no GTK)."""
        try:
            if kind == "pixbuf":
                pb = GdkPixbuf.Pixbuf.new_from_file(portal_path)
                _remove(portal_path)
                self._finish(cb, pb, None)
                return
            os.makedirs(settings.screenshot_dir, exist_ok=True)
            dest = screenshot_filename(settings.screenshot_dir, datetime.now())
            if settings.capture_mode == "area" and settings.area:
                pb = GdkPixbuf.Pixbuf.new_from_file(portal_path)
                x, y, w, h = area_to_pixels(settings.area, pb.get_width(), pb.get_height())
                pb.new_subpixbuf(x, y, w, h).copy().savev(dest, "png", [], [])
                _remove(portal_path)
            else:
                shutil.move(portal_path, dest)
            self._finish(cb, dest, None)
        except Exception as e:  # always answer, or the queue stays busy forever
            _remove(portal_path)
            self._finish(cb, None,
                         f"Could not save screenshot: {getattr(e, 'message', None) or e}")

    def _shoot(self, done) -> None:
        """Ask the portal for a screenshot; done(path | None, error | None)."""
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        except GLib.Error as e:
            done(None, f"No session bus: {e.message}")
            return
        self._counter += 1
        token = f"helpme{os.getpid()}_{self._counter}"
        sender = bus.get_unique_name()[1:].replace(".", "_")
        req_path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        state = {"sub": 0, "timer": 0, "done": False}

        def finish(path, error):
            if state["done"]:
                return
            state["done"] = True
            if state["sub"]:
                bus.signal_unsubscribe(state["sub"])
            if state["timer"]:
                GLib.source_remove(state["timer"])
            done(path, error)

        def on_response(_conn, _sender, _obj, _iface, _sig, params):
            code, results = params.unpack()
            if code != 0:
                finish(None, f"Screenshot was refused or cancelled (portal code {code})")
                return
            uri = results.get("uri", "")
            path = Gio.File.new_for_uri(uri).get_path() if uri else None
            if not path:
                finish(None, "Screenshot portal returned no file")
            else:
                finish(path, None)

        def on_timeout():
            state["timer"] = 0
            finish(None, f"Screenshot portal did not answer within {PORTAL_TIMEOUT_S} s")
            return GLib.SOURCE_REMOVE

        def on_called(conn, res):
            try:
                conn.call_finish(res)
            except GLib.Error as e:
                finish(None, f"Screenshot portal error: {e.message}")

        state["sub"] = bus.signal_subscribe(
            "org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response",
            req_path, None, Gio.DBusSignalFlags.NONE, on_response)
        state["timer"] = GLib.timeout_add_seconds(PORTAL_TIMEOUT_S, on_timeout)
        options = {"handle_token": GLib.Variant("s", token),
                   "interactive": GLib.Variant("b", False)}
        bus.call("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                 "org.freedesktop.portal.Screenshot", "Screenshot",
                 GLib.Variant("(sa{sv})", ("", options)), GLib.VariantType("(o)"),
                 Gio.DBusCallFlags.NONE, -1, None, on_called)


class CaptureError(Exception):
    pass


def _grab_screencast_frame(n: int) -> str:
    """Blocking (run in a worker thread): one frame of the monitor via the ScreenCast
    portal + PipeWire. Silent once the user has allowed sharing; the restore token
    keeps that permission. Returns the path of a PNG in the state dir."""
    ctx = GLib.MainContext.new()
    ctx.push_thread_default()
    session = None
    bus = None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        sender = bus.get_unique_name()[1:].replace(".", "_")
        iface = "org.freedesktop.portal.ScreenCast"
        seq = [0]

        def request(method, fmt, args, opts, timeout):
            seq[0] += 1
            token = f"helpmecast{os.getpid()}_{n}_{seq[0]}"
            opts["handle_token"] = GLib.Variant("s", token)
            path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
            loop = GLib.MainLoop.new(ctx, False)
            res = {}

            def on_resp(_c, _s, _o, _i, _sig, params):
                res["code"], res["results"] = params.unpack()
                loop.quit()

            def on_timeout():
                loop.quit()
                return GLib.SOURCE_REMOVE

            sub = bus.signal_subscribe(PORTAL, "org.freedesktop.portal.Request", "Response",
                                       path, None, Gio.DBusSignalFlags.NONE, on_resp)
            src = GLib.timeout_source_new_seconds(timeout)
            src.set_callback(on_timeout)
            src.attach(ctx)
            try:
                bus.call_sync(PORTAL, PORTAL_PATH, iface, method,
                              GLib.Variant(fmt, (*args, opts)), None,
                              Gio.DBusCallFlags.NONE, -1, None)
                loop.run()
            except GLib.Error as e:
                raise CaptureError(f"Screen-share portal error ({method}): {e.message}")
            finally:
                src.destroy()
                bus.signal_unsubscribe(sub)
            if "code" not in res:
                raise CaptureError(f"Screen-share portal did not answer ({method})")
            if res["code"] == 1:
                raise CaptureError("Screen sharing was not allowed. No-flash capture needs it "
                                   "once: allow it, or turn on 'Screen flash' in Settings.")
            if res["code"] != 0:
                raise CaptureError(f"Screen-share portal failed ({method}, code {res['code']})")
            return res["results"]

        r = request("CreateSession", "(a{sv})", (),
                    {"session_handle_token": GLib.Variant("s", f"helpmes{os.getpid()}_{n}")},
                    PORTAL_TIMEOUT_S)
        session = r["session_handle"]
        opts = {"types": GLib.Variant("u", 1),          # monitor
                "multiple": GLib.Variant("b", False),
                "cursor_mode": GLib.Variant("u", 1),    # hidden, like a screenshot
                "persist_mode": GLib.Variant("u", 2)}   # until revoked
        token = _read_token()
        if token:
            opts["restore_token"] = GLib.Variant("s", token)
        request("SelectSources", "(oa{sv})", (session,), opts, PORTAL_TIMEOUT_S)
        r = request("Start", "(osa{sv})", (session, ""), {}, SHARE_DIALOG_TIMEOUT_S)
        if r.get("restore_token"):
            _write_token(r["restore_token"])
        streams = r.get("streams") or []
        if not streams:
            raise CaptureError("Screen-share portal returned no screen")
        node = streams[0][0]
        try:
            ret, fds = bus.call_with_unix_fd_list_sync(
                PORTAL, PORTAL_PATH, iface, "OpenPipeWireRemote",
                GLib.Variant("(oa{sv})", (session, {})), GLib.VariantType("(h)"),
                Gio.DBusCallFlags.NONE, -1, None, None)
            fd = fds.get(ret.unpack()[0])
        except GLib.Error as e:
            raise CaptureError(f"Could not open PipeWire: {e.message}")
        os.makedirs(config.state_dir(), exist_ok=True)
        out = os.path.join(config.state_dir(), f"cast_{os.getpid()}_{n}.png")
        _grab_frame(fd, node, out)
        return out
    finally:
        if session and bus:
            try:
                bus.call_sync(PORTAL, session, "org.freedesktop.portal.Session", "Close",
                              None, None, Gio.DBusCallFlags.NONE, -1, None)
            except GLib.Error:
                pass
        ctx.pop_thread_default()


def _grab_frame(fd: int, node: int, out: str) -> None:
    pipe = None
    try:
        if not Gst.is_initialized():
            Gst.init(None)
        pipe = Gst.parse_launch(
            f"pipewiresrc fd={fd} path={node} always-copy=true num-buffers=1 "
            f"! videoconvert ! pngenc ! filesink location={out}")
        pipe.set_state(Gst.State.PLAYING)
        msg = pipe.get_bus().timed_pop_filtered(
            10 * Gst.SECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    finally:
        if pipe is not None:
            pipe.set_state(Gst.State.NULL)
        os.close(fd)
    if msg is None:
        _remove(out)
        raise CaptureError("No frame from the screen share within 10 s")
    if msg.type == Gst.MessageType.ERROR:
        _remove(out)
        raise CaptureError(f"Frame grab failed: {msg.parse_error()[0].message}")
    if not os.path.exists(out) or os.path.getsize(out) == 0:
        raise CaptureError("Frame grab produced no image")


_APP_UNIT = re.compile(r"^app-[^-]+-(.+?)(?:-\d+|-autostart|@[^.]*)\.(?:scope|service)$")
_APP_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z_][A-Za-z0-9_-]*)+$")


def portal_app_id(cgroup_text: str) -> str:
    """The app id the portal derives from our systemd scope ('' = plain host app).
    GNOME starts us as app-gnome-<id>-<pid>.scope from the app grid, but as an
    invalid-id scope from the shortcut, so the two get separate screen-share grants."""
    for line in cgroup_text.splitlines():
        unit = line.rsplit("/", 1)[-1]
        m = _APP_UNIT.match(unit)
        if m:
            app_id = m.group(1).replace("\\x2d", "-")
            return app_id if _APP_ID.match(app_id) else ""
    return ""


def current_app_id() -> str:
    try:
        with open("/proc/self/cgroup", encoding="utf-8") as f:
            return portal_app_id(f.read())
    except OSError:
        return ""


def token_path(app_id: str) -> str:
    base = config.screencast_token_path()
    return f"{base}.{app_id}" if app_id else base


def _read_token() -> str:
    try:
        with open(token_path(current_app_id()), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _write_token(token: str) -> None:
    os.makedirs(config.state_dir(), exist_ok=True)
    path = token_path(current_app_id())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(token)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _remove(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
