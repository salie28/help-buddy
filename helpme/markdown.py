"""Tiny Markdown → Pango markup renderer for answers.

render() returns blocks of (kind, text). kind is "heading", "para", "list" or "code".
For "code" the text is raw (show it without markup); for the rest it is Pango markup
that always parses. A block whose markup would not parse falls back to escaped text.
"""

import re

import gi
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Pango  # noqa: E402

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+(.*)$")
_CODE_SPAN = re.compile(r"`([^`]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?=[^\s*])([^*]+?)(?<=[^\s*])\*(?![*\w])")


def _esc(s: str) -> str:
    return GLib.markup_escape_text(s, -1)


def _emphasis(escaped: str) -> str:
    escaped = _BOLD.sub(r"<b>\1</b>", escaped)
    return _ITALIC.sub(r"<i>\1</i>", escaped)


def _inline(raw: str) -> str:
    # Swap code spans for placeholders so **bold `code` bold** still pairs up, and so
    # emphasis markers inside code stay literal.
    raw = raw.replace("\ue000", "").replace("\ue001", "")
    spans: list[str] = []

    def stash(m):
        spans.append("<tt>" + _esc(m.group(1)) + "</tt>")
        return f"\ue000{len(spans) - 1}\ue001"

    text = _emphasis(_esc(_CODE_SPAN.sub(stash, raw)))
    return re.sub("\ue000(\\d+)\ue001", lambda m: spans[int(m.group(1))], text)


def _valid(markup: str) -> bool:
    try:
        Pango.parse_markup(markup, -1, "\0")
        return True
    except GLib.Error:
        return False


def _block(kind: str, markup: str, raw: str) -> tuple[str, str]:
    return (kind, markup if _valid(markup) else _esc(raw))


def render(md: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    para: list[str] = []
    items: list[tuple[str, str]] = []  # (marker, raw text)

    def flush_para():
        if para:
            raw = "\n".join(para)
            blocks.append(_block("para", "\n".join(_inline(p) for p in para), raw))
            para.clear()

    def flush_list():
        if items:
            shown = [("•" if m in "-*+" else m, t) for m, t in items]
            raw = "\n".join(f"{m} {t}" for m, t in shown)
            markup = "\n".join(f"{_esc(m)} {_inline(t)}" for m, t in shown)
            blocks.append(_block("list", markup, raw))
            items.clear()

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            flush_para()
            flush_list()
            code = []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                code.append(lines[i])
                i += 1
            i += 1  # skip closing fence (or run past the end)
            blocks.append(("code", "\n".join(code)))
            continue
        if not line.strip():
            flush_para()
            flush_list()
        elif m := _HEADING.match(line):
            flush_para()
            flush_list()
            text = m.group(2)
            blocks.append(_block("heading",
                                 f'<span size="large" weight="bold">{_inline(text)}</span>',
                                 text))
        elif m := _ITEM.match(line):
            flush_para()
            items.append((m.group(1), m.group(2)))
        else:
            flush_list()
            para.append(line.strip())
        i += 1
    flush_para()
    flush_list()
    return blocks
