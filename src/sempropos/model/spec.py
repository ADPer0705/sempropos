"""Canonical intermediate representation (IR) for CLI tools."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

SCHEMA_VERSION = 3

SourceKind = Literal[
    "man_roff",
    "man_text",
    "tldr",
    "completion_fish",
    "completion_zsh",
    "help_probe",
]

SafetyLevel = Literal["safe", "caution", "destructive"]


@dataclass(frozen=True)
class FlagSpec:
    """Structured specification of a single command-line option or predicate."""

    short: str | None = None
    long: str | None = None
    takes_value: bool = False
    value_hint: str | None = None
    repeatable: bool = False
    description: str = ""
    section: str | None = None
    source: SourceKind = "man_roff"

    @property
    def primary(self) -> str:
        """Primary canonical flag token (short if available, else long)."""
        return self.short or self.long or "*"

    @property
    def display(self) -> str:
        """Human-readable display form such as ``-d, --max-depth=N``."""
        forms = [form for form in (self.short, self.long) if form]
        head = ", ".join(forms) if forms else "*"
        if self.takes_value:
            hint = self.value_hint or "VALUE"
            if self.long and not self.short:
                return f"{head}={hint}"
            return f"{head} <{hint}>"
        return head

    @property
    def canonical_key(self) -> tuple[str | None, str | None]:
        """Stable deduplication key."""
        return (self.short, self.long)

    def to_dict(self) -> dict[str, Any]:
        return {
            "short": self.short,
            "long": self.long,
            "display": self.display,
            "takes_value": self.takes_value,
            "value_hint": self.value_hint,
            "repeatable": self.repeatable,
            "description": self.description,
            "section": self.section,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FlagSpec:
        return cls(
            short=data.get("short"),
            long=data.get("long"),
            takes_value=bool(data.get("takes_value", False)),
            value_hint=data.get("value_hint"),
            repeatable=bool(data.get("repeatable", False)),
            description=str(data.get("description") or ""),
            section=data.get("section"),
            source=data.get("source", "man_roff"),
        )


@dataclass(frozen=True)
class PositionalSpec:
    """Structured specification of a positional command argument."""

    name: str
    required: bool = False
    variadic: bool = False
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "required": self.required,
            "variadic": self.variadic,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PositionalSpec:
        return cls(
            name=str(data["name"]),
            required=bool(data.get("required", False)),
            variadic=bool(data.get("variadic", False)),
            description=str(data.get("description") or ""),
        )


@dataclass(frozen=True)
class ExampleSpec:
    """Verified command usage example."""

    command: str
    description: str = ""
    source: SourceKind = "man_roff"
    subcommand: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "description": self.description,
            "source": self.source,
            "subcommand": self.subcommand,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExampleSpec:
        return cls(
            command=str(data["command"]),
            description=str(data.get("description") or ""),
            source=data.get("source", "man_roff"),
            subcommand=data.get("subcommand"),
        )


@dataclass(frozen=True)
class SubcommandSpec:
    """Structured specification of a CLI subcommand (e.g. ``git commit``)."""

    name: str
    summary: str = ""
    synopsis: tuple[str, ...] = ()
    flags: tuple[FlagSpec, ...] = ()
    examples: tuple[ExampleSpec, ...] = ()
    source: SourceKind = "help_probe"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "summary": self.summary,
            "synopsis": list(self.synopsis),
            "flags": [flag.to_dict() for flag in self.flags],
            "examples": [example.to_dict() for example in self.examples],
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SubcommandSpec:
        return cls(
            name=str(data["name"]),
            summary=str(data.get("summary") or ""),
            synopsis=tuple(str(item) for item in data.get("synopsis", ())),
            flags=tuple(FlagSpec.from_dict(item) for item in data.get("flags", ())),
            examples=tuple(ExampleSpec.from_dict(item) for item in data.get("examples", ())),
            source=data.get("source", "help_probe"),
        )


@dataclass(frozen=True)
class SourceProvenance:
    """Tracks where a ToolSpec's fields originated for incremental hashing & auditing."""

    source: SourceKind
    location: str
    mtime: float | None = None
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "location": self.location,
            "mtime": self.mtime,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceProvenance:
        mtime_raw = data.get("mtime")
        return cls(
            source=data["source"],
            location=str(data["location"]),
            mtime=float(mtime_raw) if mtime_raw is not None else None,
            content_hash=str(data.get("content_hash") or ""),
        )


@dataclass(frozen=True)
class ToolSpec:
    """Canonical, structured representation of an installed CLI tool."""

    name: str
    summary: str
    binary_path: str | None = None
    section: int | None = None
    aliases: tuple[str, ...] = ()
    description: str = ""
    category: str | None = None
    tags: tuple[str, ...] = ()
    synopsis: tuple[str, ...] = ()
    subcommands: tuple[SubcommandSpec, ...] = ()
    flags: tuple[FlagSpec, ...] = ()
    positionals: tuple[PositionalSpec, ...] = ()
    examples: tuple[ExampleSpec, ...] = ()
    see_also: tuple[str, ...] = ()
    sources: tuple[SourceProvenance, ...] = ()
    schema_version: int = SCHEMA_VERSION

    def content_hash(self) -> str:
        """Compute a stable SHA-256 fingerprint over semantic tool content.

        Excludes transient filesystem timestamps (``mtime``) so incremental
        re-indexing skips unchanged tools cleanly.
        """
        payload = {
            "schema_version": self.schema_version,
            "name": self.name,
            "binary_path": self.binary_path,
            "section": self.section,
            "aliases": list(self.aliases),
            "summary": self.summary,
            "description": self.description,
            "category": self.category,
            "tags": list(self.tags),
            "synopsis": list(self.synopsis),
            "subcommands": [sub.to_dict() for sub in self.subcommands],
            "flags": [flag.to_dict() for flag in self.flags],
            "positionals": [pos.to_dict() for pos in self.positionals],
            "examples": [ex.to_dict() for ex in self.examples],
            "see_also": list(self.see_also),
        }
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def aliases_text(self) -> str:
        return " ".join(self.aliases)

    def tags_text(self) -> str:
        items: list[str] = []
        if self.category:
            items.append(self.category)
        items.extend(self.tags)
        return " ".join(dict.fromkeys(items))

    def subcommands_text(self) -> str:
        parts: list[str] = []
        for sub in self.subcommands[:60]:
            if sub.summary:
                parts.append(f"{sub.name}: {sub.summary}")
            else:
                parts.append(sub.name)
        return " ; ".join(parts)

    def examples_text(self) -> str:
        parts: list[str] = []
        for ex in self.examples[:25]:
            if ex.description:
                parts.append(f"{ex.description} ({ex.command})")
            else:
                parts.append(ex.command)
        for sub in self.subcommands[:20]:
            for ex in sub.examples[:3]:
                if ex.description:
                    parts.append(f"{sub.name}: {ex.description}")
        return " ; ".join(parts)

    def flags_text(self) -> str:
        parts: list[str] = []
        for flag in self.flags[:60]:
            desc = flag.description[:140].strip()
            if desc:
                parts.append(f"{flag.display} {desc}")
        return " ; ".join(parts)

    def embedding_document(self) -> str:
        """Build a concise, high-signal natural-language card for vector embedding."""
        header = self.name
        if self.aliases:
            header = f"{header} (aliases: {', '.join(self.aliases[:4])})"
        lines: list[str] = [f"{header}: {self.summary}"]

        if self.category or self.tags:
            lines.append(f"Category: {self.tags_text()}")

        if self.description and self.description.strip().lower() != self.summary.strip().lower():
            lines.append(self.description.strip()[:300])

        if self.subcommands:
            sub_bits = [
                f"{sub.name} ({sub.summary[:60]})" if sub.summary else sub.name
                for sub in self.subcommands[:12]
            ]
            lines.append(f"Subcommands: {', '.join(sub_bits)}")

        if self.examples:
            task_bits = [ex.description.strip() for ex in self.examples[:8] if ex.description]
            if task_bits:
                lines.append(f"Common tasks: {'; '.join(task_bits)}")

        if self.flags:
            flag_bits = [
                f"{flag.primary} {flag.description[:70].strip()}"
                for flag in self.flags[:10]
                if flag.description
            ]
            if flag_bits:
                lines.append(f"Options: {'; '.join(flag_bits)}")

        return "\n".join(lines)[:2000]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "name": self.name,
            "binary_path": self.binary_path,
            "section": self.section,
            "aliases": list(self.aliases),
            "summary": self.summary,
            "description": self.description,
            "category": self.category,
            "tags": list(self.tags),
            "synopsis": list(self.synopsis),
            "subcommands": [sub.to_dict() for sub in self.subcommands],
            "flags": [flag.to_dict() for flag in self.flags],
            "positionals": [pos.to_dict() for pos in self.positionals],
            "examples": [ex.to_dict() for ex in self.examples],
            "see_also": list(self.see_also),
            "sources": [src.to_dict() for src in self.sources],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolSpec:
        section_raw = data.get("section")
        return cls(
            name=str(data["name"]),
            summary=str(data.get("summary") or ""),
            binary_path=data.get("binary_path"),
            section=int(section_raw) if section_raw is not None else None,
            aliases=tuple(str(item) for item in data.get("aliases", ())),
            description=str(data.get("description") or ""),
            category=data.get("category"),
            tags=tuple(str(item) for item in data.get("tags", ())),
            synopsis=tuple(str(item) for item in data.get("synopsis", ())),
            subcommands=tuple(
                SubcommandSpec.from_dict(item) for item in data.get("subcommands", ())
            ),
            flags=tuple(FlagSpec.from_dict(item) for item in data.get("flags", ())),
            positionals=tuple(
                PositionalSpec.from_dict(item) for item in data.get("positionals", ())
            ),
            examples=tuple(ExampleSpec.from_dict(item) for item in data.get("examples", ())),
            see_also=tuple(str(item) for item in data.get("see_also", ())),
            sources=tuple(SourceProvenance.from_dict(item) for item in data.get("sources", ())),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


@dataclass(frozen=True)
class CandidateContext:
    """Retrieved, query-scoped tool context handed to synthesis and display layers."""

    tool_id: int
    name: str
    summary: str
    description: str = ""
    category: str | None = None
    aliases: tuple[str, ...] = ()
    synopsis: tuple[str, ...] = ()
    subcommands: tuple[SubcommandSpec, ...] = ()
    flags: tuple[FlagSpec, ...] = ()
    examples: tuple[ExampleSpec, ...] = ()
    sources: tuple[str, ...] = ()
    score: float = 0.0
    match_reason: str = ""

    def to_prompt_dict(self) -> dict[str, Any]:
        """Serialize into the compact dictionary layout consumed by prompt builders."""
        return {
            "tool_id": self.tool_id,
            "tool": self.name,
            "summary": self.summary,
            "synopsis": " | ".join(self.synopsis[:3]),
            "subcommands": [{"name": sub.name, "summary": sub.summary} for sub in self.subcommands],
            "flags": [
                {
                    "flag": flag.primary,
                    "short_flag": flag.short,
                    "long_flag": flag.long,
                    "takes_value": flag.takes_value,
                    "value_hint": flag.value_hint,
                    "description": flag.description,
                }
                for flag in self.flags
            ],
            "examples": [
                {"command": ex.command, "context": ex.description} for ex in self.examples
            ],
        }


@dataclass(frozen=True)
class IndexStats:
    """Summary health metrics for ``sem doctor``."""

    tool_count: int = 0
    alias_count: int = 0
    subcommand_count: int = 0
    flag_count: int = 0
    example_count: int = 0
    tools_with_flags: int = 0
    tools_with_examples: int = 0
    tools_with_subcommands: int = 0
    source_counts: dict[str, int] = field(default_factory=dict)
