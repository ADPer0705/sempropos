"""Indexing modules for sempropos."""

from __future__ import annotations

from sempropos.index.builder import build_index
from sempropos.index.parser import (
    ParsedExample,
    ParsedFlag,
    ParsedManPage,
    parse_man_page,
)
from sempropos.index.schema import get_connection, initialize_database
from sempropos.index.staleness import (
    detect_package_db,
    is_index_stale,
    read_last_indexed,
)

__all__ = [
    # ----- Database -----
    "get_connection",
    "initialize_database",
    "detect_package_db",
    # ----- Staleness -----
    "is_index_stale",
    "read_last_indexed",
    # ----- Parser -----
    "ParsedFlag",
    "ParsedExample",
    "ParsedManPage",
    "parse_man_page",
    # ----- Builder -----
    "build_index"
]
