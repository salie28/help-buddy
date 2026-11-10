import json
import os

from helpme import config
from helpme.config import Settings, load_settings, save_settings
from tests.helpers import XdgTestCase


class ConfigTest(XdgTestCase):
    def write(self, text):
        os.makedirs(os.path.dirname(config.settings_path()), exist_ok=True)
        with open(config.settings_path(), "w") as f:
            f.write(text)

    def test_missing_settings_gives_defaults(self):
        s = load_settings()
        self.assertEqual(s, Settings())
        self.assertEqual(s.model, "sonnet")
        self.assertEqual(s.keep_days, 30)
        self.assertIs(s.jump_to_newest, True)
        self.assertEqual(s.shortcut, "<Super><Shift>h")
        self.assertEqual(s.capture_mode, "full")
        self.assertIsNone(s.area)
        self.assertIs(s.autostart, False)
        self.assertIs(s.flash, False)
        self.assertEqual(s.glass, 75)
        self.assertTrue(s.screenshot_dir.endswith(os.path.join("Screenshots", "HelpMe")))
        self.assertTrue(s.prompt.startswith("Look at this screenshot"))

    def test_paths_follow_xdg(self):
        self.assertEqual(config.settings_path(),
                         os.path.join(self.root, "config", "helpme", "settings.json"))
        self.assertEqual(config.history_path(),
                         os.path.join(self.root, "data", "helpme", "history.json"))
        self.assertEqual(config.log_path(),
                         os.path.join(self.root, "state", "helpme", "helpme.log"))
        self.assertTrue(os.path.isdir(config.work_dir()))

    def test_round_trip(self):
        s = Settings(model="haiku", area=[0.1, 0.2, 0.5, 0.5])
        save_settings(s)
        self.assertEqual(load_settings(), s)

    def test_corrupt_settings_gives_defaults(self):
        self.write("{not json")
        self.assertEqual(load_settings(), Settings())

    def test_bad_value_falls_back_per_key(self):
        self.write(json.dumps({"model": "gpt", "keep_days": "x", "flash": "yes",
                               "capture_mode": "area", "unknown": 1}))
        s = load_settings()
        self.assertEqual(s.model, "sonnet")
        self.assertEqual(s.keep_days, 30)
        self.assertEqual(s.capture_mode, "area")
        self.assertIs(s.flash, False)

    def test_area_invalid_becomes_none(self):
        for bad in ([0.5, 0.5], [0, 0, 0, 0], "x", [0.5, 0.5, 0.8, 0.2], [0, 0, 1.5, 1]):
            self.write(json.dumps({"area": bad}))
            self.assertIsNone(load_settings().area, bad)

    def test_save_is_atomic(self):
        save_settings(Settings())
        d = os.path.dirname(config.settings_path())
        self.assertEqual([f for f in os.listdir(d) if f.endswith(".tmp")], [])
        with open(config.settings_path()) as f:
            json.load(f)

    def test_glass_range(self):
        for bad in (10, 101, "x", True):
            self.write(json.dumps({"glass": bad}))
            self.assertEqual(load_settings().glass, 75, bad)
        self.write(json.dumps({"glass": 40}))
        self.assertEqual(load_settings().glass, 40)

    def test_glass_css(self):
        css = config.glass_css(75)
        self.assertIn("rgba(30, 30, 30, 0.75)", css)
        self.assertIn("rgba(12, 12, 12, 0.81)", css)
        self.assertIn("rgba(12, 12, 12, 1.00)", config.glass_css(100))
