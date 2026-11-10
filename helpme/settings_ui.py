"""Settings page and the preset-area picker."""

import os

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from helpme import config, system  # noqa: E402
from helpme.window import pixbuf_texture  # noqa: E402

MODEL_LABELS = ["Sonnet", "Haiku", "Opus"]


def _toast(title: str, timeout: int = 3) -> Adw.Toast:
    return Adw.Toast(title=title, use_markup=False, timeout=timeout)


def area_label(area) -> str:
    if not area:
        return "Not set"
    x, y, w, h = (round(v * 100) for v in area)
    return f"{w}% × {h}% of the screen, from {x}% left / {y}% top"


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self, app):
        super().__init__(title="Settings", search_enabled=False)
        self.app = app
        s = app.settings
        page = Adw.PreferencesPage()
        self.add(page)

        # ---- capture ----
        cap = Adw.PreferencesGroup(title="Capture")
        page.add(cap)
        self.mode_row = Adw.ComboRow(title="What to capture",
                                     model=Gtk.StringList.new(["Whole screen", "Preset area"]))
        self.mode_row.set_selected(1 if s.capture_mode == "area" else 0)
        self.mode_row.connect("notify::selected", self._on_mode)
        cap.add(self.mode_row)

        self.area_row = Adw.ActionRow(title="Preset area", subtitle=area_label(s.area),
                                      use_markup=False)
        pick = Gtk.Button(label="Pick area…", valign=Gtk.Align.CENTER)
        pick.connect("clicked", lambda _b: self.pick_area())
        self.area_row.add_suffix(pick)
        cap.add(self.area_row)

        flash = Adw.SwitchRow(
            title="Screen flash on capture",
            subtitle="Off: silent capture (GNOME shows its screen-share icon for a moment). "
                     "On: GNOME's normal screenshot with a white flash.",
            active=s.flash)
        flash.connect("notify::active", lambda r, _p: self._set("flash", r.get_active()))
        cap.add(flash)

        # ---- shortcut ----
        key = Adw.PreferencesGroup(title="Shortcut")
        page.add(key)
        self.key_row = Adw.ActionRow(title="Help key", subtitle=self.app.shortcut_label(),
                                     use_markup=False)
        change_key = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        change_key.connect("clicked", lambda _b: self._record_shortcut())
        self.key_row.add_suffix(change_key)
        key.add(self.key_row)

        # ---- claude ----
        ai = Adw.PreferencesGroup(title="Claude")
        page.add(ai)
        model = Adw.ComboRow(title="Model", model=Gtk.StringList.new(MODEL_LABELS))
        model.set_selected(config.MODELS.index(s.model))
        model.connect("notify::selected",
                      lambda r, _p: self._set("model", config.MODELS[r.get_selected()]))
        ai.add(model)
        self.prompt_row = Adw.EntryRow(title="Prompt sent with every screenshot", text=s.prompt,
                                       show_apply_button=True)
        self.prompt_row.connect("apply", self._on_prompt)
        ai.add(self.prompt_row)

        # ---- window ----
        win = Adw.PreferencesGroup(title="Window")
        page.add(win)
        dark = Adw.ActionRow(title="Window darkness")
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 30, 100, 5)
        scale.set_value(s.glass)
        scale.set_hexpand(True)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.LEFT)
        scale.set_size_request(180, -1)
        scale.connect("value-changed", self._on_glass)
        dark.add_suffix(scale)
        win.add(dark)
        jump = Adw.SwitchRow(title="Jump to newest request",
                             subtitle="Only changes what the window shows; it never pops up.",
                             active=s.jump_to_newest)
        jump.connect("notify::active",
                     lambda r, _p: self._set("jump_to_newest", r.get_active()))
        win.add(jump)
        auto = Adw.SwitchRow(title="Start in background at login",
                             subtitle="The key works right away; the window opens only when "
                                      "you open Help Me.",
                             active=s.autostart)
        auto.connect("notify::active", self._on_autostart)
        win.add(auto)

        # ---- screenshots ----
        shots = Adw.PreferencesGroup(title="Screenshots")
        page.add(shots)
        self.folder_row = Adw.ActionRow(title="Folder", subtitle=s.screenshot_dir,
                                        subtitle_selectable=True, use_markup=False)
        change = Gtk.Button(label="Change…", valign=Gtk.Align.CENTER)
        change.connect("clicked", self._on_folder)
        self.folder_row.add_suffix(change)
        shots.add(self.folder_row)
        keep = Adw.SpinRow.new_with_range(0, 365, 1)
        keep.set_title("Delete screenshots after (days)")
        keep.set_subtitle("0 = keep forever")
        keep.set_value(s.keep_days)
        keep.connect("notify::value", lambda r, _p: self._set("keep_days", int(r.get_value())))
        shots.add(keep)

        # ---- history ----
        hist = Adw.PreferencesGroup(title="History")
        page.add(hist)
        clear = Adw.ButtonRow(title="Clear history…", css_classes=["destructive-action"])
        clear.connect("activated", lambda _r: self.app.confirm_clear_history())
        hist.add(clear)

    # ---- handlers ----

    def _set(self, key, value):
        setattr(self.app.settings, key, value)
        self.app.settings_changed()

    def _on_mode(self, row, _pspec):
        mode = "area" if row.get_selected() == 1 else "full"
        self._set("capture_mode", mode)
        if mode == "area" and not self.app.settings.area:
            self.pick_area()

    def _record_shortcut(self):
        old = self.app.settings.shortcut
        system.suspend_shortcut()  # so pressing the current key doesn't fire a capture

        def done(accel):
            new = accel or old
            try:
                system.set_shortcut(new)
            except (ValueError, GLib.Error) as e:
                system.set_shortcut(old)
                self.add_toast(_toast(f"Could not set the shortcut: {e}"))
                return
            if accel:
                self._set("shortcut", accel)
                self.key_row.set_subtitle(self.app.shortcut_label())
                self.add_toast(_toast(f"Help key is now {self.app.shortcut_label()}"))
        ShortcutRecorder(done).present(self)

    def _on_prompt(self, row):
        text = row.get_text().strip() or config.DEFAULT_PROMPT
        row.set_text(text)
        self._set("prompt", text)
        self.add_toast(_toast("Prompt saved"))

    def _on_glass(self, scale):
        value = int(round(scale.get_value() / 5) * 5)
        if value != self.app.settings.glass:
            self._set("glass", value)

    def _on_autostart(self, row, _pspec):
        system.set_autostart(row.get_active())
        self._set("autostart", row.get_active())

    def _on_folder(self, _btn):
        dialog = Gtk.FileDialog(title="Screenshot folder")
        start = self.app.settings.screenshot_dir
        if os.path.isdir(start):
            dialog.set_initial_folder(Gio.File.new_for_path(start))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return  # cancelled
            if folder and folder.get_path():
                self._set("screenshot_dir", folder.get_path())
                self.folder_row.set_subtitle(folder.get_path())

        dialog.select_folder(self.app.window, None, done)

    def pick_area(self):
        def got(pb, error):
            if error:
                self.add_toast(_toast(error, timeout=6))
                return
            AreaPicker(self.app, pb, self._area_saved).present()
        self.app.capturer.capture_pixbuf(self.app.settings, got)

    def _area_saved(self):
        self.area_row.set_subtitle(area_label(self.app.settings.area))
        self.mode_row.set_selected(1)


