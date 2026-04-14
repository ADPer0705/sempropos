"""sempropos package."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _fallback_version() -> str:
    """Best-effort version fallback for editable/dev execution contexts."""
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.exists():
        return "0.0.0.dev+unknown"

    try:
        try:
            import tomllib  # type: ignore[attr-defined]
        except ModuleNotFoundError:
            import tomli as tomllib  # type: ignore[import-not-found]

        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        project = payload.get("project") if isinstance(payload, dict) else {}
        version_value = project.get("version") if isinstance(project, dict) else None
        if isinstance(version_value, str) and version_value.strip():
            return version_value.strip()
    except Exception:  # noqa: BLE001
        return "0.0.0.dev+unknown"

    return "0.0.0.dev+unknown"


try:
    __version__ = version("sempropos")
except PackageNotFoundError:
    __version__ = _fallback_version()
