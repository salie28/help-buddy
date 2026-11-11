"""The Help window: an answer page (newest by default) and a library of all requests."""

import os
import threading
from datetime import datetime

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from helpbuddy.ask import format_usage  # noqa: E402
from helpbuddy.markdown import render  # noqa: E402

STATUS_TEXT = {
    "thinking": "Thinking…",
    "retrying": "Error — retrying in 20 s",
    "failed": "Failed",
    "cancelled": "Cancelled",
    "done": "",
}


def pixbuf_texture(pb) -> Gdk.Texture:
    fmt = Gdk.MemoryFormat.R8G8B8A8 if pb.get_has_alpha() else Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pb.get_width(), pb.get_height(), fmt,
                                 pb.read_pixel_bytes(), pb.get_rowstride())


def load_texture_async(path: str, max_w: int, max_h: int, callback) -> None:
    """Decode + downscale in a worker thread, then callback(texture | None) on the main loop."""
    def work():
        tex = None
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, max_w, max_h, True)
            tex = pixbuf_texture(pb)
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
        super().__init__(application=app, title="Help Buddy", default_width=460,
                         default_height=720)
        self.app = app
        self.add_css_class("helpbuddy")
        self.current_id = None
        self._shown_image = None  # avoid re-decoding the screenshot on every refresh
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self.connect("close-request", self._on_close)
        self.connect("map", lambda _w: GLib.idle_add(self._park_focus))

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
        GLib.idle_add(self._park_focus)

    def show_library(self) -> None:
        if self.nav.get_visible_page() is not self.library_page:
            self.nav.pop_to_page(self.library_page)

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, use_markup=False, timeout=2))

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

        self.error_label = Gtk.Label(xalign=0, wrap=True, selectable=True,
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

        self.followups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                                     margin_top=8)
        box.append(self.followups_box)

        clamp = Adw.Clamp(maximum_size=760, child=box)
        self.answer_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                                  vexpand=True, child=clamp, focusable=True)
        view.set_content(self.answer_scroller)

        ask_bar = Gtk.Box(spacing=6, margin_top=6, margin_bottom=10, margin_start=12,
                          margin_end=12, css_classes=["ask-bar"])
        self.ask_entry = Gtk.Entry(placeholder_text="Ask a follow-up…", hexpand=True)
        self.ask_entry.connect("activate", self._on_ask)
        self.ask_btn = Gtk.Button(icon_name="mail-send-symbolic", tooltip_text="Send",
                                  css_classes=["pill-ish"])
        self.ask_btn.connect("clicked", self._on_ask)
        ask_bar.append(self.ask_entry)
        ask_bar.append(self.ask_btn)
        view.add_bottom_bar(ask_bar)
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
        if has_image and self._shown_image != req.image:
            self._shown_image = req.image
            self.picture.set_paintable(None)
            load_texture_async(req.image, 920, 520,
                               lambda tex, rid=req_id: self._set_picture(rid, tex))
        elif not has_image:
            self._shown_image = None
        mode = "Preset area" if req.mode == "area" else "Whole screen"
        extra = "" if has_image or not req.image else " · screenshot deleted"
        usage = format_usage(req.usage)
        self.meta.set_label(f"{nice_time(req.created)} · {mode}{extra}"
                            + (f"\n{usage}" if usage else ""))

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
        self._fill_followups(req)

    def _fill_followups(self, req) -> None:
        child = self.followups_box.get_first_child()
        while child is not None:
            self.followups_box.remove(child)
            child = self.followups_box.get_first_child()
        for i, f in enumerate(req.followups):
            q = Gtk.Label(label=f["q"], xalign=0, wrap=True, wrap_mode=2, selectable=True,
                          css_classes=["you"])
            self.followups_box.append(q)
            if f["status"] == "done":
                for kind, text in render(f["answer"]):
                    self.followups_box.append(self._block_widget(kind, text))
                usage = format_usage(f.get("usage") or {})
                if usage:
                    self.followups_box.append(Gtk.Label(label=usage, xalign=0,
                                                        css_classes=["dim-label", "caption"]))
                continue
            row = Gtk.Box(spacing=8)
            if f["status"] in ("thinking", "retrying"):
                row.append(Adw.Spinner())
            row.append(Gtk.Label(label=STATUS_TEXT[f["status"]], css_classes=["heading"]))
            if f["status"] in ("thinking", "retrying"):
                b = Gtk.Button(label="Cancel", css_classes=["pill-ish"])
                b.connect("clicked", lambda _b, n=i: self.app.cancel_followup(req.id, n))
                row.append(b)
            else:
                b = Gtk.Button(label="Retry", css_classes=["pill-ish"])
                b.connect("clicked", lambda _b, n=i: self.app.retry_followup(req.id, n))
                row.append(b)
            self.followups_box.append(row)
            if f["error"]:
                self.followups_box.append(Gtk.Label(
                    label=f["error"], xalign=0, wrap=True, wrap_mode=2, selectable=True,
                    css_classes=["dim-label", "error-text"]))
        busy = any(f["status"] in ("thinking", "retrying") for f in req.followups)
        ready = req.status == "done" and not busy
        self.ask_entry.set_sensitive(ready)
        self.ask_btn.set_sensitive(ready)
        self.ask_entry.set_placeholder_text(
            "Ask a follow-up…" if ready else
            ("Waiting for the answer…" if req.status in ("thinking", "retrying") or busy
             else "Follow-ups need a finished answer"))

    def _on_ask(self, *_args) -> None:
        text = self.ask_entry.get_text().strip()
        if not text or not self.current_id:
            return
        self.ask_entry.set_text("")
        self.app.ask_followup(self.current_id, text)
        GLib.timeout_add(80, self._scroll_to_bottom)

    def _scroll_to_bottom(self) -> bool:
        adj = self.answer_scroller.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())
        return GLib.SOURCE_REMOVE

    def _set_picture(self, req_id, texture) -> None:
        if req_id == self.current_id and texture is not None:
            self.picture.set_paintable(texture)

    def _block_widget(self, kind: str, text: str) -> Gtk.Widget:
        if kind == "code":
            label = Gtk.Label(label=text, xalign=0, selectable=True, wrap=True, wrap_mode=2,
                              hexpand=True, css_classes=["monospace"])
            frame = Gtk.Box(css_classes=["code"])
            frame.append(label)
            copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy",
                              valign=Gtk.Align.START, css_classes=["flat", "code-copy"])
            copy.connect("clicked", lambda _b, t=text: self._copy(t))
            frame.append(copy)
            return frame
        label = Gtk.Label(xalign=0, selectable=True, wrap=True, wrap_mode=2,
                          use_markup=True, css_classes=["answer-text", kind])
        label.set_markup(text)
        return label

    def _copy(self, text: str) -> None:
        self.get_clipboard().set(text)
        self.toast("Copied")

    def _park_focus(self) -> bool:
        """Keep keyboard focus off the selectable labels when the page opens, so no text
        caret shows up; clicking a label to select text still focuses it normally."""
        if self.nav.get_visible_page() is self.answer_page:
            self.answer_scroller.grab_focus()
        return GLib.SOURCE_REMOVE

    def _on_picture_clicked(self, *_args) -> None:
        req = self.app.history.get(self.current_id) if self.current_id else None
        if req and req.image and os.path.exists(req.image):
            Gtk.FileLauncher.new(Gio.File.new_for_path(req.image)).launch(self, None, None)

    def _on_copy(self, _btn) -> None:
        req = self.app.history.get(self.current_id) if self.current_id else None
        if req and req.answer:
            self._copy(req.answer)

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
        row.count = Gtk.Label(css_classes=["dim-label", "caption"], valign=Gtk.Align.CENTER)
        row.add_suffix(row.count)
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
        row.count.set_label(f"+{len(req.followups)}" if req.followups else "")
        row.count.set_visible(bool(req.followups))
        row.dot.set_css_classes(["dot", req.status])

    def _update_empty(self) -> None:
        self.empty.set_description(GLib.markup_escape_text(
            f"Press {self.app.shortcut_label()} anywhere to get help with your screen.", -1))
        self.lib_stack.set_visible_child_name("list" if self._rows else "empty")

    def _on_row_activated(self, _list, row) -> None:
        self.show_request(row.req_id)

    def _on_close(self, _win) -> bool:
        self.set_visible(False)
        return True
