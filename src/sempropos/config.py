"""
Unified configuration and runtime settings for sempropos.

This module manages all application-level and provider-specific configuration:
  - Application paths and data directories
  - Index metadata and file locations
  - Embedding and synthesis model specifications
  - Intelligence provider configurations and runtime settings
  - Configuration persistence (TOML-based)
"""

from __future__ import annotations

import tomllib
import tomli_w
import platformdirs
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field, asdict
from typing import Literal, get_args

from sempropos import __version__
from sempropos.utils import atomic_write_text


# ============================================================================
# Application Constants
# ============================================================================

APP_NAME = "sempropos"

# Embedding models: primary for normal RAM, floor model for constrained environments
EMBEDDING_PRIMARY_MODEL = "nomic-ai/nomic-embed-text-v1.5-Q"

# ============================================================================
# Data Files and Paths
# ============================================================================

dirs = platformdirs.PlatformDirs("sempropos", "adper")

TOOL_EMBEDDINGS_FILE = "tool_embeddings.npy"
FLAG_EMBEDDINGS_FILE = "flag_embeddings.npy"
FLAG_EMBEDDING_IDS_FILE = "flag_embedding_ids.npy"
LAST_INDEXED_FILE = "last_indexed"
INDEX_META_FILE = "index.meta"
DB_FILE = "index.db"

# ===== config files =====

INTELLIGENCE_CONFIG_FILE = "intelligence.toml"

# ============================================================================
# Information Messages
# ============================================================================

MISMATCH_UPDATE_NOTICE = (
    "[sempropos] Model/index metadata mismatch. Re-indexing required.\n"
    "Run: sempropos --update"
)
VERSION_UPDATE_NOTICE_TEMPLATE = (
    "[sempropos] Model updated in sempropos {version}. Re-indexing required.\n"
    "Run: sempropos --update"
)

# ============================================================================
# Provider Configuration Types
# ============================================================================

# ===== Provider name types =====
# Static types for Linting
SynthesisProviders = Literal[
    "tier0",
    "ollama",
]
EmbeddingProviders = Literal[
    "fastembed_local",
    "ollama",
]

# Runtime types for validation and config loading
SUPPORTED_SYNTHESIS_PROVIDERS: tuple[str, ...] = get_args(SynthesisProviders)
SUPPORTED_EMBEDDING_PROVIDERS: tuple[str, ...] = get_args(EmbeddingProviders)
SUPPORTED_PROVIDERS: tuple[str, ...] = tuple(set(SUPPORTED_SYNTHESIS_PROVIDERS + SUPPORTED_EMBEDDING_PROVIDERS))

DEFAULT_SYNTHESIS_PROVIDER = "tier0"
DEFAULT_EMBEDDING_PROVIDER = "fastembed_local"


# ===== Provider runtime configuration =====
@dataclass(frozen=True)
class ProviderRuntimeConfig:
    """
    Provider-specific runtime configuration options.

    Stores API credentials, connection details, timeout settings, and
    model specifications for a single provider.

    Attributes:
        model: Model identifier (e.g., "gpt-4", "gemma4:e2b").
        base_url: API endpoint base URL (e.g., "https://api.openai.com/v1").
        api_key_env: Environment variable name for API key (e.g., "OPENAI_API_KEY").
        api_key_secret: Keyring secret name for stored API key.
        api_key_plaintext: Plaintext API key (fallback, should use keyring).
        timeout_seconds: Request timeout in seconds.
    """

    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_key_secret: str | None = None
    api_key_plaintext: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class IntelligenceConfig:
    """
    Resolved runtime settings for synthesis and embedding providers.

    Combines selected providers, model choices, and per-provider configuration.
    Loaded from TOML and used to initialize the intelligence layer at runtime.

    Attributes:
        synthesis_provider: Selected LLM synthesis provider.
        embedding_provider: Selected embedding provider.
        providers: Dict mapping provider name to its runtime config.
    """

    synthesis_provider: str = DEFAULT_SYNTHESIS_PROVIDER
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER
    providers: dict[str, ProviderRuntimeConfig] = field(default_factory=dict)


# ============================================================================
# Application Directory Methods
# ============================================================================


def data_dir() -> Path:
    """Get the application data directory."""
    return dirs.user_data_path


def config_dir() -> Path:
    """Get the application configuration directory."""
    return dirs.user_config_path


# ===== Directory initialization =====
def ensure_data_dirs() -> None:
    """Create application data directories if missing."""
    directories = (data_dir(), config_dir())
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)


# ===== File paths =====
def db_path() -> Path:
    return data_dir() / DB_FILE


def tool_embeddings_path() -> Path:
    return data_dir() / TOOL_EMBEDDINGS_FILE


def flag_embeddings_path() -> Path:
    return data_dir() / FLAG_EMBEDDINGS_FILE


def flag_embedding_ids_path() -> Path:
    return data_dir() / FLAG_EMBEDDING_IDS_FILE


def last_indexed_path() -> Path:
    return data_dir() / LAST_INDEXED_FILE


def index_meta_path() -> Path:
    return data_dir() / INDEX_META_FILE


