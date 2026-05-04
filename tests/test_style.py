"""Tests for relic.style — the visual theme module.

These guard against regressions in the brand identity:
- Mark and palette constants are stable (changing them is a deliberate act).
- Helpers produce valid Rich markup that renders without errors.
- The custom hexagon spinner stays registered with Rich.
- Banner contains the version + tagline + brand mark.
"""

from __future__ import annotations

from io import StringIO

from rich._spinners import SPINNERS
from rich.console import Console

from relic import style


class TestPalette:
    def test_primary_is_frost_blue(self):
        # Brand colour shouldn't drift accidentally; lock the hex.
        assert style.PRIMARY == "#88C0D0"

    def test_palette_is_truecolor_hex(self):
        # Every named colour must be a usable hex string.
        for name in ("PRIMARY", "DEEP", "SECONDARY", "FG", "DIM", "SUCCESS", "WARN", "ERROR"):
            value = getattr(style, name)
            assert isinstance(value, str)
            assert value.startswith("#") and len(value) == 7

    def test_mark_is_hexagon(self):
        assert style.MARK == "⬢"
        assert style.MARK_HOLLOW == "⬡"


class TestHelpers:
    def _render(self, markup: str) -> str:
        buf = StringIO()
        Console(file=buf, force_terminal=False, width=120).print(markup)
        return buf.getvalue()

    def test_header_contains_mark_and_text(self):
        out = self._render(style.header("index"))
        assert style.MARK in out
        assert "index" in out

    def test_success_uses_check_glyph(self):
        out = self._render(style.success("done"))
        assert style.CHECK in out
        assert "done" in out

    def test_error_uses_cross_glyph(self):
        out = self._render(style.error("oops"))
        assert style.CROSS in out
        assert "oops" in out

    def test_warn_uses_warn_glyph(self):
        out = self._render(style.warn("careful"))
        assert style.WARN_GLYPH in out
        assert "careful" in out

    def test_info_uses_arrow(self):
        out = self._render(style.info("next step"))
        assert style.ARROW in out
        assert "next step" in out

    def test_kv_pads_key(self):
        # The key gets left-padded so multiple kv() calls form a clean column.
        out = self._render(style.kv("files", 11))
        assert "files" in out
        assert "11" in out

    def test_dim_does_not_swallow_content(self):
        out = self._render(style.dim("note"))
        assert "note" in out

    def test_divider_renders_a_line(self):
        out = self._render(style.divider(width=5))
        assert "─" * 5 in out


class TestBanner:
    def test_banner_includes_version_and_tagline(self):
        text = style.banner("9.9.9")
        assert "9.9.9" in text
        assert "relic" in text
        assert "codebase knowledge graph" in text

    def test_banner_uses_both_marks(self):
        text = style.banner("0.1.0")
        # The graph hint uses both filled and hollow hexagons.
        assert style.MARK in text
        assert style.MARK_HOLLOW in text

    def test_banner_renders_clean(self):
        buf = StringIO()
        Console(file=buf, force_terminal=False, width=120).print(style.banner("0.1.0"))
        assert "[bold" not in buf.getvalue()  # markup was interpreted, not printed raw


class TestSpinner:
    def test_spinner_registered(self):
        assert style.SPINNER_NAME in SPINNERS

    def test_spinner_pulses_between_marks(self):
        # The 2-frame breathe is the brand. Frames must be the two hexagons.
        frames = SPINNERS[style.SPINNER_NAME]["frames"]
        assert set(frames) == {style.MARK, style.MARK_HOLLOW}

    def test_spinner_interval_is_calm(self):
        # > 300 ms keeps it from feeling anxious. Lock that intent.
        assert SPINNERS[style.SPINNER_NAME]["interval"] >= 300


class TestTableFactory:
    def test_make_table_is_borderless(self):
        # SIMPLE_HEAD = header underline only. No inner row dividers.
        table = style.make_table(title="x")
        assert table.show_lines is False

    def test_make_table_supports_no_title(self):
        table = style.make_table()
        # Should not raise and should still render.
        buf = StringIO()
        Console(file=buf, force_terminal=False, width=80).print(table)
