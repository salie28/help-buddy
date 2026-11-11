import re
import unittest

import gi
gi.require_version("Pango", "1.0")
from gi.repository import Pango

from helpbuddy.markdown import render


def plain(markup):
    ok, _attrs, text, _accel = Pango.parse_markup(markup, -1, "\0")
    return text


def chars(s):
    return re.sub(r"[^\w<>&]", "", s)


class MarkdownTest(unittest.TestCase):
    def test_heading_bold_code(self):
        blocks = render("# Hi\n\n**a** and `x<y`")
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0][0], "heading")
        self.assertEqual(plain(blocks[0][1]), "Hi")
        self.assertIn('weight="bold"', blocks[0][1])
        self.assertEqual(blocks[1], ("para", "<b>a</b> and <tt>x&lt;y</tt>"))

    def test_code_inside_bold(self):
        self.assertEqual(render("**1. Tint on `.glass` (most likely fix)**"),
                         [("para", "<b>1. Tint on <tt>.glass</tt> (most likely fix)</b>")])
        self.assertEqual(render("**use `a*b*c` here**"),
                         [("para", "<b>use <tt>a*b*c</tt> here</b>")])

    def test_italic(self):
        self.assertEqual(render("an *odd* one"), [("para", "an <i>odd</i> one")])

    def test_code_block_raw(self):
        self.assertEqual(render("```py\nif a<b: pass\n```"), [("code", "if a<b: pass")])

    def test_code_block_keeps_markers(self):
        self.assertEqual(render("```\n**not bold**\n  indented\n```"),
                         [("code", "**not bold**\n  indented")])

    def test_lists(self):
        self.assertEqual(render("- a\n- b\n\n1. one\n2. two"),
                         [("list", "• a\n• b"), ("list", "1. one\n2. two")])

    def test_paragraph_lines_and_blank_split(self):
        self.assertEqual(render("line one\nline two\n\nnext"),
                         [("para", "line one\nline two"), ("para", "next")])

    def test_mixed_sequence(self):
        kinds = [k for k, _ in render("Intro\n- x\n```\nc\n```\nOutro")]
        self.assertEqual(kinds, ["para", "list", "code", "para"])

    def test_every_block_parses(self):
        nasty = ["a & b", "unclosed **bold", "<script>", "`` ` ``", "**a *b** c*",
                 "5 * 3 * 2", "```\nnever closed <b>", "# **h** & <x>", "- `a<b` & *c*",
                 "&amp; literal", "*", "**", "`"]
        for md in nasty:
            blocks = render(md)
            shown = ""
            for kind, text in blocks:
                if kind == "code":
                    shown += text
                else:
                    shown += plain(text)  # raises GLib.Error if invalid
            self.assertEqual(chars(shown), chars(md), md)

    def test_star_math_is_not_italic(self):
        self.assertEqual(plain(render("5 * 3 * 2")[0][1]), "5 * 3 * 2")

    def test_unclosed_bold_kept_literally(self):
        self.assertEqual(plain(render("unclosed **bold")[0][1]), "unclosed **bold")

    def test_empty(self):
        self.assertEqual(render(""), [])
        self.assertEqual(render("\n\n  \n"), [])
