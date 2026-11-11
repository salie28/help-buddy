import os
from datetime import datetime

from helpbuddy.capture import area_to_pixels, backend_for, screenshot_filename
from helpbuddy.config import Settings
from tests.helpers import XdgTestCase


class AreaTest(XdgTestCase):
    def test_area_none_is_full(self):
        self.assertEqual(area_to_pixels(None, 2560, 1600), (0, 0, 2560, 1600))

    def test_area_fractions(self):
        self.assertEqual(area_to_pixels([0.5, 0.25, 0.5, 0.5], 2560, 1600),
                         (1280, 400, 1280, 800))

    def test_area_clamped(self):
        x, y, w, h = area_to_pixels([0.9, 0.9, 0.5, 0.5], 2560, 1600)
        self.assertGreaterEqual(x, 0)
        self.assertGreaterEqual(y, 0)
        self.assertLessEqual(x + w, 2560)
        self.assertLessEqual(y + h, 1600)
        self.assertGreaterEqual(w, 16)
        self.assertGreaterEqual(h, 16)

    def test_area_min_size(self):
        x, y, w, h = area_to_pixels([0.999, 0.999, 0.0001, 0.0001], 2560, 1600)
        self.assertEqual((w, h), (16, 16))
        self.assertLessEqual(x + w, 2560)
        self.assertLessEqual(y + h, 1600)

    def test_area_on_tiny_image(self):
        self.assertEqual(area_to_pixels([0.5, 0.5, 0.1, 0.1], 10, 8), (0, 0, 10, 8))


class FilenameTest(XdgTestCase):
    def test_filename_unique(self):
        now = datetime(2026, 11, 10, 12, 23, 5)
        names = []
        for _ in range(3):
            p = screenshot_filename(self.root, now)
            names.append(os.path.basename(p))
            open(p, "wb").close()
        self.assertEqual(names, ["261110_122305.png", "261110_122305_2.png",
                                 "261110_122305_3.png"])
        self.assertEqual(os.path.dirname(p), self.root)


class BackendTest(XdgTestCase):
    def test_no_flash_uses_screencast(self):
        self.assertEqual(backend_for(Settings()), "screencast")
        self.assertEqual(backend_for(Settings(flash=False)), "screencast")

    def test_flash_uses_screenshot_portal(self):
        self.assertEqual(backend_for(Settings(flash=True)), "screenshot")


class QueueRobustnessTest(XdgTestCase):
    def run_loop(self, capturer, settings_list):
        from gi.repository import GLib
        loop = GLib.MainLoop()
        results = []

        def done(path, err):
            results.append((path, err))
            if len(results) == len(settings_list):
                loop.quit()
        for s in settings_list:
            capturer.capture(s, done)
        GLib.timeout_add_seconds(5, loop.quit)
        loop.run()
        return results

    def test_queue_survives_unexpected_error(self):
        from unittest import mock
        from gi.repository import GLib
        from helpbuddy import capture

        def boom(_n):
            raise GLib.Error("pipewire exploded")
        with mock.patch.object(capture, "_grab_screencast_frame", boom):
            c = capture.Capturer()
            results = self.run_loop(c, [Settings(screenshot_dir=self.root)] * 2)
        self.assertEqual(len(results), 2)
        for path, err in results:
            self.assertIsNone(path)
            self.assertIn("pipewire exploded", err)
        self.assertFalse(c._busy)

    def test_queue_survives_processing_error(self):
        from unittest import mock
        from helpbuddy import capture
        src = os.path.join(self.root, "portal.png")

        def fake_grab(_n):
            open(src, "wb").close()
            return src
        with mock.patch.object(capture, "_grab_screencast_frame", fake_grab), \
                mock.patch.object(capture.shutil, "move", side_effect=ValueError("weird")):
            c = capture.Capturer()
            results = self.run_loop(c, [Settings(screenshot_dir=self.root)] * 2)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(p is None and "weird" in e for p, e in results))
        self.assertFalse(c._busy)

    def test_callback_error_does_not_block_queue(self):
        from unittest import mock
        from gi.repository import GLib
        from helpbuddy import capture
        loop = GLib.MainLoop()
        calls = []

        def bad_cb(path, err):
            calls.append("bad")
            raise RuntimeError("callback bug")

        def good_cb(path, err):
            calls.append("good")
            loop.quit()
        with mock.patch.object(capture, "_grab_screencast_frame",
                               side_effect=capture.CaptureError("nope")):
            c = capture.Capturer()
            c.capture(Settings(screenshot_dir=self.root), bad_cb)
            c.capture(Settings(screenshot_dir=self.root), good_cb)
            GLib.timeout_add_seconds(5, loop.quit)
            loop.run()
        self.assertEqual(calls, ["bad", "good"])


class PortalAppIdTest(XdgTestCase):
    def test_portal_app_id_parsing(self):
        from helpbuddy.capture import portal_app_id
        base = "0::/user.slice/user-1000.slice/user@1000.service/app.slice/"
        self.assertEqual(portal_app_id(base + "app-gnome-local.claude.HelpBuddy-240067.scope"),
                         "local.claude.HelpBuddy")
        self.assertEqual(portal_app_id(base + "app-gnome-helpbuddy-1234.scope"), "")
        self.assertEqual(portal_app_id(base + "ptyxis-spawn-c8a86bce-be53.scope"), "")
        self.assertEqual(portal_app_id(base + "app-gnome-local.claude.HelpBuddy-autostart.service"),
                         "local.claude.HelpBuddy")
        self.assertEqual(portal_app_id(base + "app-gnome-org.foo\\x2dbar.App@abc.service"),
                         "org.foo-bar.App")
        self.assertEqual(portal_app_id(""), "")

    def test_token_path_per_app_id(self):
        from helpbuddy.capture import token_path
        self.assertEqual(os.path.basename(token_path("")), "screencast_token")
        self.assertEqual(os.path.basename(token_path("local.claude.HelpBuddy")),
                         "screencast_token.local.claude.HelpBuddy")
