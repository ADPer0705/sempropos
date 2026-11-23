"""Source contracts for multi-source CLI documentation extraction."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sempropos.model.spec import SourceKind, ToolSpec
from sempropos.sources.discovery import DiscoveryContext


@dataclass(frozen=True)
class ToolRecord:
    """Lightweight discovered command record (retained for source inspection)."""

    name: str
    section: int
    description: str
    source: str = "man"


@runtime_checkable
class Source(Protocol):
    """Protocol implemented by documentation sources."""

    name: SourceKind | str

    def discover(self, context: DiscoveryContext) -> Iterator[ToolSpec]:
        """Yield structured ToolSpecs or fragments for installed commands."""
        ...
