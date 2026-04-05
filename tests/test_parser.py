from __future__ import annotations

from pathlib import Path

from sempropos.index.parser import parse_man_page


FIXTURE_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> str:
    return (FIXTURE_DIR / f"{name}.txt").read_text(encoding="utf-8")


def test_parse_real_fixtures_have_basic_structure() -> None:
    for name in ["tar", "7z", "curl", "grep", "ffmpeg"]:
        parsed = parse_man_page(_load_fixture(name))
        assert set(parsed.keys()) == {"synopsis", "flags", "examples"}
        assert isinstance(parsed["synopsis"], str)
        assert isinstance(parsed["flags"], list)
        assert isinstance(parsed["examples"], list)


def test_parse_fixtures_extracts_flags_or_fallback() -> None:
    parsed = parse_man_page(_load_fixture("curl"))
    assert parsed["flags"]
    assert all("flag" in item and "description" in item for item in parsed["flags"])


def test_parse_examples_when_section_is_present() -> None:
    raw = """
NAME
  demo - tiny demo

SYNOPSIS
  demo [options]

EXAMPLES
  Basic usage
  $ demo --help
"""
    parsed = parse_man_page(raw)
    assert parsed["examples"]
    assert parsed["examples"][0]["command"] == "demo --help"


def test_missing_options_or_examples_does_not_crash() -> None:
    raw = """
NAME
  demo - tiny demo

SYNOPSIS
  demo [args]
"""
    parsed = parse_man_page(raw)
    assert parsed["synopsis"]
    assert parsed["flags"] == []
    assert parsed["examples"] == []


def test_groff_escape_sequences_are_stripped() -> None:
    raw = r"""
NAME
  demo - tiny demo

SYNOPSIS
  \fBdemo\fR [args]

OPTIONS
  -n  number of times

EXAMPLES
  $ demo -n 2
"""
    parsed = parse_man_page(raw)
    assert "\\fB" not in parsed["synopsis"]
