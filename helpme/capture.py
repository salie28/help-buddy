"""Screenshots through the desktop portal (silent, non-interactive)."""

import logging
import os
import shutil
import threading
from collections import deque
from datetime import datetime

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

MIN_SIDE = 16
PORTAL_TIMEOUT_S = 30
log = logging.getLogger("helpme")


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

    def capture_pixbuf(self, on_done) -> None:
        """on_done(pixbuf | None, error | None); nothing is saved."""
        self._queue.append(("pixbuf", None, on_done))
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

        self._shoot(shot_done)

    def _finish(self, cb, value, error) -> None:
        def run():
            self._busy = False
            if error:
                log.error("capture failed: %s", error)
            cb(value, error)
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
        except (OSError, GLib.Error) as e:
            _remove(portal_path)
            self._finish(cb, None, f"Could not save screenshot: {e}")

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


def _remove(path) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