class ShortcutRecorder(Adw.Dialog):
    """Press the new combination; Esc cancels."""

    def __init__(self, on_done):
        super().__init__(title="New help key", content_width=380)
        self._on_done = on_done
        self._result = None
        self._called = False
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=18,
                      margin_bottom=24, margin_start=24, margin_end=24)
        box.append(Gtk.Label(label="Press the new shortcut", css_classes=["title-2"]))
        self.hint = Gtk.Label(label="For example Ctrl+^ or Super+Shift+H · Esc cancels",
                              wrap=True, justify=Gtk.Justification.CENTER,
                              css_classes=["dim-label"])
        box.append(self.hint)
        view.set_content(box)
        self.set_child(view)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.connect("closed", lambda _d: self._finish())

    def _key(self, _c, keyval, _code, state):
        mods = Gdk.ModifierType(int(state)) & Gtk.accelerator_get_default_mod_mask()
        if keyval == Gdk.KEY_Escape and not mods:
            self.close()
            return True
        accel = system.accel_from_key(keyval, state)
        if accel is None:
            name = Gdk.keyval_name(keyval) or ""
            if not name.startswith(("Control_", "Shift_", "Alt_", "Super_", "Meta_")):
                self.hint.set_label("Use a combination with Ctrl, Alt or Super "
                                    "(or an F-key) · Esc cancels")
            return True
        self._result = accel
        self.close()
        return True

    def _finish(self):
        if not self._called:
            self._called = True
            self._on_done(self._result)


class AreaPicker(Gtk.Window):
    """Full-screen frozen screenshot: drag a box, Enter saves, Esc cancels."""

    def __init__(self, app, pixbuf, on_saved):
        super().__init__(application=app, title="Pick area", decorated=False,
                         css_classes=["area-picker"])
        self.app, self.on_saved = app, on_saved
        self.sel = None  # (x0, y0, x1, y1) in widget coordinates
        self._start = None

        overlay = Gtk.Overlay()
        overlay.set_child(Gtk.Picture(paintable=pixbuf_texture(pixbuf),
                                      content_fit=Gtk.ContentFit.FILL, can_shrink=True))
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        overlay.add_overlay(self.area)
        hint = Gtk.Label(label="Drag a box around the part to send  ·  Enter = save  ·  "
                               "Esc = cancel",
                         halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=24,
                         css_classes=["picker-hint"])
        overlay.add_overlay(hint)
        self.set_child(overlay)

        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        self.area.add_controller(drag)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)
        self.fullscreen()

    def _drag_begin(self, _g, x, y):
        self._start = (x, y)
        self.sel = (x, y, x, y)
        self.area.queue_draw()

    def _drag_update(self, _g, dx, dy):
        if self._start:
            x, y = self._start
            self.sel = (x, y, x + dx, y + dy)
            self.area.queue_draw()

    def _rect(self):
        if not self.sel:
            return None
        x0, y0, x1, y1 = self.sel
        w, h = self.area.get_width(), self.area.get_height()
        x0, x1 = sorted((max(0, min(w, x0)), max(0, min(w, x1))))
        y0, y1 = sorted((max(0, min(h, y0)), max(0, min(h, y1))))
        return x0, y0, x1 - x0, y1 - y0

    def _draw(self, _area, cr, w, h):
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.rectangle(0, 0, w, h)
        r = self._rect()
        if r and r[2] > 0 and r[3] > 0:
            cr.rectangle(*r)
            cr.set_fill_rule(1)  # EVEN_ODD: dim everything except the selection
        cr.fill()
        if r and r[2] > 0 and r[3] > 0:
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.set_line_width(2)
            cr.rectangle(*r)
            cr.stroke()

    def _key(self, _c, keyval, _code, _state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            r = self._rect()
            w, h = self.area.get_width(), self.area.get_height()
            if r and r[2] >= 10 and r[3] >= 10 and w and h:
                x, y, rw, rh = r
                self.app.settings.area = [x / w, y / h, rw / w, rh / h]
                self.app.settings.capture_mode = "area"
                self.app.settings_changed()
                self.on_saved()
                self.close()
            return True
        return False
