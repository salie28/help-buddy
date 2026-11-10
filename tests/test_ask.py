import base64
import json
import os
import random
import stat
import tempfile
from unittest import mock

import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib

from helpme import ask
from helpme.ask import (Result, build_command, build_payload, find_claude, parse_output,
                        prepare_image, retry_delay)
from tests.helpers import XdgTestCase

SYSTEM = '{"type":"system","subtype":"init","model":"claude-sonnet-5-5"}'
ASSIST = '{"type":"assistant","message":{"content":[{"type":"text","text":"Blue"}]}}'


def result_line(text, is_error=False):
    return json.dumps({"type": "result", "subtype": "success", "is_error": is_error,
                       "result": text})


class CommandTest(XdgTestCase):
    def test_build_command_exact(self):
        cmd = build_command("/x/claude", "sonnet")
        self.assertEqual(cmd, [
            "/x/claude", "-p", "--model", "sonnet", "--effort", "high",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--strict-mcp-config", "--setting-sources", "",
            "--system-prompt", ask.system_prompt("short"), "--no-session-persistence"])
        self.assertNotIn("--bare", cmd)

    def test_build_command_new_session(self):
        cmd = build_command("/x/claude", "sonnet", session_id="abc", length="detailed")
        self.assertEqual(cmd[-2:], ["--session-id", "abc"])
        self.assertNotIn("--no-session-persistence", cmd)
        self.assertIn(ask.system_prompt("detailed"), cmd)

    def test_build_command_resume(self):
        cmd = build_command("/x/claude", "sonnet", resume="abc")
        self.assertEqual(cmd[-2:], ["--resume", "abc"])
        self.assertNotIn("--no-session-persistence", cmd)

    def test_system_prompt_lengths(self):
        self.assertIn("120 words", ask.system_prompt("short"))
        self.assertNotIn("120 words", ask.system_prompt("detailed"))
        self.assertTrue(ask.system_prompt("short").startswith(ask.SYSTEM_PROMPT))

    def test_text_payload(self):
        msg = json.loads(ask.build_text_payload("why?"))
        self.assertEqual(msg["message"]["content"], [{"type": "text", "text": "why?"}])

    def test_payload_shape(self):
        line = build_payload(b"\x89PNG", "image/png", "hi")
        self.assertTrue(line.endswith(b"\n"))
        self.assertEqual(line.count(b"\n"), 1)
        msg = json.loads(line)
        self.assertEqual(msg["type"], "user")
        self.assertEqual(msg["message"]["role"], "user")
        img, txt = msg["message"]["content"]
        self.assertEqual(img["type"], "image")
        self.assertEqual(img["source"]["media_type"], "image/png")
        self.assertEqual(base64.b64decode(img["source"]["data"]), b"\x89PNG")
        self.assertEqual(txt, {"type": "text", "text": "hi"})


class ParseTest(XdgTestCase):
    def test_parse_success(self):
        out = "\n".join([SYSTEM, ASSIST, result_line("Blue")]) + "\n"
        self.assertEqual(parse_output(out, "", 0), Result(True, "Blue"))

    def test_parse_usage_and_session(self):
        res = json.dumps({"type": "result", "subtype": "success", "is_error": False,
                          "result": "Blue", "session_id": "sid-1", "duration_ms": 7940,
                          "total_cost_usd": 0.0105,
                          "usage": {"input_tokens": 4, "cache_read_input_tokens": 2599,
                                    "cache_creation_input_tokens": 338, "output_tokens": 760}})
        r = parse_output("\n".join([SYSTEM, res]), "", 0)
        self.assertTrue(r.ok)
        self.assertEqual(r.session_id, "sid-1")
        self.assertEqual(r.usage, {"model": "claude-sonnet-5-5", "tokens_in": 2941,
                                   "tokens_out": 760, "cost": 0.0105, "ms": 7940})

    def test_format_usage(self):
        u = {"model": "claude-sonnet-5-5", "tokens_in": 2941, "tokens_out": 760,
             "cost": 0.0105, "ms": 7940}
        self.assertEqual(ask.format_usage(u), "Sonnet 5.5 · 3.7k tokens · ≈$0.01 · 8 s")
        self.assertEqual(ask.format_usage({}), "")
        self.assertEqual(ask.format_usage({"model": "x", "tokens_in": "bad"}), "")

    def test_parse_error_result(self):
        msg = ("There's an issue with the selected model (no-such-model). It may not exist "
               "or you may not have access to it. Run --model to pick a different model.")
        out = "\n".join([SYSTEM, result_line(msg, is_error=True)])
        r = parse_output(out, "[claude-code:unrecognized_model] {}", 1)
        self.assertFalse(r.ok)
        self.assertTrue(r.text.startswith("There's an issue"))

    def test_parse_empty_and_garbage(self):
        self.assertEqual(parse_output("", "boom\n", 1), Result(False, "boom"))
        self.assertFalse(parse_output(result_line("  "), "", 0).ok)
        out = "not json\n" + result_line("Fine") + "\n{broken"
        self.assertEqual(parse_output(out, "", 0), Result(True, "Fine"))
        self.assertEqual(parse_output("", "", 3), Result(False, "claude exited with code 3"))

    def test_parse_success_text_but_nonzero_exit_fails(self):
        self.assertFalse(parse_output(result_line("Blue"), "", 1).ok)

    def test_retry_delay(self):
        self.assertEqual(retry_delay(1), 20)
        self.assertIsNone(retry_delay(2))
        self.assertIsNone(retry_delay(3))


