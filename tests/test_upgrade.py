"""Files written by an older release must keep loading in newer ones (re-running
install.sh is how users update). Add a fixture folder for every release."""
import os
import shutil

from tests.helpers import XdgTestCase

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


class UpgradeTest(XdgTestCase):
    def load_release(self, version):
        from helpbuddy import config
        src = os.path.join(FIXTURES, version)
        os.makedirs(os.path.dirname(config.settings_path()), exist_ok=True)
        os.makedirs(os.path.dirname(config.history_path()), exist_ok=True)
        shutil.copy(os.path.join(src, "settings.json"), config.settings_path())
        shutil.copy(os.path.join(src, "history.json"), config.history_path())

    def test_v1_0_0_files_load_unchanged(self):
        from helpbuddy import config
        from helpbuddy.history import History
        self.load_release("v1.0.0")
        s = config.load_settings()
        self.assertEqual((s.capture_mode, s.area, s.shortcut, s.glass),
                         ("area", [0.0, 0.0, 0.5, 1.0], "<Control>dead_circumflex", 75))
        h = History()
        h.load()
        r = h.get("0123456789abcdef0123456789abcdef")
        self.assertEqual((r.status, r.session, len(r.followups)),
                         ("done", "8e53c1c1-6722-4a4b-8fe6-104f28ddcc7f", 1))
        self.assertEqual(r.usage["tokens_out"], 560)

    def test_every_release_has_fixtures(self):
        from helpbuddy import __version__
        self.assertTrue(os.path.isdir(os.path.join(FIXTURES, f"v{__version__}")),
                        f"add tests/fixtures/v{__version__}/ for this release")


class InstallVersionTest(XdgTestCase):
    def test_install_message(self):
        from helpbuddy.system import install_message
        self.assertEqual(install_message(None, "1.0.0"), "Installed Help Buddy 1.0.0")
        self.assertEqual(install_message("1.0.0", "1.0.0"), "Reinstalled Help Buddy 1.0.0")
        self.assertEqual(install_message("1.0.0", "1.1.0"), "Updated Help Buddy 1.0.0 → 1.1.0")

    def test_installed_version_round_trip(self):
        from helpbuddy import system
        self.assertIsNone(system.read_installed_version())
        system.write_installed_version("1.0.0")
        self.assertEqual(system.read_installed_version(), "1.0.0")
