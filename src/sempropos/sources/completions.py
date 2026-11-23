"""Static parser for Fish and Zsh shell completion definitions.

Never executes shell code — parses declarative ``complete`` (Fish) and
``_arguments`` (Zsh) rules directly to enrich tools with clean machine-readable
flags and subcommands.
"""

from __future__ import annotations

import hashlib
import re
import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from sempropos.model.spec import FlagSpec, SourceKind, SubcommandSpec
from sempropos.sources.discovery import DiscoveryContext

_FISH_DIRS = (
    Path("/usr/share/fish/completions"),
    Path("/usr/local/share/fish/completions"),
    Path.home() / ".config" / "fish" / "completions",
)

_ZSH_DIRS = (
    Path("/usr/share/zsh/site-functions"),
    Path("/usr/local/share/zsh/site-functions"),
    Path("/usr/share/zsh/functions/Completion"),
)

_FISH_SUBCMD_COND_RE = re.compile(r"__fish_seen_subcommand_from\s+([A-Za-z0-9_.-]+)")
_ZSH_ARG_SPEC_RE = re.compile(
    r"""['"](?:\*|\([^)]*\))?(?P<flag>--?[A-Za-z0-9][A-Za-z0-9._-]*)(?P<eq>=|[-+])?"""
    r"""\[(?P<desc>[^\]]{1,240})\](?::(?P<hint>[^:'"]+))?"""
)


@dataclass
class CompletionFragment:
    """Structured flags & subcommands extracted from shell completions."""

    name: str
    source: SourceKind
    location: str
    mtime: float | None = None
    content_hash: str = ""
    flags: list[FlagSpec] = field(default_factory=list)
    subcommands: list[SubcommandSpec] = field(default_factory=list)


def _parse_fish_line_tokens(
    tokens: list[str],
) -> tuple[str | None, str | None, str, bool, str | None, str]:
    short_flag: str | None = None
    long_flag: str | None = None
    desc = ""
    takes_value = False
    args_value: str | None = None
    condition = ""

    idx = 1
    while idx < len(tokens):
        tok = tokens[idx]
        has_next = idx + 1 < len(tokens)
        if tok in ("-s", "--short-option") and has_next:
            val = tokens[idx + 1].lstrip("-")
            short_flag = f"-{val}" if val else short_flag
            idx += 2
        elif tok in ("-l", "--long-option") and has_next:
            val = tokens[idx + 1].lstrip("-")
            long_flag = f"--{val}" if val else long_flag
            idx += 2
        elif tok in ("-o", "--old-option") and has_next:
            val = tokens[idx + 1].lstrip("-")
            if val and not long_flag:
                short_flag = f"-{val}"
            idx += 2
        elif tok in ("-d", "--description") and has_next:
            desc = tokens[idx + 1].strip()
            idx += 2
        elif tok in ("-a", "--arguments") and has_next:
            args_value = tokens[idx + 1].strip()
            idx += 2
        elif tok in ("-n", "--condition") and has_next:
            condition = tokens[idx + 1]
            idx += 2
        elif tok in ("-r", "--require-parameter", "-x", "--exclusive"):
            takes_value = True
            idx += 1
        elif tok in ("-c", "--command") and has_next:
            idx += 2
        else:
            idx += 1

    return short_flag, long_flag, desc, takes_value, args_value, condition


