"""Tests for _split_html and _md_to_html in telegram_handler."""

import re

from src.telegram_handler import _md_to_html, _render_table, _split_html

# Regex to validate that all HTML tags are properly closed
_TAG_RE = re.compile(r"<(/?)(\w+)(?:\s[^>]*)?>")


def _has_balanced_tags(html: str) -> bool:
    """Return True if every opened tag is closed in the given HTML string."""
    stack: list[str] = []
    for m in _TAG_RE.finditer(html):
        if m.group(1) == "/":
            if not stack or stack[-1] != m.group(2):
                return False
            stack.pop()
        else:
            stack.append(m.group(2))
    return len(stack) == 0


def _reassemble(chunks: list[str]) -> str:
    """Strip closing/reopening tag pairs at chunk boundaries and rejoin."""
    # Simple approach: just concatenate — the content should be preserved
    # even if tags are added at boundaries
    full = ""
    for chunk in chunks:
        full += chunk
    # Remove redundant closing+reopening pairs like </b><b>
    full = re.sub(r"</(\w+)><\1>", "", full)
    return full


# -- _split_html tests --


class TestSplitHtmlShortText:
    def test_no_split_needed(self):
        text = "Hello world"
        assert _split_html(text, 4096) == [text]

    def test_exact_limit(self):
        text = "a" * 4096
        assert _split_html(text, 4096) == [text]

    def test_empty_string(self):
        assert _split_html("", 4096) == [""]


class TestSplitHtmlBasic:
    def test_plain_text_split(self):
        text = "word " * 1000  # 5000 chars
        chunks = _split_html(text, 100)
        combined = "".join(chunks)
        assert combined == text
        assert all(len(c) <= 110 for c in chunks)  # small overshoot from tag closing

    def test_all_content_preserved(self):
        text = "word " * 2000
        chunks = _split_html(text, 4096)
        assert "".join(chunks) == text


class TestSplitHtmlTagBoundaries:
    """The core bug: splitting must not break HTML tags."""

    def test_bold_at_split_boundary(self):
        """Construct text where <b>...</b> straddles the split point."""
        padding = "x " * 2044  # 4088 chars
        text = padding + "<b>bold text here</b> and more"
        chunks = _split_html(text, 4096)
        for chunk in chunks:
            assert _has_balanced_tags(chunk), f"Unbalanced tags in chunk: {chunk[-100:]}"

    def test_italic_at_split_boundary(self):
        padding = "y " * 2044
        text = padding + "<i>italic text here</i> and more"
        chunks = _split_html(text, 4096)
        for chunk in chunks:
            assert _has_balanced_tags(chunk)

    def test_code_at_split_boundary(self):
        padding = "z " * 2044
        text = padding + "<code>some code</code> and more"
        chunks = _split_html(text, 4096)
        for chunk in chunks:
            assert _has_balanced_tags(chunk)

    def test_nested_tags_at_boundary(self):
        """<b><i>text</i></b> straddling the limit."""
        padding = "a " * 2043
        text = padding + "<b><i>nested bold italic</i></b> rest"
        chunks = _split_html(text, 4096)
        for chunk in chunks:
            assert _has_balanced_tags(chunk)

    def test_multiple_splits_with_tags(self):
        """Text long enough to require 3+ chunks, with tags throughout."""
        segment = "<b>bold</b> normal <i>italic</i> text. "  # ~40 chars
        text = segment * 350  # ~14000 chars → 4 chunks at 4096
        chunks = _split_html(text, 4096)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert _has_balanced_tags(chunk), f"Unbalanced tags in chunk"

    def test_no_split_inside_tag_literal(self):
        """The split must not land between < and >."""
        padding = "m " * 2045  # 4090 chars
        text = padding + "<b>content</b>"
        chunks = _split_html(text, 4096)
        for chunk in chunks:
            # No chunk should end with an incomplete tag
            assert not re.search(r"<[^>]*$", chunk), f"Chunk ends mid-tag: {chunk[-30:]}"

    def test_content_integrity(self):
        """All original text content must survive the split+rejoin."""
        padding = "hello " * 700  # 4200 chars
        text = f"<b>{padding}</b>"
        chunks = _split_html(text, 4096)
        reassembled = _reassemble(chunks)
        assert padding in reassembled


