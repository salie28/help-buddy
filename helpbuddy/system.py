"""GNOME integration: global shortcut, autostart, install/uninstall."""

import os
import shlex

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, Gtk  # noqa: E402

from helpbuddy.config import APP_ID  # noqa: E402

KEYBINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/helpbuddy/"
MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
CUSTOM_KEY = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_BIN = os.path.join(REPO_ROOT, "bin", "helpbuddy")


def user_bin() -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "bin", "helpbuddy")


def helpbuddy_path() -> str:
    """Absolute path of the helpbuddy command (GNOME shortcuts don't get ~/.local/bin on PATH)."""
    link = user_bin()
    return link if os.path.exists(link) else REPO_BIN


def shortcut_command() -> str:
    return f"{shlex.quote(helpbuddy_path())} capture"


def with_keybinding(paths: list[str]) -> list[str]:
    return list(paths) if KEYBINDING_PATH in paths else list(paths) + [KEYBINDING_PATH]


def without_keybinding(paths: list[str]) -> list[str]:
    return [p for p in paths if p != KEYBINDING_PATH]


def valid_accel(accel: str) -> bool:
    ok, key, _mods = Gtk.accelerator_parse(accel)
    return bool(ok) and key != 0


_DEAD_KEYS = {"dead_circumflex": "^", "dead_grave": "`", "dead_acute": "´",
              "dead_tilde": "~", "dead_diaeresis": "¨", "dead_cedilla": "¸",
              "dead_abovering": "°", "dead_caron": "ˇ"}


def accel_from_key(keyval: int, state) -> str | None:
    """Accelerator string for a recorded key press, or None if it isn't a usable
    global shortcut (modifier alone, or a key you'd normally type)."""
    from gi.repository import Gdk
    mods = Gdk.ModifierType(int(state)) & Gtk.accelerator_get_default_mod_mask()
    keyval = Gdk.keyval_to_lower(keyval)
    name = Gdk.keyval_name(keyval) or ""
    if not name or name.startswith(("Control_", "Shift_", "Alt_", "Super_", "Meta_",
                                    "Hyper_", "ISO_Level", "Caps_Lock", "Num_Lock")):
        return None
    is_fkey = name.startswith("F") and name[1:].isdigit()
    typing_mods = mods & ~Gdk.ModifierType.SHIFT_MASK
    if not is_fkey and not typing_mods:
        return None  # plain or Shift+key would fire while typing
    accel = Gtk.accelerator_name(keyval, mods)
    return accel if valid_accel(accel) else None


def pretty_accel(accel: str) -> str:
    ok, key, mods = Gtk.accelerator_parse(accel)
    if not ok or key == 0:
        return accel
    label = Gtk.accelerator_get_label(key, mods)
    from gi.repository import Gdk
    dead = _DEAD_KEYS.get(Gdk.keyval_name(key) or "")
    if dead:
        base = Gtk.accelerator_get_label(0, mods)
        label = f"{base}+{dead}" if base else dead
    return label


def set_shortcut(accel: str) -> None:
    if not valid_accel(accel):
        raise ValueError(f"Not a valid shortcut: {accel!r}")
    custom = Gio.Settings.new_with_path(CUSTOM_KEY, KEYBINDING_PATH)
    custom.set_string("name", "Help Buddy")
    custom.set_string("command", shortcut_command())
    custom.set_string("binding", accel)
    keys = Gio.Settings.new(MEDIA_KEYS)
    keys.set_strv("custom-keybindings", with_keybinding(keys.get_strv("custom-keybindings")))
    Gio.Settings.sync()


def ensure_shortcut(accel: str) -> bool:
    """If Help Buddy is installed (our path is registered) but the binding was lost, e.g. the
    app died while recording a new key, put it back. Returns True if it fixed something."""
    keys = Gio.Settings.new(MEDIA_KEYS)
    if KEYBINDING_PATH not in keys.get_strv("custom-keybindings"):
        return False
    custom = Gio.Settings.new_with_path(CUSTOM_KEY, KEYBINDING_PATH)
    if custom.get_string("binding") == accel and custom.get_string("command"):
        return False
    set_shortcut(accel)
    return True


def suspend_shortcut() -> None:
    """Unbind the key for a moment (while recording a new one); set_shortcut restores."""
    custom = Gio.Settings.new_with_path(CUSTOM_KEY, KEYBINDING_PATH)
    custom.set_string("binding", "")
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
            "Name=Help Buddy\n"
            "Comment=Screenshot help key (runs in the background)\n"
            f"Exec={shlex.quote(helpbuddy_path())} --background\n"
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


# ---- install / uninstall ----

BMS_SCHEMA = "org.gnome.shell.extensions.blur-my-shell.applications"
BMS_SCHEMA_DIRS = [
    os.path.join(os.path.expanduser("~"), ".local/share/gnome-shell/extensions",
                 "blur-my-shell@aunetx/schemas"),
    "/usr/share/gnome-shell/extensions/blur-my-shell@aunetx/schemas",
]


