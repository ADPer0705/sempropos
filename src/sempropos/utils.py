"""Utilities and helper functions for sempropos.

Provides:
    - Keyring integration for secure secret storage (optional).
    - Atomic file writing utilities to avoid corrupt state on crash.
    - Terminal / headless environment detection.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def is_headless_environment() -> bool:
    """Detect if running in a non-interactive environment."""
    return not (sys.stdin.isatty() and sys.stdout.isatty())


def should_disable_color() -> bool:
    """Return True when ANSI colors should be suppressed (NO_COLOR or non-TTY)."""
    if os.environ.get("NO_COLOR") is not None:
        return True
    return not sys.stdout.isatty()


# ==================================================
# Keyring integration utilities
# ==================================================


def keyring_store_secret(secret_name: str, value: str) -> bool:
    """Store a secret in the system keyring (if available)."""
    try:
        import keyring
    except ModuleNotFoundError:
        return False

    try:
        keyring.set_password("sempropos", secret_name, value)
    except Exception:  # noqa: BLE001
        return False
    return True


def keyring_load_secret(secret_name: str) -> str | None:
    """Retrieve a secret from the system keyring (if available)."""
    try:
        import keyring
    except ModuleNotFoundError:
        return None

    try:
        value = keyring.get_password("sempropos", secret_name)
    except Exception:  # noqa: BLE001
        return None

    return value.strip() if value and value.strip() else None


# ==================================================
# Atomic file writing utilities
# ==================================================


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to a file atomically using tempfile and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            delete=False,
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name

        os.replace(tmp_path, str(path))
    except Exception:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        raise


def atomic_write_bytes(path: Path, content: bytes) -> None:
    """Write bytes to a file atomically using tempfile and os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=str(path.parent),
            delete=False,
        ) as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name

        os.replace(tmp_path, str(path))
    except Exception:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        raise
