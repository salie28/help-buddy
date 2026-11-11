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
        from helpme.ask import cleanup_sessions
        cleanup_sessions(self.settings.keep_days, time.time())
        try:
            from helpme import system
            if system.ensure_shortcut(self.settings.shortcut):
                log.warning("help key binding was missing; restored %s", self.settings.shortcut)
        except (ValueError, GLib.Error) as e:
            log.error("could not check the help key binding: %s", e)
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
        if "capture" in args:
            # Closed (or never opened) → open it after the shot, so it isn't in it.
            # Open on any desktop → leave it alone: no raise, no focus.
            show = config.should_show_window(self.window is not None,
                                             self.window is not None
                                             and self.window.get_visible())
            if show:
                self.ensure_window()
            self.start_capture(present_after=show)
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
        from helpme.system import pretty_accel
        return pretty_accel(self.settings.shortcut)

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
            req = self.history.add(path, mode, prompt=self.settings.prompt)
        if self.window is not None:
            self.window.request_added(req.id, jump=self.settings.jump_to_newest)
        if not error:
            self._ask(req.id)

    def _ask(self, req_id):
        req = self.history.get(req_id)
        self.asker.ask(req_id, req.image, self.settings, self._on_retrying, self._on_done,
                       new_session=True)

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
                                attempts=attempts, session=result.session_id,
                                usage=result.usage)
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
            from helpme.ask import delete_session
            delete_session(req.session)
            self.history.update(req_id, status="thinking", error="", attempts=0, session="",
                                usage={})
            self._ask(req_id)
        self._refresh(req_id)

    def cancel(self, req_id):
        if not req_id or self.history.get(req_id) is None:
            return
        self.asker.cancel(req_id)
        self.history.update(req_id, status="cancelled", error="")
        self._refresh(req_id)

    # ---- follow-ups (job id "<req_id>:<index>") ----

    def ask_followup(self, req_id, question):
        req = self.history.get(req_id)
        question = question.strip()
        if req is None or not question or req.status != "done":
            return
        i = self.history.add_followup(req_id, question)
        self._refresh(req_id)
        self._ask_followup(req_id, i)

    def _ask_followup(self, req_id, i):
        from helpme.ask import followup_prompt, session_file
        req = self.history.get(req_id)
        if req.session and os.path.exists(session_file(req.session)):
            # Same conversation: claude resumes the saved session (screenshot not resent).
            self.asker.ask(f"{req_id}:{i}", req.image, self.settings, self._on_f_retrying,
                           self._on_f_done, prompt=req.followups[i]["q"], resume=req.session)
            return
        # Older request without a saved session: resend screenshot + the conversation.
        previous = [(f["q"], f["answer"]) for f in req.followups[:i] if f["status"] == "done"]
        text = followup_prompt(req.prompt or self.settings.prompt, req.answer, previous,
                               req.followups[i]["q"])
        self.asker.ask(f"{req_id}:{i}", req.image, self.settings, self._on_f_retrying,
                       self._on_f_done, prompt=text)

    def _followup_of(self, job_id):
        req_id, _, i = job_id.partition(":")
        req = self.history.get(req_id)
        if req is None or not i.isdigit() or int(i) >= len(req.followups):
            return None, None
        return req_id, int(i)

    def _on_f_retrying(self, job_id, error, _delay):
        req_id, i = self._followup_of(job_id)
        if req_id is None:
            return
        self.history.update_followup(req_id, i, status="retrying", error=error, attempts=1)
        self._refresh(req_id)

    def _on_f_done(self, job_id, result, attempts):
        req_id, i = self._followup_of(job_id)
        if req_id is None:
            return
        if result.ok:
            self.history.update_followup(req_id, i, status="done", answer=result.text,
                                         error="", attempts=attempts, usage=result.usage)
        else:
            self.history.update_followup(req_id, i, status="failed", error=result.text,
                                         attempts=attempts)
        self._refresh(req_id)

    def retry_followup(self, req_id, i):
        req = self.history.get(req_id)
        if req is None or i >= len(req.followups):
            return
        if not req.image or not os.path.exists(req.image):
            self.history.update_followup(req_id, i, status="failed",
                                         error="The screenshot for this request is gone.")
        else:
            self.history.update_followup(req_id, i, status="thinking", error="", attempts=0)
            self._ask_followup(req_id, i)
        self._refresh(req_id)

    def cancel_followup(self, req_id, i):
        req = self.history.get(req_id)
        if req is None or i >= len(req.followups):
            return
        self.asker.cancel(f"{req_id}:{i}")
        self.history.update_followup(req_id, i, status="cancelled", error="")
        self._refresh(req_id)

    def _cancel_all(self, req):
        from helpme.ask import delete_session
        self.asker.cancel(req.id)
        for i in range(len(req.followups)):
            self.asker.cancel(f"{req.id}:{i}")
        delete_session(req.session)

    def delete(self, req_id):
        req = self.history.get(req_id)
        if req is not None:
            self._cancel_all(req)
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
            self._cancel_all(req)
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
    args = argv[1:]
    if "--version" in args:
        from helpme import __version__
        print(f"Help Me {__version__}")
        return 0
    if "--install" in args or "--uninstall" in args:
        from helpme import system
        if "--install" in args:
            system.install()
        else:
            system.uninstall()
            if "--purge" in args:
                for path in system.purge_data():
                    print(f"purged {path}")
        return 0
    return HelpMeApp().run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
