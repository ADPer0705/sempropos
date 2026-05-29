"""
Man page parsing for synopsis, options, and examples.
"""

from __future__ import annotations

import re
import subprocess
from typing import TypedDict

class ParsedFlag(TypedDict):
    flag: str
    long_flag: str | None
    takes_value: bool
    value_hint: str | None
    description: str

class ParsedExample(TypedDict):
    command: str
    context: str | None

class ParsedManPage(TypedDict):
    synopsis: str
    flags: list[ParsedFlag]
    examples: list[ParsedExample]


# Strict start of line, no indent, uppercase chars/slashes/spaces only
SECTION_PATTERN = re.compile(r"^[A-Z][A-Z0-9\s_/-]+$")
SEE_REDIRECT_PATTERN = re.compile(r"see\s+([\w.+-]+)\s*\((\w+)\)", re.IGNORECASE)

# Relaxed spacing to catch single spaces or tabs between flag and description
FLAG_PATTERN = re.compile(
    r"^\s{0,8}"
    r"(-[\w=-]+)"                       # primary flag: -v or --verbose
    r"(?:,\s*(-[\w=-]+))?"              # optional alias: , --verbose
    r"(?:\s+[<=\[]?([A-Za-z0-9_-]+)[>=\]]?)?" # optional value hint
    r"(?:\s+(.+))?"                     # description (any whitespace separator)
)

EXAMPLE_PATTERN = re.compile(r"^\s*(?:[$#>]\s*)?([a-zA-Z0-9_-]+.*)")


def _strip_groff(raw_text: str) -> str:
    text = re.sub(r"\\f[BIRP]", "", raw_text)
    text = re.sub(r"^\.[A-Z]+\s+", "", text, flags=re.MULTILINE)
    return text

def _split_sections(text: str) -> dict[str, str]:
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
    line_count = sum(1 for line in text.splitlines() if line.strip())
    has_synopsis = bool(re.search(r"^\s*SYNOPSIS\s*$", text, flags=re.MULTILINE))
    has_options = bool(re.search(r"^\s*(OPTIONS|FLAGS?)\s*$", text, flags=re.MULTILINE))
    return line_count < 30 and not has_synopsis and not has_options

def _extract_options_block(sections: dict[str, str]) -> str:
    for key in sections:
        normalized = key.upper()
        if "OPTION" in normalized or normalized in ("FLAGS", "FLAG"):
            return sections[key]
    return ""

def _parse_flags(options_block: str) -> list[ParsedFlag]:
    if not options_block.strip():
        return []

    flags: list[ParsedFlag] = []

    for line in options_block.splitlines():
        if not line.strip():
            continue

        match = FLAG_PATTERN.match(line)
        if match:
            first, second, value_hint, description = match.groups()
            forms = [f for f in (first, second) if f]

            short = next((f for f in forms if not f.startswith("--")), None)
            long = next((f for f in forms if f.startswith("--")), None)
            primary = short or long or forms[0]

            flags.append(
                ParsedFlag(
                    flag=primary,
                    long_flag=long if (short and long) else None,
                    takes_value=bool(value_hint),
                    value_hint=value_hint,
                    description=(description or "").strip(),
                )
            )
            continue

        if flags:
            # continuation line: append to previous flag's description
            flags[-1]["description"] = f"{flags[-1]['description']} {line.strip()}".strip()
            continue
        
        # Ignored pre-flag paragraph text (doesn't break the loop anymore)
        continue

    if not flags:
        return [
            ParsedFlag(
                flag="*",
                long_flag=None,
                takes_value=False,
                value_hint=None,
                description=options_block.strip(),
            )
        ]

    return flags

def _clean_example_command(raw_line: str) -> str | None:
    line = raw_line.strip()
    if not line:
        return None
    if line[0] in {"$", "#", ">"}:
        line = line[1:].strip()
    return line or None

def _parse_examples(examples_block: str) -> list[ParsedExample]:
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
                examples.append(ParsedExample(command=command, context=pending_context))
                pending_context = None
            continue

        # Prevent overwriting context blocks spanning multiple lines
        pending_context = f"{pending_context} {line.strip()}".strip() if pending_context else line.strip()

    return examples


def parse_man_page(raw_text: str, _visited: set[str] | None = None) -> ParsedManPage:
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

    # Dynamic fuzzy lookup for SYNOPSIS (catches "COMMAND SYNOPSIS", etc.)
    synopsis = next((v for k, v in sections.items() if "SYNOPSIS" in k.upper()), "")
    options_block = _extract_options_block(sections)

    examples_block = ""
    for key in sections:
        if "EXAMPLE" in key.upper():
            examples_block = sections[key]
            break

    return ParsedManPage(
        synopsis=synopsis,
        flags=_parse_flags(options_block),
        examples=_parse_examples(examples_block),
    )