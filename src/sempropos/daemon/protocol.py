"""Versioned newline-delimited JSON-RPC framing for the sem Unix-socket daemon."""

from __future__ import annotations

import json
import socket
from typing import Any

PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 2 * 1024 * 1024


def encode_message(payload: dict[str, Any]) -> bytes:
    """Encode a JSON dict as a single newline-delimited UTF-8 frame."""
    return (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


def read_message(sock: socket.socket) -> dict[str, Any] | None:
    """Read one newline-delimited JSON frame from ``sock``, or ``None`` on EOF."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        nl_idx = chunk.find(b"\n")
        if nl_idx != -1:
            chunks.append(chunk[:nl_idx])
            break
        total += len(chunk)
        if total > MAX_MESSAGE_BYTES:
            raise ValueError("Daemon RPC message exceeded size limit.")
        chunks.append(chunk)

    raw = b"".join(chunks).strip()
    if not raw:
        return None
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("Daemon RPC message must be a JSON object.")
    return parsed
