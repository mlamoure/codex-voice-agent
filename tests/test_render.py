"""render.markdown_html: Codex Markdown becomes HTML that Qt can show without loading anything."""
import re

import pytest

from codex_voice_agent.render import markdown_html, only_allowed_tags, plain_html

EVIL = "https://evil.example/p.png"

# Every form Qt was seen to fetch from (qml6 probe), plus the forms a regex filter misses.
IMAGE_TRICKS = [
    f"inline ![alt]({EVIL})",
    f'titled ![alt]({EVIL} "t")',
    f"angle ![alt](<{EVIL}>)",
    f"reference ![alt][r]\n\n[r]: {EVIL}",
    f"collapsed ![r][]\n\n[r]: {EVIL}",
    f"shortcut ![r]\n\n[r]: {EVIL}",
    f"nested ![a [b] c]({EVIL})",
    f"linked image [![alt]({EVIL})](https://example.com)",
    f'html <img src="{EVIL}">',
    f'<table background="{EVIL}"><tr><td>x</td></tr></table>',
    f'<div style="background-image:url({EVIL})">x</div>',
    f'<p style="background:url({EVIL})">x</p>',
    f"| a |\n|---|\n| ![alt]({EVIL}) |",
    f"- item ![alt]({EVIL})\n> quote ![alt]({EVIL})",
]


@pytest.mark.parametrize("md", IMAGE_TRICKS)
def test_no_image_survives(md):
    html = markdown_html(md)
    assert only_allowed_tags(html), html
    # raw HTML may survive only as escaped text; a URL only as text or a clickable link
    for tag in re.findall(r"<[^>]*>", html):
        assert not re.search(r"img|src=|background|url\(", tag, re.I), tag
        assert EVIL not in tag or tag.startswith("<a href="), tag


def test_images_become_their_alt_text():
    assert "[image: tracker]" in markdown_html(f"![tracker]({EVIL})")
    assert "[image]" in markdown_html(f"![]({EVIL})")


def test_formatting_is_kept():
    html = markdown_html("**bold** *em* `code` ~~gone~~\n\n```py\nx = 1 < 2\n```\n\n1. one\n2. two\n\n| a | b |\n|:-|-:|\n| 1 | 2 |")
    for part in ("<strong>bold</strong>", "<em>em</em>", "<code>code</code>", "<s>gone</s>",
                 '<code class="language-py">x = 1 &lt; 2', "<ol>", '<table border="1"', 'style="text-align:right"'):
        assert part in html, part
    assert only_allowed_tags(html)


def test_only_web_links_become_links():
    assert '<a href="https://example.com/a">x</a>' in markdown_html("[x](https://example.com/a)")
    for url in ("javascript:alert(1)", "file:///etc/passwd", "data:text/html,x", "/relative"):
        assert "<a " not in markdown_html(f"[x]({url})"), url


def test_raw_html_is_text():
    html = markdown_html("<b>hi</b> & <script>x</script>")
    assert "&lt;b&gt;hi&lt;/b&gt;" in html and "<script" not in html and "<b>" not in html


def test_allow_list_rejects_anything_else():
    assert not only_allowed_tags('<p><img src="x"></p>')
    assert not only_allowed_tags('<td style="background:url(x)">')
    assert not only_allowed_tags('<a href="file:///x">')
    assert not only_allowed_tags('<p class="x">')
    assert plain_html("a <img>\nb") == "<p>a &lt;img&gt;<br>b</p>"
