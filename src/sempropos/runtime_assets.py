"""Runtime asset installation for local binaries and models."""

from __future__ import annotations

import hashlib
import os
import platform
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path
from shutil import disk_usage, which

import requests
from tqdm import tqdm

from sempropos import config

_DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 300
_MIN_FREE_SPACE_BY_TIER = {
    "primary": 200 * 1024 * 1024,
    "floor": 100 * 1024 * 1024,
}


def _platform_asset_name() -> str:
    """Resolve the pinned llama.cpp asset name for this machine."""
    system = platform.system()
    machine = platform.machine().lower()
    alias_map = {
        "amd64": "x86_64",
        "arm64": "aarch64",
    }
    machine = alias_map.get(machine, machine)
    asset = config.LLAMA_CPP_ASSETS.get((system, machine))
    if asset is None:
        raise RuntimeError(
            f"No prebuilt llama-cli for {system}/{machine}. "
            "Install llama.cpp manually and ensure llama-cli is in PATH."
        )
    return asset


def _download_timeout_seconds() -> int:
    """Resolve download timeout in seconds from environment."""
    raw = os.environ.get("SEMPROPOS_DOWNLOAD_TIMEOUT", "").strip()
    if not raw:
        return _DEFAULT_DOWNLOAD_TIMEOUT_SECONDS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_DOWNLOAD_TIMEOUT_SECONDS
    return value if value > 0 else _DEFAULT_DOWNLOAD_TIMEOUT_SECONDS


def _download_to_file(url: str, output_path: Path, progress: bool) -> None:
    """Stream-download a URL to output_path with optional progress reporting."""
    response = requests.get(
        url,
        stream=True,
        timeout=_download_timeout_seconds(),
    )
    response.raise_for_status()

    total = int(response.headers.get("content-length", 0) or 0)
    with output_path.open("wb") as file_obj:
        with tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=f"Downloading {output_path.name}",
            disable=not progress,
        ) as bar:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if not chunk:
                    continue
                file_obj.write(chunk)
                bar.update(len(chunk))


def _verify_sha256(path: Path, expected: str) -> bool:
    """Verify a file hash against the pinned SHA-256 digest."""
    if not expected:
        # Development mode while hashes are being pinned.
        return True

    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest() == expected


def _detect_ollama_compatible_model() -> str | None:
    """Return a compatible local Ollama model if one is available."""
    try:
        response = requests.get("http://localhost:11434/api/tags", timeout=1.0)
        response.raise_for_status()
        payload = response.json()
    except Exception:  # noqa: BLE001
        return None

    models = [
        item.get("name") for item in payload.get("models", []) if item.get("name")
    ]
    return config.select_ollama_model(models)


def _select_synthesis_tier(use_floor_model: bool) -> str:
    """Choose synthesis model tier using CLI override and RAM thresholds."""
    if use_floor_model:
        return "floor"

    try:
        import psutil  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        return "primary"

    available = int(psutil.virtual_memory().available)
    if available < config.SYNTHESIS_LOW_RAM_THRESHOLD_BYTES:
        print(config.INSTALL_FLOOR_NOTICE)
        return "floor"
    return "primary"


def _extract_llama_cli(archive_path: Path, output_path: Path) -> None:
    """Extract llama-cli from a downloaded archive and install it to output_path."""
    with tempfile.TemporaryDirectory(prefix="sempropos-llama-") as temp_dir:
        extract_dir = Path(temp_dir)

        if archive_path.name.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as zip_file:
                zip_file.extractall(extract_dir)
        elif archive_path.name.endswith(".tar.gz"):
            with tarfile.open(archive_path, mode="r:gz") as tar_file:
                tar_file.extractall(extract_dir)
        else:
            raise RuntimeError(f"Unsupported llama archive format: {archive_path.name}")

        matches = list(extract_dir.rglob("llama-cli"))
        if not matches:
            raise RuntimeError(
                "Downloaded llama.cpp archive does not contain llama-cli"
            )

        source = matches[0]
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(source.read_bytes())

    current_mode = output_path.stat().st_mode
    output_path.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def ensure_llama_cli(progress: bool = True) -> Path:
    """Ensure a local llama-cli binary exists in sempropos data dir."""
    config.ensure_data_dirs()
    local_path = config.local_llama_cli_path()

    if local_path.exists() and local_path.stat().st_mode & stat.S_IXUSR:
        return local_path

    asset_name = _platform_asset_name()
    asset_url = (
        "https://github.com/ggerganov/llama.cpp/releases/download/"
        f"{config.LLAMA_CPP_RELEASE}/{asset_name}"
    )

    asseturl_lower = asset_url.lower()
    if asseturl_lower.endswith(".zip"):
        suffix = ".zip"
    elif asseturl_lower.endswith(".tar.gz"):
        suffix = ".tar.gz"
    else:
        suffix = ".tmp"

    with tempfile.NamedTemporaryFile(
        prefix="sempropos-llama-", suffix=suffix, delete=False
    ) as temp_file:
        archive_path = Path(temp_file.name)

    try:
        _download_to_file(asset_url, archive_path, progress=progress)
        _extract_llama_cli(archive_path, local_path)
    finally:
        archive_path.unlink(missing_ok=True)

    return local_path


