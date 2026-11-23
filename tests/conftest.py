"""Shared pytest fixtures for isolated offline testing."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    """Point sempropos data, config, and runtime directories to an isolated temp tree."""
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    runtime_dir = tmp_path / "run"
    data_dir.mkdir()
    config_dir.mkdir()
    runtime_dir.mkdir()

    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(data_dir))
    monkeypatch.setenv("SEMPROPOS_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("SEMPROPOS_RUNTIME_DIR", str(runtime_dir))
    return {"data": data_dir, "config": config_dir, "run": runtime_dir}
