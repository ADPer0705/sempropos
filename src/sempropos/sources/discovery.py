"""Fast PATH binary discovery and symlink alias resolution."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Multi-call dispatchers or generic interpreters whose symlinks are distinct
# tools (e.g. `ls -> coreutils` or `modprobe -> kmod`) rather than aliases.
_MULTICALL_TARGETS = frozenset(
    {
        "busybox",
        "toybox",
        "coreutils",
        "kmod",
        "systemd",
        "systemctl",
        "env",
        "sh",
        "bash",
        "dash",
        "zsh",
        "fish",
        "python",
        "python3",
        "perl",
        "ruby",
        "node",
        "bun",
        "deno",
        "java",
        "flatpak",
        "snap",
        "distrobox-host-exec",
    }
)


@dataclass(frozen=True)
class DiscoveryContext:
    """Snapshot of runnable executables currently installed in ``$PATH``."""

    binaries: dict[str, Path]
    symlink_aliases: dict[str, str]

    @property
    def all_binary_names(self) -> frozenset[str]:
        return frozenset(self.binaries.keys())

    def canonical_name(self, name: str) -> str:
        """Resolve an alias to its canonical target name if it aliases another binary."""
        return self.symlink_aliases.get(name, name)


def _is_multicall_target(target_name: str) -> bool:
    lowered = target_name.lower()
    return lowered in _MULTICALL_TARGETS or lowered.startswith(
        ("python3.", "perl5.", "ruby3.", "ld-linux", "ld.so")
    )


def discover_path_binaries(path_env: str | None = None) -> DiscoveryContext:
    """Scan ``$PATH`` once to enumerate installed runnable binaries and symlink aliases."""
    raw_path = os.environ.get("PATH", "") if path_env is None else path_env
    binaries: dict[str, Path] = {}
    seen_dirs: set[str] = set()

    for entry in raw_path.split(os.pathsep):
        if not entry:
            continue
        dir_path = Path(entry)
        try:
            real_dir = str(dir_path.resolve())
        except OSError:
            continue
        if real_dir in seen_dirs or not dir_path.is_dir():
            continue
        seen_dirs.add(real_dir)

        try:
            with os.scandir(dir_path) as it:
                for dirent in it:
                    name = dirent.name
                    if not name or name.startswith(".") or name in binaries:
                        continue
                    try:
                        if dirent.is_dir(follow_symlinks=True):
                            continue
                        full = Path(dirent.path)
                        if os.access(full, os.X_OK):
                            binaries[name] = full
                    except OSError:
                        continue
        except OSError:
            continue

    # Detect true tool aliases via symlinks (e.g., docker -> podman, egrep -> grep).
    symlink_aliases: dict[str, str] = {}
    for name, path in binaries.items():
        try:
            if not path.is_symlink():
                continue
            target = path.resolve()
        except OSError:
            continue

        target_name = target.name
        if (
            target_name
            and target_name != name
            and target_name in binaries
            and not _is_multicall_target(target_name)
        ):
            symlink_aliases[name] = target_name

    return DiscoveryContext(binaries=binaries, symlink_aliases=symlink_aliases)


def compute_missing_binaries(
    indexed_names: set[str],
    context: DiscoveryContext,
    *,
    limit: int | None = None,
) -> list[str]:
    """Return installed runnable binaries not represented in the index."""
    missing = sorted(
        name
        for name in context.binaries
        if name not in indexed_names and context.canonical_name(name) not in indexed_names
    )
    if limit is not None:
        return missing[:limit]
    return missing
