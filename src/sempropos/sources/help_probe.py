"""Safe ``--help`` probing and structured CLI layout parsing.

Discovers and parses documentation for installed ``$PATH`` binaries that lack
man pages (e.g. ``7z``, ``uv``, ``ruff``, ``cargo``, custom Go/Rust/Python CLIs).
Applies strict safety guardrails: denylist filtering, setuid/setgid exclusion,
sanitized headless environment, detached process group, and hard timeouts.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Sequence
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
        "gparted",
        "synaptic",
        "liveinst",
        "anaconda",
        "calamares",
        "ubiquity",
        "kdesu",
        "kdesudo",
        "gksu",
        "gksudo",
        "lxqt-sudo",
        "beesu",
        "yad",
        "xmessage",
        "gxmessage",
        "ksshaskpass",
        "lxpolkit",
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
    "polkit-",
    "hyprpolkit",
    "hyprland-",
    "akonadi",
    "kwallet",
    "drkonqi",
    "x11",
    "xorg",
    "nvidia-",
    "vulkan",
    "mesa-",
    "glx",
)

_UNSAFE_SUFFIXES = (
    "-pkexec",
    "_pkexec",
    "-askpass",
    "_askpass",
    "askpass",
    "-gui",
    "_gui",
    "-gtk",
    "-gtk3",
    "-gtk4",
    "-dialog",
    "-dialog6",
    "-applet",
    "-installer",
)

_SCRIPT_UNSAFE_CALL_RE = re.compile(
    r"\b(?:pkexec|gksu|gksudo|kdesu|kdesudo|lxqt-sudo|beesu|"
    r"zenity|kdialog|yad|xmessage|gxmessage)\b"
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
    r"(?:(?:\s*:\s+|\s{2,})(?P<desc>.+)|\s*)$"
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


def _extract_desktop_exec_name(exec_value: str) -> str | None:
    try:
        tokens = shlex.split(exec_value)
    except ValueError:
        tokens = exec_value.split()

    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        base = Path(tok).name
        if base == "env":
            idx += 1
            continue
        if "=" in tok and not tok.startswith(("=", "/")) and not tok.startswith("-"):
            idx += 1
            continue
        if base == "pkexec":
            idx += 1
            while idx < len(tokens) and tokens[idx].startswith("-"):
                flag = tokens[idx]
                idx += 2 if flag in ("--user", "-u") else 1
            continue
        return base or None
    return None


def _parse_desktop_gui_exec(desktop_file: Path) -> str | None:
    try:
        text = desktop_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None

    in_entry = False
    entry_type = ""
    terminal = False
    exec_val = ""

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            if in_entry:
                break
            in_entry = line == "[Desktop Entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key == "Type":
            entry_type = value
        elif key == "Terminal":
            terminal = value.lower() == "true"
        elif key == "Exec":
            exec_val = value

    if entry_type == "Application" and not terminal and exec_val:
        return _extract_desktop_exec_name(exec_val)
    return None


def discover_gui_binaries(application_dirs: Sequence[Path] | None = None) -> frozenset[str]:
    """Discover non-terminal GUI application executables from ``.desktop`` entries."""
    if application_dirs is None:
        raw_dirs: list[Path] = []
        xdg_data = os.environ.get("XDG_DATA_DIRS", "")
        for entry in xdg_data.split(os.pathsep):
            if entry:
                raw_dirs.append(Path(entry) / "applications")
        raw_dirs.extend(
            [
                Path("/usr/share/applications"),
                Path("/usr/local/share/applications"),
                Path.home() / ".local" / "share" / "applications",
            ]
        )
        dirs_to_scan = list(dict.fromkeys(raw_dirs))
    else:
        dirs_to_scan = list(application_dirs)

    gui_names: set[str] = set()
    for app_dir in dirs_to_scan:
        if not app_dir.is_dir():
            continue
        try:
            desktop_files = list(app_dir.glob("*.desktop"))
        except OSError:
            continue
        for desktop_file in desktop_files:
            exe_name = _parse_desktop_gui_exec(desktop_file)
            if exe_name:
                gui_names.add(exe_name)
    return frozenset(gui_names)


@functools.lru_cache(maxsize=1)
def _cached_gui_binaries() -> frozenset[str]:
    return discover_gui_binaries()


def _script_invokes_gui_or_escalation(binary_path: Path) -> bool:
    """Return True if a shebang script invokes privilege-escalation or GUI dialog helpers."""
    try:
        with binary_path.open("rb") as handle:
            head = handle.read(8192)
    except OSError:
        return False

    if not head.startswith(b"#!"):
        return False

    lines = head.decode("utf-8", errors="ignore").splitlines()[1:]
    body = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
    return bool(_SCRIPT_UNSAFE_CALL_RE.search(body))


def is_safe_to_probe(
    name: str,
    binary_path: Path,
    *,
    gui_binaries: frozenset[str] | None = None,
) -> bool:
    """Return True if ``binary_path`` passes static safety checks for ``--help``."""
    lowered = name.lower()
    if name in _UNSAFE_EXACT or lowered in _UNSAFE_EXACT:
        return False
    if any(lowered.startswith(prefix) for prefix in _UNSAFE_PREFIXES):
        return False
    if any(lowered.endswith(suffix) for suffix in _UNSAFE_SUFFIXES):
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

    active_gui = _cached_gui_binaries() if gui_binaries is None else gui_binaries
    if name in active_gui or lowered in active_gui:
        return False

    try:
        st = binary_path.stat()
    except OSError:
        return False

    # Never invoke setuid or setgid binaries.
    if st.st_mode & (stat.S_ISUID | stat.S_ISGID):
        return False
    if not stat.S_ISREG(st.st_mode):
        return False

    return not _script_invokes_gui_or_escalation(binary_path)


def _sanitized_env(sandbox_home: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": sandbox_home,
        "TMPDIR": sandbox_home,
        "XDG_RUNTIME_DIR": sandbox_home,
        "DBUS_SYSTEM_BUS_ADDRESS": "unix:path=/dev/null",
        "DBUS_SESSION_BUS_ADDRESS": "unix:path=/dev/null",
        "QT_QPA_PLATFORM": "offscreen",
        "SUDO_ASKPASS": "/bin/false",
        "SSH_ASKPASS": "/bin/false",
        "SSH_ASKPASS_REQUIRE": "never",
        "GIT_ASKPASS": "/bin/false",
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
        "CLICOLOR": "0",
        "PAGER": "cat",
        "COLUMNS": "120",
    }


def _resolve_setpriv() -> str | None:
    if sys.platform.startswith("linux"):
        return shutil.which("setpriv")
    return None


def run_help_probe(binary_path: Path, *, timeout: float = 1.5) -> str | None:
    """Execute ``[binary_path, "--help"]`` safely in an isolated temp dir."""
    setpriv = _resolve_setpriv()
    cmd = (
        [setpriv, "--no-new-privs", "--", str(binary_path), "--help"]
        if setpriv
        else [str(binary_path), "--help"]
    )
    proc: subprocess.Popen[bytes] | None = None
    with tempfile.TemporaryDirectory(prefix="sem-probe-") as sandbox_dir:
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=sandbox_dir,
                env=_sanitized_env(sandbox_dir),
                start_new_session=True,
            )
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if proc is not None:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGKILL)
                with contextlib.suppress(OSError):
                    proc.communicate()
            return None
        except OSError:
            return None
        finally:
            if proc is not None:
                with contextlib.suppress(OSError):
                    os.killpg(proc.pid, signal.SIGKILL)

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
    # Reject generic option/privilege/display errors unless actual usage follows.
    if (
        len(text.splitlines()) <= 4
        and any(
            err in lowered
            for err in (
                "unrecognized option",
                "invalid option",
                "unknown option",
                "illegal option",
                "must be setuid root",
                "cannot open display",
                "failed to open display",
                "could not connect to display",
                "no protocol specified",
            )
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


def _try_add_subcommand(line: str, frag: HelpProbeFragment, seen_subs: set[str]) -> None:
    if len(frag.subcommands) >= _MAX_SUBCOMMANDS:
        return
    m_sub = _SUBCMD_ENTRY_RE.match(line)
    if not m_sub:
        return
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


def _try_add_flag(
    head: str,
    desc: str,
    section_name: str | None,
    frag: HelpProbeFragment,
    seen_flags: set[tuple[str | None, str | None]],
) -> None:
    if len(frag.flags) >= _MAX_FLAGS:
        return
    parsed = parse_flag_header(head, desc, section=section_name, source="help_probe")
    if parsed is not None and parsed.canonical_key not in seen_flags:
        seen_flags.add(parsed.canonical_key)
        frag.flags.append(parsed)


def _match_section_header(line: str, indent: int) -> tuple[str, str] | None:
    if indent > 2:
        return None
    sec_match = _SECTION_HEADER_RE.match(line)
    if not sec_match:
        return None
    raw_title = (sec_match.group("angle") or sec_match.group("colon") or "").strip()
    classified = _classify_section(raw_title)
    if classified != "other" or raw_title.upper() in ("USAGE", "EXAMPLES"):
        return classified, raw_title
    return None


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
    pending_head: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if pending_head:
                _try_add_flag(pending_head, "", section_name, frag, seen_flags)
                pending_head = None
            continue

        indent = len(line) - len(line.lstrip())
        if pending_head is not None:
            desc_line = stripped if (indent >= 6 and not stripped.startswith("-")) else ""
            _try_add_flag(pending_head, desc_line, section_name, frag, seen_flags)
            pending_head = None
            if desc_line:
                continue

        usage_match = _USAGE_RE.match(line)
        if usage_match:
            usage = " ".join(usage_match.group(1).split())
            if usage and usage not in frag.synopsis and len(frag.synopsis) < 5:
                frag.synopsis.append(usage)
            section_kind = "usage"
            continue

        sec_change = _match_section_header(line, indent)
        if sec_change is not None:
            section_kind, section_name = sec_change
            continue

        if (
            section_kind == "header"
            and len(preamble_lines) < 3
            and _is_preamble_candidate(stripped)
        ):
            preamble_lines.append(stripped)
        elif section_kind == "commands":
            _try_add_subcommand(line, frag, seen_subs)
        elif section_kind in ("options", "usage", "other"):
            m_flag = _FLAG_ENTRY_RE.match(line)
            if m_flag:
                desc = (m_flag.group("desc") or "").strip()
                if desc:
                    _try_add_flag(m_flag.group("head"), desc, section_name, frag, seen_flags)
                else:
                    pending_head = m_flag.group("head")

    if pending_head:
        _try_add_flag(pending_head, "", section_name, frag, seen_flags)
    _finalize_help_summary(frag, preamble_lines)
    return frag


def probe_tool_help(
    name: str,
    binary_path: Path,
    *,
    timeout: float = 1.5,
    gui_binaries: frozenset[str] | None = None,
) -> HelpProbeFragment | None:
    """Safely probe and parse ``<binary> --help`` if permitted."""
    if not is_safe_to_probe(name, binary_path, gui_binaries=gui_binaries):
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
