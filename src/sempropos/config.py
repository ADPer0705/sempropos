"""Configuration and shared paths for sempropos."""

from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "sempropos"
EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

TOOL_EMBEDDINGS_FILE = "tool_embeddings.npy"
FLAG_EMBEDDINGS_FILE = "flag_embeddings.npy"
FLAG_EMBEDDING_IDS_FILE = "flag_embedding_ids.npy"
LAST_INDEXED_FILE = "last_indexed"
DB_FILE = "index.db"

LOCAL_LLM_MODEL_CANDIDATES = (
    "qwen2.5-1.5b-q4_k_m.gguf",
    "qwen2.5-1.5b-instruct-q4_k_m.gguf",
)

MODEL_REPO_ID = "Qwen/Qwen2.5-1.5B-Instruct-GGUF"
MODEL_FILENAME = "qwen2.5-1.5b-instruct-q4_k_m.gguf"
LLAMA_RELEASES_API = "https://api.github.com/repos/ggerganov/llama.cpp/releases/latest"


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


def models_dir() -> Path:
    """Return the directory where local GGUF models are stored."""
    return data_dir() / "models"


def model_path_candidates() -> list[Path]:
    """Return model file candidates in preference order."""
    return [models_dir() / name for name in LOCAL_LLM_MODEL_CANDIDATES]


def preferred_model_path() -> Path:
    """Return the primary GGUF model path expected by install tooling."""
    return model_path_candidates()[0]


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
    data_dir().mkdir(parents=True, exist_ok=True)
    models_dir().mkdir(parents=True, exist_ok=True)
    bin_dir().mkdir(parents=True, exist_ok=True)
