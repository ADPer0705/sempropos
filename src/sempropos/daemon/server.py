"""Lightweight single-process Unix-socket daemon keeping retrieval and models warm.

Automatically shuts down after ``idle_timeout_seconds`` of inactivity so memory
is never held permanently when the user isn't querying ``sem``.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import signal
import socket
import threading
import time
from pathlib import Path
from typing import Any

from sempropos import __version__, config
from sempropos.daemon.protocol import PROTOCOL_VERSION, encode_message, read_message
from sempropos.intelligence.facade import is_embedding_model_warm
from sempropos.service import SemService


class SemDaemonServer:
    """Unix-domain socket RPC server wrapping a warm :class:`SemService`."""

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        pid_path: Path | None = None,
        lock_path: Path | None = None,
        idle_timeout_seconds: int | None = None,
    ) -> None:
        cfg = config.load_config().daemon
        self.socket_path = socket_path or config.daemon_socket_path()
        self.pid_path = pid_path or config.daemon_pid_path()
        self.lock_path = lock_path or config.daemon_lock_path()
        self.idle_timeout = (
            cfg.idle_timeout_seconds if idle_timeout_seconds is None else idle_timeout_seconds
        )

        self._service = SemService()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._started_at = time.time()
        self._last_activity = time.monotonic()
        self._request_count = 0
        self._lock_fd: int | None = None
        self._server_sock: socket.socket | None = None

    def _acquire_single_instance_lock(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.lock_path.parent, 0o700)

        fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError("Another sem daemon instance is already running.") from exc
        self._lock_fd = fd

    def _release_resources(self) -> None:
        if self._server_sock is not None:
            with contextlib.suppress(OSError):
                self._server_sock.close()
            self._server_sock = None

        with contextlib.suppress(OSError):
            self.socket_path.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            self.pid_path.unlink(missing_ok=True)

        self._service.close()

        if self._lock_fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                os.close(self._lock_fd)
            self._lock_fd = None

    def _dispatch(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "ping":
            return {"pong": True, "pid": os.getpid(), "version": __version__}

        if method == "status":
            return {
                "running": True,
                "pid": os.getpid(),
                "version": __version__,
                "socket": str(self.socket_path),
                "uptime_seconds": round(time.time() - self._started_at, 1),
                "idle_seconds": round(time.monotonic() - self._last_activity, 1),
                "idle_timeout_seconds": self.idle_timeout,
                "requests_served": self._request_count,
                "embeddings_warm": is_embedding_model_warm(),
            }

        if method == "reload":
            self._service.reload()
            return {"reloaded": True}

        if method == "shutdown":
            self._stop_event.set()
            return {"stopping": True}

        if method == "list_names":
            return {"names": self._service.list_names()}

        if method == "doctor":
            return self._service.doctor()

        if method == "search":
            return self._service.search(
                str(params.get("query") or ""),
                limit=int(params.get("limit") or 10),
                category=params.get("category"),
            )

        if method == "show":
            return self._service.show(
                str(params.get("tool") or ""),
                subcommand=params.get("subcommand"),
                flag_filter=params.get("flag_filter"),
            )

        if method == "ask":
            return self._service.ask(
                str(params.get("query") or ""),
                tool_filter=params.get("tool_filter"),
                backend=params.get("backend"),
                alternatives=int(params.get("alternatives") or 1),
            )

        raise ValueError(f"Unknown daemon RPC method: {method}")

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(60.0)
            try:
                req = read_message(conn)
                if req is None:
                    return
                method = str(req.get("method") or "")
                params_raw = req.get("params")
                params = params_raw if isinstance(params_raw, dict) else {}

                with self._lock:
                    self._last_activity = time.monotonic()
                    self._request_count += 1
                    result = self._dispatch(method, params)

                resp = {"version": PROTOCOL_VERSION, "ok": True, "result": result}
            except Exception as exc:  # noqa: BLE001
                resp = {"version": PROTOCOL_VERSION, "ok": False, "error": str(exc)}

            with contextlib.suppress(OSError):
                conn.sendall(encode_message(resp))

    def request_stop(self) -> None:
        """Signal the server accept loop to terminate cleanly."""
        self._stop_event.set()

    def serve_forever(self, *, install_signals: bool = True) -> None:
        """Bind the Unix domain socket and serve until idle timeout or stop signal."""
        self._acquire_single_instance_lock()

        # Remove any stale socket left by an ungraceful crash.
        with contextlib.suppress(OSError):
            self.socket_path.unlink(missing_ok=True)

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket_path))
        with contextlib.suppress(OSError):
            os.chmod(self.socket_path, 0o600)
        sock.listen(16)
        sock.settimeout(0.5)
        self._server_sock = sock

        with contextlib.suppress(OSError):
            self.pid_path.write_text(str(os.getpid()), encoding="utf-8")

        if install_signals and threading.current_thread() is threading.main_thread():

            def _handle_sig(_signum: int, _frame: object) -> None:
                self._stop_event.set()

            signal.signal(signal.SIGTERM, _handle_sig)
            signal.signal(signal.SIGINT, _handle_sig)

        try:
            while not self._stop_event.is_set():
                if self.idle_timeout > 0 and (
                    time.monotonic() - self._last_activity >= self.idle_timeout
                ):
                    break
                try:
                    client_sock, _ = sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                self._handle_client(client_sock)
        finally:
            self._release_resources()
