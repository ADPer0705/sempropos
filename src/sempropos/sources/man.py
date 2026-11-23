"""Direct filesystem man(1)/man(8) extractor with clean rendered fallback.

Walks ``manpath`` directories (``man1``, ``man8``) reading compressed roff files
directly via :mod:`sempropos.sources.roff_parser` — eliminating thousands of
``man`` subprocess spawns and avoiding 80-column wrapping/hyphenation corruption.
Falls back to ``MANWIDTH=10000 GROFF_NO_SGR=1 man -P cat`` only when a page
uses complex custom roff macros that yield low structured confidence.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from sempropos.sources.discovery import DiscoveryContext
from sempropos.sources.merger import ManBundle
from sempropos.sources.roff_parser import (
    ParsedManDocument,
    parse_rendered_man_text,
    parse_roff_source,
    read_man_file,
)

_COMPRESSION_SUFFIXES = (".gz", ".xz", ".lzma", ".bz2", ".zst")
_DEFAULT_MANPATHS = (
    "/usr/local/share/man",
    "/usr/share/man",
    "/usr/local/man",
    "/opt/homebrew/share/man",
)
_MAN_WORKERS = min(os.cpu_count() or 4, 8)
_MAN_INFLIGHT = _MAN_WORKERS * 2

# Strips section suffix like `find.1.gz` -> (`find`, 1) or `ss.8ssl.gz` -> (`ss`, 8)
_MAN_FILENAME_RE = re.compile(r"^(?P<name>.+?)\.(?P<sec>[18])[a-zA-Z0-9_-]*$")


def discover_manpath_dirs() -> list[Path]:
    """Return existing man root directories in search priority order."""
    raw = os.environ.get("MANPATH", "")
    if not raw:
        try:
            proc = subprocess.run(
                ["manpath"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                raw = proc.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raw = ""

    candidates: list[str] = [p for p in raw.split(os.pathsep) if p] if raw else []
    for default in _DEFAULT_MANPATHS:
        if default not in candidates:
            candidates.append(default)

    user_man = str(Path.home() / ".local" / "share" / "man")
    if user_man not in candidates:
        candidates.insert(0, user_man)

    dirs: list[Path] = []
    seen: set[Path] = set()
    for entry in candidates:
        path = Path(entry)
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved not in seen and path.is_dir():
            seen.add(resolved)
            dirs.append(path)
    return dirs


def _parse_man_filename(filename: str) -> tuple[str, int] | None:
    stem = filename
    for ext in _COMPRESSION_SUFFIXES:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    match = _MAN_FILENAME_RE.match(stem)
    if not match:
        return None
    return match.group("name"), int(match.group("sec"))


def discover_man_files(
    context: DiscoveryContext,
    *,
    man_roots: list[Path] | None = None,
    sections: tuple[int, ...] = (1, 8),
) -> dict[str, tuple[Path, int]]:
    """Map canonical installed tool names to their highest-priority man file on disk.

    An exact filename match (e.g. ``git.1.gz``) always takes precedence over a
    symlink-alias filename (e.g. ``git-receive-pack.1.gz``) regardless of
    filesystem directory iteration order.
    """
    roots = man_roots if man_roots is not None else discover_manpath_dirs()
    installed = context.all_binary_names
    found: dict[str, tuple[Path, int, bool]] = {}

    for root in roots:
        for sec in sections:
            sec_dir = root / f"man{sec}"
            if not sec_dir.is_dir():
                continue
            try:
                with os.scandir(sec_dir) as it:
                    for dirent in it:
                        parsed = _parse_man_filename(dirent.name)
                        if parsed is None:
                            continue
                        raw_name, parsed_sec = parsed
                        if raw_name not in installed:
                            continue
                        canonical = context.canonical_name(raw_name)
                        is_exact = raw_name == canonical
                        existing = found.get(canonical)
                        if existing is None or (is_exact and not existing[2]):
                            found[canonical] = (Path(dirent.path), parsed_sec, is_exact)
            except OSError:
                continue

    return {name: (path, sec) for name, (path, sec, _exact) in found.items()}


def _render_man_fallback(name: str, section: int) -> str | None:
    """Render a man page cleanly with wide columns and no SGR sequences."""
    env = dict(os.environ)
    env.update(
        {
            "MANWIDTH": "10000",
            "GROFF_NO_SGR": "1",
            "MAN_KEEP_FORMATTING": "0",
            "PAGER": "cat",
            "LC_ALL": "C.UTF-8",
        }
    )
    try:
        proc = subprocess.run(
            ["man", "-P", "cat", str(section), name],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    return proc.stdout


def extract_single_man_page(
    name: str,
    path: Path,
    section: int,
    *,
    allow_rendered_fallback: bool = True,
) -> ManBundle | None:
    """Parse one man file directly with roff parser, falling back to clean render if needed."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    raw_roff = read_man_file(path)
    if raw_roff is None:
        return None

    digest = hashlib.sha256(raw_roff.encode("utf-8", errors="ignore")).hexdigest()[:16]
    doc: ParsedManDocument = parse_roff_source(raw_roff, name)
    source_kind: str = "man_roff"

    if doc.low_confidence and allow_rendered_fallback:
        rendered = _render_man_fallback(name, section)
        if rendered:
            fallback_doc = parse_rendered_man_text(rendered, name)
            if len(fallback_doc.flags) > len(doc.flags) or (
                not doc.synopsis and fallback_doc.synopsis
            ):
                doc = fallback_doc
                source_kind = "man_text"

    return ManBundle(
        doc=doc,
        section=section,
        source="man_text" if source_kind == "man_text" else "man_roff",
        location=str(path),
        mtime=mtime,
        content_hash=digest,
    )


def extract_man_bundles(
    context: DiscoveryContext,
    *,
    man_roots: list[Path] | None = None,
    allow_rendered_fallback: bool = True,
) -> dict[str, ManBundle]:
    """Concurrently extract man-page bundles for all installed tools with bounded memory."""
    file_map = discover_man_files(context, man_roots=man_roots)
    if not file_map:
        return {}

    results: dict[str, ManBundle] = {}
    items = iter(file_map.items())
    in_flight: dict[Future[ManBundle | None], str] = {}

    with ThreadPoolExecutor(max_workers=_MAN_WORKERS) as pool:

        def submit_next() -> bool:
            try:
                tool_name, (path, sec) = next(items)
            except StopIteration:
                return False
            in_flight[
                pool.submit(
                    extract_single_man_page,
                    tool_name,
                    path,
                    sec,
                    allow_rendered_fallback=allow_rendered_fallback,
                )
            ] = tool_name
            return True

        for _ in range(_MAN_INFLIGHT):
            if not submit_next():
                break

        while in_flight:
            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                tool_name = in_flight.pop(future)
                try:
                    bundle = future.result()
                    if bundle is not None:
                        results[tool_name] = bundle
                except Exception:  # noqa: BLE001
                    pass
                submit_next()

    return results
