"""Unified configuration and runtime settings for sempropos.

Manages:
  - Application data, config, model, binary, tldr, and runtime socket paths
  - Role-specific configuration (`SynthesisConfig`, `EmbeddingConfig`, `DaemonConfig`, `IndexingConfig`)
  - Index metadata persistence and compatibility checks
  - TOML config persistence (`config.toml`, with automatic read migration from legacy `intelligence.toml`)
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, get_args

import platformdirs
import tomli_w

from sempropos import __version__
from sempropos.utils import atomic_write_text

# ============================================================================
# Application Constants
# ============================================================================

APP_NAME = "sempropos"

# Default embedding model (FastEmbed quantized Nomic)
EMBEDDING_PRIMARY_MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"
RECOMMENDED_EMBEDDING_MODEL = EMBEDDING_PRIMARY_MODEL

# Default managed local GGUF model tier and Ollama recommendation
RECOMMENDED_LOCAL_MODEL_TIER = "balanced"
RECOMMENDED_SYNTHESIS_MODEL = "qwen2.5-coder:1.5b"

# Bump whenever the embedding representation or index schema changes so stale
# assets trigger a clean rebuild on `sem update`.
EMBEDDING_REVISION = 3
INDEX_REVISION = 3

# Subdirectories inside data_dir()
EMBEDDINGS_CACHE_DIR = "embeddings"
MODELS_CACHE_DIR = "models"
BIN_CACHE_DIR = "bin"
TLDR_CACHE_DIR = "tldr"

# ============================================================================
# Data Files and Paths
# ============================================================================

dirs = platformdirs.PlatformDirs("sempropos", "adper")

TOOL_EMBEDDINGS_FILE = "tool_embeddings.npy"
TOOL_EMBEDDING_IDS_FILE = "tool_embedding_ids.npy"
LAST_INDEXED_FILE = "last_indexed"
INDEX_META_FILE = "index.meta"
DB_FILE = "index.db"

CONFIG_FILE = "config.toml"
LEGACY_INTELLIGENCE_CONFIG_FILE = "intelligence.toml"
# Backward-compatible export name
INTELLIGENCE_CONFIG_FILE = CONFIG_FILE

# ============================================================================
# Information Messages
# ============================================================================

MISMATCH_UPDATE_NOTICE = (
    "[sem] Index is missing or incompatible with the current configuration.\nRun: sem update"
)
VERSION_UPDATE_NOTICE_TEMPLATE = (
    "[sem] Index format changed in sempropos {version}.\nRun: sem update"
)

# ============================================================================
# Provider Configuration Types
# ============================================================================

SynthesisProviders = Literal[
    "local_gguf",
    "ollama",
    "openai_compatible",
    "tier0",
]
EmbeddingProviders = Literal[
    "fastembed_local",
    "ollama",
    "none",
]

SUPPORTED_SYNTHESIS_PROVIDERS: tuple[str, ...] = get_args(SynthesisProviders)
SUPPORTED_EMBEDDING_PROVIDERS: tuple[str, ...] = get_args(EmbeddingProviders)
SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(
    dict.fromkeys(SUPPORTED_SYNTHESIS_PROVIDERS + SUPPORTED_EMBEDDING_PROVIDERS)
)

DEFAULT_SYNTHESIS_PROVIDER = "tier0"
DEFAULT_EMBEDDING_PROVIDER = "fastembed_local"


@dataclass(frozen=True)
class SynthesisConfig:
    """Configuration for the LLM command synthesis role."""

    provider: str = DEFAULT_SYNTHESIS_PROVIDER
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_key_secret: str | None = None
    api_key_plaintext: str | None = None
    timeout_seconds: float = 30.0
    think: bool = False
    temperature: float = 0.0
    max_tokens: int = 256
    keep_alive: str | int | None = "30m"
    stop: list[str] | None = None
    n_ctx: int = 2048
    n_gpu_layers: int = -1
    threads: int | None = None


@dataclass(frozen=True)
class EmbeddingConfig:
    """Configuration for the vector embedding role (cleanly separated from synthesis)."""

    provider: str = DEFAULT_EMBEDDING_PROVIDER
    model: str | None = RECOMMENDED_EMBEDDING_MODEL
    base_url: str | None = None
    timeout_seconds: float = 30.0
    lazy_fallback: bool = True


@dataclass(frozen=True)
class DaemonConfig:
    """Configuration for the auto-managed local background daemon."""

    enabled: bool = True
    idle_timeout_seconds: int = 900
    auto_start: bool = True


@dataclass(frozen=True)
class IndexingConfig:
    """Configuration for multi-source documentation ingestion."""

    enable_tldr: bool = True
    enable_completions: bool = True
    probe_help: bool = True
    help_probe_timeout: float = 1.5


@dataclass(frozen=True)
class AppConfig:
    """Top-level sempropos runtime configuration."""

    synthesis: SynthesisConfig = field(default_factory=SynthesisConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    daemon: DaemonConfig = field(default_factory=DaemonConfig)
    indexing: IndexingConfig = field(default_factory=IndexingConfig)


# Legacy aliases preserved for external imports during transition
RoleConfig = SynthesisConfig
IntelligenceConfig = AppConfig


# ============================================================================
# Application Directory Methods
# ============================================================================


def data_dir() -> Path:
    """Get the application data directory (overridable via SEMPROPOS_DATA_DIR)."""
    override = os.environ.get("SEMPROPOS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return dirs.user_data_path


def config_dir() -> Path:
    """Get the application configuration directory (overridable via SEMPROPOS_CONFIG_DIR)."""
    override = os.environ.get("SEMPROPOS_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return dirs.user_config_path


def runtime_dir() -> Path:
    """Get user-private runtime directory for daemon socket/PID files."""
    override = os.environ.get("SEMPROPOS_RUNTIME_DIR")
    if override:
        return Path(override).expanduser()
    if os.environ.get("SEMPROPOS_DATA_DIR"):
        # Keep isolated test / custom data dirs self-contained.
        return data_dir() / "run"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        candidate = Path(xdg_runtime) / APP_NAME
        if Path(xdg_runtime).is_dir():
            return candidate
    return data_dir() / "run"


def embeddings_cache_dir() -> Path:
    return data_dir() / EMBEDDINGS_CACHE_DIR


def models_dir() -> Path:
    return data_dir() / MODELS_CACHE_DIR


def bin_dir() -> Path:
    return data_dir() / BIN_CACHE_DIR


def tldr_dir() -> Path:
    return data_dir() / TLDR_CACHE_DIR


def ensure_data_dirs() -> None:
    """Create application data and config directories if missing."""
    for directory in (
        data_dir(),
        config_dir(),
        embeddings_cache_dir(),
        models_dir(),
        bin_dir(),
        tldr_dir(),
    ):
        directory.mkdir(parents=True, exist_ok=True)


# ===== File paths =====
def db_path() -> Path:
    return data_dir() / DB_FILE


def tool_embeddings_path() -> Path:
    return data_dir() / TOOL_EMBEDDINGS_FILE


def tool_embedding_ids_path() -> Path:
    return data_dir() / TOOL_EMBEDDING_IDS_FILE


def last_indexed_path() -> Path:
    return data_dir() / LAST_INDEXED_FILE


def index_meta_path() -> Path:
    return data_dir() / INDEX_META_FILE


def config_path() -> Path:
    return config_dir() / CONFIG_FILE


def legacy_intelligence_config_path() -> Path:
    return config_dir() / LEGACY_INTELLIGENCE_CONFIG_FILE


def intelligence_config_path() -> Path:
    """Return active config path (preferring config.toml, falling back if legacy exists)."""
    primary = config_path()
    if primary.exists():
        return primary
    legacy = legacy_intelligence_config_path()
    if legacy.exists():
        return legacy
    return primary


def daemon_socket_path() -> Path:
    return runtime_dir() / "sem.sock"


def daemon_pid_path() -> Path:
    return runtime_dir() / "sem.pid"


def daemon_lock_path() -> Path:
    return runtime_dir() / "sem.lock"


# ============================================================================
# Index Metadata I/O Methods
# ============================================================================


def write_index_meta(
    *,
    embedding_dim: int,
    embedding_model: str,
    embedding_provider: str,
    tool_count: int | None = None,
    flag_count: int | None = None,
    subcommand_count: int | None = None,
    example_count: int | None = None,
) -> None:
    """Write index metadata file used for query-time compatibility checks."""
    destination = index_meta_path()

    payload: dict[str, Any] = {
        "embedding_dim": int(embedding_dim),
        "embedding_model": embedding_model,
        "embedding_provider": embedding_provider,
        "embedding_revision": int(EMBEDDING_REVISION),
        "index_revision": int(INDEX_REVISION),
        "indexed_at": datetime.now(tz=UTC).isoformat(),
        "sempropos_version": __version__,
        "status": "complete",
    }
    if tool_count is not None:
        payload["tool_count"] = int(tool_count)
    if flag_count is not None:
        payload["flag_count"] = int(flag_count)
    if subcommand_count is not None:
        payload["subcommand_count"] = int(subcommand_count)
    if example_count is not None:
        payload["example_count"] = int(example_count)

    atomic_write_text(destination, tomli_w.dumps(payload))


def read_index_meta() -> dict[str, Any] | None:
    """Read index metadata file."""
    path = index_meta_path()
    if not path.exists():
        return None

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None


def _parse_positive_int(value: Any) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _validate_embedding_meta(meta: dict[str, Any], settings: AppConfig) -> tuple[str, int, str]:
    embedding_provider = str(meta.get("embedding_provider") or "none")
    if embedding_provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if settings.embedding.provider == "none":
        return "", 0, "none"

    if _parse_positive_int(meta.get("embedding_revision")) != EMBEDDING_REVISION:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    model_name = str(meta.get("embedding_model") or "")
    dim_value = _parse_positive_int(meta.get("embedding_dim"))
    if embedding_provider == "none" or not model_name or dim_value <= 0:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if settings.embedding.provider != embedding_provider:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if settings.embedding.model and settings.embedding.model != model_name:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if not tool_embeddings_path().exists():
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    return model_name, dim_value, embedding_provider


def read_validated_index_meta() -> tuple[str, int, str]:
    """Return validated embedding metadata needed by query-time retrieval.

    Raises:
        RuntimeError: When the index is missing, incomplete, or incompatible.
    """
    meta = read_index_meta()
    if meta is None or str(meta.get("status") or "complete") != "complete":
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if _parse_positive_int(meta.get("index_revision")) != INDEX_REVISION:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if not db_path().exists():
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    return _validate_embedding_meta(meta, load_config())


def is_index_complete() -> bool:
    """Return True when a usable index is present on disk."""
    try:
        read_validated_index_meta()
    except RuntimeError:
        return False
    return True


# ============================================================================
# Configuration Loading / Saving
# ============================================================================


def normalize_provider_name(value: Any, supported: tuple[str, ...]) -> str | None:
    """Normalize and validate a provider name string."""
    if not isinstance(value, str) or not value.strip():
        return None
    lowered = value.strip().lower().replace("-", "_")
    return lowered if lowered in supported else None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _as_float(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    return default


def _parse_synthesis(block: dict[str, Any]) -> SynthesisConfig:
    provider = (
        normalize_provider_name(block.get("provider"), SUPPORTED_SYNTHESIS_PROVIDERS)
        or DEFAULT_SYNTHESIS_PROVIDER
    )
    keep_alive_raw = block.get("keep_alive")
    keep_alive: str | int | None = (
        keep_alive_raw
        if isinstance(keep_alive_raw, (str, int)) and not isinstance(keep_alive_raw, bool)
        else "30m"
    )
    stop_raw = block.get("stop")
    stop = [str(item) for item in stop_raw] if isinstance(stop_raw, list) else None
    threads_raw = block.get("threads")
    threads = (
        int(threads_raw)
        if isinstance(threads_raw, int) and not isinstance(threads_raw, bool) and threads_raw > 0
        else None
    )

    return SynthesisConfig(
        provider=provider,
        model=_as_str(block.get("model")),
        base_url=_as_str(block.get("base_url")),
        api_key_env=_as_str(block.get("api_key_env")),
        api_key_secret=_as_str(block.get("api_key_secret")),
        api_key_plaintext=_as_str(block.get("api_key_plaintext")),
        timeout_seconds=_as_float(block.get("timeout_seconds"), 30.0),
        think=bool(block.get("think")) if isinstance(block.get("think"), bool) else False,
        temperature=_as_float(block.get("temperature"), 0.0),
        max_tokens=_as_int(block.get("max_tokens"), 256),
        keep_alive=keep_alive,
        stop=stop,
        n_ctx=_as_int(block.get("n_ctx"), 2048),
        n_gpu_layers=_as_int(block.get("n_gpu_layers"), -1),
        threads=threads,
    )


def _parse_embedding(block: dict[str, Any]) -> EmbeddingConfig:
    provider = (
        normalize_provider_name(block.get("provider"), SUPPORTED_EMBEDDING_PROVIDERS)
        or DEFAULT_EMBEDDING_PROVIDER
    )
    default_model = RECOMMENDED_EMBEDDING_MODEL if provider == "fastembed_local" else None
    model = _as_str(block.get("model")) or default_model
    lazy_raw = block.get("lazy_fallback")
    return EmbeddingConfig(
        provider=provider,
        model=model,
        base_url=_as_str(block.get("base_url")),
        timeout_seconds=_as_float(block.get("timeout_seconds"), 30.0),
        lazy_fallback=bool(lazy_raw) if isinstance(lazy_raw, bool) else True,
    )


def _parse_daemon(block: dict[str, Any]) -> DaemonConfig:
    enabled_raw = block.get("enabled")
    auto_start_raw = block.get("auto_start")
    return DaemonConfig(
        enabled=bool(enabled_raw) if isinstance(enabled_raw, bool) else True,
        idle_timeout_seconds=max(30, _as_int(block.get("idle_timeout_seconds"), 900)),
        auto_start=bool(auto_start_raw) if isinstance(auto_start_raw, bool) else True,
    )


def _parse_indexing(block: dict[str, Any]) -> IndexingConfig:
    tldr_raw = block.get("enable_tldr")
    comp_raw = block.get("enable_completions")
    probe_raw = block.get("probe_help")
    return IndexingConfig(
        enable_tldr=bool(tldr_raw) if isinstance(tldr_raw, bool) else True,
        enable_completions=bool(comp_raw) if isinstance(comp_raw, bool) else True,
        probe_help=bool(probe_raw) if isinstance(probe_raw, bool) else True,
        help_probe_timeout=max(0.2, _as_float(block.get("help_probe_timeout"), 1.5)),
    )


def load_config() -> AppConfig:
    """Load configuration from ``config.toml`` (falling back to legacy ``intelligence.toml``)."""
    path = config_path()
    if not path.exists():
        legacy = legacy_intelligence_config_path()
        if legacy.exists():
            path = legacy

    raw: dict[str, Any] = {}
    if path.exists():
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            raw = {}

    synth_block = raw.get("synthesis")
    embed_block = raw.get("embedding")
    daemon_block = raw.get("daemon")
    indexing_block = raw.get("indexing")

    return AppConfig(
        synthesis=_parse_synthesis(synth_block if isinstance(synth_block, dict) else {}),
        embedding=_parse_embedding(embed_block if isinstance(embed_block, dict) else {}),
        daemon=_parse_daemon(daemon_block if isinstance(daemon_block, dict) else {}),
        indexing=_parse_indexing(indexing_block if isinstance(indexing_block, dict) else {}),
    )


def save_config(settings: AppConfig) -> Path:
    """Persist application settings to ``config.toml``, omitting ``None`` fields."""
    path = config_path()

    def strip_none(items: list[tuple[str, Any]]) -> dict[str, Any]:
        return {k: v for k, v in items if v is not None}

    clean_dict = asdict(settings, dict_factory=strip_none)
    atomic_write_text(path, tomli_w.dumps(clean_dict))
    return path


# Backward-compatible aliases
load_intelligence_config = load_config
save_intelligence_config = save_config
