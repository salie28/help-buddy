"""GNOME integration: global shortcut, autostart, install/uninstall."""

import os
import shlex

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from helpme.config import APP_ID  # noqa: E402

KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/helpme/"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_BIN = os.path.join(REPO_ROOT, "bin", "helpme")


def user_bin() -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "helpme")


def helpme_path() -> str:
    """Absolute path of the helpme command (GNOME shortcuts don't get ~/.local/bin on PATH)."""
    link = user_bin()
    return link if os.path.exists(link) else REPO_BIN


def shortcut_command() -> str:
    return f"{shlex.quote(helpme_path())} capture"


def with_keybinding(paths: list[str]) -> list[str]:
    return list(paths) if KEYBINDING_PATH in paths else list(paths) + [KEYBINDING_PATH]


def without_keybinding(paths: list[str]) -> list[str]:
    return [p for p in paths if p != KEYBINDING_PATH]


def valid_accel(accel: str) -> bool:
    ok, key, _mods = Gtk.accelerator_parse(accel)
    return bool(ok) and key != 0


def set_shortcut(accel: str) -> None:
    if not valid_accel(accel):
        raise ValueError(f"Not a valid shortcut: {accel!r}")
    custom = Gio.Settings.new_with_path(CUSTOM_KEY, KEYBINDING_PATH)
    custom.set_string("name", "Help Me")
    custom.set_string("command", shortcut_command())
    custom.set_string("binding", accel)
    keys = Gio.Settings.new(MEDIA_KEYS)
    keys.set_strv("custom-keybindings", with_keybinding(keys.get_strv("custom-keybindings")))
    Gio.Settings.sync()


def remove_shortcut() -> None:
    keys = Gio.Settings.new(MEDIA_KEYS)
    keys.set_strv("custom-keybindings", without_keybinding(keys.get_strv("custom-keybindings")))
    custom = Gio.Settings.new_with_path(CUSTOM_KEY, KEYBINDING_PATH)
    for k in ("name", "command", "binding"):
        custom.reset(k)
    Gio.Settings.sync()


def autostart_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "autostart", f"{APP_ID}.desktop")


def autostart_desktop() -> str:
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Help Me\n"
            "Comment=Screenshot help key (runs in the background)\n"
            f"Exec={shlex.quote(helpme_path())} --background\n"
            "Icon=help-browser-symbolic\n"
            "NoDisplay=true\n"
            "X-GNOME-Autostart-enabled=true\n")


def set_autostart(on: bool) -> None:
    path = autostart_path()
    if on:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(autostart_desktop())
    else:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