def intelligence_config_path() -> Path:
    return config_dir() / INTELLIGENCE_CONFIG_FILE


# ============================================================================
# Index Metadata I/O Methods
# ============================================================================


def write_index_meta(
    *,
    embedding_dim: int,
    embedding_model: str,
    embedding_provider: str,
) -> None:
    """Write index metadata file used for query-time compatibility checks."""
    destination = index_meta_path()

    payload = {
        "embedding_dim": int(embedding_dim),
        "embedding_model": embedding_model,
        "embedding_provider": embedding_provider,
        "indexed_at": datetime.now(tz=timezone.utc).isoformat(),
        "sempropos_version": __version__,
    }
    atomic_write_text(
        destination,
        tomli_w.dumps(payload),
    )

#TODO: Decide read_validated_index_meta() vs read_index_meta()
def read_index_meta() -> dict | None:
    """Read index metadata file."""
    path = index_meta_path()
    if not path.exists():
        return None

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return None

    return payload if isinstance(payload, dict) else None


def read_validated_index_meta() -> tuple[str, int, str]:
    """Return validated embedding metadata needed by query-time retrieval."""
    meta = read_index_meta()
    if meta is None:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    model_name = str(meta.get("embedding_model") or "")
    embedding_dim = meta.get("embedding_dim")
    embedding_provider = str(meta.get("embedding_provider") or "")
    sempropos_version = str(meta.get("sempropos_version") or "")

    if not model_name or embedding_dim is None or not embedding_provider:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if embedding_provider not in SUPPORTED_EMBEDDING_PROVIDERS:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    try:
        dim_value = int(embedding_dim)
    except (TypeError, ValueError):
        raise RuntimeError(MISMATCH_UPDATE_NOTICE) from None
    if dim_value <= 0:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    if sempropos_version and sempropos_version != __version__:
        raise RuntimeError(VERSION_UPDATE_NOTICE_TEMPLATE.format(version=__version__))

    settings = load_intelligence_config()
    if settings.embedding_provider != embedding_provider:
        raise RuntimeError(MISMATCH_UPDATE_NOTICE)

    return model_name, dim_value, embedding_provider


# ============================================================================
# Provider Configuration Methods
# ============================================================================


def normalize_provider_name(
    value: str | None, supported: tuple[str, ...]
) -> str | None:
    """Normalize and validate a provider name string."""
    if not value:
        return None
    lowered = value.strip().lower().replace("-", "_")
    return lowered if lowered in supported else None


def load_intelligence_config() -> IntelligenceConfig:
    """Load intelligence settings from TOML config file."""
    try:
        intelligence_config = tomllib.loads(
            intelligence_config_path().read_text(encoding="utf-8")
        )
    except (OSError, tomllib.TOMLDecodeError):
        intelligence_config = {}

    synthesis_provider = normalize_provider_name(
        intelligence_config.get("synthesis_provider"), SUPPORTED_SYNTHESIS_PROVIDERS
    )
    embedding_provider = normalize_provider_name(
        intelligence_config.get("embedding_provider"), SUPPORTED_EMBEDDING_PROVIDERS
    )

    if not synthesis_provider:
        synthesis_provider = DEFAULT_SYNTHESIS_PROVIDER
    if not embedding_provider:  
        embedding_provider = DEFAULT_EMBEDDING_PROVIDER

    raw_providers = intelligence_config.get("providers", {})
    providers = {}

    for name, block in raw_providers.items():
        if not isinstance(name, str) or name not in SUPPORTED_PROVIDERS:
            continue
        if not isinstance(block, dict):
            block = {}

        providers[name] = ProviderRuntimeConfig(
            model=(block.get("model") if isinstance(block.get("model"), str) else None),
            base_url=(
                block.get("base_url")
                if isinstance(block.get("base_url"), str)
                else None
            ),
            api_key_env=(
                block.get("api_key_env")
                if isinstance(block.get("api_key_env"), str)
                else None
            ),
            api_key_secret=(
                block.get("api_key_secret")
                if isinstance(block.get("api_key_secret"), str)
                else None
            ),
            api_key_plaintext=(
                block.get("api_key_plaintext")
                if isinstance(block.get("api_key_plaintext"), str)
                else None
            ),
            timeout_seconds=(
                float(block.get("timeout_seconds"))
                if "timeout_seconds" in block
                and isinstance(block.get("timeout_seconds"), (int, float))
                else 30.0
            ),
        )

    return IntelligenceConfig(
        synthesis_provider=synthesis_provider,
        embedding_provider=embedding_provider,
        providers=providers,
    )


def save_intelligence_config(settings: IntelligenceConfig) -> Path:
    """Persist intelligence settings to TOML file, safely dropping None values."""
    path = intelligence_config_path()

    # Dictionary factory to strip None values so tomli_w doesn't crash
    def strip_none(data):
        return {k: v for k, v in data if v is not None}

    clean_dict = asdict(settings, dict_factory=strip_none)
    toml_string = tomli_w.dumps(clean_dict)
    
    atomic_write_text(path, toml_string)
    return path