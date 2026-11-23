"""Direct macro-aware parser for man(7) and mdoc(7) manual pages.

Reads compressed (.gz, .xz, .bz2, .zst) and uncompressed roff files directly
from disk without spawning thousands of `man` subprocesses, avoiding 80-column
wrapping and soft-hyphenation corruption while extracting structured flags
(including GNU coreutils flags inside DESCRIPTION), subcommands, and real
examples (without prose pollution).
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sempropos.model.spec import ExampleSpec, FlagSpec, SourceKind, SubcommandSpec

_MAX_DESC = 420
_MAX_SUMMARY = 240
_MAX_FLAGS = 400
_MAX_EXAMPLES = 40

# Option-bearing section names.
_OPTION_SECTION_TOKENS = (
    "OPTION",
    "FLAG",
    "SWITCH",
    "EXPRESSION",
    "PREDICATE",
    "TEST",
    "ACTION",
    "MODIFIER",
    "ARGUMENT",
    "PRIMARY",
    "OPERATOR",
    "PARAMETER",
    "KEY",
)

# Prose starters that must never be mistaken for shell example commands.
_PROSE_STARTERS = frozenset(
    {
        "the",
        "this",
        "that",
        "these",
        "those",
        "note",
        "warning",
        "caution",
        "see",
        "for",
        "if",
        "when",
        "where",
        "to",
        "in",
        "on",
        "at",
        "by",
        "with",
        "without",
        "from",
        "into",
        "use",
        "using",
        "you",
        "your",
        "it",
        "its",
        "is",
        "are",
        "was",
        "were",
        "be",
        "can",
        "may",
        "will",
        "would",
        "should",
        "must",
        "not",
        "no",
        "or",
        "and",
        "but",
        "also",
        "however",
        "example",
        "examples",
        "simple",
        "safer",
        "above",
        "below",
        "here",
        "some",
        "all",
        "each",
        "every",
        "any",
        "only",
        "since",
        "because",
        "although",
        "while",
        "whereas",
        "default",
        "notice",
        "remember",
        "otherwise",
        "alternatively",
    }
)

_SO_REDIRECT_RE = re.compile(r"^\s*\.so\s+(.+)$", re.MULTILINE)
_SEE_ALSO_RE = re.compile(r"\b([a-zA-Z0-9][a-zA-Z0-9_.+-]{0,40})\s*\(\s*([18][a-zA-Z]*)\s*\)")

# Matches flag header lines such as:
#   -h, --human-readable
#   -d, --max-depth=N
#   -B, --block-size=SIZE
#   --color[=WHEN]
#   -e PATTERN, --regexp=PATTERN
#   -size n[cwbkMG]
#   -L, --dereference
_FLAG_TOKEN_RE = re.compile(
    r"^(?P<flag>--[A-Za-z0-9][A-Za-z0-9._-]*|-[A-Za-z0-9?@#%+][A-Za-z0-9._-]*)"
    r"(?:(?P<opt_eq>\[=|=)(?P<eq_val>[^,\s\]]+)\]?)?"
    r"(?:\s+(?P<sp_val><[^>]+>|\[[^\]]+\]|[A-Z][A-Z0-9_./-]{0,24}|[a-z][a-z0-9_-]{0,12}\[[^\]]+\]))?"
)

_RENDERED_SECTION_RE = re.compile(r"^[A-Z][A-Z0-9\s_/-]{1,60}$")
_RENDERED_FLAG_LINE_RE = re.compile(
    r"^\s{1,12}(-[A-Za-z0-9?@#][A-Za-z0-9._=-]*(?:(?:\[=|=)[^,\s\]]+\]?)?"
    r"(?:,\s*--?[A-Za-z0-9][A-Za-z0-9._=-]*(?:(?:\[=|=)[^,\s\]]+\]?)?)*"
    r"(?:\s+(?:<[^>]+>|\[[^\]]+\]|[A-Z][A-Z0-9_./-]{0,24}))?)"
    r"(?:\s{2,}(.*)|\s*$)"
)


@dataclass
class ParsedManDocument:
    """Structured output from parsing a single man page."""

    name: str
    summary: str = ""
    description: str = ""
    aliases: list[str] = field(default_factory=list)
    synopsis: list[str] = field(default_factory=list)
    subcommands: list[SubcommandSpec] = field(default_factory=list)
    flags: list[FlagSpec] = field(default_factory=list)
    examples: list[ExampleSpec] = field(default_factory=list)
    see_also: list[str] = field(default_factory=list)
    dialect: str = "man"
    low_confidence: bool = False


# ============================================================================
# File Decompression & .so Redirects
# ============================================================================


def _decompress_zstd(raw: bytes) -> bytes | None:
    try:
        from compression import zstd as std_zstd  # type: ignore[import-not-found]

        return bytes(std_zstd.decompress(raw))
    except Exception:  # noqa: BLE001
        pass

    try:
        import zstandard  # type: ignore[import-not-found]

        return bytes(zstandard.ZstdDecompressor().decompress(raw))
    except Exception:  # noqa: BLE001
        pass

    try:
        proc = subprocess.run(
            ["zstd", "-dc"],
            input=raw,
            check=False,
            capture_output=True,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def read_man_file(path: Path, *, _depth: int = 0) -> str | None:
    """Read and decompress a local man page file, resolving ``.so`` redirects."""
    if _depth > 4 or not path.exists():
        return None

    try:
        raw_bytes = path.read_bytes()
    except OSError:
        return None

    suffix = path.suffix.lower()
    data: bytes | None = raw_bytes
    try:
        if suffix == ".gz":
            data = gzip.decompress(raw_bytes)
        elif suffix in (".xz", ".lzma"):
            data = lzma.decompress(raw_bytes)
        elif suffix == ".bz2":
            data = bz2.decompress(raw_bytes)
        elif suffix == ".zst":
            data = _decompress_zstd(raw_bytes)
    except OSError:
        return None

    if data is None:
        return None

    text = data.decode("utf-8", errors="replace")

    # Resolve `.so man1/podman.1` style redirects within the same man tree.
    stripped = text.strip()
    if len(stripped.splitlines()) <= 5:
        match = _SO_REDIRECT_RE.search(stripped)
        if match:
            rel_target = match.group(1).strip().strip('"')
            man_root = path.parent.parent
            for ext in ("", ".gz", ".xz", ".bz2", ".zst"):
                candidate = man_root / f"{rel_target}{ext}"
                if candidate.exists() and candidate != path:
                    return read_man_file(candidate, _depth=_depth + 1)

    return text


# ============================================================================
# Shared Text & Flag Parsing Helpers
# ============================================================================


def clean_roff_inline(text: str) -> str:
    """Strip inline roff font/character escapes into clean plain text."""
    if not text:
        return ""
    s = text
    # Strip comments `\" ...`
    s = re.sub(r'\\".*$', "", s)
    s = re.sub(r"\\#.*$", "", s)
    # Named special glyphs
    replacements = (
        (r"\-", "-"),
        (r"\(mi", "-"),
        (r"\(hy", "-"),
        (r"\(en", "-"),
        (r"\(em", "--"),
        (r"\(dq", '"'),
        (r"\(aq", "'"),
        (r"\(lq", '"'),
        (r"\(rq", '"'),
        (r"\(oq", "'"),
        (r"\(cq", "'"),
        (r"\(ga", "`"),
        (r"\(ti", "~"),
        (r"\(ha", "^"),
        (r"\(rs", "\\"),
        (r"\(sl", "/"),
        (r"\(bu", "*"),
        (r"\(co", "(c)"),
        (r"\ ", " "),
        (r"\~", " "),
        (r"\0", " "),
        (r"\|", ""),
        (r"\^", ""),
        (r"\,", ""),
        (r"\/", ""),
        (r"\&", ""),
        (r"\:", ""),
        (r"\%", ""),
        (r"\c", ""),
        (r"\e", "\\"),
    )
    for src, dst in replacements:
        s = s.replace(src, dst)

    # Strip OSC-8 / device control escapes like `\X'tty: link https://...'` (GNU coreutils help2man)
    s = re.sub(r"\\X'[^']*'", "", s)
    # Strip color escapes `\m[blue]`, `\m[]`, `\M[...]` and vertical shifts `\u`, `\d`
    s = re.sub(r"\\[mM](?:\[[^\]]*\]|\(..|.)", "", s)
    s = re.sub(r"\\[ud]", "", s)
    # Font switches: \fB, \f(CW, \f[BI]
    s = re.sub(r"\\f(?:\[[^\]]*\]|\(..|.)", "", s)
    # Size changes: \s-1, \s+2, \s0, \s[10]
    s = re.sub(r"\\s(?:\[[^\]]*\]|[+-]?\d+)", "", s)
    # String / register interpolations
    s = re.sub(r"\\[*n](?:\[[^\]]*\]|\(..|.)", "", s)
    # Generic 2-char or bracketed glyph escapes
    s = re.sub(r"\\\((..)", r"\1", s)
    s = re.sub(r"\\\[([^\]]+)\]", "", s)
    # Stray backslash before punctuation
    s = re.sub(r"\\([.,'\"`~^|(){}[\]<>])", r"\1", s)
    return " ".join(s.split())


def _split_macro_args(rest: str) -> list[str]:
    """Tokenize roff macro arguments respecting double quotes."""
    args: list[str] = []
    i = 0
    n = len(rest)
    while i < n:
        while i < n and rest[i].isspace():
            i += 1
        if i >= n:
            break
        if rest[i] == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                if rest[i] == '"':
                    if i + 1 < n and rest[i + 1] == '"':
                        buf.append('"')
                        i += 2
                        continue
                    i += 1
                    break
                buf.append(rest[i])
                i += 1
            args.append("".join(buf))
        else:
            start = i
            while i < n and not rest[i].isspace():
                i += 1
            args.append(rest[start:i])
    return args


def _extract_value_hint(eq_val: str | None, sp_val: str | None) -> str | None:
    if eq_val:
        return eq_val.strip("<>[]") or None
    if not sp_val:
        return None
    cleaned = sp_val.strip("<>[]")
    if cleaned and (
        sp_val.startswith(("<", "["))
        or cleaned.isupper()
        or "[" in sp_val
        or cleaned in ("file", "dir", "path", "pattern", "num", "n", "size", "cmd")
    ):
        return cleaned
    return None


def parse_flag_header(
    header: str,
    description: str = "",
    *,
    section: str | None = None,
    source: SourceKind = "man_roff",
) -> FlagSpec | None:
    """Parse a flag header line like ``-d, --max-depth=N`` into a ``FlagSpec``."""
    cleaned = " ".join(header.strip().split()).rstrip(":")
    if (
        not cleaned
        or not cleaned.startswith("-")
        or cleaned in ("-", "--")
        or cleaned.startswith("---")
    ):
        return None
    if re.match(r"^-\s+", cleaned):
        return None

    parts = re.split(r",\s*(?=-)|\s+[/|]\s+(?=-)", cleaned)
    short_flag: str | None = None
    long_flag: str | None = None
    value_hint: str | None = None

    for part in parts:
        match = _FLAG_TOKEN_RE.match(part.strip())
        if not match:
            continue
        flag_name = match.group("flag")
        if "=" in flag_name:
            flag_name, inline_val = flag_name.split("=", 1)
            value_hint = value_hint or inline_val.strip("<>[]") or None

        if flag_name.startswith("--"):
            long_flag = long_flag or flag_name
        else:
            short_flag = short_flag or flag_name

        value_hint = value_hint or _extract_value_hint(match.group("eq_val"), match.group("sp_val"))

    if not (short_flag or long_flag):
        return None
    if (short_flag and len(short_flag) > 28) or (long_flag and len(long_flag) > 48):
        return None

    return FlagSpec(
        short=short_flag,
        long=long_flag,
        takes_value=bool(value_hint),
        value_hint=value_hint[:32] if value_hint else None,
        description=" ".join(description.split())[:_MAX_DESC],
        section=section,
        source=source,
    )


def _is_option_section(section_name: str) -> bool:
    upper = section_name.upper()
    return any(tok in upper for tok in _OPTION_SECTION_TOKENS)


_NON_FLAG_SECTIONS = (
    "NAME",
    "SYNOPSIS",
    "EXAMPLE",
    "SEE ALSO",
    "AUTHOR",
    "COPYRIGHT",
    "LICENSE",
    "BUG",
    "REPORTING",
    "HISTORY",
    "EXIT STATUS",
    "RETURN VALUE",
    "ENVIRONMENT",
    "FILES",
)


def _is_flag_eligible_section(section_name: str) -> bool:
    upper = section_name.upper()
    if _is_option_section(upper) or "DESCRIPTION" in upper:
        return True
    return bool(upper) and not any(bad in upper for bad in _NON_FLAG_SECTIONS)


def _extract_subcommand_name(header: str, tool_name: str) -> str | None:
    """Extract subcommand name from headers like ``git-add(1)`` or ``commit``."""
    first = header.split()[0].strip(",:")
    # Strip trailing man section `(1)` / `(8)`
    first = re.sub(r"\(\d[a-zA-Z]*\)$", "", first)
    prefix = f"{tool_name}-"
    if first.startswith(prefix):
        first = first[len(prefix) :]
    if re.match(r"^[a-z][a-z0-9_-]{0,28}$", first) and first != tool_name:
        return first
    return None


def _looks_like_shell_command(line: str, tool_name: str, *, in_code_block: bool = False) -> bool:
    """Distinguish real command examples from English prose in EXAMPLES sections."""
    s = line.strip()
    if not s or len(s) < 2 or len(s) > 260:
        return False

    # Strip explicit shell prompt prefixes
    prompt_marked = False
    if s.startswith(("$ ", "# ", "% ", "> ")):
        prompt_marked = True
        s = s[2:].strip()

    if not s:
        return False

    # Sentences ending in a period without flags/pipes/paths are almost always prose.
    if (
        s.endswith(".")
        and not prompt_marked
        and not in_code_block
        and " -" not in s
        and "|" not in s
        and "/" not in s
    ):
        return False

    first_token = s.split()[0].lstrip("(")
    lowered_first = first_token.lower()

    # Exact match with the tool being documented (or sudo/env + tool)
    if first_token == tool_name or first_token.endswith(f"/{tool_name}"):
        return True
    if lowered_first in ("sudo", "env", "time", "watch", "nohup") and tool_name in s.split():
        return True

    # Pipeline invoking the tool (e.g. `cat foo | grep bar` or `ps aux | awk ...`)
    if "|" in s and re.search(rf"(?:^|\|\s*){re.escape(tool_name)}\b", s):
        return True

    if prompt_marked and lowered_first not in _PROSE_STARTERS:
        return True

    if in_code_block:
        if lowered_first in _PROSE_STARTERS:
            return False
        # Must look command-like: contains flag, pipe, redirect, path, or assignment
        if re.search(r"(?:\s--?[A-Za-z0-9]|/|\||>|<\s|\$\(|=)", s):
            return True

    return False


def _clean_command_line(line: str) -> str:
    s = line.strip()
    while s.startswith(("$ ", "# ", "% ", "> ")):
        s = s[2:].strip()
    return s


def _extract_name_summary(name_text: str, fallback_name: str) -> tuple[str, list[str]]:
    """Parse ``NAME`` section like ``tar, gtar - an archiving utility``."""
    collapsed = " ".join(name_text.split())
    if not collapsed:
        return "", []
    match = re.match(r"^(.+?)\s+(?:--|[-–—])\s+(.+)$", collapsed)
    if not match:
        return collapsed[:_MAX_SUMMARY], []

    left, summary = match.group(1), match.group(2).strip()
    names = [n.strip() for n in re.split(r"[,\s]+", left) if n.strip()]
    aliases = [
        n for n in names if n != fallback_name and re.match(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", n)
    ]
    return summary[:_MAX_SUMMARY], aliases


def _extract_see_also(text: str, self_name: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = {self_name}
    for match in _SEE_ALSO_RE.finditer(text):
        target = match.group(1).strip()
        if target and target not in seen:
            seen.add(target)
            found.append(target)
    return found[:25]


def _extract_examples_from_lines(
    lines: list[tuple[str, bool]],
    tool_name: str,
    source: SourceKind,
) -> list[ExampleSpec]:
    """Extract verified examples from ``(line, in_code_block)`` pairs."""
    examples: list[ExampleSpec] = []
    seen_cmds: set[str] = set()
    pending_desc: list[str] = []

    for raw_line, in_code in lines:
        s = raw_line.strip()
        if not s:
            if len(pending_desc) > 2:
                pending_desc = pending_desc[-2:]
            continue

        if _looks_like_shell_command(s, tool_name, in_code_block=in_code):
            cmd = _clean_command_line(s)
            if cmd and cmd not in seen_cmds:
                seen_cmds.add(cmd)
                desc = " ".join(pending_desc).strip()[:220]
                desc = desc.rstrip(":")
                examples.append(ExampleSpec(command=cmd, description=desc, source=source))
                if len(examples) >= _MAX_EXAMPLES:
                    break
            pending_desc = []
        else:
            # Keep concise preceding prose as context for the next command.
            cleaned = s.lstrip("•*- ").strip()
            if cleaned and not cleaned.startswith(("Note:", "NOTE:", "Warning:")):
                pending_desc.append(cleaned)
                if len(pending_desc) > 3:
                    pending_desc = pending_desc[-2:]

    return examples


def _dedupe_flags(flags: list[FlagSpec]) -> list[FlagSpec]:
    merged: list[FlagSpec] = []
    by_key: dict[tuple[str | None, str | None], int] = {}
    for flag in flags:
        key = flag.canonical_key
        if key in by_key:
            idx = by_key[key]
            existing = merged[idx]
            if len(flag.description) > len(existing.description):
                merged[idx] = flag
            continue
        by_key[key] = len(merged)
        merged.append(flag)
        if len(merged) >= _MAX_FLAGS:
            break
    return merged


# ============================================================================
# man(7) Macro Parser
# ============================================================================


def _render_man_macro_line(macro: str, rest: str) -> str:
    """Render inline font/formatting man(7) macros into plain text."""
    args = _split_macro_args(rest)
    if macro in ("B", "I", "SM", "SB", "R"):
        return clean_roff_inline(" ".join(args))
    if macro in ("BI", "BR", "IB", "IR", "RB", "RI"):
        return clean_roff_inline("".join(args))
    return clean_roff_inline(rest)


def _join_roff_continuations(raw: str) -> list[str]:
    """Join lines ending with a single unescaped trailing backslash."""
    out: list[str] = []
    buf = ""
    for line in raw.splitlines():
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        if buf:
            out.append(buf + line)
            buf = ""
        else:
            out.append(line)
    if buf:
        out.append(buf)
    return out


def _parse_man7(raw: str, tool_name: str) -> ParsedManDocument:  # noqa: C901
    """Parse a standard ``man(7)`` macro source file."""
    doc = ParsedManDocument(name=tool_name, dialect="man")
    lines = _join_roff_continuations(raw)

    section = ""
    subsection = ""
    in_code = False
    in_synopsis_macro = False
    syn_buf: list[str] = []

    name_lines: list[str] = []
    cur_syn_line: list[str] = []
    desc_paragraphs: list[str] = []

    def flush_syn() -> None:
        nonlocal cur_syn_line
        if cur_syn_line:
            joined = " ".join(cur_syn_line).strip()
            if joined:
                doc.synopsis.append(joined)
            cur_syn_line = []

    cur_desc_para: list[str] = []
    see_also_lines: list[str] = []
    example_lines: list[tuple[str, bool]] = []

    # Active tagged paragraph (.TP / .TQ / .IP / DocBook .PP + .RS) state
    expect_tp_header = False
    pending_headers: list[str] = []
    active_header: str | None = None
    active_body: list[str] = []
    last_plain_line: str | None = None
    in_docbook_rs = False

    def flush_tagged() -> None:
        nonlocal active_header, active_body, pending_headers, in_docbook_rs
        if not active_header:
            in_docbook_rs = False
            return
        body_text = " ".join(active_body).strip()
        sec_upper = section.upper()
        headers_to_parse = [*pending_headers, active_header]
        if _is_flag_eligible_section(sec_upper):
            parsed_any = False
            for hdr in headers_to_parse:
                parsed_flag = parse_flag_header(
                    hdr,
                    body_text,
                    section=subsection or section or None,
                    source="man_roff",
                )
                if parsed_flag is not None:
                    doc.flags.append(parsed_flag)
                    parsed_any = True
            if not parsed_any and "COMMAND" in sec_upper and body_text:
                sub_name = _extract_subcommand_name(active_header, tool_name)
                if sub_name:
                    doc.subcommands.append(
                        SubcommandSpec(name=sub_name, summary=body_text[:200], source="man_roff")
                    )
        elif "COMMAND" in sec_upper and body_text:
            sub_name = _extract_subcommand_name(active_header, tool_name)
            if sub_name:
                doc.subcommands.append(
                    SubcommandSpec(name=sub_name, summary=body_text[:200], source="man_roff")
                )
        active_header = None
        active_body = []
        pending_headers = []
        in_docbook_rs = False

    def flush_desc_para() -> None:
        nonlocal cur_desc_para
        if cur_desc_para:
            joined = " ".join(cur_desc_para).strip()
            if joined:
                desc_paragraphs.append(joined)
            cur_desc_para = []

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        i += 1
        stripped = raw_line.strip()

        # Skip roff comments and conditional/definition boilerplate
        if not stripped or stripped.startswith(('.\\"', "'\\\"", ".\\#", ".de ", ".ig")):
            if not stripped:
                flush_desc_para()
                if "EXAMPLE" in section.upper():
                    example_lines.append(("", in_code))
            continue

        if stripped.startswith((".", "'")):
            parts = stripped[1:].lstrip().split(None, 1)
            if not parts:
                continue
            macro = parts[0]
            rest = parts[1] if len(parts) > 1 else ""

            if macro == "SH":
                flush_tagged()
                flush_desc_para()
                flush_syn()
                in_code = False
                expect_tp_header = False
                section = clean_roff_inline(" ".join(_split_macro_args(rest))).strip().upper()
                subsection = ""
                continue

            if macro == "SS":
                flush_tagged()
                flush_desc_para()
                flush_syn()
                subsection = clean_roff_inline(" ".join(_split_macro_args(rest))).strip()
                continue

            if macro in ("EX", "nf"):
                in_code = True
                continue

            if macro in ("EE", "fi"):
                in_code = False
                continue

            if macro == "SY":
                in_synopsis_macro = True
                syn_buf = [clean_roff_inline(rest)]
                continue

            if macro == "YS":
                if syn_buf:
                    line_s = " ".join(part for part in syn_buf if part).strip()
                    if line_s:
                        doc.synopsis.append(line_s)
                syn_buf = []
                in_synopsis_macro = False
                continue

            if macro == "OP":
                op_args = _split_macro_args(rest)
                if op_args:
                    piece = (
                        f"[{clean_roff_inline(op_args[0])} {clean_roff_inline(op_args[1])}]"
                        if len(op_args) > 1
                        else f"[{clean_roff_inline(op_args[0])}]"
                    )
                    if in_synopsis_macro:
                        syn_buf.append(piece)
                continue

            if macro == "TP":
                flush_tagged()
                flush_desc_para()
                expect_tp_header = True
                continue

            if macro == "TQ":
                if active_header:
                    pending_headers.append(active_header)
                    active_header = None
                expect_tp_header = True
                continue

            if macro == "IP":
                ip_args = _split_macro_args(rest)
                hdr = clean_roff_inline(ip_args[0]) if ip_args else ""
                # Bare `.IP` (used by ronn/pandoc in jq.1) continues the active `.TP` body!
                if not hdr and active_header is not None:
                    continue
                flush_tagged()
                flush_desc_para()
                if hdr and hdr not in ("*", "•", "o", "-"):
                    active_header = hdr
                continue

            if macro == "RS":
                # DocBook pattern: `.PP` -> header line -> `.RS 4` -> description -> `.RE`
                if active_header is None and last_plain_line:
                    active_header = last_plain_line
                    in_docbook_rs = True
                    last_plain_line = None
                continue

            if macro == "RE":
                if in_docbook_rs:
                    flush_tagged()
                continue

            if macro in ("PP", "LP", "P", "sp", "br"):
                if expect_tp_header:
                    continue
                if "SYNOPSIS" in section:
                    flush_syn()
                if macro in ("PP", "LP", "P"):
                    flush_tagged()
                    flush_desc_para()
                    last_plain_line = None
                if "EXAMPLE" in section.upper() and macro in ("PP", "LP", "P", "sp"):
                    example_lines.append(("", in_code))
                continue

            if macro in ("B", "I", "BI", "BR", "IB", "IR", "RB", "RI", "SM", "SB", "R"):
                rendered = _render_man_macro_line(macro, rest)
            else:
                # Ignore layout/meta macros (.TH, .nr, .ds, .ie, .el, .ad, .nh, .hy, .RS, etc.)
                continue
        else:
            rendered = clean_roff_inline(raw_line)

        if not rendered:
            continue

        if expect_tp_header:
            active_header = rendered
            expect_tp_header = False
            continue

        if in_synopsis_macro:
            syn_buf.append(rendered)
            continue

        if active_header is not None:
            if len(" ".join(active_body)) < _MAX_DESC + 60:
                active_body.append(rendered)
            continue

        if section == "NAME":
            name_lines.append(rendered)
        elif "SYNOPSIS" in section:
            cur_syn_line.append(rendered)
        elif "DESCRIPTION" in section:
            if len(desc_paragraphs) < 2:
                cur_desc_para.append(rendered)
            last_plain_line = rendered
        elif "EXAMPLE" in section:
            example_lines.append((rendered, in_code))
        elif "SEE ALSO" in section:
            see_also_lines.append(rendered)
        else:
            last_plain_line = rendered

    flush_tagged()
    flush_desc_para()
    flush_syn()

    doc.summary, doc.aliases = _extract_name_summary(" ".join(name_lines), tool_name)
    doc.description = " ".join(desc_paragraphs[:2])[:500].strip()
    doc.synopsis = _clean_synopsis_lines(doc.synopsis)
    doc.flags = _dedupe_flags(doc.flags)
    doc.examples = _extract_examples_from_lines(example_lines, tool_name, "man_roff")
    doc.see_also = _extract_see_also(" ".join(see_also_lines), tool_name)
    return doc


# ============================================================================
# mdoc(7) Macro Parser
# ============================================================================


def _render_mdoc_tokens(tokens: list[str], tool_name: str) -> str:
    """Render inline mdoc(7) macro callables into readable text."""
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "Fl":
            if i + 1 < len(tokens) and not tokens[i + 1].startswith("."):
                nxt = tokens[i + 1]
                if nxt == "-":
                    out.append("--")
                elif nxt.startswith("-"):
                    out.append(f"-{nxt}")
                else:
                    out.append(f"-{nxt}")
                i += 2
                continue
            out.append("-")
            i += 1
            continue
        if tok == "Ar":
            if i + 1 < len(tokens):
                out.append(tokens[i + 1].upper())
                i += 2
                continue
            i += 1
            continue
        if tok == "Nm":
            if i + 1 < len(tokens) and tokens[i + 1] not in (",", ".", ";", ":"):
                out.append(tokens[i + 1])
                i += 2
            else:
                out.append(tool_name)
                i += 1
            continue
        if tok == "Op":
            inner = _render_mdoc_tokens(tokens[i + 1 :], tool_name)
            out.append(f"[{inner}]")
            break
        if tok == "Xr" and i + 2 < len(tokens):
            out.append(f"{tokens[i + 1]}({tokens[i + 2]})")
            i += 3
            continue
        if tok in (
            "Cm",
            "Ql",
            "Sq",
            "Dq",
            "Pq",
            "Pa",
            "Ev",
            "Ic",
            "Li",
            "Va",
            "Em",
            "Sy",
            "No",
            "Ns",
        ):
            i += 1
            continue
        out.append(tok)
        i += 1

    text = " ".join(out)
    text = re.sub(r"\s+([,.;:)\]])", r"\1", text)
    text = re.sub(r"([(\[])\s+", r"\1", text)
    return clean_roff_inline(text)


def _parse_mdoc7(raw: str, tool_name: str) -> ParsedManDocument:  # noqa: C901
    """Parse an ``mdoc(7)`` semantic manual page (OpenSSH, BSD, mandoc tools)."""
    doc = ParsedManDocument(name=tool_name, dialect="mdoc")
    lines = _join_roff_continuations(raw)

    section = ""
    in_code = False
    name_tokens: list[str] = []
    desc_paras: list[str] = []
    cur_desc: list[str] = []
    example_lines: list[tuple[str, bool]] = []
    see_also_targets: list[str] = []

    active_flag_header: str | None = None
    active_flag_body: list[str] = []

    def flush_flag() -> None:
        nonlocal active_flag_header, active_flag_body
        if not active_flag_header:
            return
        parsed = parse_flag_header(
            active_flag_header,
            " ".join(active_flag_body),
            section=section or None,
            source="man_roff",
        )
        if parsed is not None:
            doc.flags.append(parsed)
        active_flag_header = None
        active_flag_body = []

    for raw_line in lines:
        stripped = raw_line.strip()
        if not stripped or stripped.startswith(('.\\"', "'\\\"")):
            continue

        if stripped.startswith("."):
            parts = stripped[1:].lstrip().split(None, 1)
            if not parts:
                continue
            macro = parts[0]
            rest = parts[1] if len(parts) > 1 else ""
            args = _split_macro_args(rest)

            if macro == "Sh":
                flush_flag()
                in_code = False
                section = clean_roff_inline(" ".join(args)).upper()
                continue
            if macro == "Nd":
                doc.summary = clean_roff_inline(" ".join(args))[:_MAX_SUMMARY]
                continue
            if macro == "Nm" and section == "NAME":
                if args:
                    name_tokens.extend(a.rstrip(",") for a in args if a.rstrip(","))
                continue
            if macro == "Bd":
                in_code = True
                continue
            if macro == "Ed":
                in_code = False
                continue
            if macro == "Dl":
                rendered = _render_mdoc_tokens(args, tool_name)
                if rendered:
                    example_lines.append((rendered, True))
                continue
            if macro == "It":
                flush_flag()
                rendered = _render_mdoc_tokens(args, tool_name)
                if rendered.startswith("-") and (
                    _is_option_section(section) or "DESCRIPTION" in section
                ):
                    active_flag_header = rendered
                continue
            if macro == "El":
                flush_flag()
                continue
            if macro == "Xr" and args:
                target = args[0].strip()
                if target and target != tool_name and target not in see_also_targets:
                    see_also_targets.append(target)
                continue
            if macro == "Pp":
                if cur_desc:
                    desc_paras.append(" ".join(cur_desc))
                    cur_desc = []
                continue
            if macro in ("Dd", "Dt", "Os", "Bl", "Ss", "An", "Lk", "Mt", "Rs", "Re"):
                continue

            rendered = _render_mdoc_tokens([macro, *args], tool_name)
        else:
            rendered = clean_roff_inline(raw_line)

        if not rendered:
            continue

        if active_flag_header is not None:
            if len(" ".join(active_flag_body)) < _MAX_DESC + 60:
                active_flag_body.append(rendered)
            continue

        if "SYNOPSIS" in section:
            doc.synopsis.append(rendered)
        elif "DESCRIPTION" in section and len(desc_paras) < 2:
            cur_desc.append(rendered)
        elif "EXAMPLE" in section:
            example_lines.append((rendered, in_code))

    flush_flag()
    if cur_desc:
        desc_paras.append(" ".join(cur_desc))

    doc.aliases = [n for n in name_tokens if n != tool_name]
    doc.description = " ".join(desc_paras[:2])[:500].strip()
    doc.synopsis = _clean_synopsis_lines(doc.synopsis)
    doc.flags = _dedupe_flags(doc.flags)
    doc.examples = _extract_examples_from_lines(example_lines, tool_name, "man_roff")
    doc.see_also = see_also_targets[:25]
    return doc


def _clean_synopsis_lines(raw_lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in raw_lines:
        cleaned = " ".join(line.split()).strip()
        if not cleaned or len(cleaned) > 220 or cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
        if len(out) >= 6:
            break
    return out


# ============================================================================
# Public Roff + Rendered Fallback Entry Points
# ============================================================================


def parse_roff_source(raw: str, tool_name: str) -> ParsedManDocument:
    """Auto-detect ``mdoc(7)`` vs ``man(7)`` and parse into a ``ParsedManDocument``."""
    head = raw[:4000]
    is_mdoc = bool(re.search(r"^\.(?:Dd|Dt|Sh)\b", head, re.MULTILINE)) and not bool(
        re.search(r"^\.TH\b", head, re.MULTILINE)
    )
    doc = _parse_mdoc7(raw, tool_name) if is_mdoc else _parse_man7(raw, tool_name)

    # Mark low confidence when a sizeable page yielded neither flags nor synopsis
    # (e.g. heavy custom `.de` macros or `tbl` tables) so caller may try clean rendered fallback.
    non_empty_lines = sum(1 for ln in raw.splitlines() if ln.strip() and not ln.startswith('.\\"'))
    if non_empty_lines > 40 and not doc.flags and not doc.synopsis:
        doc.low_confidence = True
    return doc


def _strip_rendered_artifacts(text: str) -> str:
    """Remove terminal overstriking, ANSI SGR sequences, and wrap-hyphenation."""
    # Backspace overstrikes (a\ba -> a, _\ba -> a)
    cleaned = re.sub(r".\x08", "", text)
    # ANSI escapes
    cleaned = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", cleaned)
    # Soft/Unicode hyphenation at line wraps (`appar‐\n   ent` -> `apparent`)
    cleaned = re.sub(r"[\u2010\u00ad]\n[ \t]+", "", cleaned)
    return cleaned


def _extract_rendered_flags(sections: dict[str, list[str]]) -> list[FlagSpec]:
    flags: list[FlagSpec] = []
    for key, sec_lines in sections.items():
        if not (_is_option_section(key) or "DESCRIPTION" in key):
            continue
        active_hdr: str | None = None
        active_desc: list[str] = []

        def flush_flag(sec_key: str = key) -> None:
            nonlocal active_hdr, active_desc
            if active_hdr:
                parsed = parse_flag_header(
                    active_hdr,
                    " ".join(active_desc),
                    section=sec_key,
                    source="man_text",
                )
                if parsed is not None:
                    flags.append(parsed)
            active_hdr = None
            active_desc = []

        for ln in sec_lines:
            if not ln.strip():
                flush_flag()
                continue
            m = _RENDERED_FLAG_LINE_RE.match(ln)
            if m:
                flush_flag()
                active_hdr = m.group(1).strip()
                if m.group(2):
                    active_desc.append(m.group(2).strip())
            elif active_hdr is not None and len(ln) - len(ln.lstrip()) >= 4:
                if len(" ".join(active_desc)) < _MAX_DESC:
                    active_desc.append(ln.strip())
            else:
                flush_flag()
        flush_flag()
    return _dedupe_flags(flags)


def _extract_rendered_description(sec_lines: list[str]) -> str:
    para: list[str] = []
    for ln in sec_lines:
        if not ln.strip():
            if para:
                break
            continue
        if _RENDERED_FLAG_LINE_RE.match(ln):
            break
        para.append(ln.strip())
    return " ".join(para)[:500]


def parse_rendered_man_text(raw_text: str, tool_name: str) -> ParsedManDocument:
    """Clean fallback parser for ``MANWIDTH=10000 man -P cat`` rendered output."""
    text = _strip_rendered_artifacts(raw_text)
    sections: dict[str, list[str]] = {}
    current: str | None = None

    for line in text.splitlines():
        if _RENDERED_SECTION_RE.match(line):
            current = " ".join(line.strip().split()).upper()
            sections.setdefault(current, [])
            continue
        if current is not None:
            sections[current].append(line.rstrip())

    doc = ParsedManDocument(name=tool_name, dialect="rendered")
    if "NAME" in sections:
        doc.summary, doc.aliases = _extract_name_summary(" ".join(sections["NAME"]), tool_name)

    syn_lines = [
        ln.strip()
        for key, sec_lines in sections.items()
        if "SYNOPSIS" in key
        for ln in sec_lines
        if ln.strip()
    ]
    doc.synopsis = _clean_synopsis_lines(syn_lines)

    if "DESCRIPTION" in sections:
        doc.description = _extract_rendered_description(sections["DESCRIPTION"])

    doc.flags = _extract_rendered_flags(sections)

    ex_lines = [
        (ln, len(ln) - len(ln.lstrip()) >= 8)
        for key, sec_lines in sections.items()
        if "EXAMPLE" in key
        for ln in sec_lines
    ]
    doc.examples = _extract_examples_from_lines(ex_lines, tool_name, "man_text")

    for key, sec_lines in sections.items():
        if "SEE ALSO" in key:
            doc.see_also = _extract_see_also(" ".join(sec_lines), tool_name)
            break

    return doc
