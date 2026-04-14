from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from sempropos import config
from sempropos.index import staleness


def test_to_utc_timestamp_normalizes_naive_and_aware() -> None:
    base_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    naive = datetime(2026, 1, 1, 12, 0, 0)
    aware_offset = datetime(2026, 1, 1, 14, 0, 0, tzinfo=timezone(timedelta(hours=2)))

    naive_ts = staleness._to_utc_timestamp(naive)
    aware_ts = staleness._to_utc_timestamp(aware_offset)
    base_ts = staleness._to_utc_timestamp(base_utc)

    assert naive_ts == base_ts
    assert aware_ts == base_ts


def test_is_stale_uses_package_db_mtime(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(tmp_path))

    now = datetime.now(tz=timezone.utc)
    config.last_indexed_path().write_text(now.isoformat(), encoding="utf-8")

    pkg_db = tmp_path / "pkg-db"
    pkg_db.write_text("ok", encoding="utf-8")

    newer = now.timestamp() + 60
    Path(pkg_db).touch()
    import os

    os.utime(pkg_db, (newer, newer))

    monkeypatch.setattr(staleness, "detect_package_db", lambda: str(pkg_db))

    assert staleness.is_stale() is True