class ImageTest(XdgTestCase):
    def png(self, w, h, noise=False):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
        if noise:
            rnd = random.Random(1)
            data = bytes(rnd.getrandbits(8) for _ in range(w * h * 3))
            pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(data),
                                                 GdkPixbuf.Colorspace.RGB, False, 8, w, h, w * 3)
        else:
            pb.fill(0x2266ffff)
        path = os.path.join(self.root, f"{w}x{h}.png")
        pb.savev(path, "png", [], [])
        return path

    def test_prepare_image_small_png_untouched(self):
        path = self.png(64, 64)
        data, mt = prepare_image(path)
        self.assertEqual(mt, "image/png")
        with open(path, "rb") as f:
            self.assertEqual(data, f.read())

    def test_prepare_image_big_png_becomes_jpeg(self):
        path = self.png(2560, 1600, noise=True)
        with mock.patch.object(ask, "MAX_IMAGE_BYTES", 100):
            data, mt = prepare_image(path)
        self.assertEqual(mt, "image/jpeg")
        loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
        loader.write(data)
        loader.close()
        pb = loader.get_pixbuf()
        self.assertLessEqual(max(pb.get_width(), pb.get_height()), 1920)
        self.assertAlmostEqual(pb.get_width() / pb.get_height(), 2560 / 1600, places=2)


    def test_prepare_image_alpha_png_becomes_jpeg(self):
        pb = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 300, 200)
        pb.fill(0x2266ff80)
        path = os.path.join(self.root, "alpha.png")
        pb.savev(path, "png", [], [])
        with mock.patch.object(ask, "MAX_IMAGE_BYTES", 10):
            data, mt = prepare_image(path)
        self.assertEqual(mt, "image/jpeg")
        self.assertEqual(data[:2], b"\xff\xd8")


class FindClaudeTest(XdgTestCase):
    def test_find_claude_fallback(self):
        empty = os.path.join(self.root, "emptybin")
        os.makedirs(empty)
        with mock.patch.dict(os.environ, {"PATH": empty}):
            self.assertIsNone(find_claude())
            fake = os.path.join(self.root, ".local", "bin", "claude")
            os.makedirs(os.path.dirname(fake))
            with open(fake, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(fake, os.stat(fake).st_mode | stat.S_IXUSR)
            self.assertEqual(find_claude(), fake)


class FollowupPromptTest(XdgTestCase):
    def test_followup_prompt_has_whole_conversation(self):
        from helpme.ask import followup_prompt
        text = followup_prompt("help me", "Use INSTR.", [("and SUBSTR?", "SUBSTR cuts.")],
                               "show an example")
        for part in ("help me", "Use INSTR.", "and SUBSTR?", "SUBSTR cuts.", "show an example"):
            self.assertIn(part, text)
        self.assertLess(text.index("Use INSTR."), text.index("and SUBSTR?"))
        self.assertTrue(text.rstrip().endswith("show an example") or
                        text.index("show an example") > text.index("SUBSTR cuts."))

    def test_ask_uses_prompt_override(self):
        from unittest import mock
        seen = {}
        a = ask.Asker()
        with mock.patch.object(ask.Asker, "_attempt", lambda self, rid, job: seen.update(
                rid=rid, prompt=job.prompt)):
            a.ask("r1:0", "/x.png", ask.config.Settings(prompt="default"), None, None,
                  prompt="override")
        self.assertEqual(seen, {"rid": "r1:0", "prompt": "override"})
        with mock.patch.object(ask.Asker, "_attempt", lambda self, rid, job: seen.update(
                prompt=job.prompt)):
            a.ask("r2", "/x.png", ask.config.Settings(prompt="default"), None, None)
        self.assertEqual(seen["prompt"], "default")


class SessionFileTest(XdgTestCase):
    def test_session_file_location(self):
        path = ask.session_file("sid-1")
        work = ask.config.work_dir()
        import re
        self.assertEqual(os.path.basename(os.path.dirname(path)), re.sub(r"[^A-Za-z0-9]", "-", work))
        self.assertTrue(path.endswith("/sid-1.jsonl"))
        self.assertTrue(path.startswith(os.path.join(self.root, ".claude", "projects")))

    def test_delete_and_cleanup_sessions(self):
        import time
        os.makedirs(os.path.dirname(ask.session_file("x")), exist_ok=True)
        old, new = ask.session_file("old"), ask.session_file("new")
        for p, age in ((old, 40), (new, 1)):
            open(p, "w").close()
            t = time.time() - age * 86400
            os.utime(p, (t, t))
        ask.delete_session("new")
        self.assertFalse(os.path.exists(new))
        ask.delete_session("")  # no-op
        ask.delete_session("../../etc")  # never escapes the folder
        open(new, "w").close()
        self.assertEqual(ask.cleanup_sessions(30, time.time()), [old])
        self.assertTrue(os.path.exists(new))
        self.assertEqual(ask.cleanup_sessions(0, time.time()), [])
