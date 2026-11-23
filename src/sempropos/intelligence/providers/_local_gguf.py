"""Built-in managed local GGUF synthesis provider with grammar-constrained JSON output.

Supports two zero-daemon drivers automatically:
  1. **In-process ``llama-cpp-python``** (when installed via ``sempropos[local-llm]``).
  2. **Managed ``llama-server`` binary** (on ``$PATH`` or ``data_dir()/bin/llama-server``),
     spawned privately on an ephemeral loopback port and cleanly terminated on shutdown.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sempropos.config import SynthesisConfig
from sempropos.intelligence.contracts import (
    ProviderConfigurationError,
    ProviderExecutionError,
    ProviderUnavailableError,
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.intelligence.models import (
    find_llama_server_binary,
    has_llama_cpp_python,
    installed_model_names,
    local_runtime_kind,
    resolve_model_path,
)
from sempropos.intelligence.prompt import context_to_str, parse_synthesis_output

# In-process llama_cpp singleton cache (kept warm inside the daemon)
_LLAMA_INSTANCE: Any = None
_LLAMA_MODEL_PATH: Path | None = None

# Managed llama-server child process cache
_SERVER_PROC: subprocess.Popen[bytes] | None = None
_SERVER_PORT: int | None = None
_SERVER_MODEL_PATH: Path | None = None


def is_available(role_config: SynthesisConfig | None = None) -> bool:
    """Return True when a local GGUF runtime AND at least one model file are present."""
    if local_runtime_kind() is None:
        return False
    model_id = role_config.model if role_config is not None else None
    return resolve_model_path(model_id) is not None


def get_available_models() -> list[str]:
    """Return identifiers of all locally downloaded GGUF models."""
    return installed_model_names()


def is_model_loaded() -> bool:
    """Return True when a local GGUF model is warm in memory or managed server."""
    if _LLAMA_INSTANCE is not None:
        return True
    return _SERVER_PROC is not None and _SERVER_PROC.poll() is None


def unload_model() -> None:
    """Unload in-process model and terminate any managed ``llama-server`` child."""
    global _LLAMA_INSTANCE, _LLAMA_MODEL_PATH, _SERVER_PROC, _SERVER_PORT, _SERVER_MODEL_PATH

    _LLAMA_INSTANCE = None
    _LLAMA_MODEL_PATH = None

    if _SERVER_PROC is not None:
        with contextlib.suppress(OSError):
            _SERVER_PROC.terminate()
            _SERVER_PROC.wait(timeout=3)
        if _SERVER_PROC.poll() is None:
            with contextlib.suppress(OSError):
                _SERVER_PROC.kill()
                _SERVER_PROC.wait(timeout=2)
    _SERVER_PROC = None
    _SERVER_PORT = None
    _SERVER_MODEL_PATH = None


shutdown = unload_model
atexit.register(unload_model)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _get_inprocess_llama(model_path: Path, options: SynthesisOptions) -> Any:
    global _LLAMA_INSTANCE, _LLAMA_MODEL_PATH
    if _LLAMA_INSTANCE is not None and model_path == _LLAMA_MODEL_PATH:
        return _LLAMA_INSTANCE

    try:
        from llama_cpp import Llama  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ProviderUnavailableError(
            "llama-cpp-python is not installed. Install with `pip install 'sempropos[local-llm]'` "
            "or install `llama-server`."
        ) from exc

    n_threads = options.threads or min(os.cpu_count() or 4, 8)
    _LLAMA_INSTANCE = Llama(
        model_path=str(model_path),
        n_ctx=options.n_ctx,
        n_gpu_layers=options.n_gpu_layers,
        n_threads=n_threads,
        verbose=False,
    )
    _LLAMA_MODEL_PATH = model_path
    return _LLAMA_INSTANCE


def _ensure_llama_server(model_path: Path, options: SynthesisOptions) -> str:
    """Start (or reuse) a private local ``llama-server`` child process and return its base URL."""
    global _SERVER_PROC, _SERVER_PORT, _SERVER_MODEL_PATH

    if (
        _SERVER_PROC is not None
        and _SERVER_PROC.poll() is None
        and model_path == _SERVER_MODEL_PATH
        and _SERVER_PORT is not None
    ):
        return f"http://127.0.0.1:{_SERVER_PORT}"

    unload_model()
    binary = find_llama_server_binary()
    if binary is None:
        raise ProviderUnavailableError(
            "Neither `llama-cpp-python` nor `llama-server` was found on this machine."
        )

    port = _find_free_port()
    n_threads = options.threads or min(os.cpu_count() or 4, 8)
    cmd = [
        str(binary),
        "--model",
        str(model_path),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--ctx-size",
        str(options.n_ctx),
        "--threads",
        str(n_threads),
        "--log-disable",
    ]
    if options.n_gpu_layers >= 0:
        cmd.extend(["--n-gpu-layers", str(options.n_gpu_layers)])

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ProviderExecutionError(f"Failed to launch llama-server: {exc}") from exc

    _SERVER_PROC = proc
    _SERVER_PORT = port
    _SERVER_MODEL_PATH = model_path
    base_url = f"http://127.0.0.1:{port}"

    # Poll /health until ready (bounded timeout).
    deadline = time.monotonic() + min(options.timeout_seconds, 20.0)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            unload_model()
            raise ProviderExecutionError(
                f"llama-server exited prematurely with status {proc.returncode}."
            )
        try:
            req = urllib.request.Request(f"{base_url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=0.5) as resp:
                if resp.status == 200:
                    return base_url
        except Exception:  # noqa: BLE001
            time.sleep(0.15)

    unload_model()
    raise ProviderExecutionError("Timed out waiting for local llama-server to become ready.")


def _synthesize_inprocess(
    prompt: StructuredPrompt,
    model_path: Path,
    options: SynthesisOptions,
) -> str:
    llm = _get_inprocess_llama(model_path, options)
    formatted_context = context_to_str(prompt.context)

    kwargs: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
            },
        ],
        "temperature": options.temperature,
        "max_tokens": options.max_tokens,
    }
    if options.stop:
        kwargs["stop"] = options.stop
    if options.structured_output and prompt.json_schema is not None:
        kwargs["response_format"] = {
            "type": "json_object",
            "schema": prompt.json_schema,
        }

    try:
        response = llm.create_chat_completion(**kwargs)
        return str(response["choices"][0]["message"]["content"]).strip()
    except Exception as exc:  # noqa: BLE001
        raise ProviderExecutionError(f"local_gguf in-process synthesis failed: {exc}") from exc


def _synthesize_via_server(
    prompt: StructuredPrompt,
    model_path: Path,
    options: SynthesisOptions,
) -> str:
    base_url = _ensure_llama_server(model_path, options)
    formatted_context = context_to_str(prompt.context)
    body: dict[str, Any] = {
        "messages": [
            {"role": "system", "content": prompt.system},
            {
                "role": "user",
                "content": f"Context:\n{formatted_context}\n\nTask: {prompt.query}",
            },
        ],
        "temperature": options.temperature,
        "max_tokens": options.max_tokens,
        "stream": False,
    }
    if options.stop:
        body["stop"] = options.stop
    if options.structured_output and prompt.json_schema is not None:
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "sem_command", "schema": prompt.json_schema},
        }

    req = urllib.request.Request(
        f"{base_url}/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=options.timeout_seconds) as resp:
            payload = json.load(resp)
        return str(payload["choices"][0]["message"]["content"]).strip()
    except (urllib.error.URLError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise ProviderExecutionError(f"local_gguf server synthesis failed: {exc}") from exc


def warm_model(model: str | None = None, options: SynthesisOptions | None = None) -> str:
    """Load the configured local GGUF model into memory / child server ahead of queries."""
    model_path = resolve_model_path(model)
    if model_path is None:
        raise ProviderConfigurationError("No local GGUF model file installed.")
    opts = options or SynthesisOptions()
    if has_llama_cpp_python():
        _get_inprocess_llama(model_path, opts)
        return f"loaded {model_path.name} (in-process)"
    _ensure_llama_server(model_path, opts)
    return f"loaded {model_path.name} (llama-server)"


def synthesize(
    prompt: StructuredPrompt,
    model: str | None = None,
    options: SynthesisOptions | None = None,
) -> SynthesisResult:
    """Generate a structured shell command using the managed local GGUF model."""
    runtime = local_runtime_kind()
    if runtime is None:
        raise ProviderUnavailableError(
            "No local GGUF runtime installed. Install `sempropos[local-llm]` or `llama-server`."
        )

    model_path = resolve_model_path(model)
    if model_path is None:
        raise ProviderConfigurationError(
            "No local GGUF model found. Run `sem model install balanced`."
        )

    opts = options or SynthesisOptions()
    raw = (
        _synthesize_inprocess(prompt, model_path, opts)
        if runtime == "llama_cpp"
        else _synthesize_via_server(prompt, model_path, opts)
    )
    parsed = parse_synthesis_output(raw)
    return SynthesisResult(
        provider="local_gguf",
        command=parsed.command or "",
        raw_output=raw,
        tool=parsed.tool,
        explanation=parsed.explanation,
        alternatives=parsed.alternatives,
        metadata={"model": model_path.name, "runtime": runtime},
    )
