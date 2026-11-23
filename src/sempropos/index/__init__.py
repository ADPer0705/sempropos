"""Indexing modules for sempropos."""

from __future__ import annotations

from sempropos.index import repository
from sempropos.index.builder import build_index
from sempropos.index.parser import (
    ParsedExample,
    ParsedFlag,
    ParsedManPage,
    parse_man_page,
)
from sempropos.index.schema import (
    get_connection,
    initialize_database,
    open_connection,
    read_schema_version,
)
from sempropos.index.staleness import (
    detect_package_db,
    is_index_stale,
    read_last_indexed,
)

__all__ = [
    # ----- Database & Repository -----
    "get_connection",
    "open_connection",
    "initialize_database",
    "read_schema_version",
    "repository",
    # ----- Staleness -----
    "detect_package_db",
    "is_index_stale",
    "read_last_indexed",
    # ----- Parser (legacy until Phase 2 migration) -----
    "ParsedFlag",
    "ParsedExample",
    "ParsedManPage",
    "parse_man_page",
    # ----- Builder -----
    "build_index",
]