def ensure_model(tier: str = "primary", progress: bool = True) -> Path:
    """Ensure the selected GGUF synthesis model exists and matches SHA policy."""
    config.ensure_data_dirs()
    spec = config.synthesis_model_spec(tier)
    destination = config.models_dir() / spec["filename"]

    required = _MIN_FREE_SPACE_BY_TIER.get(tier, _MIN_FREE_SPACE_BY_TIER["primary"])
    free_space = disk_usage(config.models_dir()).free
    if free_space < required:
        raise RuntimeError(
            "Insufficient disk space for model download. "
            f"Need at least {required // (1024 * 1024)} MiB free."
        )

    if destination.exists() and _verify_sha256(destination, spec["sha256"]):
        return destination
    if destination.exists() and not _verify_sha256(destination, spec["sha256"]):
        destination.unlink(missing_ok=True)

    try:
        from huggingface_hub import hf_hub_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "huggingface_hub is required for model download. "
            "Install dependencies and retry sempropos --install."
        ) from exc

    path = hf_hub_download(
        repo_id=spec["repo_id"],
        filename=spec["filename"],
        local_dir=str(config.models_dir()),
    )

    model_path = Path(path)
    if not _verify_sha256(model_path, spec["sha256"]):
        model_path.unlink(missing_ok=True)
        raise RuntimeError("Model download corrupted. Re-run sempropos --install.")

    if progress:
        print(f"[sempropos] Model ready: {path}")
    return model_path


def ensure_runtime_assets(
    progress: bool = True,
    *,
    use_floor_model: bool = False,
) -> dict[str, str]:
    """Install/resolve local runtime assets needed for synthesis and indexing."""
    tier = _select_synthesis_tier(use_floor_model=use_floor_model)

    ollama_model = _detect_ollama_compatible_model()
    if ollama_model is not None:
        if progress:
            print(
                "[sempropos] Ollama detected with compatible model. Skipping model download."
            )
        return {
            "backend": "ollama",
            "synthesis_tier": tier,
            "synthesis_model": ollama_model,
        }

    local_cli_ready = False

    if progress:
        print("[sempropos] Ensuring local llama.cpp runtime...")
    try:
        llama_path = ensure_llama_cli(progress=progress)
        local_cli_ready = True
        if progress:
            print(f"[sempropos] llama-cli ready: {llama_path}")
    except (RuntimeError, OSError, requests.RequestException) as exc:
        if which("llama-cli"):
            local_cli_ready = True
            if progress:
                print("[sempropos] Using llama-cli from PATH.")
        elif progress:
            print(f"[sempropos] Unable to install llama-cli: {exc}")

    if progress:
        print(f"[sempropos] Ensuring local GGUF model ({tier})...")
    model_path: Path | None = None
    try:
        model_path = ensure_model(tier=tier, progress=progress)
        if progress:
            print(f"[sempropos] Model ready: {model_path}")
    except (RuntimeError, OSError, requests.RequestException) as exc:
        if progress:
            print(f"[sempropos] Unable to download synthesis model: {exc}")

    if local_cli_ready and model_path is not None:
        return {
            "backend": "llama_cpp",
            "synthesis_tier": tier,
            "synthesis_model": model_path.name,
        }

    return {
        "backend": "tier0",
        "synthesis_tier": tier,
        "synthesis_model": model_path.name if model_path is not None else "",
    }
