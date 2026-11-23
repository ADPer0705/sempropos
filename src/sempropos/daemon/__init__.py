"""Auto-managed local Unix-socket daemon for low-latency queries."""

from sempropos.daemon.client import (
    DaemonClient,
    DaemonUnavailableError,
    default_socket_path,
    try_fast_json_ask,
    try_fast_porcelain_ask,
)

__all__ = [
    "DaemonClient",
    "DaemonUnavailableError",
    "default_socket_path",
    "try_fast_porcelain_ask",
    "try_fast_json_ask",
]
