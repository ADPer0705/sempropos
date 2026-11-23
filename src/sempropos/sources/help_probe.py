"""Safe ``--help`` probing and structured CLI layout parsing.

Discovers and parses documentation for installed ``$PATH`` binaries that lack
man pages (e.g. ``7z``, ``uv``, ``ruff``, ``cargo``, custom Go/Rust/Python CLIs).
Applies strict safety guardrails: denylist filtering, setuid/setgid exclusion,
sanitized headless environment, detached process group, and hard timeouts.
"""

from __future__ import annotations

import hashlib
import os
import re
import signal
import stat
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from sempropos.model.spec import FlagSpec, SubcommandSpec
from sempropos.sources.roff_parser import parse_flag_header

_MAX_OUTPUT_BYTES = 128 * 1024
_MAX_FLAGS = 200
_MAX_SUBCOMMANDS = 80

# Commands that must NEVER be invoked automatically during indexing.
_UNSAFE_EXACT = frozenset(
    {
        "[",
        "test",
        "true",
        "false",
        "yes",
        "sleep",
        "sync",
        "reboot",
        "poweroff",
        "shutdown",
        "halt",
        "init",
        "telinit",
        "runlevel",
        "su",
        "sudo",
        "doas",
        "pkexec",
        "login",
        "logout",
        "passwd",
        "chpasswd",
        "chsh",
        "visudo",
        "mount",
        "umount",
        "swapon",
        "swapoff",
        "fdisk",
        "cfdisk",
        "sfdisk",
        "parted",
        "wipefs",
        "dd",
        "rm",
        "rmdir",
        "shred",
        "kill",
        "killall",
        "pkill",
        "xkill",
        "startx",
        "xinit",
        "X",
        "Xorg",
        "Xwayland",
        "sway",
        "hyprland",
        "i3",
        "wayfire",
        "kwin_wayland",
        "kwin_x11",
        "mutter",
        "firefox",
        "chromium",
        "google-chrome",
        "brave",
        "code",
        "slack",
        "discord",
        "spotify",
        "steam",
        "vlc",
        "gimp",
        "blender",
        "inkscape",
        "libreoffice",
        "soffice",
        "vim",
        "nvim",
        "vi",
        "nano",
        "emacs",
        "less",
        "more",
        "top",
        "htop",
        "btop",
        "tmux",
        "screen",
        "sh",
        "bash",
        "zsh",
        "fish",
        "dash",
        "csh",
        "tcsh",
        "ksh",
        "ash",
        "ssh",
        "scp",
        "sftp",
        "telnet",
        "ftp",
        "wall",
        "write",
        "mesg",
        "eject",
        "chvt",
        "openvt",
        "deallocvt",
        "clear",
        "reset",
        "tput",
        "stty",
        "dialog",
        "whiptail",
        "zenity",
        "kdialog",
        "pinentry",
        "gpg-agent",
        "ssh-agent",
        "dbus-daemon",
        "dbus-launch",
    }
)

