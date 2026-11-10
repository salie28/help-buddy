"""Help Me application: single instance, `helpme capture` from the global shortcut."""

import logging
import os
import sys
import time

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from helpme import config  # noqa: E402
from helpme.ask import Asker  # noqa: E402
from helpme.capture import Capturer  # noqa: E402
from helpme.config import APP_ID, load_settings  # noqa: E402
from helpme.history import History, cleanup_screenshots  # noqa: E402

log = logging.getLogger("helpme")
STYLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")


class HelpMeApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID,
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.settings = None
        self.history = None
        self.asker = Asker()
        self.capturer = Capturer()
        self._first_command = True

    # ---- lifecycle ----

    def do_startup(self):
        Adw.Application.do_startup(self)
        self.hold()  # keep running with the window hidden
        GLib.set_application_name("Help Me")
        config.setup_logging()
        self.settings = load_settings()
        self.history = History()
        self.history.load()
        cleanup_screenshots(self.settings.screenshot_dir, self.settings.keep_days, time.time())
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        provider = Gtk.CssProvider()
        provider.load_from_path(STYLE)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._glass = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._glass,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self.apply_glass()
        for name, handler in (("capture", lambda *_: self.start_capture()),
                              ("settings", lambda *_: self.open_settings()),
                              ("clear-history", lambda *_: self.confirm_clear_history()),
                              ("quit", lambda *_: self.quit())):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
        log.info("Help Me started")

    def do_command_line(self, command_line):
        args = command_line.get_arguments()[1:]
        first, self._first_command = self._first_command, False
        if "capture" in args:
            # Only the press that starts the app opens the window (after the shot, so it
            # isn't in it); later presses never raise or focus anything.
            if first:
                self.ensure_window()
            self.start_capture(present_after=first)
        elif "--background" in args:
            pass
        else:
            self.ensure_window().present()
        return 0

    def ensure_window(self):
        if self.window is None:
            from helpme.window import HelpWindow
            self.window = HelpWindow(self)
        return self.window

    def shortcut_label(self) -> str:
        ok, key, mods = Gtk.accelerator_parse(self.settings.shortcut)
        return Gtk.accelerator_get_label(key, mods) if ok else self.settings.shortcut

    # ---- requests ----

    def apply_glass(self):
        self._glass.load_from_string(config.glass_css(self.settings.glass))

    def start_capture(self, present_after=False):
        def done(path, error):
            self._on_captured(path, error)
            if present_after:
                self.ensure_window().present()
        self.capturer.capture(self.settings, done)

    def _on_captured(self, path, error):
        mode = "area" if self.settings.capture_mode == "area" and self.settings.area else "full"
        if error:
            req = self.history.add("", mode)
            self.history.update(req.id, status="failed", error=error)
        else:
            req = self.history.add(path, mode)
        if self.window is not None:
            self.window.request_added(req.id, jump=self.settings.jump_to_newest)
        if not error:
            self._ask(req.id)

    def _ask(self, req_id):
        req = self.history.get(req_id)
        self.asker.ask(req_id, req.image, self.settings, self._on_retrying, self._on_done)

    def _on_retrying(self, req_id, error, _delay):
        if self.history.get(req_id) is None:
            return
        self.history.update(req_id, status="retrying", error=error, attempts=1)
        self._refresh(req_id)

    def _on_done(self, req_id, result, attempts):
        if self.history.get(req_id) is None:
            return
        if result.ok:
            self.history.update(req_id, status="done", answer=result.text, error="",
                                attempts=attempts)
        else:
            self.history.update(req_id, status="failed", error=result.text, attempts=attempts)
        self._refresh(req_id)

    def _refresh(self, req_id):
        if self.window is not None:
            self.window.refresh(req_id)

    def retry(self, req_id):
        req = self.history.get(req_id) if req_id else None
        if req is None:
            return
        if not req.image or not os.path.exists(req.image):
            self.history.update(req_id, status="failed",
                                error="The screenshot for this request is gone, so it can't "
                                      "be sent again.")
        else:
            self.history.update(req_id, status="thinking", error="", attempts=0)
            self._ask(req_id)
        self._refresh(req_id)

    def cancel(self, req_id):
        if not req_id or self.history.get(req_id) is None:
            return
        self.asker.cancel(req_id)
        self.history.update(req_id, status="cancelled", error="")
        self._refresh(req_id)

    def delete(self, req_id):
        self.asker.cancel(req_id)
        self.history.delete(req_id)
        if self.window is not None:
            self.window.removed(req_id)

    def confirm_clear_history(self):
        win = self.ensure_window()
        dialog = Adw.AlertDialog(heading="Clear history?",
                                 body="All answers are removed. Screenshots stay in "
                                      "their folder.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: r == "clear" and self.clear_history())
        dialog.present(win)

    def clear_history(self):
        for req in self.history.all():
            self.asker.cancel(req.id)
        self.history.clear()
        if self.window is not None:
            self.window.reload()

    def open_settings(self):
        from helpme.settings_ui import SettingsDialog
        win = self.ensure_window()
        if not win.get_visible():
            win.present()
        SettingsDialog(self).present(win)

    def settings_changed(self):
        config.save_settings(self.settings)
        self.apply_glass()


def main(argv) -> int:
    return HelpMeApp().run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
