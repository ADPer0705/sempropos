"""Runtime asset installation for local binaries and models."""

from __future__ import annotations

import platform
import stat
import tarfile
import tempfile
import zipfile
from pathlib import Path

import requests
from tqdm import tqdm

from sempropos import config


def _linux_arch_patterns() -> tuple[str, ...]:
    """Return candidate architecture aliases used in llama.cpp release assets."""
    if platform.system().lower() != "linux":
        raise RuntimeError("sempropos currently supports Linux only")

    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64"}:
        return ("x86_64", "x64", "amd64")
    if machine in {"aarch64", "arm64"}:
        return ("aarch64", "arm64")

    raise RuntimeError(f"Unsupported CPU architecture: {machine}")


def _download_to_file(url: str, output_path: Path, progress: bool) -> None:
    """Stream-download a URL to output_path with optional progress reporting."""
    response = requests.get(url, stream=True, timeout=60)
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


def _pick_llama_asset(assets: list[dict], arch_aliases: tuple[str, ...]) -> str:
    """Choose the best matching Linux llama.cpp release archive URL."""
    candidates: list[tuple[int, str]] = []

    for item in assets:
        name = str(item.get("name") or "")
        url = str(item.get("browser_download_url") or "")
        lowered = name.lower()
        if not url:
            continue
        if not (lowered.endswith(".zip") or lowered.endswith(".tar.gz")):
            continue
        if "linux" not in lowered and "ubuntu" not in lowered:
            continue
        if not any(alias in lowered for alias in arch_aliases):
            continue

        score = 0
        if "bin" in lowered:
            score += 2
        if "cuda" not in lowered:
            score += 1
        candidates.append((score, url))

    if not candidates:
        raise RuntimeError("No matching llama.cpp Linux binary found for this architecture")

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


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
            raise RuntimeError("Downloaded llama.cpp archive does not contain llama-cli")

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

    arch_aliases = _linux_arch_patterns()
    release = requests.get(config.LLAMA_RELEASES_API, timeout=30)
    release.raise_for_status()
    payload = release.json()

    asset_url = _pick_llama_asset(payload.get("assets", []), arch_aliases)

    with tempfile.NamedTemporaryFile(prefix="sempropos-llama-", suffix=".tmp", delete=False) as temp_file:
        archive_path = Path(temp_file.name)

    try:
        _download_to_file(asset_url, archive_path, progress=progress)
        _extract_llama_cli(archive_path, local_path)
    finally:
        archive_path.unlink(missing_ok=True)

    return local_path


def ensure_model(progress: bool = True) -> Path:
    """Ensure the preferred GGUF model exists in sempropos data dir."""
    config.ensure_data_dirs()
    existing = config.resolve_model_path()
    if existing is not None:
        return existing

    try:
        from huggingface_hub import hf_hub_download
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "huggingface_hub is required for model download. "
            "Install dependencies and retry sempropos --install."
        ) from exc

    path = hf_hub_download(
        repo_id=config.MODEL_REPO_ID,
        filename=config.MODEL_FILENAME,
        local_dir=str(config.models_dir()),
    )

    if progress:
        print(f"[sempropos] Model ready: {path}")
    return Path(path)


def ensure_runtime_assets(progress: bool = True) -> None:
    """Install local runtime assets needed for synthesis and indexing."""
    if progress:
        print("[sempropos] Ensuring local llama.cpp runtime...")
    llama_path = ensure_llama_cli(progress=progress)
    if progress:
        print(f"[sempropos] llama-cli ready: {llama_path}")

    if progress:
        print("[sempropos] Ensuring local GGUF model...")
    model_path = ensure_model(progress=progress)
    if progress:
        print(f"[sempropos] Model ready: {model_path}")