_UNSAFE_PREFIXES = (
    "mkfs",
    "fsck",
    "gnome-",
    "kde",
    "kf5",
    "kf6",
    "plasma",
    "kwin",
    "xdg-",
    "gtk-",
    "qt",
    "gst-",
    "pulse",
    "pipewire",
    "wireplumber",
    "systemd-",
    "dbus-",
    "pinentry-",
    "x11",
    "xorg",
    "nvidia-",
    "vulkan",
    "mesa-",
    "glx",
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_USAGE_RE = re.compile(r"^\s*usage\s*:\s*(.+)$", re.IGNORECASE)
_SECTION_HEADER_RE = re.compile(
    r"^\s*(?:<(?P<angle>[A-Za-z][A-Za-z0-9 _/-]{1,36})>|(?P<colon>[A-Za-z][A-Za-z0-9 _/-]{1,36}):)\s*$"
)
_FLAG_ENTRY_RE = re.compile(
    r"^\s{1,8}(?P<head>--?[A-Za-z0-9?@#][A-Za-z0-9._={}\[\]|/@!*:-]*"
    r"(?:,\s*--?[A-Za-z0-9][A-Za-z0-9._={}\[\]|/@!*:-]*)*"
    r"(?:\s+(?:<[^>]+>|\[[^\]]+\]|[A-Z][A-Z0-9_./-]{0,24}))?)"
    r"(?:\s*:\s+|\s{2,})(?P<desc>.+)$"
)
_SUBCMD_ENTRY_RE = re.compile(
    r"^\s{1,6}(?P<name>[a-zA-Z0-9][a-zA-Z0-9._-]{0,28})"
    r"(?:,\s*[a-zA-Z0-9._-]+)?"
    r"(?:\s*:\s+|\s{2,})(?P<desc>.+)$"
)


@dataclass
class HelpProbeFragment:
    """Structured tool details parsed from ``<tool> --help``."""

    name: str
    binary_path: str
    summary: str = ""
    description: str = ""
    synopsis: list[str] = field(default_factory=list)
    subcommands: list[SubcommandSpec] = field(default_factory=list)
    flags: list[FlagSpec] = field(default_factory=list)
    mtime: float | None = None
    content_hash: str = ""


def is_safe_to_probe(name: str, binary_path: Path) -> bool:
    """Return True if ``binary_path`` passes static safety checks for ``--help``."""
    lowered = name.lower()
    if name in _UNSAFE_EXACT or lowered in _UNSAFE_EXACT:
        return False
    if any(lowered.startswith(prefix) for prefix in _UNSAFE_PREFIXES):
        return False
    if lowered.endswith(("d", "-daemon", "-server", "-helper", "-agent")) and lowered not in (
        "fd",
        "sed",
        "find",
        "ld",
        "od",
        "dd",
        "ed",
        "id",
        "md",
        "rd",
        "hd",
        "xxd",
        "zstd",
        "podman",
    ):
        return False

    try:
        st = binary_path.stat()
    except OSError:
        return False

    # Never invoke setuid or setgid binaries.
    if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
        return False
    return stat.S_ISREG(st.st_mode)


def _sanitized_env() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "CLICOLOR": "0",
        "PAGER": "cat",
        "COLUMNS": "120",
    }


