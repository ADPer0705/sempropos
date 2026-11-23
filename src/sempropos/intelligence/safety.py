"""Deterministic command grounding, flag annotation, and destructive safety analysis."""

from __future__ import annotations

import re
import shlex
import shutil
from dataclasses import dataclass

from sempropos.model.spec import SafetyLevel, ToolSpec

_SHELL_BUILTINS = frozenset(
    {
        "alias",
        "bg",
        "cd",
        "command",
        "echo",
        "eval",
        "exec",
        "exit",
        "export",
        "fg",
        "hash",
        "history",
        "jobs",
        "kill",
        "local",
        "printf",
        "pwd",
        "read",
        "set",
        "source",
        "test",
        "time",
        "type",
        "ulimit",
        "umask",
        "unalias",
        "unset",
        "wait",
    }
)

_WRAPPER_COMMANDS = frozenset(
    {"sudo", "doas", "env", "time", "nice", "nohup", "command", "builtin"}
)
_WRAPPER_VALUE_FLAGS = frozenset({"-u", "-g", "-C", "-h", "-p", "-n", "--user", "--group", "-U"})

# Explicit destructive patterns: (regex, warning_message)
_DESTRUCTIVE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\brm\b[^\n;|&]*\s-[^\s]*r[^\s]*f|\brm\b[^\n;|&]*\s-[^\s]*f[^\s]*r|\brm\b[^\n;|&]*--recursive[^\n;|&]*--force"
        ),
        "Recursive forced deletion (`rm -rf`) permanently deletes files without confirmation.",
    ),
    (
        re.compile(r"\bfind\b[^\n;|&]*\s-delete\b"),
        "`find -delete` permanently removes matched files. Preview without `-delete` first.",
    ),
    (
        re.compile(r"\bdd\b[^\n;|&]*\bof=/dev/"),
        "`dd of=/dev/...` writes raw blocks directly to a device and can destroy data.",
    ),
    (
        re.compile(r"\b(?:mkfs(?:\.[a-z0-9]+)?|wipefs|shred|fdisk|parted|sfdisk)\b"),
        "Disk/filesystem formatting or wiping command can irreversibly destroy data.",
    ),
    (
        re.compile(r"\bchmod\b[^\n;|&]*\s-R\b[^\n;|&]*\b777\b"),
        "Recursive `chmod -R 777` grants world-writable permissions across a directory tree.",
    ),
    (
        re.compile(r"\bgit\b[^\n;|&]*\breset\b[^\n;|&]*--hard\b"),
        "`git reset --hard` permanently discards uncommitted local changes.",
    ),
    (
        re.compile(r"\bgit\b[^\n;|&]*\bpush\b[^\n;|&]*(?:--force|-f)\b"),
        "`git push --force` rewrites remote commit history.",
    ),
    (
        re.compile(r"\bgit\b[^\n;|&]*\bclean\b[^\n;|&]*-[^\s]*f"),
        "`git clean -f` permanently deletes untracked files.",
    ),
    (
        re.compile(r"\biptables\b[^\n;|&]*\s-F\b|\bnft\b[^\n;|&]*\bflush\b"),
        "Flushing firewall rules can drop network connectivity or expose services.",
    ),
    (
        re.compile(r"\b(?:kill|pkill|killall)\b[^\n;|&]*(?:-9|-SIGKILL|-KILL)\b"),
        "SIGKILL (`-9`) terminates processes immediately without graceful cleanup.",
    ),
    (
        re.compile(r"\b(?:reboot|poweroff|shutdown|halt)\b"),
        "System power/reboot command will interrupt active sessions.",
    ),
    (
        re.compile(r"\b(?:curl|wget)\b[^\n]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh)\b"),
        "Piping remote output directly into a shell executes uninspected code.",
    ),
    (
        re.compile(r">\s*/dev/(?:sd|nvme|vd|hd|loop)"),
        "Redirecting output directly to a block device overwrites raw disk data.",
    ),
)

# Caution-level patterns (mutating operations worth reviewing before pressing Enter)
_CAUTION_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?:^|[;&|]\s*)sudo\b"), "Runs with elevated `sudo` privileges."),
    (re.compile(r"\brm\b"), "Deletes files; verify target paths before running."),
    (re.compile(r"\bsed\b[^\n;|&]*\s-i\b"), "`sed -i` edits files in place."),
    (re.compile(r"\b(?:chmod|chown)\b"), "Modifies file permissions or ownership."),
    (
        re.compile(r"\bsystemctl\b[^\n;|&]*\b(?:stop|disable|mask|restart)\b"),
        "Modifies running system service state.",
    ),
    (
        re.compile(r"\b(?:docker|podman)\b[^\n;|&]*\b(?:rm|rmi|prune)\b"),
        "Removes containers or images.",
    ),
    (re.compile(r"\b(?:kill|pkill|killall)\b"), "Sends termination signals to running processes."),
)


@dataclass(frozen=True)
class FlagAnnotation:
    """Explanation for a single flag used in a synthesized command."""

    flag: str
    description: str
    verified: bool = True


