import unittest

from helpme.config import should_show_window


class ShowWindowRuleTest(unittest.TestCase):
    def test_first_press_opens(self):
        self.assertTrue(should_show_window(window_exists=False, window_visible=False))

    def test_closed_window_reopens(self):
        self.assertTrue(should_show_window(window_exists=True, window_visible=False))

    def test_open_window_is_left_alone(self):
        # open on any desktop: never raised, never focused
        self.assertFalse(should_show_window(window_exists=True, window_visible=True))
