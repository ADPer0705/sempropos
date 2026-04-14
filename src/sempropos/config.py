"""Configuration and shared paths for sempropos."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from sempropos import __version__

APP_NAME = "sempropos"
EMBEDDING_PRIMARY_MODEL = "BAAI/bge-base-en-v1.5"
EMBEDDING_FLOOR_MODEL = "BAAI/bge-small-en-v1.5"

EMBEDDING_DIMS = {
    EMBEDDING_PRIMARY_MODEL: 768,
    EMBEDDING_FLOOR_MODEL: 384,
}

EMBEDDING_LOW_DISK_THRESHOLD_BYTES = 200 * 1024 * 1024
SYNTHESIS_LOW_RAM_THRESHOLD_BYTES = 3 * 1024 * 1024 * 1024

TOOL_EMBEDDINGS_FILE = "tool_embeddings.npy"
FLAG_EMBEDDINGS_FILE = "flag_embeddings.npy"
FLAG_EMBEDDING_IDS_FILE = "flag_embedding_ids.npy"
LAST_INDEXED_FILE = "last_indexed"
INDEX_META_FILE = "index.meta"
DB_FILE = "index.db"

LLAMA_CPP_RELEASE = "b5000"

LLAMA_CPP_ASSETS = {
    ("Linux", "x86_64"): f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-x64.zip",
    ("Linux", "aarch64"): f"llama-{LLAMA_CPP_RELEASE}-bin-ubuntu-arm64.zip",
}

SYNTHESIS_MODELS = {
    "primary": {
        "repo_id": "unsloth/gemma-4-E2B-it-GGUF",
        "filename": "gemma-4-E2B-it-Q4_K_M.gguf",
        "sha256": "",
    },
    "floor": {
        "repo_id": "Qwen/Qwen3-0.6B-GGUF",
        "filename": "qwen3-0.6b-q4_k_m.gguf",
        "sha256": "",
    },
}

OLLAMA_PREFERRED = ["gemma4:e2b", "qwen3:1.7b", "qwen3:0.6b", "gemma3:1b"]

INSTALL_FLOOR_NOTICE = (
    "[sempropos] Using compact synthesis model (limited RAM detected)."
)
EMBEDDING_FLOOR_NOTICE = "[sempropos] Using compact embedding model (low disk space)."

MISMATCH_UPDATE_NOTICE = (
    "[sempropos] Model/index metadata mismatch. Re-indexing required.\n"
    "Run: sempropos --update"
)

VERSION_UPDATE_NOTICE_TEMPLATE = (
    "[sempropos] Model updated in sempropos {version}. Re-indexing required.\n"
    "Run: sempropos --update"
)


def synthesis_model_spec(tier: str) -> dict[str, str]:
    """Return synthesis model metadata for a known tier."""
    if tier not in SYNTHESIS_MODELS:
        raise KeyError(f"Unknown synthesis tier: {tier}")
    spec = SYNTHESIS_MODELS[tier]
    return {
        "repo_id": str(spec["repo_id"]),
        "filename": str(spec["filename"]),
        "sha256": str(spec["sha256"]),
    }


def data_dir() -> Path:
    """
    Get the data directory for the application.

    The directory is determined in the following order:
    1. The `SEMPROPOS_DATA_DIR` environment variable, if set.
    2. The `XDG_DATA_HOME` environment variable, if set, with the application name appended.
    3. The default path `~/.local/share/sempropos`.
    """
    base = os.environ.get("SEMPROPOS_DATA_DIR")
    if base:
        return Path(base).expanduser()

    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg).expanduser() / APP_NAME

    return Path("~/.local/share").expanduser() / APP_NAME


def db_path() -> Path:
    """Return the SQLite index database path."""
    return data_dir() / DB_FILE


def tool_embeddings_path() -> Path:
    """Return the persisted tool embedding matrix path."""
    return data_dir() / TOOL_EMBEDDINGS_FILE


def flag_embeddings_path() -> Path:
    """Return the persisted flag embedding matrix path."""
    return data_dir() / FLAG_EMBEDDINGS_FILE


def flag_embedding_ids_path() -> Path:
    """Return the flag embedding id mapping path."""
    return data_dir() / FLAG_EMBEDDING_IDS_FILE


def last_indexed_path() -> Path:
    """Return the staleness marker file path."""
    return data_dir() / LAST_INDEXED_FILE


def index_meta_path() -> Path:
    """Return index metadata file path."""
    return data_dir() / INDEX_META_FILE


def models_dir() -> Path:
    """Return the directory where local GGUF models are stored."""
    return data_dir() / "models"


def model_path_candidates() -> list[Path]:
    """Return model file candidates in preference order."""
    candidates = [
        Path(models_dir() / str(spec["filename"])) for spec in SYNTHESIS_MODELS.values()
    ]
    # Keep deterministic order and drop duplicates.
    seen: set[Path] = set()
    output: list[Path] = []
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def preferred_model_path() -> Path:
    """Return the primary GGUF model path expected by install tooling."""
    return models_dir() / str(SYNTHESIS_MODELS["primary"]["filename"])


def resolve_model_path() -> Path | None:
    """Return the first locally available model candidate, if any."""
    for path in model_path_candidates():
        if path.exists():
            return path
    return None


def bin_dir() -> Path:
    """Return the directory where local helper binaries are stored."""
    return data_dir() / "bin"


def local_llama_cli_path() -> Path:
    """Return the expected location of the bundled llama-cli binary."""
    return bin_dir() / "llama-cli"


def ensure_data_dirs() -> None:
    """Create application data directories when missing."""
    directories = (data_dir(), models_dir(), bin_dir())
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
        _assert_directory_writable(directory)


def _assert_directory_writable(directory: Path) -> None:
    """Raise RuntimeError when a target directory is not writable."""
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=True):
            pass
    except OSError as exc:
        raise RuntimeError(f"Directory is not writable: {directory}") from exc


def get_embedding_dim(model_name: str) -> int:
    """Return embedding dimensionality for a configured embedding model."""
    dim = EMBEDDING_DIMS.get(model_name)
    if dim is None:
        raise KeyError(f"Unknown embedding model: {model_name}")
    return int(dim)


def write_index_meta(
    *,
    embedding_model: str,
    embedding_dim: int,
    synthesis_model: str,
    embedding_provider: str | None = None,
) -> None:
    """Write index metadata used for query-time compatibility checks."""
    ensure_data_dirs()
    destination = index_meta_path()
    temporary = destination.with_suffix(".tmp")

    payload = {
        "embedding_model": embedding_model,
        "embedding_dim": int(embedding_dim),
        "embedding_provider": embedding_provider,
        "synthesis_model": synthesis_model,
        "indexed_at": datetime.now(tz=timezone.utc).isoformat(),
        "sempropos_version": __version__,
    }
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, destination)


def read_index_meta() -> dict | None:
    """Read index metadata; return None when absent or invalid."""
    path = index_meta_path()
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def select_ollama_model(models: list[str]) -> str | None:
    """Select the first compatible Ollama model from a discovered list."""
    for candidate in OLLAMA_PREFERRED:
        if candidate in models:
            return candidate
    for candidate in OLLAMA_PREFERRED:
        for item in models:
            if item.startswith(candidate):
                return item
    return None
