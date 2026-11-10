import os

from helpme import system
from helpme.system import (KEYBINDING_PATH, autostart_desktop, autostart_path, set_autostart,
                           shortcut_command, valid_accel, with_keybinding, without_keybinding)
from tests.helpers import XdgTestCase


class KeybindingListTest(XdgTestCase):
    def test_with_keybinding_idempotent(self):
        once = with_keybinding(["/a/"])
        self.assertEqual(once, ["/a/", KEYBINDING_PATH])
        self.assertEqual(with_keybinding(once), once)

    def test_without_keybinding(self):
        self.assertEqual(without_keybinding(["/a/", KEYBINDING_PATH, "/b/"]), ["/a/", "/b/"])
        self.assertEqual(without_keybinding(["/a/"]), ["/a/"])

    def test_valid_accel(self):
        self.assertTrue(valid_accel("<Super><Shift>h"))
        self.assertTrue(valid_accel("<Control><Alt>q"))
        for bad in ("<Super>", "", "hello world", "<Nope>x"):
            self.assertFalse(valid_accel(bad), bad)


class CommandTest(XdgTestCase):
    def test_shortcut_command_is_absolute(self):
        cmd = shortcut_command()
        self.assertTrue(os.path.isabs(cmd.split()[0]), cmd)
        self.assertTrue(cmd.endswith(" capture"))

    def test_autostart_desktop(self):
        text = autostart_desktop()
        exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
        self.assertTrue(os.path.isabs(exec_line[len("Exec="):].split()[0]))
        self.assertTrue(exec_line.endswith(" --background"))
        self.assertIn("X-GNOME-Autostart-enabled=true", text)
        self.assertIn("[Desktop Entry]", text)

    def test_set_autostart_writes_and_removes(self):
        self.assertTrue(autostart_path().startswith(os.path.join(self.root, "config")))
        set_autostart(True)
        with open(autostart_path()) as f:
            self.assertEqual(f.read(), autostart_desktop())
        set_autostart(False)
        self.assertFalse(os.path.exists(autostart_path()))
        set_autostart(False)  # no error when already gone
