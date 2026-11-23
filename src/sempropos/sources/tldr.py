"""Offline-first tldr-pages extractor and archive synchronizer.

Discovers local tldr caches (`~/.cache/tldr`, `~/.local/share/tealdeer`,
`data_dir()/tldr`) or syncs the official MIT-licensed `tldr-pages.en.zip`
snapshot during `sem install` / `sem update`. Maps both root pages (`tar.md`)
and subcommand pages (`git-commit.md` -> `git` subcommand `commit`).
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import urllib.request
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sempropos import config
from sempropos.model.spec import ExampleSpec, SubcommandSpec
from sempropos.sources.discovery import DiscoveryContext
from sempropos.utils import atomic_write_bytes

TLDR_ARCHIVE_URL = "https://github.com/tldr-pages/tldr/releases/latest/download/tldr-pages.en.zip"
TLDR_ARCHIVE_FILENAME = "tldr-pages.en.zip"

_PLACEHOLDER_RE = re.compile(r"\{\{([^}]+)\}\}")


@dataclass(frozen=True)
class ParsedTldrPage:
    """Structured content of a single tldr markdown page."""

    name: str
    summary: str
    description: str
    examples: tuple[ExampleSpec, ...] = ()


@dataclass
class TldrFragment:
    """Aggregated tldr data for one canonical CLI tool (root + subcommand pages)."""

    name: str
    summary: str = ""
    description: str = ""
    examples: list[ExampleSpec] = field(default_factory=list)
    subcommands: list[SubcommandSpec] = field(default_factory=list)
    location: str = ""
    mtime: float | None = None
    content_hash: str = ""


def _platform_dirs() -> tuple[str, ...]:
    """Return tldr platform folders in ascending priority (later overrides earlier)."""
    sys_name = platform.system().lower()
    if sys_name == "darwin":
        return ("common", "osx")
    if sys_name == "windows":
        return ("common", "windows")
    return ("common", "linux")


def _clean_tldr_command(cmd: str) -> str:
    """Convert tldr ``{{path/to/file}}`` tokens into clean readable placeholders."""

    def repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        # Handle optional short syntax like `{{-r|--recursive}}`
        if "|" in inner and inner.startswith("-"):
            return inner.split("|", 1)[0].strip()
        return f"<{inner}>"

    return _PLACEHOLDER_RE.sub(repl, cmd.strip())


def parse_tldr_markdown(
    raw: str, default_name: str, *, subcommand: str | None = None
) -> ParsedTldrPage:
    """Parse a standard tldr-pages markdown file."""
    name = default_name
    desc_lines: list[str] = []
    examples: list[ExampleSpec] = []
    pending_desc = ""

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("# "):
            name = stripped[2:].strip() or default_name
            continue

        if stripped.startswith(">"):
            body = stripped[1:].strip()
            if body.lower().startswith(("more information:", "see also:")):
                continue
            if body:
                desc_lines.append(body.replace("`", ""))
            continue

        if stripped.startswith("- "):
            pending_desc = stripped[2:].strip().rstrip(":")
            continue

        if stripped.startswith("`") and stripped.endswith("`") and len(stripped) > 2:
            cmd = _clean_tldr_command(stripped[1:-1])
            if cmd:
                examples.append(
                    ExampleSpec(
                        command=cmd,
                        description=pending_desc,
                        source="tldr",
                        subcommand=subcommand,
                    )
                )
            pending_desc = ""

    summary = desc_lines[0] if desc_lines else ""
    description = " ".join(desc_lines)
    return ParsedTldrPage(
        name=name,
        summary=summary[:240],
        description=description[:500],
        examples=tuple(examples),
    )


def find_local_tldr_sources() -> list[Path]:
    """Locate existing local tldr directories or zip archives (highest priority first)."""
    candidates: list[Path] = []
    env_dir = os.environ.get("TLDR_CACHE_DIR")
    if env_dir:
        candidates.append(Path(env_dir).expanduser())

    sem_tldr = config.tldr_dir()
    candidates.append(sem_tldr / TLDR_ARCHIVE_FILENAME)
    candidates.append(sem_tldr)

    home = Path.home()
    candidates.extend(
        [
            home / ".cache" / "tldr",
            home / ".local" / "share" / "tealdeer" / "tldr-pages",
            home / ".cache" / "tealdeer" / "tldr-pages",
            home / ".tldr" / "tldr",
        ]
    )

    found: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not path.exists():
            continue
        if path.is_file() and path.suffix == ".zip":
            seen.add(resolved)
            found.append(path)
        elif path.is_dir() and ((path / "pages" / "common").is_dir() or (path / "common").is_dir()):
            # Accept either `<root>/pages/common` or `<root>/common`.
            seen.add(resolved)
            found.append(path)
    return found


def sync_tldr_archive(dest_dir: Path | None = None, *, timeout: float = 20.0) -> Path:
    """Download the official English ``tldr-pages.en.zip`` snapshot for offline use."""
    target_dir = dest_dir or config.tldr_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive_path = target_dir / TLDR_ARCHIVE_FILENAME

    req = urllib.request.Request(
        TLDR_ARCHIVE_URL,
        headers={"User-Agent": "sempropos/1.0 (+https://github.com/ADPer0705/sempropos)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        payload = response.read()

    # Validate zip integrity before replacing on disk.
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        if not any(name.endswith(".md") for name in zf.namelist()):
            raise RuntimeError("Downloaded tldr archive contained no markdown pages.")

    atomic_write_bytes(archive_path, payload)
    return archive_path


def _iter_pages_from_source(source_path: Path) -> Iterator[tuple[str, str, str, float | None]]:
    """Yield ``(page_stem, raw_markdown, location, mtime)`` in platform priority order."""
    plat_order = _platform_dirs()

    if source_path.is_file() and source_path.suffix == ".zip":
        try:
            mtime = source_path.stat().st_mtime
            with zipfile.ZipFile(source_path, "r") as zf:
                names_by_plat: dict[str, list[str]] = {p: [] for p in plat_order}
                for entry in zf.namelist():
                    if not entry.endswith(".md"):
                        continue
                    parts = Path(entry).parts
                    # Matches `common/tar.md` or `pages/common/tar.md`
                    if len(parts) >= 2 and parts[-2] in names_by_plat:
                        names_by_plat[parts[-2]].append(entry)
                for plat in plat_order:
                    for entry in sorted(names_by_plat[plat]):
                        stem = Path(entry).stem
                        try:
                            raw = zf.read(entry).decode("utf-8", errors="replace")
                        except (OSError, KeyError):
                            continue
                        yield stem, raw, f"{source_path}!{entry}", mtime
        except (OSError, zipfile.BadZipFile):
            return
        return

    base = source_path / "pages" if (source_path / "pages" / "common").is_dir() else source_path
    for plat in plat_order:
        plat_dir = base / plat
        if not plat_dir.is_dir():
            continue
        for md_file in sorted(plat_dir.glob("*.md")):
            try:
                mtime = md_file.stat().st_mtime
                raw = md_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            yield md_file.stem, raw, str(md_file), mtime


def extract_tldr_fragments(
    context: DiscoveryContext,
    *,
    source_paths: list[Path] | None = None,
) -> dict[str, TldrFragment]:
    """Extract tldr root + subcommand documentation for all installed ``$PATH`` tools."""
    sources = source_paths if source_paths is not None else find_local_tldr_sources()
    if not sources:
        return {}

    # Use the highest-priority valid local source to avoid duplicate reading.
    active_source = sources[0]
    installed = context.all_binary_names

    # Collect raw pages (platform-specific pages overwrite `common` for the same stem).
    raw_pages: dict[str, tuple[str, str, float | None]] = {}
    for stem, raw, loc, mtime in _iter_pages_from_source(active_source):
        raw_pages[stem] = (raw, loc, mtime)

    fragments: dict[str, TldrFragment] = {}

    for stem, (raw, loc, mtime) in raw_pages.items():
        # Case 1: Direct match for an installed binary (`tar`, `7z`, `uv`, `git`, `du`, `ss`).
        if stem in installed:
            canonical = context.canonical_name(stem)
            parsed = parse_tldr_markdown(raw, canonical)
            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
            frag = fragments.setdefault(
                canonical,
                TldrFragment(name=canonical, location=loc, mtime=mtime, content_hash=digest),
            )
            if not frag.summary and parsed.summary:
                frag.summary = parsed.summary
            if not frag.description and parsed.description:
                frag.description = parsed.description
            if parsed.examples:
                existing_cmds = {ex.command for ex in frag.examples}
                for ex in parsed.examples:
                    if ex.command not in existing_cmds:
                        frag.examples.append(ex)
                        existing_cmds.add(ex.command)
            continue

        # Case 2: Hyphenated subcommand page (`git-commit`, `docker-run`, `systemctl-status`).
        if "-" in stem:
            parent_raw, sub_name = stem.split("-", 1)
            if parent_raw in installed and sub_name:
                canonical_parent = context.canonical_name(parent_raw)
                parsed_sub = parse_tldr_markdown(raw, stem, subcommand=sub_name)
                digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
                frag = fragments.setdefault(
                    canonical_parent,
                    TldrFragment(
                        name=canonical_parent,
                        location=loc,
                        mtime=mtime,
                        content_hash=digest,
                    ),
                )
                frag.subcommands.append(
                    SubcommandSpec(
                        name=sub_name,
                        summary=parsed_sub.summary,
                        examples=parsed_sub.examples,
                        source="tldr",
                    )
                )

    return fragments
