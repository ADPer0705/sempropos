"""Indexing modules for sempropos."""

from __future__ import annotations

from sempropos.index import repository
from sempropos.index.builder import IndexBuildReport, build_index
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
    watched_paths,
)

__all__ = [
    "get_connection",
    "open_connection",
    "initialize_database",
    "read_schema_version",
    "repository",
    "detect_package_db",
    "watched_paths",
    "is_index_stale",
    "read_last_indexed",
    "IndexBuildReport",
    "build_index",
]
