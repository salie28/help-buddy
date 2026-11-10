"""The Help window: an answer page (newest by default) and a library of all requests."""

import os
import threading
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from helpme.markdown import render  # noqa: E402

STATUS_TEXT = {
    "thinking": "Thinking…",
    "retrying": "Error — retrying in 20 s",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "done": "",
}


def load_texture_async(path: str, max_w: int, max_h: int, callback) -> None:
    """Decode + downscale in a worker thread, then callback(texture | None) on the main loop."""
    def work():
        tex = None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_w, max_h, True)
            fmt = (Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha()
                   else Gdk.MemoryFormat.R8G8B8)
            tex = Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt,
                                        pb.read_pixel_bytes(), pb.get_rowstride())
        except (GLib.Error, OSError):
            tex = None
        GLib.idle_add(lambda: (callback(tex), GLib.SOURCE_REMOVE)[1])
    threading.Thread(target=work, daemon=True).start()


def nice_time(iso: str) -> str:
    try:
        t = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if t.date() == datetime.now().date():
        return "Today " + t.strftime("%H:%M")
    return t.strftime("%d %b %H:%M")


def first_line(text: str, limit: int = 80) -> str:
    for line in text.splitlines():
        line = line.strip().lstrip("#*->`• ").replace("**", "").replace("`", "").strip()
        if line:
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


def _button(icon: str, tooltip: str, action: str | None = None) -> Gtk.Button:
    b = Gtk.Button(icon_name=icon, tooltip_text=tooltip)
    if action:
        b.set_action_name(action)
    return b


class HelpWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Help Me", default_width=460,
                         default_height=720)
        self.app = app
        self.add_css_class("helpme")
        self.current_id = None
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self.connect("close-request", self._on_close)

        self.toasts = Adw.ToastOverlay()
        glass = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, css_classes=["glass"])
        glass.append(self.toasts)
        self.toasts.set_vexpand(True)
        self.set_content(glass)

        self.nav = Adw.NavigationView()
        self.toasts.set_child(self.nav)
        self.nav.add(self._build_library())
        self.nav.add(self._build_answer())

        for req in reversed(app.history.all()):  # oldest first, each added on top
            self._add_row(req.id)
        self._update_empty()
        newest = app.history.newest()
        if newest is not None:
            self.show_request(newest.id)

    # ---- public API used by the app ----

    def request_added(self, req_id: str, jump: bool) -> None:
        self._add_row(req_id)
        self._update_empty()
        if jump:
            self.show_request(req_id)

    def refresh(self, req_id: str) -> None:
        row = self._rows.get(req_id)
        if row is not None:
            self._fill_row(row, req_id)
        if req_id == self.current_id:
            self._fill_answer(req_id)

    def removed(self, req_id: str) -> None:
        row = self._rows.pop(req_id, None)
        if row is not None:
            self.listbox.remove(row)
        self._update_empty()
        if req_id == self.current_id:
            self.current_id = None
            self.show_library()

    def reload(self) -> None:
        for row in list(self._rows.values()):
            self.listbox.remove(row)
        self._rows.clear()
        for req in reversed(self.app.history.all()):
            self._add_row(req.id)
        self._update_empty()
        if self.current_id and self.app.history.get(self.current_id) is None:
            self.current_id = None
            self.show_library()

    def show_request(self, req_id: str) -> None:
        self.current_id = req_id
        self._fill_answer(req_id)
        if self.nav.get_visible_page() is not self.answer_page:
            self.nav.push(self.answer_page)

    def show_library(self) -> None:
        if self.nav.get_visible_page() is not self.library_page:
            self.nav.pop_to_page(self.library_page)

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=2))

    # ---- building ----

    def _header(self, subtitle_holder: bool = False) -> Adw.HeaderBar:
        header = Adw.HeaderBar()
        header.pack_start(_button("camera-photo-symbolic", "Capture now", "app.capture"))
        menu = Gio.Menu()
        menu.append("Clear history", "app.clear-history")
        menu.append("Quit", "app.quit")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu"))
        header.pack_end(_button("emblem-system-symbolic", "Settings", "app.settings"))
        return header

    def _build_library(self) -> Adw.NavigationPage:
        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(self._header())
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE,
                                   css_classes=["library"])
        self.listbox.connect("row-activated", self._on_row_activated)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroller.set_child(self.listbox)
        self.empty = Adw.StatusPage(icon_name="help-browser-symbolic", title="No questions yet")
        self.lib_stack = Gtk.Stack()
        self.lib_stack.add_named(scroller, "list")
        self.lib_stack.add_named(self.empty, "empty")
        view.set_content(self.lib_stack)
        self.library_page = Adw.NavigationPage(title="Library", tag="library", child=view)
        return self.library_page

    def _build_answer(self) -> Adw.NavigationPage:
        view = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)
        view.add_top_bar(self._header())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=6, margin_bottom=18, margin_start=16, margin_end=16)

        self.picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True,
                                   height_request=220, css_classes=["shot"],
                                   cursor=Gdk.Cursor.new_from_name("pointer"),
                                   tooltip_text="Open full screenshot")
        click = Gtk.GestureClick()
        click.connect("released", self._on_picture_clicked)
        self.picture.add_controller(click)
        box.append(self.picture)

        self.meta = Gtk.Label(xalign=0, css_classes=["dim-label", "caption"])
        box.append(self.meta)

        self.status_box = Gtk.Box(spacing=8)
        self.spinner = Adw.Spinner()
        self.status_label = Gtk.Label(xalign=0, css_classes=["heading"])
        self.status_box.append(self.spinner)
        self.status_box.append(self.status_label)
        box.append(self.status_box)

        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True, focusable=False,
                                     wrap_mode=2, css_classes=["dim-label", "error-text"])
        box.append(self.error_label)

        self.answer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                  css_classes=["answer"])
        box.append(self.answer_box)

        actions = Gtk.Box(spacing=6, margin_top=6, halign=Gtk.Align.START)
        self.copy_btn = Gtk.Button(label="Copy", css_classes=["pill-ish"])
        self.copy_btn.connect("clicked", self._on_copy)
        self.retry_btn = Gtk.Button(label="Retry", css_classes=["pill-ish"])
        self.retry_btn.connect("clicked", lambda _b: self.app.retry(self.current_id))
        self.cancel_btn = Gtk.Button(label="Cancel", css_classes=["pill-ish"])
        self.cancel_btn.connect("clicked", lambda _b: self.app.cancel(self.current_id))
        self.delete_btn = Gtk.Button(label="Delete", css_classes=["pill-ish", "destructive"])
        self.delete_btn.connect("clicked", self._on_delete)
        for b in (self.copy_btn, self.retry_btn, self.cancel_btn, self.delete_btn):
            actions.append(b)
        box.append(actions)

        clamp = Adw.Clamp(maximum_size=760, child=box)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
                                      child=clamp)
        view.set_content(scroller)
        self.answer_page = Adw.NavigationPage(title="Answer", tag="answer", child=view)
        return self.answer_page

    # ---- answer page ----

    def _fill_answer(self, req_id: str) -> None:
        req = self.app.history.get(req_id)
        if req is None:
            return
        self.answer_page.set_title(nice_time(req.created))
        has_image = bool(req.image) and os.path.exists(req.image)
        self.picture.set_visible(has_image)
        if has_image:
            self.picture.set_paintable(None)
            load_texture_async(req.image, 920, 520,
                               lambda tex, rid=req_id: self._set_picture(rid, tex))
        mode = "Preset area" if req.mode == "area" else "Whole screen"
        extra = "" if has_image or not req.image else " · screenshot deleted"
        self.meta.set_label(f"{nice_time(req.created)} · {mode}{extra}")

        busy = req.status in ("thinking", "retrying")
        self.spinner.set_visible(busy)
        self.status_label.set_label(STATUS_TEXT[req.status])
        self.status_box.set_visible(req.status != "done")
        self.error_label.set_label(req.error)
        self.error_label.set_visible(bool(req.error) and req.status != "done")

        child = self.answer_box.get_first_child()
        while child is not None:
            self.answer_box.remove(child)
            child = self.answer_box.get_first_child()
        for kind, text in render(req.answer):
            self.answer_box.append(self._block_widget(kind, text))
        self.answer_box.set_visible(bool(req.answer))

        self.copy_btn.set_visible(bool(req.answer))
        self.retry_btn.set_visible(req.status in ("failed", "cancelled"))
        self.cancel_btn.set_visible(busy)

    def _set_picture(self, req_id, texture) -> None:
        if req_id == self.current_id and texture is not None:
            self.picture.set_paintable(texture)

    @staticmethod
    def _block_widget(kind: str, text: str) -> Gtk.Widget:
        if kind == "code":
            label = Gtk.Label(label=text, xalign=0, selectable=True, wrap=True, wrap_mode=2,
                              focusable=False, css_classes=["monospace"])
            frame = Gtk.Box(css_classes=["code"])
            frame.append(label)
            return frame
        # focusable=False: otherwise GTK puts a text caret in the first label on open;
        # mouse selection still works.
        label = Gtk.Label(xalign=0, selectable=True, wrap=True, wrap_mode=2, focusable=False,
                          use_markup=True, css_classes=["answer-text", kind])
        label.set_markup(text)
        return label

    def _on_picture_clicked(self, *_args) -> None:
        req = self.app.history.get(self.current_id) if self.current_id else None
        if req and req.image and os.path.exists(req.image):
            Gtk.FileLauncher.new(Gio.File.new_for_path(req.image)).launch(self, None, None)

    def _on_copy(self, _btn) -> None:
        req = self.app.history.get(self.current_id) if self.current_id else None
        if req and req.answer:
            self.get_clipboard().set(req.answer)
            self.toast("Copied")

    def _on_delete(self, _btn) -> None:
        req_id = self.current_id
        if not req_id:
            return
        dialog = Adw.AlertDialog(heading="Delete this request?",
                                 body="The answer and its screenshot are removed.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, r: r == "delete" and self.app.delete(req_id))
        dialog.present(self)

    # ---- library ----

    def _add_row(self, req_id: str) -> None:
        row = Adw.ActionRow(activatable=True, use_markup=False)
        row.req_id = req_id
        row.thumb = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True,
                                width_request=96, height_request=60, css_classes=["thumb"])
        row.add_prefix(row.thumb)
        row.dot = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["dot"])
        row.add_suffix(row.dot)
        self._fill_row(row, req_id)
        req = self.app.history.get(req_id)
        if req and req.image and os.path.exists(req.image):
            load_texture_async(req.image, 192, 120,
                               lambda tex, r=row: tex is not None and r.thumb.set_paintable(tex))
        self.listbox.prepend(row)
        self._rows[req_id] = row

    def _fill_row(self, row, req_id: str) -> None:
        req = self.app.history.get(req_id)
        if req is None:
            return
        title = first_line(req.answer) if req.status == "done" else (
            STATUS_TEXT[req.status] + (": " + first_line(req.error, 60) if req.error else ""))
        row.set_title(title or "(empty answer)")
        row.set_subtitle(nice_time(req.created))
        row.dot.set_css_classes(["dot", req.status])

    def _update_empty(self) -> None:
        self.empty.set_description(
            f"Press {self.app.shortcut_label()} anywhere to get help with your screen.")
        self.lib_stack.set_visible_child_name("list" if self._rows else "empty")

    def _on_row_activated(self, _list, row) -> None:
        self.show_request(row.req_id)

    def _on_close(self, _win) -> bool:
        self.set_visible(False)
        return True
