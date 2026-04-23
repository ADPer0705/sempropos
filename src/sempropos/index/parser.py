"""
Man page parsing for synopsis, options, and examples.

This module parses raw man page text into a small normalized structure consumed by
the indexing pipeline. It focuses on practical extraction rather than strict man
macro correctness and is designed to degrade gracefully when section structure is
inconsistent across tools/distributions.

The parser is responsible for:
- Extracting the SYNOPSIS section for command usage patterns.
- Identifying and parsing OPTIONS or FLAGS sections into individual flag definitions.
- Extracting example commands from EXAMPLES sections, along with any relevant context.
- Handling common man page formatting and redirection patterns to improve parsing robustness.

Returned object shape (parse_man_page):
- synopsis: str
- flags: list[ParsedFlag]
- examples: list[ParsedExample]
"""

from __future__ import annotations

import re
import subprocess
from typing import TypedDict


class ParsedFlag(TypedDict):
    """Normalized representation of one parsed option/flag line."""

    flag: str
    long_flag: str | None
    takes_value: bool
    value_hint: str | None
    description: str


class ParsedExample(TypedDict):
    """One parsed example command with optional preceding context text."""

    command: str
    context: str | None


class ParsedManPage(TypedDict):
    """Structured parser output consumed by index builder."""

    synopsis: str
    flags: list[ParsedFlag]
    examples: list[ParsedExample]


# Regex patterns for parsing man pages
SECTION_PATTERN = re.compile(r"^\s*[A-Z][A-Z\s]+\s*$")
SEE_REDIRECT_PATTERN = re.compile(r"see\s+([\w.+-]+)\s*\((\w+)\)", re.IGNORECASE)

FLAG_PATTERN = re.compile(
    r"^\s{0,8}"
    r"(-[\w=-]+)"
    r"(?:,\s*(-[\w=-]+))?"
    r"(?:\s+[<=\[]?(\w+)[>=\]]?)?"
    r"\s{2,}(.+)"
)

EXAMPLE_PATTERN = re.compile(r"^\s+([$#>]?\s*\w[\w.-]*)")

# ==================================================
# Parsing Logic
# ==================================================


def _strip_groff(raw_text: str) -> str:
    """Remove common groff control markup that interferes with parsing."""
    text = re.sub(r"\\f[BIRP]", "", raw_text)
    text = re.sub(r"^\.[A-Z]+\s+", "", text, flags=re.MULTILINE)
    return text


def _split_sections(text: str) -> dict[str, str]:
    """Split man page content into uppercase heading-based sections.

    Heading detection is intentionally simple (all-caps line) because this parser
    runs across many distributions and output formats from `man -P cat`.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in text.splitlines():
        if SECTION_PATTERN.match(line):
            current = " ".join(line.strip().split())
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line.rstrip())

    return {name: "\n".join(lines).strip() for name, lines in sections.items()}


def _read_redirected_man_page(tool: str) -> str | None:
    """Read and return a redirected man page body for a tool name.

    Returns None if `man` invocation fails or returns a non-zero exit status.
    """
    try:
        result = subprocess.run(
            ["man", "-P", "cat", tool],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None

    if result.returncode != 0:
        return None
    return result.stdout


def _is_stub_page(text: str) -> bool:
    """Detect short redirection stubs that do not contain useful content.

    Many man pages are thin stubs like "see foo(1)". This heuristic keeps full
    pages in-place while following likely redirect stubs.
    """
    line_count = len([line for line in text.splitlines() if line.strip()])
    has_synopsis = bool(re.search(r"^\s*SYNOPSIS\s*$", text, flags=re.MULTILINE))
    has_options = bool(re.search(r"^\s*(OPTIONS|FLAGS?)\s*$", text, flags=re.MULTILINE))
    return line_count < 30 and not has_synopsis and not has_options


def _extract_options_block(sections: dict[str, str]) -> str:
    """Return the first section body that appears to define options or flags."""
    for key in sections:
        normalized = key.upper()
        if "OPTION" in normalized or normalized == "FLAGS" or normalized == "FLAG":
            return sections[key]
    return ""


def _parse_flags(options_block: str) -> list[ParsedFlag]:
    """Parse OPTIONS text into structured flags, with a robust fallback mode.

    If line-wise parsing fails early, a single wildcard flag entry is returned
    containing the raw options block in `description`. This preserves information
    for retrieval even when precise splitting is not possible.
    """
    if not options_block.strip():
        return []

    flags: list[ParsedFlag] = []
    fallback = False

    for line in options_block.splitlines():
        if not line.strip():
            continue

        match = FLAG_PATTERN.match(line)
        if match:
            first, second, value_hint, description = match.groups()
            forms = [item for item in (first, second) if item]

            short = next((f for f in forms if not f.startswith("--")), None)
            long = next((f for f in forms if f.startswith("--")), None)

            primary = short or long or forms[0]
            flags.append(
                {
                    "flag": primary,
                    "long_flag": long if short and long else None,
                    "takes_value": bool(value_hint),
                    "value_hint": value_hint,
                    "description": description.strip(),
                }
            )
            continue

        if flags:
            flags[-1]["description"] = (
                f"{flags[-1]['description']} {line.strip()}".strip()
            )
            continue

        fallback = True
        break

    if fallback or not flags:
        return [
            {
                "flag": "*",
                "long_flag": None,
                "takes_value": False,
                "value_hint": None,
                "description": options_block.strip(),
            }
        ]

    return flags


def _clean_example_command(raw_line: str) -> str | None:
    """Normalize a candidate example line into a shell command string.

    Leading shell prompts ($, #, >) are stripped when present.
    """
    line = raw_line.strip()
    if not line:
        return None

    if line[0] in {"$", "#", ">"}:
        line = line[1:].strip()

    if not line:
        return None

    return line


def _parse_examples(examples_block: str) -> list[ParsedExample]:
    """Extract example commands and nearest context text from EXAMPLES.

    Non-command lines are treated as pending context and attached to the next
    recognized command line.
    """
    if not examples_block.strip():
        return []

    examples: list[ParsedExample] = []
    pending_context: str | None = None

    for line in examples_block.splitlines():
        if not line.strip():
            continue

        if EXAMPLE_PATTERN.match(line):
            command = _clean_example_command(line)
            if command:
                examples.append({"command": command, "context": pending_context})
                pending_context = None
            continue

        pending_context = line.strip()

    return examples


def parse_man_page(raw_text: str, _visited: set[str] | None = None) -> ParsedManPage:
    """Parse man page text into structured synopsis, flags, and examples.

    Redirect handling:
    - If the page appears to be a short "see X(...)" stub, the parser attempts
      to load and parse the referenced page.
    - `_visited` is used to avoid redirect loops.
    """
    visited = _visited or set()
    text = _strip_groff(raw_text)

    redirect = SEE_REDIRECT_PATTERN.search(text)
    if redirect and _is_stub_page(text):
        tool = redirect.group(1)
        if tool not in visited:
            redirected = _read_redirected_man_page(tool)
            if redirected:
                return parse_man_page(redirected, _visited=visited | {tool})

    sections = _split_sections(text)
    synopsis = sections.get("SYNOPSIS", "")
    options_block = _extract_options_block(sections)

    examples_block = ""
    for key in sections:
        if "EXAMPLE" in key.upper():
            examples_block = sections[key]
            break

    return {
        "synopsis": synopsis,
        "flags": _parse_flags(options_block),
        "examples": _parse_examples(examples_block),
    }