def with_item(items: list[str], item: str) -> list[str]:
    return list(items) if item in items else list(items) + [item]


def without_item(items: list[str], item: str) -> list[str]:
    return [i for i in items if i != item]


def link_is_ours(path: str) -> bool:
    return os.path.islink(path) and os.path.realpath(path) == os.path.realpath(REPO_BIN)


def desktop_path() -> str:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"),
                                                           ".local", "share")
    return os.path.join(base, "applications", f"{APP_ID}.desktop")


def desktop_entry() -> str:
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Help Buddy\n"
            "Comment=Screenshot your screen and ask Claude for help\n"
            f"Exec={shlex.quote(helpbuddy_path())}\n"
            "Icon=help-browser-symbolic\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
            "Keywords=help;claude;screenshot;ai;\n"
            "StartupNotify=true\n")


def _bms_settings():
    """Blur My Shell's application settings, or None if BMS isn't installed."""
    source = Gio.SettingsSchemaSource.get_default()
    if source and source.lookup(BMS_SCHEMA, True):
        return Gio.Settings.new(BMS_SCHEMA)
    for d in BMS_SCHEMA_DIRS:
        if os.path.exists(os.path.join(d, "gschemas.compiled")):
            src = Gio.SettingsSchemaSource.new_from_directory(d, source, False)
            schema = src.lookup(BMS_SCHEMA, False)
            if schema:
                return Gio.Settings.new_full(schema, None, None)
    return None


def set_bms_whitelist(on: bool) -> bool:
    bms = _bms_settings()
    if bms is None:
        return False
    current = bms.get_strv("whitelist")
    bms.set_strv("whitelist", with_item(current, APP_ID) if on else without_item(current, APP_ID))
    Gio.Settings.sync()
    return True


def installed_version_path() -> str:
    from helpbuddy import config
    return os.path.join(config.state_dir(), "installed_version")


def read_installed_version() -> str | None:
    try:
        with open(installed_version_path(), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def write_installed_version(version: str) -> None:
    os.makedirs(os.path.dirname(installed_version_path()), exist_ok=True)
    with open(installed_version_path(), "w", encoding="utf-8") as f:
        f.write(version + "\n")


def install_message(previous: str | None, current: str) -> str:
    if previous is None:
        return f"Installed Help Buddy {current}"
    if previous == current:
        return f"Reinstalled Help Buddy {current}"
    return f"Updated Help Buddy {previous} → {current}"


def install() -> None:
    from helpbuddy import __version__
    from helpbuddy.config import load_settings
    previous = read_installed_version()
    link = user_bin()
    os.makedirs(os.path.dirname(link), exist_ok=True)
    if os.path.lexists(link) and not link_is_ours(link):
        if not os.path.islink(link):
            raise SystemExit(f"{link} exists and is not a symlink; not touching it.")
        os.remove(link)
    if not os.path.lexists(link):
        os.symlink(REPO_BIN, link)
    print(f"command:   {link} -> {REPO_BIN}")
    os.makedirs(os.path.dirname(desktop_path()), exist_ok=True)
    with open(desktop_path(), "w", encoding="utf-8") as f:
        f.write(desktop_entry())
    print(f"app entry: {desktop_path()}")
    settings = load_settings()
    set_shortcut(settings.shortcut)
    print(f"shortcut:  {settings.shortcut} -> {shortcut_command()}")
    if settings.autostart:
        set_autostart(True)
        print(f"autostart: {autostart_path()}")
    if set_bms_whitelist(True):
        print(f"blur:      {APP_ID} added to Blur My Shell's application whitelist")
    else:
        print("blur:      Blur My Shell not found; the window stays tinted without blur")
    write_installed_version(__version__)
    print(install_message(previous, __version__))


def uninstall() -> None:
    from helpbuddy import config
    link = user_bin()
    if link_is_ours(link):
        os.remove(link)
        print(f"removed {link}")
    for path in (desktop_path(), autostart_path()):
        if os.path.exists(path):
            os.remove(path)
            print(f"removed {path}")
    remove_shortcut()
    print("removed the shortcut")
    if set_bms_whitelist(False):
        print("removed the Blur My Shell whitelist entry")
    print("kept: settings", config.settings_path(), "| history", config.history_path(),
          "| log", config.log_path(), "| screenshots in your screenshot folder")


def purge_data() -> list[str]:
    """Delete Help Buddy's own data (settings, history, log, saved Claude sessions).
    Screenshots are never touched. Returns the removed paths."""
    import shutil
    from helpbuddy import ask, config
    removed = []
    for path in (config.config_dir(), config.data_dir(),
                 os.path.dirname(ask.session_file("x")), config.state_dir()):
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed
