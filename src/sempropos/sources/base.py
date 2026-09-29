"""Source-neutral contracts for ingesting command documentation.

A *source* knows how to enumerate documented commands and return the raw text
for one command. Today only man pages are implemented; tldr pages and tool
``--help`` output are intended to implement the same interface so the indexer
does not need to change when they are added.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ToolRecord:
    """A single documented tool discovered by a source."""

    name: str
    section: int
    description: str
    source: str = "man"


@dataclass(frozen=True)
class ParsedTool:
    """A parsed tool ready to be written to the index."""

    record: ToolRecord
    synopsis: str = ""
    flags: list[dict] = field(default_factory=list)
    examples: list[dict] = field(default_factory=list)


@runtime_checkable
class Source(Protocol):
    """Interface implemented by every documentation source."""

    name: str

    def discover(self) -> Iterator[ToolRecord]:
        """Yield every documented tool this source can provide."""
        ...

    def load(self, record: ToolRecord) -> str | None:
        """Return the raw documentation text for *record*, or ``None``."""
        ...
