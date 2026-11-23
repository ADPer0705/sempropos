"""Deterministic field-priority merger assembling canonical ``ToolSpec`` objects.

Combines partial tool fragments from man(7)/mdoc(7), tldr-pages, shell
completions, and safe ``--help`` probing with clear provenance and taxonomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sempropos.model.spec import (
    ExampleSpec,
    FlagSpec,
    SourceKind,
    SourceProvenance,
    SubcommandSpec,
    ToolSpec,
)
from sempropos.sources.completions import CompletionFragment
from sempropos.sources.discovery import DiscoveryContext
from sempropos.sources.help_probe import HelpProbeFragment
from sempropos.sources.roff_parser import ParsedManDocument
from sempropos.sources.tldr import TldrFragment

_SOURCE_PRIORITY: dict[SourceKind, int] = {
    "man_roff": 0,
    "completion_fish": 1,
    "help_probe": 2,
    "completion_zsh": 3,
    "man_text": 4,
    "tldr": 5,
}

# Lightweight deterministic taxonomy rules (keywords -> (category, extra tags)).
_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "archive",
        ("archive", "compress", "decompress", "zip", "unzip", "tar", "7z", "gzip", "bzip2", "xz", "zstd"),
        ("archive", "compress", "extract"),
    ),
    (
        "network",
        ("socket", "tcp", "udp", "http", "url", "ssh", "dns", "packet", "firewall", "network", "port", "curl", "wget", "ping", "ip"),
        ("network", "socket", "port"),
    ),
    (
        "filesystem",
        ("directory", "file space", "disk usage", "filesystem", "mount", "copy files", "remove files", "search for files", "permissions", "mode bits", "inode", "symlink"),
        ("files", "directory", "disk"),
    ),
    (
        "vcs",
        ("git", "version control", "content tracker", "repository", "commit", "mercurial", "subversion", "jujutsu"),
        ("git", "vcs", "repo"),
    ),
    (
        "container",
        ("container", "docker", "podman", "kubernetes", "kubectl", "pod", "oci", "helm"),
        ("container", "image"),
    ),
    (
        "process",
        ("process", "signal", "pid", "memory usage", "cpu", "scheduler", "kill", "systemd", "service", "daemon", "journal"),
        ("process", "service", "system"),
    ),
    (
        "text",
        ("pattern", "regex", "stream editor", "json", "yaml", "xml", "csv", "grep", "ripgrep", "awk", "sed", "sort lines", "filter"),
        ("text", "search", "parse"),
    ),
    (
        "package",
        ("package manager", "python package", "dependency", "install packages", "pip", "cargo", "npm", "apt", "dnf", "pacman", "brew", "uv"),
        ("package", "install", "env"),
    ),
    (
        "media",
        ("video", "audio", "image", "ffmpeg", "convert", "transcode", "pdf", "png", "jpeg"),
        ("media", "convert"),
    ),
    (
        "security",
        ("encrypt", "decrypt", "cipher", "gpg", "ssl", "tls", "certificate", "hash", "checksum", "sha256", "key"),
        ("crypto", "security"),
    ),
)


@dataclass
class ManBundle:
    """Man-page fragment with provenance metadata."""

    doc: ParsedManDocument
    section: int
    source: SourceKind
    location: str
    mtime: float | None = None
    content_hash: str = ""


@dataclass
class ToolSourceBundle:
    """All extracted fragments belonging to one canonical tool."""

    name: str
    binary_path: str | None = None
    aliases: set[str] = field(default_factory=set)
    man: ManBundle | None = None
    tldr: TldrFragment | None = None
    completion: CompletionFragment | None = None
    help_probe: HelpProbeFragment | None = None


def _classify_tool(
    name: str,
    summary: str,
    description: str,
    subcommands: tuple[SubcommandSpec, ...],
) -> tuple[str | None, tuple[str, ...]]:
    haystack = " ".join(
        [name, summary, description[:200], " ".join(s.name for s in subcommands[:15])]
    ).lower()
    matched_cat: str | None = None
    tags: list[str] = []

    for category, keywords, extra_tags in _CATEGORY_RULES:
        if any(kw in haystack for kw in keywords):
            if matched_cat is None:
                matched_cat = category
            for tag in extra_tags:
                if tag not in tags:
                    tags.append(tag)

    return matched_cat, tuple(tags[:8])


def _merge_flags(sources_flags: list[list[FlagSpec]]) -> tuple[FlagSpec, ...]:
    """Merge flags by canonical short/long key, keeping highest-priority metadata."""
    ordered: list[FlagSpec] = []
    by_short: dict[str, int] = {}
    by_long: dict[str, int] = {}

    all_flags: list[FlagSpec] = []
    for batch in sources_flags:
        all_flags.extend(batch)

    all_flags.sort(key=lambda f: _SOURCE_PRIORITY.get(f.source, 99))

    for flag in all_flags:
        idx: int | None = None
        if flag.short and flag.short in by_short:
            idx = by_short[flag.short]
        elif flag.long and flag.long in by_long:
            idx = by_long[flag.long]

        if idx is not None:
            existing = ordered[idx]
            # Enrich missing alias, value hint, or empty description from secondary source.
            short = existing.short or flag.short
            long = existing.long or flag.long
            takes_value = existing.takes_value or flag.takes_value
            value_hint = existing.value_hint or flag.value_hint
            desc = existing.description if len(existing.description) >= len(flag.description) else flag.description
            updated = FlagSpec(
                short=short,
                long=long,
                takes_value=takes_value,
                value_hint=value_hint,
                repeatable=existing.repeatable or flag.repeatable,
                description=desc,
                section=existing.section or flag.section,
                source=existing.source,
            )
            ordered[idx] = updated
            if short:
                by_short[short] = idx
            if long:
                by_long[long] = idx
        else:
            new_idx = len(ordered)
            ordered.append(flag)
            if flag.short:
                by_short[flag.short] = new_idx
            if flag.long:
                by_long[flag.long] = new_idx

    return tuple(ordered)


def _merge_subcommands(batches: list[list[SubcommandSpec]]) -> tuple[SubcommandSpec, ...]:
    by_name: dict[str, SubcommandSpec] = {}

    for batch in batches:
        for sub in batch:
            key = sub.name.strip()
            if not key:
                continue
            if key not in by_name:
                by_name[key] = sub
                continue
            existing = by_name[key]
            summary = existing.summary or sub.summary
            if sub.source == "tldr" and sub.summary:
                summary = sub.summary
            synopsis = tuple(dict.fromkeys([*existing.synopsis, *sub.synopsis]))
            flags = _merge_flags([list(existing.flags), list(sub.flags)])
            examples = _merge_examples([list(existing.examples), list(sub.examples)])
            by_name[key] = SubcommandSpec(
                name=key,
                summary=summary,
                synopsis=synopsis,
                flags=flags,
                examples=examples,
                source=existing.source,
            )

    return tuple(sorted(by_name.values(), key=lambda s: s.name))


def _merge_examples(batches: list[list[ExampleSpec]]) -> tuple[ExampleSpec, ...]:
    """Order examples with tldr first, then man_roff, deduplicated by command."""
    all_examples: list[ExampleSpec] = []
    for batch in batches:
        all_examples.extend(batch)

    order = {"tldr": 0, "man_roff": 1, "man_text": 2, "help_probe": 3}
    all_examples.sort(key=lambda e: order.get(e.source, 9))

    deduped: list[ExampleSpec] = []
    seen: set[str] = set()
    for ex in all_examples:
        norm = " ".join(ex.command.split())
        if not norm or norm in seen:
            continue
        seen.add(norm)
        deduped.append(ex)
    return tuple(deduped[:30])


def _collect_provenances(bundle: ToolSourceBundle) -> tuple[SourceProvenance, ...]:
    provenances: list[SourceProvenance] = []
    if bundle.man:
        provenances.append(
            SourceProvenance(
                source=bundle.man.source,
                location=bundle.man.location,
                mtime=bundle.man.mtime,
                content_hash=bundle.man.content_hash,
            )
        )
    if bundle.tldr:
        provenances.append(
            SourceProvenance(
                source="tldr",
                location=bundle.tldr.location,
                mtime=bundle.tldr.mtime,
                content_hash=bundle.tldr.content_hash,
            )
        )
    if bundle.completion:
        provenances.append(
            SourceProvenance(
                source=bundle.completion.source,
                location=bundle.completion.location,
                mtime=bundle.completion.mtime,
                content_hash=bundle.completion.content_hash,
            )
        )
    if bundle.help_probe:
        provenances.append(
            SourceProvenance(
                source="help_probe",
                location=bundle.help_probe.binary_path,
                mtime=bundle.help_probe.mtime,
                content_hash=bundle.help_probe.content_hash,
            )
        )
    return tuple(provenances)


def _select_summary_and_desc(bundle: ToolSourceBundle) -> tuple[str, str]:
    man, tldr, probe = bundle.man, bundle.tldr, bundle.help_probe
    summary = (
        (tldr.summary if tldr and tldr.summary else "")
        or (man.doc.summary if man and man.doc.summary else "")
        or (probe.summary if probe and probe.summary else "")
        or (man.doc.description.split(".", 1)[0][:200] if man and man.doc.description else "")
        or f"Command-line utility {bundle.name}"
    )
    description = (
        (man.doc.description if man and man.doc.description else "")
        or (tldr.description if tldr and tldr.description else "")
        or (probe.description if probe and probe.description else "")
    )
    return summary.strip(), description.strip()


def merge_tool_bundle(bundle: ToolSourceBundle) -> ToolSpec | None:
    """Assemble one canonical ``ToolSpec`` from its multi-source fragments."""
    provenances = _collect_provenances(bundle)
    if not provenances:
        return None

    man, tldr, comp, probe = bundle.man, bundle.tldr, bundle.completion, bundle.help_probe
    summary, description = _select_summary_and_desc(bundle)

    synopsis_items = [*(man.doc.synopsis if man else ()), *(probe.synopsis if probe else ())]
    synopsis = tuple(dict.fromkeys(s for s in synopsis_items if s))[:6]

    aliases = set(bundle.aliases) | (set(man.doc.aliases) if man else set())
    aliases.discard(bundle.name)

    flags = _merge_flags(
        [
            man.doc.flags if man else [],
            comp.flags if comp else [],
            probe.flags if probe else [],
        ]
    )
    subcommands = _merge_subcommands(
        [
            tldr.subcommands if tldr else [],
            probe.subcommands if probe else [],
            comp.subcommands if comp else [],
            man.doc.subcommands if man else [],
        ]
    )
    examples = _merge_examples(
        [
            tldr.examples if tldr else [],
            man.doc.examples if man else [],
        ]
    )
    see_also = tuple(man.doc.see_also) if man else ()
    category, tags = _classify_tool(bundle.name, summary, description, subcommands)

    return ToolSpec(
        name=bundle.name,
        binary_path=bundle.binary_path,
        section=man.section if man else None,
        aliases=tuple(sorted(aliases)),
        summary=summary,
        description=description,
        category=category,
        tags=tags,
        synopsis=synopsis,
        subcommands=subcommands,
        flags=flags,
        examples=examples,
        see_also=see_also,
        sources=provenances,
    )


def attach_symlink_aliases(
    bundles: dict[str, ToolSourceBundle],
    context: DiscoveryContext,
) -> None:
    """Attach discovered symlink aliases (e.g. ``docker -> podman``) onto canonical bundles."""
    for alias, target in context.symlink_aliases.items():
        if target in bundles:
            bundles[target].aliases.add(alias)
