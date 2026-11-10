import os
from datetime import datetime

from helpme.capture import area_to_pixels, backend_for, screenshot_filename
from helpme.config import Settings
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
