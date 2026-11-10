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
    prompt: str = ""  # the prompt sent with the screenshot (context for follow-ups)
    followups: list = dataclasses.field(default_factory=list)  # [{q, answer, status, error, attempts}]


def _followup_from_dict(d) -> dict | None:
    if not isinstance(d, dict) or not isinstance(d.get("q"), str):
        return None
    if d.get("status") not in STATUSES:
        return None
    attempts = d.get("attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        attempts = 0
    return {"q": d["q"], "status": d["status"],
            "answer": d["answer"] if isinstance(d.get("answer"), str) else "",
            "error": d["error"] if isinstance(d.get("error"), str) else "",
            "attempts": attempts}


def _from_dict(d) -> Request | None:
    """A stored entry, or None if a required field is missing or the wrong type.
    Optional fields (answer, error, attempts) fall back to their defaults."""
    if not isinstance(d, dict):
        return None
    for key in ("id", "created", "image", "mode", "status"):
        if not isinstance(d.get(key), str):
            return None
    if d["status"] not in STATUSES or d["mode"] not in ("full", "area"):
        return None
    attempts = d.get("attempts")
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 0:
        attempts = 0
    return Request(id=d["id"], created=d["created"], image=d["image"], mode=d["mode"],
                   status=d["status"],
                   answer=d["answer"] if isinstance(d.get("answer"), str) else "",
                   error=d["error"] if isinstance(d.get("error"), str) else "",
                   attempts=attempts,
                   prompt=d["prompt"] if isinstance(d.get("prompt"), str) else "",
                   followups=[f for f in map(_followup_from_dict, d.get("followups") or []
                                             if isinstance(d.get("followups"), list) else [])
                              if f is not None])


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
            for f in r.followups:
                if f["status"] in ("thinking", "retrying"):
                    f["status"], f["error"] = "failed", INTERRUPTED
                    changed = True
        if changed:
            self.save()

    def save(self) -> None:
        config.write_json_atomic(self.path, [dataclasses.asdict(r) for r in self._items])

    def add(self, image: str, mode: str, prompt: str = "") -> Request:
        r = Request(id=uuid.uuid4().hex,
                    created=datetime.now().isoformat(timespec="seconds"),
                    image=image, mode=mode, status="thinking", prompt=prompt)
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

    def add_followup(self, req_id: str, question: str) -> int:
        r = self.get(req_id)
        if r is None:
            raise KeyError(req_id)
        r.followups.append({"q": question, "answer": "", "status": "thinking", "error": "",
                            "attempts": 0})
        self.save()
        return len(r.followups) - 1

    def update_followup(self, req_id: str, index: int, **fields) -> dict:
        r = self.get(req_id)
        if r is None:
            raise KeyError(req_id)
        f = r.followups[index]
        for k, v in fields.items():
            if k not in f:
                raise AttributeError(k)
            f[k] = v
        self.save()
        return f

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
