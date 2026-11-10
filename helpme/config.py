"""Paths, settings and logging for Help Me."""

import dataclasses
import json
import logging
import logging.handlers
import os
from dataclasses import dataclass

APP_ID = "local.claude.HelpMe"
MODELS = ("sonnet", "haiku", "opus")
CAPTURE_MODES = ("full", "area")
DEFAULT_PROMPT = ("Look at this screenshot, work out what I'm doing and what I probably "
                  "need help with, then help — short and practical.")


def _xdg(var: str, fallback: str) -> str:
    return os.environ.get(var) or os.path.join(os.path.expanduser("~"), fallback)


def config_dir() -> str:
    return os.path.join(_xdg("XDG_CONFIG_HOME", ".config"), "helpme")


def data_dir() -> str:
    return os.path.join(_xdg("XDG_DATA_HOME", ".local/share"), "helpme")


def state_dir() -> str:
    return os.path.join(_xdg("XDG_STATE_HOME", ".local/state"), "helpme")


def settings_path() -> str:
    return os.path.join(config_dir(), "settings.json")


def history_path() -> str:
    return os.path.join(data_dir(), "history.json")


def log_path() -> str:
    return os.path.join(state_dir(), "helpme.log")


def screencast_token_path() -> str:
    return os.path.join(state_dir(), "screencast_token")


def work_dir() -> str:
    """Empty folder claude runs in, so no project CLAUDE.md is picked up."""
    path = os.path.join(state_dir(), "work")
    os.makedirs(path, exist_ok=True)
    return path


def default_screenshot_dir() -> str:
    pictures = None
    try:
        from gi.repository import GLib
        pictures = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_PICTURES)
    except Exception:
        pass
    if not pictures:
        pictures = os.path.join(os.path.expanduser("~"), "Pictures")
    return os.path.join(pictures, "Screenshots", "HelpMe")


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
    "autostart": lambda v: isinstance(v, bool),
    "jump_to_newest": lambda v: isinstance(v, bool),
}


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
    log = logging.getLogger("helpme")
    if log.handlers:
        return
    os.makedirs(state_dir(), exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
