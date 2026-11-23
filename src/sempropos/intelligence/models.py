"""Curated local GGUF model catalog and download/runtime manager.

Keeps local CLI command synthesis frictionless: users can install a small,
task-tested GGUF model with ``sem model install balanced`` (or ``tiny`` /
``quality``) without manually configuring Ollama.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sempropos import config


@dataclass(frozen=True)
class LocalModelSpec:
    """Metadata for a curated small GGUF model tuned for CLI command synthesis."""

    tier: str
    model_id: str
    filename: str
    url: str
    size_mb: int
    min_ram_gb: int
    description: str
    sha256: str | None = None


MODEL_CATALOG: tuple[LocalModelSpec, ...] = (
    LocalModelSpec(
        tier="tiny",
        model_id="qwen2.5-coder-0.5b",
        filename="qwen2.5-coder-0.5b-instruct-q4_k_m.gguf",
        url=(
            "https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct-GGUF/resolve/main/"
            "qwen2.5-coder-0.5b-instruct-q4_k_m.gguf"
        ),
        size_mb=398,
        min_ram_gb=2,
        description="Ultra-fast CPU model (~400 MB); ideal for low-RAM machines and instant responses",
    ),
    LocalModelSpec(
        tier="balanced",
        model_id="qwen2.5-coder-1.5b",
        filename="qwen2.5-coder-1.5b-instruct-q4_k_m.gguf",
        url=(
            "https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF/resolve/main/"
            "qwen2.5-coder-1.5b-instruct-q4_k_m.gguf"
        ),
        size_mb=986,
        min_ram_gb=4,
        description="Recommended default (~1.0 GB); crisp CLI flag accuracy and fast local generation",
    ),
    LocalModelSpec(
        tier="quality",
        model_id="qwen2.5-coder-3b",
        filename="qwen2.5-coder-3b-instruct-q4_k_m.gguf",
        url=(
            "https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct-GGUF/resolve/main/"
            "qwen2.5-coder-3b-instruct-q4_k_m.gguf"
        ),
        size_mb=1930,
        min_ram_gb=8,
        description="Highest accuracy for complex pipelines & multi-flag synthesis (~2.0 GB)",
    ),
)

_BY_KEY: dict[str, LocalModelSpec] = {}
for _spec in MODEL_CATALOG:
    _BY_KEY[_spec.tier] = _spec
    _BY_KEY[_spec.model_id] = _spec
    _BY_KEY[_spec.filename] = _spec


def list_catalog() -> tuple[LocalModelSpec, ...]:
    """Return all curated local GGUF model entries."""
    return MODEL_CATALOG


def get_model_spec(identifier: str) -> LocalModelSpec | None:
    """Resolve a tier name (``balanced``), model id, or filename to its catalog entry."""
    return _BY_KEY.get(identifier.strip().lower())


def has_llama_cpp_python() -> bool:
    """Return True when ``llama-cpp-python`` is importable in this Python environment."""
    return importlib.util.find_spec("llama_cpp") is not None


def find_llama_server_binary() -> Path | None:
    """Locate a ``llama-server`` binary in ``data_dir()/bin`` or on ``$PATH``."""
    managed = config.bin_dir() / "llama-server"
    if managed.is_file() and os.access(managed, os.X_OK):
        return managed

    for candidate in ("llama-server", "llama.cpp-server"):
        located = shutil.which(candidate)
        if located:
            return Path(located)
    return None


def local_runtime_kind() -> str | None:
    """Return active local GGUF inference driver (``llama_cpp``, ``llama_server``, or ``None``)."""
    if has_llama_cpp_python():
        return "llama_cpp"
    if find_llama_server_binary() is not None:
        return "llama_server"
    return None


def installed_model_files() -> list[Path]:
    """Return all downloaded ``.gguf`` files inside ``config.models_dir()``."""
    mdir = config.models_dir()
    if not mdir.is_dir():
        return []
    return sorted(p for p in mdir.glob("*.gguf") if p.is_file())


def installed_model_names() -> list[str]:
    """Return friendly identifiers (catalog model_id or stem) for installed GGUF files."""
    names: list[str] = []
    for path in installed_model_files():
        spec = get_model_spec(path.name)
        names.append(spec.model_id if spec else path.name)
    return names


def resolve_model_path(identifier: str | None) -> Path | None:
    """Resolve a tier, model_id, filename, or explicit filesystem path to a local GGUF file."""
    if not identifier:
        installed = installed_model_files()
        return installed[0] if installed else None

    explicit = Path(identifier).expanduser()
    if explicit.is_file():
        return explicit

    spec = get_model_spec(identifier)
    if spec is not None:
        candidate = config.models_dir() / spec.filename
        if candidate.is_file():
            return candidate

    direct = config.models_dir() / identifier
    if direct.is_file():
        return direct
    if not identifier.endswith(".gguf"):
        with_ext = config.models_dir() / f"{identifier}.gguf"
        if with_ext.is_file():
            return with_ext

    return None


def download_model(
    identifier: str,
    *,
    progress_callback: Callable[[int, int | None], None] | None = None,
    timeout: float = 60.0,
) -> Path:
    """Download a curated GGUF model atomically into ``config.models_dir()``."""
    spec = get_model_spec(identifier)
    if spec is None:
        available = ", ".join(f"{m.tier} ({m.model_id})" for m in MODEL_CATALOG)
        raise ValueError(f"Unknown model '{identifier}'. Available: {available}")

    dest_dir = config.models_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / spec.filename
    if dest_path.is_file():
        return dest_path

    req = urllib.request.Request(
        spec.url,
        headers={"User-Agent": "sempropos/1.0 (+https://github.com/ADPer0705/sempropos)"},
        method="GET",
    )
    tmp_path: Path | None = None
    hasher = hashlib.sha256()
    downloaded = 0

    try:
        with (
            urllib.request.urlopen(req, timeout=timeout) as response,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=dest_dir,
                prefix=f"{spec.filename}.",
                suffix=".part",
                delete=False,
            ) as tmp_file,
        ):
            tmp_path = Path(tmp_file.name)
            total_hdr = response.headers.get("Content-Length")
            total_bytes = int(total_hdr) if total_hdr and total_hdr.isdigit() else None

            while True:
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                tmp_file.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback(downloaded, total_bytes)

            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        if spec.sha256 and hasher.hexdigest().lower() != spec.sha256.lower():
            raise RuntimeError(f"SHA256 verification failed for {spec.filename}.")

        os.replace(tmp_path, dest_path)
        tmp_path = None
        return dest_path
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def remove_model(identifier: str) -> Path | None:
    """Delete an installed local GGUF model file and return its removed path."""
    path = resolve_model_path(identifier)
    if path is None or not path.is_file():
        return None
    # Only delete files managed inside config.models_dir().
    try:
        path.resolve().relative_to(config.models_dir().resolve())
    except ValueError:
        return None
    path.unlink(missing_ok=True)
    return path
