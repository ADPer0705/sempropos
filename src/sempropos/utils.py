"""
Utilities and helper functions for sempropos.

This module provides shared, reusable helper logic including:
    - Keyring integration for secure secret storage (optional, depends on `keyring` package).
    - Atomic file writing utilities to ensure safe updates to files without risking corruption.
    - Miscellaneous utilities such as environment detection.
"""

from __future__ import annotations

import os
import sys
import subprocess
import importlib.util
import tempfile
from pathlib import Path

# ----- Miscellaneous utilities -----

def is_headless_environment() -> bool:
    """Detect if running in headless (non-interactive) environment."""
    return not (sys.stdin.isatty() and sys.stdout.isatty())

def install_and_import(package: str) -> None:
    """Dynamically install and import a package if it's not already available."""
    if importlib.util.find_spec(package) is not None:
        return importlib.import_module(package)
    
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

        return importlib.import_module(package)
    except subprocess.CalledProcessError as e:
        raise ImportError(f"Failed to install package '{package}'. Please install it manually. Error: {e}") from e

# ==================================================
# Keyring integration utilities
# ==================================================

def keyring_store_secret(secret_name: str, value: str) -> bool:
    """Store a secret in the system keyring (if available)."""
    try:
        import keyring  # type: ignore[import-not-found]
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
        import keyring  # type: ignore[import-not-found]
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
    tmp_path = None

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
    tmp_path = None

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