"""Ask Claude (via `claude -p` on the user's subscription) about a screenshot."""

import base64
import json
import logging
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from helpme import config  # noqa: E402

SYSTEM_PROMPT = ("The user pressed a 'help me' key and sent a screenshot of their screen. "
                 "Help with what they are most likely stuck on. Be concise and practical. "
                 "Use Markdown. Reply in the language of the user's prompt.")
LENGTH_RULES = {
    "short": " Lead with the answer. Keep it under about 120 words unless steps or code "
             "are truly needed.",
    "detailed": " Be thorough where it helps, but stay practical.",
}
RETRY_DELAY_S = 20
MAX_ATTEMPTS = 2
MAX_IMAGE_BYTES = 3_500_000
MAX_LONG_EDGE = 1920

log = logging.getLogger("helpme")


@dataclass
class Result:
    ok: bool
    text: str
    usage: dict = field(default_factory=dict)
    session_id: str = ""


def system_prompt(length: str) -> str:
    return SYSTEM_PROMPT + LENGTH_RULES.get(length, LENGTH_RULES["short"])


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    fallback = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude")
    return fallback if os.access(fallback, os.X_OK) else None


def build_command(claude: str, model: str, session_id: str | None = None,
                  resume: str | None = None, length: str = "short") -> list[str]:
    """New saved session (session_id), continue one (resume), or a throwaway run."""
    cmd = [claude, "-p", "--model", model, "--effort", "high",
           "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
           "--tools", "", "--strict-mcp-config", "--setting-sources", "",
           "--system-prompt", system_prompt(length)]
    if resume:
        return cmd + ["--resume", resume]
    if session_id:
        return cmd + ["--session-id", session_id]
    return cmd + ["--no-session-persistence"]


def build_text_payload(prompt: str) -> bytes:
    msg = {"type": "user", "message": {"role": "user",
                                       "content": [{"type": "text", "text": prompt}]}}
    return (json.dumps(msg) + "\n").encode("utf-8")