@dataclass(frozen=True)
class CommandVerification:
    """Grounding, flag breakdown, and safety classification for a shell command."""

    command: str
    executable: str | None
    executable_exists: bool
    safety_level: SafetyLevel = "safe"
    warnings: tuple[str, ...] = ()
    flags_explained: tuple[FlagAnnotation, ...] = ()
    unverified_flags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "executable": self.executable,
            "executable_exists": self.executable_exists,
            "safety_level": self.safety_level,
            "warnings": list(self.warnings),
            "flags_explained": [
                {"flag": f.flag, "description": f.description, "verified": f.verified}
                for f in self.flags_explained
            ],
            "unverified_flags": list(self.unverified_flags),
        }


def _tokenize_command(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def _extract_primary_segment(tokens: list[str]) -> list[str]:
    """Return tokens of the first command segment before pipe/chain operators."""
    seg: list[str] = []
    for tok in tokens:
        if tok in ("|", "&&", "||", ";"):
            break
        seg.append(tok)
    return seg


def extract_primary_executable(command: str) -> tuple[str | None, list[str]]:
    """Extract the primary executable name and its arguments, skipping env vars and wrappers."""
    tokens = _extract_primary_segment(_tokenize_command(command))
    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        # Skip leading environment assignments like `LANG=C`
        if "=" in tok and not tok.startswith("-"):
            idx += 1
            continue
        if tok in _WRAPPER_COMMANDS:
            idx += 1
            # Skip wrapper flags (and their values such as `sudo -u root` / `nice -n 10`).
            while idx < len(tokens) and tokens[idx].startswith("-"):
                flag_tok = tokens[idx]
                idx += 1
                if flag_tok in _WRAPPER_VALUE_FLAGS and idx < len(tokens):
                    idx += 1
            continue
        return tok, tokens[idx + 1 :]
    return None, []


def _extract_flag_tokens(args: list[str], spec: ToolSpec) -> list[str]:
    """Split CLI args into flag tokens, expanding bundled short flags (e.g. ``-xzf``)."""
    known_shorts = {f.short for f in spec.flags if f.short}
    known_longs = {f.long for f in spec.flags if f.long}
    for sub in spec.subcommands:
        known_shorts.update(f.short for f in sub.flags if f.short)
        known_longs.update(f.long for f in sub.flags if f.long)

    found: list[str] = []
    for tok in args:
        if tok == "--":
            break
        if not tok.startswith("-") or tok == "-":
            continue
        if tok.startswith("--"):
            found.append(tok.split("=", 1)[0])
        elif tok in known_shorts or tok in known_longs:
            found.append(tok)
        elif len(tok) > 2 and tok[1:].isalpha():
            # Expand bundled flags like `-la` or `-xzf` if single-letter forms exist
            singles = [f"-{ch}" for ch in tok[1:]]
            if any(s in known_shorts for s in singles):
                found.extend(singles)
            else:
                found.append(tok)
        else:
            found.append(tok.split("=", 1)[0])
    return list(dict.fromkeys(found))


def _build_flag_lookup(spec: ToolSpec) -> dict[str, str]:
    flag_map: dict[str, str] = {}
    for flag in spec.flags:
        if flag.short:
            flag_map[flag.short] = flag.description
        if flag.long:
            flag_map[flag.long] = flag.description
    for sub in spec.subcommands:
        for flag in sub.flags:
            if flag.short and flag.short not in flag_map:
                flag_map[flag.short] = flag.description
            if flag.long and flag.long not in flag_map:
                flag_map[flag.long] = flag.description
    return flag_map


def _classify_risk(command: str) -> tuple[SafetyLevel, list[str]]:
    warnings: list[str] = []
    for pattern, msg in _DESTRUCTIVE_PATTERNS:
        if pattern.search(command):
            warnings.append(msg)
    if warnings:
        return "destructive", warnings

    for pattern, msg in _CAUTION_PATTERNS:
        if pattern.search(command):
            warnings.append(msg)
    if warnings:
        return "caution", warnings
    return "safe", []


def verify_command(
    command: str,
    spec: ToolSpec | None = None,
    *,
    check_path: bool = True,
) -> CommandVerification:
    """Ground executable & flags against ``ToolSpec`` and classify operational risk."""
    exe, args = extract_primary_executable(command)
    warnings: list[str] = []

    if exe is None:
        exists = False
    elif not check_path:
        exists = True
    else:
        exists = exe in _SHELL_BUILTINS or shutil.which(exe) is not None
        if not exists and spec is not None and (exe == spec.name or exe in spec.aliases):
            exists = True

    if exe and not exists:
        warnings.append(f"Executable `{exe}` was not found on PATH.")

    level, risk_warnings = _classify_risk(command)
    warnings.extend(risk_warnings)

    explained: list[FlagAnnotation] = []
    unverified: list[str] = []
    if spec is not None and args:
        flag_map = _build_flag_lookup(spec)
        for flag_tok in _extract_flag_tokens(args, spec):
            if flag_tok in flag_map:
                explained.append(
                    FlagAnnotation(flag=flag_tok, description=flag_map[flag_tok], verified=True)
                )
            elif flag_map:
                unverified.append(flag_tok)

    return CommandVerification(
        command=command,
        executable=exe,
        executable_exists=exists,
        safety_level=level,
        warnings=tuple(dict.fromkeys(warnings)),
        flags_explained=tuple(explained),
        unverified_flags=tuple(unverified),
    )