class TestSplitHtmlEdgeCases:
    def test_tag_right_at_limit(self):
        """A tag that starts exactly at position limit-1."""
        text = "a" * 4093 + "<b>x</b>"
        chunks = _split_html(text, 4096)
        for chunk in chunks:
            assert _has_balanced_tags(chunk)

    def test_very_small_limit(self):
        """Stress test with tiny limit."""
        text = "<b>hello</b> <i>world</i>"
        chunks = _split_html(text, 15)
        for chunk in chunks:
            assert _has_balanced_tags(chunk)
        reassembled = _reassemble(chunks)
        assert "hello" in reassembled
        assert "world" in reassembled

    def test_no_spaces_in_text(self):
        """Text with no whitespace — forced to split at limit."""
        text = "a" * 10000
        chunks = _split_html(text, 4096)
        assert "".join(chunks) == text


# -- _md_to_html tests --


class TestMdToHtml:
    def test_bold(self):
        assert "<b>text</b>" in _md_to_html("**text**")

    def test_italic(self):
        assert "<i>text</i>" in _md_to_html("*text*")

    def test_code(self):
        assert "<code>text</code>" in _md_to_html("`text`")

    def test_heading(self):
        assert "<b>Title</b>" in _md_to_html("# Title")

    def test_html_escape(self):
        result = _md_to_html("P&L report <script>")
        assert "&amp;" in result
        assert "&lt;script&gt;" in result

    def test_combined(self):
        result = _md_to_html("**bold** and *italic* and `code`")
        assert "<b>bold</b>" in result
        assert "<i>italic</i>" in result
        assert "<code>code</code>" in result


# -- Integration: _md_to_html → _split_html pipeline --


class TestMdToHtmlThenSplit:
    def test_realistic_long_response(self):
        """Simulate a real Claude response that exceeds 4096 after HTML conversion."""
        paragraphs = []
        for i in range(50):
            paragraphs.append(
                f"## Član {i}\n\n"
                f"**Stav {i}.1** — Ovo je tekst zakona koji objašnjava pravilo broj {i}. "
                f"Porez na dodatu vrednost (PDV) se obračunava po stopi od 20% na "
                f"*sve isporuke dobara* i `pružanje usluga` u svim fazama prometa.\n"
            )
        md_text = "\n".join(paragraphs)
        html_text = _md_to_html(md_text)

        assert len(html_text) > 4096, "Test text must exceed Telegram limit"

        chunks = _split_html(html_text, 4096)
        assert len(chunks) >= 2
        for i, chunk in enumerate(chunks):
            assert len(chunk) <= 4096 + 50, f"Chunk {i} too long: {len(chunk)}"
            assert _has_balanced_tags(chunk), f"Chunk {i} has unbalanced tags"

    def test_ampersand_in_response(self):
        """P&L → P&amp;L should survive split correctly."""
        text = "A " * 2040 + "**P&L report** more text " * 20
        html_text = _md_to_html(text)
        chunks = _split_html(html_text, 4096)
        for chunk in chunks:
            assert _has_balanced_tags(chunk)
            # No raw & that isn't part of an entity
            assert "&" not in re.sub(r"&\w+;", "", chunk), "Unescaped & found"


# -- Fenced code block tests --


class TestMdToHtmlCodeBlocks:
    def test_code_block_no_lang(self):
        result = _md_to_html("```\nprint('hello')\n```")
        assert "<pre>" in result
        assert "print(&#x27;hello&#x27;)" in result or "print(" in result

    def test_code_block_with_lang(self):
        result = _md_to_html("```python\nx = 1\n```")
        assert 'class="language-python"' in result
        assert "<pre>" in result

    def test_code_block_preserves_content(self):
        """Special chars inside code blocks must be escaped, not formatted."""
        result = _md_to_html("```\n**not bold** & <tag>\n```")
        assert "<b>" not in result
        assert "&amp;" in result
        assert "&lt;tag&gt;" in result

    def test_code_block_surrounded_by_text(self):
        result = _md_to_html("Before\n```\ncode\n```\nAfter")
        assert "Before" in result
        assert "After" in result
        assert "<pre>" in result

    def test_multiple_code_blocks(self):
        text = "```python\na=1\n```\nMiddle\n```\nb=2\n```"
        result = _md_to_html(text)
        assert result.count("<pre>") == 2


# -- Table tests --