def run_help_probe(binary_path: Path, *, timeout: float = 1.5) -> str | None:
    """Execute ``[binary_path, "--help"]`` safely and return cleaned output, or ``None``."""
    proc: subprocess.Popen[bytes] | None = None
    try:
        proc = subprocess.Popen(
            [str(binary_path), "--help"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_sanitized_env(),
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except OSError:
                proc.kill()
            proc.communicate()
        return None
    except OSError:
        return None

    # Many tools (like 7z or Go/flag tools) exit 0 or 2 on --help and print to stdout or stderr.
    combined = stdout if len(stdout.strip()) >= len(stderr.strip()) else stderr
    if not combined:
        combined = stdout or stderr
    if not combined:
        return None

    # Reject binary output.
    sample = combined[:1024]
    if b"\x00" in sample:
        return None

    text = _ANSI_RE.sub("", combined[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace"))
    lowered = text.lower()
    # Reject generic "unrecognized option --help" errors unless actual usage follows.
    if (
        len(text.splitlines()) <= 4
        and any(
            err in lowered
            for err in ("unrecognized option", "invalid option", "unknown option", "illegal option")
        )
        and "usage:" not in lowered
    ):
        return None

    return text


def _classify_section(title: str) -> str:
    upper = title.upper()
    if any(k in upper for k in ("COMMAND", "SUBCOMMAND", "ACTION")):
        return "commands"
    if any(k in upper for k in ("OPTION", "FLAG", "SWITCH")):
        return "options"
    if any(k in upper for k in ("ARGUMENT", "POSITIONAL")):
        return "arguments"
    return "other"


def _finalize_help_summary(frag: HelpProbeFragment, preamble_lines: list[str]) -> None:
    if preamble_lines:
        frag.summary = preamble_lines[0][:240]
        frag.description = " ".join(preamble_lines)[:500]
    elif frag.synopsis:
        frag.summary = f"Command-line utility ({frag.synopsis[0][:160]})"
    elif frag.subcommands:
        sub_preview = ", ".join(s.name for s in frag.subcommands[:6])
        frag.summary = f"CLI tool with subcommands: {sub_preview}"


def _is_preamble_candidate(stripped: str) -> bool:
    lowered = stripped.lower()
    return (
        not stripped.startswith("-")
        and "copyright" not in lowered
        and not re.match(r"^\d+-bit\s+locale=", lowered)
    )


def parse_help_output(name: str, raw_text: str, binary_path: str = "") -> HelpProbeFragment:
    """Parse Clap, Cobra, Click/Typer, argparse, GNU, and 7-Zip ``--help`` outputs."""
    text = _ANSI_RE.sub("", raw_text)
    frag = HelpProbeFragment(
        name=name,
        binary_path=binary_path,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    )

    section_kind = "header"
    section_name: str | None = None
    preamble_lines: list[str] = []
    seen_flags: set[tuple[str | None, str | None]] = set()
    seen_subs: set[str] = set()

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        usage_match = _USAGE_RE.match(line)
        if usage_match:
            usage = " ".join(usage_match.group(1).split())
            if usage and usage not in frag.synopsis and len(frag.synopsis) < 5:
                frag.synopsis.append(usage)
            section_kind = "usage"
            continue

        sec_match = _SECTION_HEADER_RE.match(line)
        if sec_match and len(line) - len(line.lstrip()) <= 2:
            raw_title = (sec_match.group("angle") or sec_match.group("colon") or "").strip()
            classified = _classify_section(raw_title)
            if classified != "other" or raw_title.upper() in ("USAGE", "EXAMPLES"):
                section_kind = classified
                section_name = raw_title
                continue

        if section_kind == "header":
            if len(preamble_lines) < 3 and _is_preamble_candidate(stripped):
                preamble_lines.append(stripped)
            continue

        if section_kind == "commands":
            m_sub = _SUBCMD_ENTRY_RE.match(line)
            if m_sub and len(frag.subcommands) < _MAX_SUBCOMMANDS:
                sub_name = m_sub.group("name").strip()
                if sub_name not in seen_subs and not sub_name.startswith("-"):
                    seen_subs.add(sub_name)
                    frag.subcommands.append(
                        SubcommandSpec(
                            name=sub_name,
                            summary=" ".join(m_sub.group("desc").split())[:200],
                            source="help_probe",
                        )
                    )
            continue

        if section_kind in ("options", "usage", "other") and len(frag.flags) < _MAX_FLAGS:
            m_flag = _FLAG_ENTRY_RE.match(line)
            if m_flag:
                parsed = parse_flag_header(
                    m_flag.group("head"),
                    m_flag.group("desc"),
                    section=section_name,
                    source="help_probe",
                )
                if parsed is not None and parsed.canonical_key not in seen_flags:
                    seen_flags.add(parsed.canonical_key)
                    frag.flags.append(parsed)

    _finalize_help_summary(frag, preamble_lines)
    return frag


def probe_tool_help(
    name: str,
    binary_path: Path,
    *,
    timeout: float = 1.5,
) -> HelpProbeFragment | None:
    """Safely probe and parse ``<binary> --help`` if permitted."""
    if not is_safe_to_probe(name, binary_path):
        return None

    output = run_help_probe(binary_path, timeout=timeout)
    if not output:
        return None

    frag = parse_help_output(name, output, binary_path=str(binary_path))
    try:
        frag.mtime = binary_path.stat().st_mtime
    except OSError:
        frag.mtime = None

    if not (frag.summary or frag.flags or frag.subcommands or frag.synopsis):
        return None
    return frag
