"""Paths, settings and logging for Help Buddy."""

import dataclasses
import json
import logging
import logging.handlers
import os
import re
from dataclasses import dataclass

APP_ID = "local.claude.HelpBuddy"
MODELS = ("sonnet", "haiku", "opus")
CAPTURE_MODES = ("full", "area")
DEFAULT_PROMPT = ("Look at this screenshot, work out what I'm doing and what I probably "
                  "need help with, then help — short and practical.")


def _xdg(var: str, fallback: str) -> str:
    return os.environ.get(var) or os.path.join(os.path.expanduser("~"), fallback)


def config_dir() -> str:
    return os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "helpbuddy")


def data_dir() -> str:
    return os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), "helpbuddy")


def state_dir() -> str:
    return os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), "helpbuddy")


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def history_path() -> str:
    return os.path.join(data_dir(), "history.json")


def log_path() -> str:
    return os.path.join(state_dir(), "helpbuddy.log")


def screencast_token_path() -> str:
    return os.path.join(state_dir(), "screencast_token")


def work_dir_path() -> str:
    return os.path.join(state_dir(), "work")


def work_dir() -> str:
    """Empty folder claude runs in, so no project CLAUDE.md is picked up (created)."""
    path = work_dir_path()
    os.makedirs(path, exist_ok=True)
    return path


def claude_project_dir(work_path: str) -> str:
    """Claude Code's project folder for a working directory (where sessions are saved)."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    return os.path.join(base, "projects", re.sub(r"[^A-Za-z0-9]", "-", work_path))


def default_screenshot_dir() -> str:
    pictures = None
    try:
        from gi.repository import GLib
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    except Exception:
        pass
    if not pictures:
        pictures = os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(pictures, "Screenshots", "HelpBuddy")


@dataclass
class Settings:
    capture_mode: str = "full"
    area: list | None = None  # [x, y, w, h] as fractions of the full screenshot
    shortcut: str = "<Super><Shift>h"
    prompt: str = DEFAULT_PROMPT
    model: str = "sonnet"
    screenshot_dir: str = dataclasses.field(default_factory=default_screenshot_dir)
    keep_days: int = 30
    flash: bool = False  # False = silent ScreenCast frame grab, True = Screenshot portal
    answer_length: str = "short"  # "short" (≈120 words) or "detailed"
    glass: int = 75  # window darkness in percent (30 = very see-through, 100 = solid)
    autostart: bool = False
    jump_to_newest: bool = True


def _valid_area(v) -> bool:
    if not isinstance(v, list) or len(v) != 4:
        return False
    if not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in v):
        return False
    x, y, w, h = v
    eps = 1e-9
    return (w > 0 and h > 0 and 0 <= x and 0 <= y
            and x + w <= 1 + eps and y + h <= 1 + eps)


_VALIDATORS = {
    "capture_mode": lambda v: v in CAPTURE_MODES,
    "area": lambda v: v is None or _valid_area(v),
    "shortcut": lambda v: isinstance(v, str) and v.strip() != "",
    "prompt": lambda v: isinstance(v, str) and v.strip() != "",
    "model": lambda v: v in MODELS,
    "screenshot_dir": lambda v: isinstance(v, str) and v.strip() != "",
    "keep_days": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 3650,
    "flash": lambda v: isinstance(v, bool),
    "answer_length": lambda v: v in ("short", "detailed"),
    "glass": lambda v: isinstance(v, int) and not isinstance(v, bool) and 30 <= v <= 100,
    "autostart": lambda v: isinstance(v, bool),
    "jump_to_newest": lambda v: isinstance(v, bool),
}


def should_show_window(window_exists: bool, window_visible: bool) -> bool:
    """On a key press: open the Help window if it is closed (or was never opened);
    if it is open — on any desktop — leave it alone (no raise, no focus)."""
    return not (window_exists and window_visible)


def glass_css(glass: int) -> str:
    """Window tint for the given darkness (percent); the blur comes from Blur My Shell."""
    top = glass / 100
    bottom = min(1.0, top + 0.06)
    return ("window.helpbuddy .glass { background-image: linear-gradient(to bottom, "
            f"rgba(30, 30, 30, {top:.2f}), rgba(12, 12, 12, {bottom:.2f})); }}")


def load_settings(path: str | None = None) -> Settings:
    path = path or settings_path()
    s = Settings()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return s
    if not isinstance(raw, dict):
        return s
    for key, ok in _VALIDATORS.items():
        if key in raw and ok(raw[key]):
            value = raw[key]
            if key == "area" and value is not None:
                value = [float(n) for n in value]
            setattr(s, key, value)
    return s


def write_json_atomic(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def save_settings(s: Settings, path: str | None = None) -> None:
    write_json_atomic(path or settings_path(), dataclasses.asdict(s))


def setup_logging() -> None:
    log = logging.getLogger("helpbuddy")
    if log.handlers:
        return
    os.makedirs(state_dir(), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
