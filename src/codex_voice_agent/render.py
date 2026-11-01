"""Codex Markdown -> the small HTML subset the panel shows with Text.RichText.

Codex output can be steered by what it reads (files, repositories, web pages). Qt loads any
image a rich text names as soon as the text is shown: Markdown images in every form (inline,
reference, shortcut) and, in HTML, <img>, `background` attributes and CSS backgrounds. So
the panel never renders Codex's Markdown itself. The backend parses it here, with raw HTML off
and every image turned into its alt text, and only this parser's own output reaches Qt.
A final allow-list check falls back to escaped plain text if anything else ever shows up.
"""
from __future__ import annotations

import re
from html import escape

from markdown_it import MarkdownIt

_WEB_LINK = re.compile(r"^https?://", re.I)

# Every tag the renderer below may emit, with the only attributes it may carry.
_TAG = re.compile(
    r"</?(?:p|em|strong|s|code|pre|blockquote|ul|li|h[1-6]|thead|tbody|tr|br|hr)\s*/?>"
    r"|<table border=\"1\" cellspacing=\"0\" cellpadding=\"4\">|</table>"
    r"|<ol(?: start=\"\d+\")?>|</ol>"
    r"|<(?:th|td)(?: style=\"text-align:(?:left|center|right)\")?>|</(?:th|td)>"
    r"|<code class=\"language-[^\"<>&\s]*\">"
    r"|<a href=\"https?://[^\"<>\s]*\">|</a>"
)


def _image(self, tokens, idx, options, env) -> str:
    alt = self.renderInlineAsText(tokens[idx].children or [], options, env).strip()
    return escape(f"[image: {alt}]" if alt else "[image]")


def _table_open(self, tokens, idx, options, env) -> str:
    return '<table border="1" cellspacing="0" cellpadding="4">\n'  # Qt draws no grid otherwise


def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "linkify": False, "typographer": False})
    md.enable(["table", "strikethrough"])
    # Links and image targets: web URLs only (anything else stays literal text).
    md.validateLink = lambda url: bool(_WEB_LINK.match(url.strip()))
    md.add_render_rule("image", _image)
    md.add_render_rule("table_open", _table_open)
    return md


_MD = _parser()


def only_allowed_tags(html: str) -> bool:
    return all(_TAG.fullmatch(tag) for tag in re.findall(r"<[^>]*>", html))


def plain_html(text: str) -> str:
    return "<p>" + escape(text).replace("\n", "<br>") + "</p>"


def markdown_html(text: str) -> str:
    """Codex Markdown as HTML that loads nothing when Qt renders it."""
    try:
        html = _MD.render(text).strip()
    except Exception:  # never lose a reply over a parser bug
        return plain_html(text)
    return html if only_allowed_tags(html) else plain_html(text)
