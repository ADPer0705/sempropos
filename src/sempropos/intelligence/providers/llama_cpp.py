"""llama.cpp synthesis provider."""

from __future__ import annotations

import shutil
import subprocess

from sempropos import config
from sempropos.intelligence.config import ProviderRuntimeConfig
from sempropos.intelligence.contracts import (
    ProviderExecutionError,
    ProviderUnavailableError,
    SynthesisRequest,
    SynthesisResult,
)
from sempropos.intelligence.providers.base import ProviderInfo, SynthesisProvider


def _extract_command(text: str) -> str:
    for line in text.splitlines():
        cleaned = line.strip().strip("`")
        if not cleaned:
            continue
        if cleaned.lower().startswith("command:"):
            cleaned = cleaned.split(":", maxsplit=1)[1].strip()
        if cleaned:
            return cleaned
    return text.strip()


def _build_final_prompt(base_prompt: str, model_hint: str) -> str:
    lowered = model_hint.lower()
    if "qwen3" in lowered:
        return base_prompt + "\n/no_think"
    return base_prompt


class LlamaCppProvider(SynthesisProvider):
    """Provider that runs llama-cli with a local GGUF model."""

    def __init__(self, runtime: ProviderRuntimeConfig | None = None) -> None:
        self._runtime = runtime or ProviderRuntimeConfig()

    @property
    def info(self) -> ProviderInfo:
        return ProviderInfo(name="llama_cpp", local=True)

    def _executable(self) -> str | None:
        local = config.local_llama_cli_path()
        if local.exists() and local.is_file() and local.stat().st_mode & 0o111:
            return str(local)
        return shutil.which("llama-cli")

    def available(self) -> bool:
        return (
            self._executable() is not None and config.resolve_model_path() is not None
        )

    def synthesize(self, request: SynthesisRequest) -> SynthesisResult:
        executable = self._executable()
        if executable is None:
            raise ProviderUnavailableError("llama-cli not found")

        model_path = config.resolve_model_path()
        if model_path is None:
            raise ProviderUnavailableError(
                "No GGUF model found in sempropos model directory"
            )
        if self._runtime.model:
            candidate = config.models_dir() / self._runtime.model
            if candidate.exists():
                model_path = candidate

        final_prompt = _build_final_prompt(request.prompt, model_path.name)

        result = subprocess.run(
            [
                executable,
                "-m",
                str(model_path),
                "--prompt",
                final_prompt,
                "-n",
                str(request.max_tokens),
                "--temp",
                str(request.temperature),
                "-c",
                "1024",
                "--no-display-prompt",
                "--log-disable",
            ],
            check=False,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise ProviderExecutionError(stderr or "llama-cli execution failed")

        raw = result.stdout or ""
        return SynthesisResult(
            provider="llama_cpp",
            command=_extract_command(raw),
            raw_output=raw,
            metadata={"model": model_path.name},
        )