def session_file(session_id: str) -> str:
    """Where claude keeps a saved session: its own project folder for Help Me's work dir,
    so these never show up in the user's normal /resume lists."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    project = re.sub(r"[^A-Za-z0-9]", "-", config.work_dir_path())
    return os.path.join(base, "projects", project, f"{session_id}.jsonl")


def delete_session(session_id: str) -> None:
    if not session_id or not re.fullmatch(r"[A-Za-z0-9-]+", session_id):
        return
    try:
        os.remove(session_file(session_id))
    except OSError:
        pass


def cleanup_sessions(keep_days: int, now: float) -> list[str]:
    if keep_days <= 0:
        return []
    folder = os.path.dirname(session_file("x"))
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    cutoff, removed = now - keep_days * 86400, []
    for name in names:
        p = os.path.join(folder, name)
        try:
            if name.endswith(".jsonl") and os.path.getmtime(p) < cutoff:
                os.remove(p)
                removed.append(p)
        except OSError:
            pass
    return removed


def format_usage(u: dict) -> str:
    try:
        total = int(u["tokens_in"]) + int(u["tokens_out"])
        cost, secs = float(u["cost"]), round(float(u["ms"]) / 1000)
    except (KeyError, TypeError, ValueError):
        return ""
    m = re.fullmatch(r"claude-([a-z]+)-(\d+)(?:-(\d+))?", str(u.get("model", "")))
    model = (f"{m.group(1).capitalize()} {m.group(2)}" + (f".{m.group(3)}" if m.group(3) else "")
             if m else str(u.get("model", "")) or "Claude")
    tokens = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
    price = "<$0.01" if cost < 0.005 else f"≈${cost:.2f}"
    return f"{model} · {tokens} tokens · {price} · {secs} s"


def prepare_image(path: str) -> tuple[bytes, str]:
    with open(path, "rb") as f:
        data = f.read()
    if len(data) <= MAX_IMAGE_BYTES:
        return data, "image/png"
    pb = GdkPixbuf.Pixbuf.new_from_file(path)
    w, h = pb.get_width(), pb.get_height()
    scale = min(1.0, MAX_LONG_EDGE / max(w, h))
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if pb.get_has_alpha():
        # JPEG (glycin encoder) only takes RGB: flatten onto white.
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, nw, nh)
        flat.fill(0xffffffff)
        pb.composite(flat, 0, 0, nw, nh, 0, 0, nw / w, nh / h,
                     GdkPixbuf.InterpType.BILINEAR, 255)
        pb = flat
    elif (nw, nh) != (w, h):
        pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
    ok, buf = pb.save_to_bufferv("jpeg", ["quality"], ["85"])
    return bytes(buf), "image/jpeg"


def build_payload(data: bytes, media_type: str, prompt: str) -> bytes:
    msg = {"type": "user", "message": {"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                     "data": base64.b64encode(data).decode("ascii")}},
        {"type": "text", "text": prompt},
    ]}}
    return (json.dumps(msg) + "\n").encode("utf-8")


def _usage(result: dict, model: str) -> dict:
    u = result.get("usage")
    if not isinstance(u, dict):
        return {}
    num = lambda k: u.get(k) if isinstance(u.get(k), (int, float)) else 0  # noqa: E731
    return {"model": model,
            "tokens_in": num("input_tokens") + num("cache_read_input_tokens")
            + num("cache_creation_input_tokens"),
            "tokens_out": num("output_tokens"),
            "cost": result.get("total_cost_usd") or 0,
            "ms": result.get("duration_ms") or 0}


def parse_output(stdout: str, stderr: str, exit_code: int) -> Result:
    result, model = None, ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and isinstance(event.get("model"), str):
            model = event["model"]
        if event.get("type") == "result":
            result = event
    text = ""
    if result is not None and isinstance(result.get("result"), str):
        text = result["result"].strip()
    if exit_code == 0 and result is not None and not result.get("is_error") and text:
        sid = result.get("session_id")
        return Result(True, text, _usage(result, model), sid if isinstance(sid, str) else "")
    if text:
        return Result(False, text)
    err_lines = [l.strip() for l in stderr.splitlines() if l.strip()]
    if err_lines:
        return Result(False, err_lines[-1])
    if exit_code == 0:
        return Result(False, "Claude returned an empty answer")
    return Result(False, f"claude exited with code {exit_code}")


def followup_prompt(first_prompt: str, first_answer: str,
                    previous: list[tuple[str, str]], question: str) -> str:
    """Text sent with the same screenshot for a follow-up (claude -p keeps no session)."""
    parts = ["This is a follow-up about the same screenshot.",
             f"The user first asked:\n{first_prompt}", f"You answered:\n{first_answer}"]
    for q, a in previous:
        parts += [f"The user then asked:\n{q}", f"You answered:\n{a}"]
    parts.append(f"Now answer the user's new question:\n{question}")
    return "\n\n".join(parts)


def retry_delay(attempt: int) -> int | None:
    """Seconds to wait before the next attempt, given attempts made so far."""
    return RETRY_DELAY_S if attempt < MAX_ATTEMPTS else None


class _Job:
    def __init__(self, image, model, prompt, on_retrying, on_done):
        self.image, self.model, self.prompt = image, model, prompt
        self.length = "short"
        self.new_session = False
        self.resume = None
        self.session_id = None
        self.on_retrying, self.on_done = on_retrying, on_done
        self.attempts = 0
        self.proc = None
        self.timer = 0
        self.cancellable = Gio.Cancellable()
        self.cancelled = False


class Asker:
    """Runs one claude request per screenshot, with one automatic retry."""

    def __init__(self):
        self._jobs: dict[str, _Job] = {}

    def ask(self, req_id, image, settings, on_retrying, on_done, prompt=None,
            new_session=False, resume=None) -> None:
        """new_session: save the conversation so follow-ups can resume it.
        resume: continue that saved session with a text-only message."""
        self.cancel(req_id)
        job = _Job(image, settings.model, prompt if prompt is not None else settings.prompt,
                   on_retrying, on_done)
        job.length = getattr(settings, "answer_length", "short")
        job.new_session, job.resume = new_session, resume
        self._jobs[req_id] = job
        self._attempt(req_id, job)

    def cancel(self, req_id) -> None:
        job = self._jobs.pop(req_id, None)
        if job is None:
            return
        job.cancelled = True
        job.cancellable.cancel()
        if job.timer:
            GLib.source_remove(job.timer)
            job.timer = 0
        if job.proc is not None:
            job.proc.force_exit()
        log.info("request %s cancelled", req_id)

    def running(self, req_id) -> bool:
        return req_id in self._jobs

    def _attempt(self, req_id, job) -> None:
        job.timer = 0
        job.attempts += 1
        claude = find_claude()
        if claude is None:
            self._finish_attempt(req_id, job, Result(
                False, "claude CLI not found (looked in PATH and ~/.local/bin)"))
            return
        if job.resume:
            payload_bytes = build_text_payload(job.prompt)
        else:
            try:
                data, media_type = prepare_image(job.image)
            except (OSError, GLib.Error) as e:
                self._finish_attempt(req_id, job,
                                     Result(False, f"Could not read screenshot: {e}"))
                return
            payload_bytes = build_payload(data, media_type, job.prompt)
        job.session_id = str(uuid.uuid4()) if job.new_session else None
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_PIPE
                                              | Gio.SubprocessFlags.STDOUT_PIPE
                                              | Gio.SubprocessFlags.STDERR_PIPE)
        launcher.set_cwd(config.work_dir())
        try:
            job.proc = launcher.spawnv(build_command(claude, job.model, job.session_id,
                                                     job.resume, job.length))
        except GLib.Error as e:
            self._finish_attempt(req_id, job, Result(False, f"Could not start claude: {e.message}"))
            return
        log.info("request %s attempt %d started (%s)", req_id, job.attempts, job.image)
        payload = GLib.Bytes.new(payload_bytes)
        job.proc.communicate_async(payload, job.cancellable, self._on_communicated, (req_id, job))

    def _on_communicated(self, proc, res, user_data) -> None:
        req_id, job = user_data
        if job.cancelled:
            return
        try:
            _ok, out, err = proc.communicate_finish(res)
        except GLib.Error as e:
            self._finish_attempt(req_id, job, Result(False, f"claude failed: {e.message}"))
            return
        job.proc = None
        code = proc.get_exit_status() if proc.get_if_exited() else -1
        stdout = out.get_data().decode("utf-8", "replace") if out else ""
        stderr = err.get_data().decode("utf-8", "replace") if err else ""
        result = parse_output(stdout, stderr, code)
        if job.session_id:
            if result.ok:
                result.session_id = result.session_id or job.session_id
            else:
                delete_session(job.session_id)  # half-made session of a failed attempt
        self._finish_attempt(req_id, job, result)

    def _finish_attempt(self, req_id, job, result: Result) -> None:
        if job.cancelled:
            return
        if result.ok:
            log.info("request %s answered after %d attempt(s)", req_id, job.attempts)
            self._jobs.pop(req_id, None)
            job.on_done(req_id, result, job.attempts)
            return
        delay = retry_delay(job.attempts)
        if delay is not None:
            log.warning("request %s attempt %d failed (%s): %s — retrying in %ds",
                        req_id, job.attempts, job.image, result.text, delay)
            job.timer = GLib.timeout_add_seconds(delay, self._retry, req_id, job)
            job.on_retrying(req_id, result.text, delay)
            return
        log.error("request %s failed after %d attempts (%s): %s",
                  req_id, job.attempts, job.image, result.text)
        self._jobs.pop(req_id, None)
        job.on_done(req_id, result, job.attempts)

    def _retry(self, req_id, job) -> bool:
        if not job.cancelled:
            self._attempt(req_id, job)
        return GLib.SOURCE_REMOVE
