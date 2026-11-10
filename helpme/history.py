"""Request history (persisted) and old-screenshot cleanup."""

import dataclasses
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from helpme import config

INTERRUPTED = "Interrupted (the app was closed while this was running)"
STATUSES = ("thinking", "retrying", "done", "failed", "cancelled")
_OWN_FILE = re.compile(r"^\d{6}_\d{6}(_\d+)?\.png$")


@dataclass
class Request:
    id: str
    created: str
    image: str
    mode: str
    status: str
    answer: str = ""
    error: str = ""
    attempts: int = 0


def _from_dict(d) -> Request | None:
    if not isinstance(d, dict):
        return None
    try:
        r = Request(**{f.name: d[f.name] for f in dataclasses.fields(Request)})
    except (KeyError, TypeError):
        return None
    if r.status not in STATUSES or not isinstance(r.id, str):
        return None
    return r


class History:
    def __init__(self, path: str | None = None):
        self.path = path or config.history_path()
        self._items: list[Request] = []  # oldest first

    def load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, ValueError):
            raw = []
        if not isinstance(raw, list):
            raw = []
        self._items = [r for r in map(_from_dict, raw) if r is not None]
        changed = False
        for r in self._items:
            if r.status in ("thinking", "retrying"):
                r.status, r.error = "failed", INTERRUPTED
                changed = True
        if changed:
            self.save()

    def save(self) -> None:
        config.write_json_atomic(self.path, [dataclasses.asdict(r) for r in self._items])

    def add(self, image: str, mode: str) -> Request:
        r = Request(id=uuid.uuid4().hex,
                    created=datetime.now().isoformat(timespec="seconds"),
                    image=image, mode=mode, status="thinking")
        self._items.append(r)
        self.save()
        return r

    def get(self, req_id: str) -> Request | None:
        return next((r for r in self._items if r.id == req_id), None)

    def update(self, req_id: str, **fields) -> Request:
        r = self.get(req_id)
        if r is None:
            raise KeyError(req_id)
        for k, v in fields.items():
            if not hasattr(r, k):
                raise AttributeError(k)
            setattr(r, k, v)
        self.save()
        return r

    def delete(self, req_id: str) -> None:
        r = self.get(req_id)
        if r is None:
            return
        self._items.remove(r)
        if r.image:
            try:
                os.remove(r.image)
            except OSError:
                pass
        self.save()

    def all(self) -> list[Request]:
        return list(reversed(self._items))

    def newest(self) -> Request | None:
        return self._items[-1] if self._items else None

    def clear(self) -> None:
        self._items = []
        self.save()


def cleanup_screenshots(dirpath: str, keep_days: int, now: float) -> list[str]:
    """Delete our own YYMMDD_HHMMSS*.png files older than keep_days (0 = keep all)."""
    if keep_days <= 0:
        return []
    try:
        names = os.listdir(dirpath)
    except OSError:
        return []
    cutoff = now - keep_days * 86400
    deleted = []
    for name in names:
        if not _OWN_FILE.match(name):
            continue
        p = os.path.join(dirpath, name)
        try:
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
                deleted.append(p)
        except OSError:
            pass
    return deleted
