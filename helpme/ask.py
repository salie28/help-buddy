"""Ask Claude (via `claude -p` on the user's subscription) about a screenshot."""

import base64
import json
import logging
import os
import shutil
from dataclasses import dataclass

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from helpme import config  # noqa: E402

SYSTEM_PROMPT = ("The user pressed a 'help me' key and sent a screenshot of their screen. "
                 "Help with what they are most likely stuck on. Be concise and practical. "
                 "Use Markdown. Reply in the language of the user's prompt.")
RETRY_DELAY_S = 20
MAX_ATTEMPTS = 2
MAX_IMAGE_BYTES = 3_500_000
MAX_LONG_EDGE = 1920

log = logging.getLogger("helpme")


@dataclass
class Result:
    ok: bool
    text: str


def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    fallback = os.path.join(os.path.expanduser("~"), ".local", "bin", "claude")
    return fallback if os.access(fallback, os.X_OK) else None


def build_command(claude: str, model: str) -> list[str]:
    return [claude, "-p", "--model", model,
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--no-session-persistence", "--strict-mcp-config",
            "--setting-sources", "", "--system-prompt", SYSTEM_PROMPT]


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


def parse_output(stdout: str, stderr: str, exit_code: int) -> Result:
    result = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "result":
            result = event
    text = ""
    if result is not None and isinstance(result.get("result"), str):
        text = result["result"].strip()
    if exit_code == 0 and result is not None and not result.get("is_error") and text:
        return Result(True, text)
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

    def ask(self, req_id, image, settings, on_retrying, on_done, prompt=None) -> None:
        self.cancel(req_id)
        job = _Job(image, settings.model, prompt if prompt is not None else settings.prompt,
                   on_retrying, on_done)
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
        try:
            data, media_type = prepare_image(job.image)
        except (OSError, GLib.Error) as e:
            self._finish_attempt(req_id, job, Result(False, f"Could not read screenshot: {e}"))
            return
        launcher = Gio.SubprocessLauncher.new(Gio.SubprocessFlags.STDIN_PIPE
                                              | Gio.SubprocessFlags.STDOUT_PIPE
                                              | Gio.SubprocessFlags.STDERR_PIPE)
        launcher.set_cwd(config.work_dir())
        try:
            job.proc = launcher.spawnv(build_command(claude, job.model))
        except GLib.Error as e:
            self._finish_attempt(req_id, job, Result(False, f"Could not start claude: {e.message}"))
            return
        log.info("request %s attempt %d started (%s)", req_id, job.attempts, job.image)
        payload = GLib.Bytes.new(build_payload(data, media_type, job.prompt))
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
        self._finish_attempt(req_id, job, parse_output(stdout, stderr, code))

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