class TestMdToHtmlTables:
    def test_two_column_table(self):
        """Two-column tables render as 'Key: Value' lines."""
        table = "| Porez | Stopa |\n| --- | --- |\n| PDV | 20% |\n| Akcize | 10% |\n"
        result = _md_to_html(table)
        assert "<b>PDV</b>: 20%" in result
        assert "<b>Akcize</b>: 10%" in result
        assert "<pre>" not in result

    def test_multi_column_table(self):
        """3+ columns render as vertical blocks with header labels."""
        table = "| Vrsta | Stopa | Primena |\n| --- | --- | --- |\n| Opšta | 20% | Većina |\n| Posebna | 10% | Hrana |\n"
        result = _md_to_html(table)
        assert "▸ <b>Opšta</b>" in result
        assert "Stopa: 20%" in result
        assert "Primena: Većina" in result
        assert "<pre>" not in result

    def test_table_header_separator(self):
        """Separator row (|---|---|) should not appear in output."""
        table = "| A | B |\n| --- | --- |\n| 1 | 2 |\n"
        result = _md_to_html(table)
        assert "---" not in result
        assert "1" in result

    def test_table_with_special_chars(self):
        table = "| Opis | Vrednost |\n| --- | --- |\n| P&L | <100 |\n"
        result = _md_to_html(table)
        assert "&amp;" in result
        assert "&lt;100" in result

    def test_render_table_direct_two_col(self):
        lines = ["| A | B |", "| --- | --- |", "| 1 | 2 |"]
        result = _render_table(lines)
        assert "<b>1</b>: 2" in result

    def test_render_table_direct_multi_col(self):
        lines = ["| X | Y | Z |", "| --- | --- | --- |", "| a | b | c |"]
        result = _render_table(lines)
        assert "▸ <b>a</b>" in result
        assert "Y: b" in result

    def test_header_only_table(self):
        """Table with only header row, no data."""
        lines = ["| A | B |", "| --- | --- |"]
        result = _render_table(lines)
        assert "A" in result


# -- Bullet list tests --


class TestMdToHtmlLists:
    def test_unordered_list(self):
        result = _md_to_html("- Item one\n- Item two")
        assert "• Item one" in result
        assert "• Item two" in result
        assert "- Item" not in result

    def test_asterisk_list(self):
        """* should also convert to bullet."""
        result = _md_to_html("* First\n* Second")
        assert "• First" in result

    def test_numbered_list_preserved(self):
        result = _md_to_html("1. First\n2. Second")
        assert "1. First" in result
        assert "2. Second" in result


# -- Link tests --


class TestMdToHtmlLinks:
    def test_basic_link(self):
        result = _md_to_html("[Click here](https://example.com)")
        assert '<a href="https://example.com">Click here</a>' in result

    def test_link_with_special_chars_in_text(self):
        result = _md_to_html("[P&L Report](https://example.com)")
        assert "P&amp;L Report" in result
        assert '<a href=' in result


# -- Strikethrough tests --


class TestMdToHtmlStrikethrough:
    def test_strikethrough(self):
        result = _md_to_html("~~deleted~~")
        assert "<s>deleted</s>" in result


# -- Horizontal rule tests --


class TestMdToHtmlHorizontalRule:
    def test_triple_dash(self):
        result = _md_to_html("Above\n---\nBelow")
        assert "Above" in result
        assert "Below" in result
        assert "---" not in result

    def test_triple_asterisk(self):
        result = _md_to_html("Above\n***\nBelow")
        assert "***" not in result


# -- Mixed content integration --


class TestMdToHtmlMixed:
    def test_code_block_and_table(self):
        text = (
            "# Primer\n\n"
            "```python\nx = 1\n```\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "**Napomena**: ovo je test."
        )
        result = _md_to_html(text)
        assert "<b>Primer</b>" in result
        assert 'class="language-python"' in result
        assert "<b>1</b>: 2" in result  # two-col table → key:value
        assert "<b>Napomena</b>" in result

    def test_full_realistic_response(self):
        """Simulate a Claude accounting response."""
        text = (
            "## PDV stope u Srbiji\n\n"
            "Prema Zakonu o PDV-u, postoje sledeće stope:\n\n"
            "| Vrsta | Stopa |\n"
            "| --- | --- |\n"
            "| Opšta | 20% |\n"
            "| Posebna | 10% |\n\n"
            "**Napomena**: Posebna stopa se primenjuje na:\n"
            "- Hranu\n"
            "- Lekove\n"
            "- Komunalne usluge\n\n"
            "Za više detalja pogledajte [Zakon o PDV](https://example.com)."
        )
        result = _md_to_html(text)
        # Headers
        assert "<b>PDV stope u Srbiji</b>" in result
        # Table rendered as key-value
        assert "<b>Opšta</b>: 20%" in result
        assert "<b>Posebna</b>: 10%" in result
        assert "<pre>" not in result
        # Bold
        assert "<b>Napomena</b>" in result
        # Bullets
        assert "• Hranu" in result
        assert "• Lekove" in result
        # Link
        assert '<a href="https://example.com">' in result
        # All tags balanced
        assert _has_balanced_tags(result)
