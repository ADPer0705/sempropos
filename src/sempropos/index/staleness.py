"""Index staleness checks across package managers and user binary directories."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sempropos import config

# Installed-package database locations (fixes Arch pacman/local vs sync).
PACKAGE_DB_PATHS: dict[str, str] = {
    "debian": "/var/lib/dpkg/status",
    "arch": "/var/lib/pacman/local",
    "fedora": "/var/lib/rpm/rpmdb.sqlite",
    "rpm_legacy": "/var/lib/rpm/Packages",
    "alpine": "/lib/apk/db/installed",
    "homebrew_arm": "/opt/homebrew/Cellar",
    "homebrew_intel": "/usr/local/Cellar",
    "nix": "/nix/var/nix/profiles/default",
}


def detect_package_db() -> str | None:
    """Return the first known installed-package database path present locally."""
    for path in PACKAGE_DB_PATHS.values():
        if Path(path).exists():
            return path
    return None


def watched_paths() -> list[Path]:
    """Return all package DBs and user bin directories whose changes imply staleness."""
    paths: list[Path] = []
    pkg = detect_package_db()
    if pkg:
        paths.append(Path(pkg))

    home = Path.home()
    for user_bin in (home / ".local" / "bin", home / ".cargo" / "bin"):
        if user_bin.is_dir():
            paths.append(user_bin)
    return paths


def read_last_indexed() -> datetime | None:
    """Read the last_indexed timestamp marker as an ISO datetime."""
    marker = config.last_indexed_path()
    if not marker.exists():
        return None

    try:
        raw = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None

    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _to_utc_timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC).timestamp()
    return value.astimezone(UTC).timestamp()


def is_index_stale() -> bool:
    """Return True when installed software or user bin dirs changed since last index."""
    last_indexed = read_last_indexed()
    if last_indexed is None:
        return True

    last_ts = _to_utc_timestamp(last_indexed)
    for path in watched_paths():
        try:
            if path.stat().st_mtime > last_ts:
                return True
        except OSError:
            continue
    return False
