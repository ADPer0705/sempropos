"""Ultra-thin stdlib-only Unix-socket client for the sem warm daemon.

Intentionally imports ONLY Python standard-library modules so ``--porcelain`` /
``Ctrl+S`` queries can execute against a warm daemon without importing Typer,
Rich, NumPy, or FastEmbed.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from sempropos.daemon.protocol import PROTOCOL_VERSION, encode_message, read_message


class DaemonUnavailableError(RuntimeError):
    """Raised when the warm daemon is disabled, unreachable, or fails to start."""


def default_socket_path() -> Path:
    """Resolve daemon socket path using only stdlib + env vars (zero heavy imports)."""
    rt_override = os.environ.get("SEMPROPOS_RUNTIME_DIR")
    if rt_override:
        return Path(rt_override).expanduser() / "sem.sock"
    data_override = os.environ.get("SEMPROPOS_DATA_DIR")
    if data_override:
        return Path(data_override).expanduser() / "run" / "sem.sock"
    xdg_rt = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_rt and Path(xdg_rt).is_dir():
        return Path(xdg_rt) / "sempropos" / "sem.sock"
    xdg_data = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data).expanduser() if xdg_data else (Path.home() / ".local" / "share")
    return base / "sempropos" / "run" / "sem.sock"


class DaemonClient:
    """Stdlib-only JSON-RPC client with optional lazy daemon auto-start."""

    def __init__(self, socket_path: Path | None = None, *, timeout: float = 35.0) -> None:
        self.socket_path = socket_path or default_socket_path()
        self.timeout = timeout

    def _raw_call(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float | None = None
    ) -> dict[str, Any]:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout if timeout is not None else self.timeout)
        try:
            sock.connect(str(self.socket_path))
            frame = encode_message(
                {"version": PROTOCOL_VERSION, "method": method, "params": params or {}}
            )
            sock.sendall(frame)
            resp = read_message(sock)
        except OSError as exc:
            raise DaemonUnavailableError(f"Cannot connect to sem daemon: {exc}") from exc
        finally:
            sock.close()

        if resp is None:
            raise DaemonUnavailableError("Empty response from sem daemon.")
        if not resp.get("ok"):
            raise RuntimeError(str(resp.get("error") or "Daemon RPC error"))
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    def is_running(self) -> bool:
        if not self.socket_path.exists():
            return False
        try:
            res = self._raw_call("ping", timeout=0.5)
            return bool(res.get("pong"))
        except Exception:  # noqa: BLE001
            return False

    def ensure_started(self, *, wait_seconds: float = 3.0) -> bool:
        """Spawn the background daemon if not running and wait briefly until ready."""
        if self.is_running():
            return True

        cmd = [sys.executable, "-m", "sempropos.daemon", "--socket", str(self.socket_path)]
        try:
            subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                close_fds=True,
            )
        except OSError:
            return False

        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            if self.is_running():
                return True
            time.sleep(0.05)
        return False

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        auto_start: bool = True,
    ) -> dict[str, Any]:
        """Send an RPC call to the warm daemon, auto-starting it if requested."""
        try:
            return self._raw_call(method, params)
        except DaemonUnavailableError:
            if not auto_start:
                raise
            if not self.ensure_started():
                raise
            return self._raw_call(method, params)

    def status(self) -> dict[str, Any]:
        if not self.is_running():
            return {"running": False, "socket": str(self.socket_path)}
        return self._raw_call("status", timeout=1.5)

    def stop(self) -> bool:
        if not self.is_running():
            return False
        try:
            self._raw_call("shutdown", timeout=2.0)
        except Exception:  # noqa: BLE001
            return False
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and self.socket_path.exists():
            time.sleep(0.05)
        return True

    def notify_reload(self) -> bool:
        if not self.is_running():
            return False
        try:
            self._raw_call("reload", timeout=2.0)
            return True
        except Exception:  # noqa: BLE001
            return False


def try_fast_porcelain_ask(
    query: str,
    *,
    tool_filter: str | None = None,
    backend: str | None = None,
) -> tuple[int, str, str] | None:
    """Attempt an ultra-fast warm-daemon query without importing Typer/Rich.

    Returns ``(exit_code, stdout_text, stderr_text)`` if served by a running daemon,
    or ``None`` if no warm daemon is running (so the full CLI handles auto-start/fallback).
    """
    client = DaemonClient(timeout=35.0)
    if not client.is_running():
        return None
    try:
        resp = client._raw_call(
            "ask",
            {
                "query": query,
                "tool_filter": tool_filter,
                "backend": backend,
                "alternatives": 1,
            },
        )
    except Exception:  # noqa: BLE001
        return None

    status = str(resp.get("status") or "")
    cmd = resp.get("command")
    if status == "ok" and isinstance(cmd, str) and cmd.strip():
        return 0, cmd.strip(), ""
    err = str(resp.get("error") or "Unable to synthesize a command.")
    code = 3 if status in ("index_error", "no_candidates") else 1
    return code, "", err


def try_fast_json_ask(
    query: str,
    *,
    tool_filter: str | None = None,
    backend: str | None = None,
) -> tuple[int, str] | None:
    """Fast-path JSON ask when a warm daemon is already up."""
    client = DaemonClient(timeout=35.0)
    if not client.is_running():
        return None
    try:
        resp = client._raw_call(
            "ask",
            {"query": query, "tool_filter": tool_filter, "backend": backend, "alternatives": 1},
        )
    except Exception:  # noqa: BLE001
        return None
    status = str(resp.get("status") or "")
    code = 0 if status == "ok" else (3 if status in ("index_error", "no_candidates") else 1)
    return code, json.dumps(resp, ensure_ascii=False, indent=2)
