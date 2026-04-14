from __future__ import annotations

import pytest
import requests


def test_network_is_blocked_by_default() -> None:
    with pytest.raises(RuntimeError) as exc:
        requests.get("https://example.com", timeout=1)

    assert "disabled in tests" in str(exc.value)
