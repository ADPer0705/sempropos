"""Test bootstrap helpers."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from sempropos.index import embedder
from sempropos.retrieval import bm25, flag_retrieval, semantic


@pytest.fixture(autouse=True)
def _isolated_sempropos_data_dir(tmp_path, monkeypatch):
    """Run each test against an isolated sempropos data directory."""
    data_dir = tmp_path / "sempropos-data"
    monkeypatch.setenv("SEMPROPOS_DATA_DIR", str(data_dir))
    yield


@pytest.fixture(autouse=True)
def _reset_in_process_caches():
    """Ensure retrieval and embedding caches do not leak across tests."""
    bm25._reset_cache()
    semantic._reset_cache()
    flag_retrieval._reset_cache()
    embedder.reset_cache()
    yield
    bm25._reset_cache()
    semantic._reset_cache()
    flag_retrieval._reset_cache()
    embedder.reset_cache()


@pytest.fixture(autouse=True)
def _block_network_requests(monkeypatch):
    """Disallow live network access in tests unless explicitly opted in."""
    if os.environ.get("SEMPROPOS_TEST_ALLOW_NETWORK") == "1":
        yield
        return

    def _deny_network(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError(
            "Live network access is disabled in tests. Use mocks or set "
            "SEMPROPOS_TEST_ALLOW_NETWORK=1 for explicit live checks."
        )

    import requests

    monkeypatch.setattr(requests.sessions.Session, "request", _deny_network)
    yield