def parse_fish_completion(raw: str, tool_name: str, location: str = "") -> CompletionFragment:
    """Statically parse a Fish ``complete`` completion file."""
    frag = CompletionFragment(
        name=tool_name,
        source="completion_fish",
        location=location,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
    )
    seen_flags: set[tuple[str | None, str | None]] = set()
    seen_subs: set[str] = set()

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or not stripped.startswith("complete "):
            continue
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            continue

        short_flag, long_flag, desc, takes_value, args_value, condition = _parse_fish_line_tokens(
            tokens
        )

        if not short_flag and not long_flag and args_value:
            if (
                "__fish_use_subcommand" in condition
                or "not __fish_seen_subcommand_from" in condition
            ):
                for sub_tok in args_value.split():
                    if re.match(r"^[a-z][a-z0-9_-]{0,28}$", sub_tok) and sub_tok not in seen_subs:
                        seen_subs.add(sub_tok)
                        frag.subcommands.append(
                            SubcommandSpec(
                                name=sub_tok, summary=desc[:200], source="completion_fish"
                            )
                        )
            continue

        if _FISH_SUBCMD_COND_RE.search(condition):
            continue

        key = (short_flag, long_flag)
        if (short_flag or long_flag) and key not in seen_flags:
            seen_flags.add(key)
            frag.flags.append(
                FlagSpec(
                    short=short_flag,
                    long=long_flag,
                    takes_value=takes_value,
                    description=desc[:300],
                    source="completion_fish",
                )
            )

    return frag


def parse_zsh_completion(raw: str, tool_name: str, location: str = "") -> CompletionFragment:
    """Statically parse common ``_arguments`` flag entries from a Zsh ``_tool`` file."""
    frag = CompletionFragment(
        name=tool_name,
        source="completion_zsh",
        location=location,
        content_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16],
    )
    seen_flags: set[tuple[str | None, str | None]] = set()

    for match in _ZSH_ARG_SPEC_RE.finditer(raw):
        flag_tok = match.group("flag")
        eq = match.group("eq")
        desc = " ".join(match.group("desc").split()).strip()
        hint = (match.group("hint") or "").strip()

        short_flag = flag_tok if not flag_tok.startswith("--") else None
        long_flag = flag_tok if flag_tok.startswith("--") else None
        takes_value = bool(eq == "=" or hint)
        value_hint = hint.split()[0][:24] if hint and hint != " " else None

        key = (short_flag, long_flag)
        if key in seen_flags or not desc:
            continue
        seen_flags.add(key)
        frag.flags.append(
            FlagSpec(
                short=short_flag,
                long=long_flag,
                takes_value=takes_value,
                value_hint=value_hint,
                description=desc[:300],
                source="completion_zsh",
            )
        )
        if len(frag.flags) >= 200:
            break

    return frag


def _scan_completion_dir(
    dirs: tuple[Path, ...],
    pattern: str,
    *,
    is_recursive: bool,
    strip_leading_underscore: bool,
    context: DiscoveryContext,
    results: dict[str, CompletionFragment],
    parser_fn: Callable[[str, str, str], CompletionFragment],
) -> None:
    installed = context.all_binary_names
    for directory in dirs:
        if not directory.is_dir():
            continue
        paths = sorted(directory.rglob(pattern) if is_recursive else directory.glob(pattern))
        for path in paths:
            if not path.is_file():
                continue
            name = path.name[1:] if strip_leading_underscore else path.stem
            if name not in installed:
                continue
            canonical = context.canonical_name(name)
            if canonical in results:
                continue
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            frag = parser_fn(raw, canonical, str(path))
            frag.mtime = mtime
            if frag.flags or frag.subcommands:
                results[canonical] = frag


def extract_completion_fragments(
    context: DiscoveryContext,
    *,
    fish_dirs: tuple[Path, ...] = _FISH_DIRS,
    zsh_dirs: tuple[Path, ...] = _ZSH_DIRS,
) -> dict[str, CompletionFragment]:
    """Extract completion specs for installed ``$PATH`` tools (Fish preferred over Zsh)."""
    results: dict[str, CompletionFragment] = {}
    _scan_completion_dir(
        fish_dirs,
        "*.fish",
        is_recursive=False,
        strip_leading_underscore=False,
        context=context,
        results=results,
        parser_fn=parse_fish_completion,
    )
    _scan_completion_dir(
        zsh_dirs,
        "_*",
        is_recursive=True,
        strip_leading_underscore=True,
        context=context,
        results=results,
        parser_fn=parse_zsh_completion,
    )
    return results
