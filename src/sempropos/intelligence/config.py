"""Persistent configuration for intelligence providers."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sempropos import config as app_config
from sempropos.intelligence.contracts import (
    EmbeddingProviderName,
    SynthesisProviderName,
)

try:
    import tomllib  # type: ignore[attr-defined]
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]


SUPPORTED_SYNTHESIS_PROVIDERS: tuple[SynthesisProviderName, ...] = (
    "llama_cpp",
    "ollama",
    "openai_compatible",
    "anthropic",
    "gemini",
    "mistral",
    "huggingface",
    "tier0",
)
SUPPORTED_EMBEDDING_PROVIDERS: tuple[EmbeddingProviderName, ...] = (
    "fastembed_local",
    "openai_compatible",
    "huggingface",
)

DEFAULT_SYNTHESIS_PROVIDER: SynthesisProviderName = "tier0"
DEFAULT_EMBEDDING_PROVIDER: EmbeddingProviderName = "fastembed_local"


@dataclass(frozen=True)
class ProviderRuntimeConfig:
    """Provider-specific runtime options loaded from persisted config."""

    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    api_key_secret: str | None = None
    api_key_plaintext: str | None = None
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class IntelligenceSettings:
    """Resolved runtime settings for synthesis and embedding providers."""

    synthesis_provider: SynthesisProviderName = DEFAULT_SYNTHESIS_PROVIDER
    embedding_provider: EmbeddingProviderName = DEFAULT_EMBEDDING_PROVIDER
    embedding_model: str | None = None
    providers: dict[str, ProviderRuntimeConfig] = field(default_factory=dict)


def _default_config_path() -> Path:
    override = os.environ.get("SEMPROPOS_INTELLIGENCE_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path("~/.config/sempropos/intelligence.toml").expanduser()


def config_path() -> Path:
    """Return intelligence config path."""
    return _default_config_path()


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists() or tomllib is None:
        return {}
    try:
        with path.open("rb") as file_obj:
            payload = tomllib.load(file_obj)
    except OSError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _to_synthesis_provider_name(value: str | None) -> SynthesisProviderName | None:
    if not value:
        return None
    lowered = value.strip().lower().replace("-", "_")
    if lowered in SUPPORTED_SYNTHESIS_PROVIDERS:
        return lowered  # type: ignore[return-value]
    return None


def _to_embedding_provider_name(value: str | None) -> EmbeddingProviderName | None:
    if not value:
        return None
    lowered = value.strip().lower().replace("-", "_")
    if lowered in SUPPORTED_EMBEDDING_PROVIDERS:
        return lowered  # type: ignore[return-value]
    return None


def _provider_cfg(payload: dict[str, Any], key: str) -> ProviderRuntimeConfig:
    providers_section = payload.get("providers")
    if not isinstance(providers_section, dict):
        providers_section = {}

    block = providers_section.get(key)
    if not isinstance(block, dict):
        block = {}

    timeout = block.get("timeout_seconds", 30.0)
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError):
        timeout_value = 30.0

    return ProviderRuntimeConfig(
        model=block.get("model") if isinstance(block.get("model"), str) else None,
        base_url=block.get("base_url")
        if isinstance(block.get("base_url"), str)
        else None,
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
        timeout_seconds=timeout_value,
    )


def _merge_provider_cfg(
    base: ProviderRuntimeConfig,
    override: ProviderRuntimeConfig,
) -> ProviderRuntimeConfig:
    return ProviderRuntimeConfig(
        model=override.model if override.model is not None else base.model,
        base_url=override.base_url if override.base_url is not None else base.base_url,
        api_key_env=(
            override.api_key_env
            if override.api_key_env is not None
            else base.api_key_env
        ),
        api_key_secret=(
            override.api_key_secret
            if override.api_key_secret is not None
            else base.api_key_secret
        ),
        api_key_plaintext=(
            override.api_key_plaintext
            if override.api_key_plaintext is not None
            else base.api_key_plaintext
        ),
        timeout_seconds=(
            override.timeout_seconds
            if override.timeout_seconds != 30.0 or base.timeout_seconds == 30.0
            else base.timeout_seconds
        ),
    )


def _default_provider_configs() -> dict[str, ProviderRuntimeConfig]:
    return {
        "llama_cpp": ProviderRuntimeConfig(),
        "ollama": ProviderRuntimeConfig(base_url="http://localhost:11434"),
        "openai_compatible": ProviderRuntimeConfig(
            base_url="https://api.openai.com/v1",
            api_key_env="OPENAI_API_KEY",
        ),
        "anthropic": ProviderRuntimeConfig(
            base_url="https://api.anthropic.com/v1",
            api_key_env="ANTHROPIC_API_KEY",
        ),
        "gemini": ProviderRuntimeConfig(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key_env="GEMINI_API_KEY",
        ),
        "mistral": ProviderRuntimeConfig(
            base_url="https://api.mistral.ai/v1",
            api_key_env="MISTRAL_API_KEY",
        ),
        "huggingface": ProviderRuntimeConfig(
            base_url="https://api-inference.huggingface.co/models",
            api_key_env="HF_TOKEN",
        ),
        "tier0": ProviderRuntimeConfig(),
    }


def load_settings() -> IntelligenceSettings:
    """Load intelligence settings from persisted TOML config."""
    payload = _load_toml(_default_config_path())

    intelligence_section = payload.get("intelligence")
    if not isinstance(intelligence_section, dict):
        intelligence_section = {}

    embedding_section = payload.get("embeddings")
    if not isinstance(embedding_section, dict):
        embedding_section = {}

    synthesis_provider = (
        _to_synthesis_provider_name(intelligence_section.get("synthesis_provider"))
        or DEFAULT_SYNTHESIS_PROVIDER
    )
    embedding_provider = (
        _to_embedding_provider_name(
            intelligence_section.get("embedding_provider")
            or embedding_section.get("provider")
        )
        or DEFAULT_EMBEDDING_PROVIDER
    )

    embedding_model: str | None = None
    raw_embedding_model = intelligence_section.get(
        "embedding_model"
    ) or embedding_section.get("model")
    if isinstance(raw_embedding_model, str) and raw_embedding_model.strip():
        embedding_model = raw_embedding_model.strip()

    defaults = _default_provider_configs()
    providers: dict[str, ProviderRuntimeConfig] = {}
    for provider_name, default_cfg in defaults.items():
        override_cfg = _provider_cfg(payload, provider_name)
        providers[provider_name] = _merge_provider_cfg(default_cfg, override_cfg)

    return IntelligenceSettings(
        synthesis_provider=synthesis_provider,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        providers=providers,
    )


def _settings_to_toml(settings: IntelligenceSettings) -> str:
    lines: list[str] = [
        "[intelligence]",
        f'synthesis_provider = "{settings.synthesis_provider}"',
        f'embedding_provider = "{settings.embedding_provider}"',
    ]
    if settings.embedding_model:
        lines.append(f'embedding_model = "{settings.embedding_model}"')

    lines.extend(
        [
            "",
            "[embeddings]",
            f'provider = "{settings.embedding_provider}"',
        ]
    )
    if settings.embedding_model:
        lines.append(f'model = "{settings.embedding_model}"')

    for provider_name in SUPPORTED_SYNTHESIS_PROVIDERS:
        provider = settings.providers.get(provider_name, ProviderRuntimeConfig())
        lines.extend(["", f"[providers.{provider_name}]"])
        if provider.model:
            lines.append(f'model = "{provider.model}"')
        if provider.base_url:
            lines.append(f'base_url = "{provider.base_url}"')
        if provider.api_key_env:
            lines.append(f'api_key_env = "{provider.api_key_env}"')
        if provider.api_key_secret:
            lines.append(f'api_key_secret = "{provider.api_key_secret}"')
        if provider.api_key_plaintext:
            lines.append(f'api_key_plaintext = "{provider.api_key_plaintext}"')
        lines.append(f"timeout_seconds = {provider.timeout_seconds}")

    lines.append("")
    return "\n".join(lines)


def save_settings(settings: IntelligenceSettings) -> Path:
    """Persist intelligence settings to TOML."""
    path = _default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_settings_to_toml(settings), encoding="utf-8")
    return path


def update_settings(
    *,
    synthesis_provider: SynthesisProviderName | None = None,
    embedding_provider: EmbeddingProviderName | None = None,
    embedding_model: str | None = None,
) -> IntelligenceSettings:
    """Update top-level settings and persist them."""
    current = load_settings()
    updated = IntelligenceSettings(
        synthesis_provider=synthesis_provider or current.synthesis_provider,
        embedding_provider=embedding_provider or current.embedding_provider,
        embedding_model=embedding_model
        if embedding_model is not None
        else current.embedding_model,
        providers=current.providers,
    )
    save_settings(updated)
    return updated


def _replace_provider(
    settings: IntelligenceSettings,
    provider_name: str,
    provider_cfg: ProviderRuntimeConfig,
) -> IntelligenceSettings:
    providers = dict(settings.providers)
    providers[provider_name] = provider_cfg
    return IntelligenceSettings(
        synthesis_provider=settings.synthesis_provider,
        embedding_provider=settings.embedding_provider,
        embedding_model=settings.embedding_model,
        providers=providers,
    )


def update_provider_config(
    provider_name: str,
    *,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    timeout_seconds: float | None = None,
    api_key_plaintext: str | None = None,
    api_key_secret: str | None = None,
) -> IntelligenceSettings:
    """Update and persist provider runtime settings."""
    if provider_name not in SUPPORTED_SYNTHESIS_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider_name}")

    current = load_settings()
    base = current.providers.get(provider_name, ProviderRuntimeConfig())
    updated_provider = ProviderRuntimeConfig(
        model=model if model is not None else base.model,
        base_url=base_url if base_url is not None else base.base_url,
        api_key_env=api_key_env if api_key_env is not None else base.api_key_env,
        api_key_secret=api_key_secret
        if api_key_secret is not None
        else base.api_key_secret,
        api_key_plaintext=(
            api_key_plaintext
            if api_key_plaintext is not None
            else base.api_key_plaintext
        ),
        timeout_seconds=(
            timeout_seconds if timeout_seconds is not None else base.timeout_seconds
        ),
    )
    updated = _replace_provider(current, provider_name, updated_provider)
    save_settings(updated)
    return updated


def _is_headless() -> bool:
    return not (sys.stdin.isatty() and sys.stdout.isatty())


def _keyring_store(secret_name: str, value: str) -> bool:
    try:
        import keyring  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return False

    try:
        keyring.set_password("sempropos", secret_name, value)
    except Exception:  # noqa: BLE001
        return False
    return True


def _keyring_load(secret_name: str) -> str | None:
    try:
        import keyring  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return None

    try:
        value = keyring.get_password("sempropos", secret_name)
    except Exception:  # noqa: BLE001
        return None
    if value and value.strip():
        return value.strip()
    return None


def store_provider_api_key(provider_name: str, api_key: str) -> str:
    """Store provider API key using keyring when possible, else config fallback."""
    if provider_name not in SUPPORTED_SYNTHESIS_PROVIDERS:
        raise ValueError(f"Unsupported provider: {provider_name}")

    secret_name = f"sempropos.{provider_name}.api_key"
    if not _is_headless() and _keyring_store(secret_name, api_key):
        update_provider_config(
            provider_name,
            api_key_secret=secret_name,
            api_key_plaintext=None,
        )
        return "keyring"

    update_provider_config(
        provider_name,
        api_key_plaintext=api_key,
        api_key_secret=None,
    )
    return "config"


def resolve_provider_api_key(
    runtime: ProviderRuntimeConfig, default_env: str
) -> str | None:
    """Resolve API key from env var, keyring, then persisted plaintext fallback."""
    env_name = runtime.api_key_env or default_env
    env_value = os.environ.get(env_name)
    if env_value and env_value.strip():
        return env_value.strip()

    if runtime.api_key_secret:
        secret_value = _keyring_load(runtime.api_key_secret)
        if secret_value:
            return secret_value

    if runtime.api_key_plaintext and runtime.api_key_plaintext.strip():
        return runtime.api_key_plaintext.strip()

    return None


def supported_synthesis_providers() -> tuple[SynthesisProviderName, ...]:
    """Return supported synthesis providers."""
    return SUPPORTED_SYNTHESIS_PROVIDERS


def supported_embedding_providers() -> tuple[EmbeddingProviderName, ...]:
    """Return supported embedding providers."""
    return SUPPORTED_EMBEDDING_PROVIDERS


def synthesis_provider_from_text(value: str) -> SynthesisProviderName | None:
    """Parse synthesis provider name from user input."""
    return _to_synthesis_provider_name(value)


def embedding_provider_from_text(value: str) -> EmbeddingProviderName | None:
    """Parse embedding provider name from user input."""
    return _to_embedding_provider_name(value)


def default_embedding_model(force_floor: bool = False) -> str:
    """Return default local embedding model."""
    if force_floor:
        return app_config.EMBEDDING_FLOOR_MODEL
    return app_config.EMBEDDING_PRIMARY_MODEL
