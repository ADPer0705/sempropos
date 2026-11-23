"""Entry point for running ``python -m sempropos.daemon``."""

from __future__ import annotations

import argparse
from pathlib import Path

from sempropos.daemon.server import SemDaemonServer


def main() -> None:
    parser = argparse.ArgumentParser(description="sempropos warm background daemon")
    parser.add_argument("--socket", type=Path, default=None, help="Unix socket path")
    parser.add_argument(
        "--idle-timeout",
        type=int,
        default=None,
        help="Auto-shutdown after N seconds of inactivity",
    )
    args = parser.parse_args()

    server = SemDaemonServer(
        socket_path=args.socket,
        idle_timeout_seconds=args.idle_timeout,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
