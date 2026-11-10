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
            "/x/claude", "-p", "--model", "sonnet",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--tools", "", "--no-session-persistence", "--strict-mcp-config",
            "--setting-sources", "", "--system-prompt", ask.SYSTEM_PROMPT])
        self.assertNotIn("--bare", cmd)

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
