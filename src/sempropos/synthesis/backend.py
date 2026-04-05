"""Local synthesis backend detection and execution."""

from __future__ import annotations

import shutil
import socket
import subprocess

import requests

from sempropos import config


def _local_llama_cli() -> str | None:
    """Resolve an executable llama-cli path from local data dir or PATH."""
    local = config.local_llama_cli_path()
    if local.exists() and local.is_file() and local.stat().st_mode & 0o111:
        return str(local)

    in_path = shutil.which("llama-cli")
    if in_path:
        return in_path

    return None


def detect() -> str:
    """Detect available synthesis backend."""
    if _local_llama_cli() is not None:
        return "llama_cpp"

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.2)
    try:
        sock.connect(("127.0.0.1", 11434))
        return "ollama"
    except OSError:
        return "tier0"
    finally:
        sock.close()


def _extract_command(text: str) -> str:
    """Extract the first command-like line from model output text."""
    for line in text.splitlines():
        cleaned = line.strip().strip("`")
        if not cleaned:
            continue
        if cleaned.lower().startswith("command:"):
            cleaned = cleaned.split(":", maxsplit=1)[1].strip()
        if cleaned:
            return cleaned
    return text.strip()


def _run_llama_cpp(prompt: str) -> str:
    """Run local llama-cli against the configured GGUF model."""
    executable = _local_llama_cli()
    if executable is None:
        raise RuntimeError("llama-cli not found")

    model_path = config.resolve_model_path()
    if model_path is None:
        raise RuntimeError("No GGUF model found in local sempropos models directory")

    result = subprocess.run(
        [
            executable,
            "-m",
            str(model_path),
            "--prompt",
            prompt,
            "-n",
            "80",
            "--temp",
            "0.1",
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
        raise RuntimeError(stderr or "llama-cli execution failed")

    return _extract_command(result.stdout)


def _select_ollama_model() -> str:
    """Pick a preferred Qwen Ollama model, with safe fallbacks."""
    response = requests.get("http://localhost:11434/api/tags", timeout=2.0)
    response.raise_for_status()
    payload = response.json()

    models = [item.get("name") for item in payload.get("models", []) if item.get("name")]
    preferred = [
        "qwen2.5:1.5b",
        "qwen2.5:0.5b",
    ]

    for candidate in preferred:
        if candidate in models:
            return candidate

    for model in models:
        if model.startswith("qwen2.5:1.5b"):
            return model
    for model in models:
        if model.startswith("qwen2.5:0.5b"):
            return model

    if not models:
        raise RuntimeError("No Ollama models available")

    return models[0]


def _run_ollama(prompt: str) -> str:
    """Run prompt generation through the local Ollama HTTP API."""
    model = _select_ollama_model()

    response = requests.post(
        "http://localhost:11434/api/generate",
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.1,
                "num_predict": 80,
            },
        },
        timeout=30.0,
    )
    response.raise_for_status()
    payload = response.json()
    return _extract_command(payload.get("response", ""))


def run(prompt: str, backend: str) -> str:
    """Run synthesis using the selected backend."""
    if backend == "llama_cpp":
        return _run_llama_cpp(prompt)
    if backend == "ollama":
        return _run_ollama(prompt)
    if backend == "tier0":
        return ""
    raise ValueError(f"Unknown backend: {backend}")
