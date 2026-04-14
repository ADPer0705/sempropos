"""Index staleness checks based on package database mtimes."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sempropos import config

PACKAGE_DB_PATHS = {
    "debian": "/var/lib/dpkg/status",
    "arch": "/var/lib/pacman/sync",
    "fedora": "/var/lib/rpm/Packages",
    "alpine": "/lib/apk/db/installed",
}


def detect_package_db() -> str | None:
    """Return the first known package database path that exists locally."""
    for path in PACKAGE_DB_PATHS.values():
        if Path(path).exists():
            return path
    return None


def read_last_indexed() -> datetime | None:
    """Read the last_indexed timestamp marker as an ISO datetime."""
    marker = config.last_indexed_path()
    if not marker.exists():
        return None

    raw = marker.read_text(encoding="utf-8").strip()
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _to_utc_timestamp(value: datetime) -> float:
    """Convert datetime to epoch seconds in UTC consistently."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).timestamp()
    return value.astimezone(timezone.utc).timestamp()


def is_stale() -> bool:
    """Return True when package metadata is newer than the last index run."""
    last_indexed = read_last_indexed()
    if last_indexed is None:
        return True

    db_path = detect_package_db()
    if db_path is None:
        return False

    last_indexed_ts = _to_utc_timestamp(last_indexed)

    pkg_mtime_ts = Path(db_path).stat().st_mtime
    return pkg_mtime_ts > last_indexed_ts
