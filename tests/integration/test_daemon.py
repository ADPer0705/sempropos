"""Integration tests for SemService and the Unix-socket warm daemon."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from sempropos import config
from sempropos.daemon.client import DaemonClient, try_fast_porcelain_ask
from sempropos.daemon.server import SemDaemonServer
from sempropos.index import repository, schema
from sempropos.model import ExampleSpec, FlagSpec, SubcommandSpec, ToolSpec


def _seed_test_index(isolated_env: dict[str, Path]) -> None:
    config.save_config(
        config.AppConfig(
            synthesis=config.SynthesisConfig(provider="tier0"),
            embedding=config.EmbeddingConfig(provider="none", model=None),
        )
    )
    schema.initialize_database()
    with schema.get_connection() as conn:
        repository.upsert_tool(
            conn,
            ToolSpec(
                name="du",
                summary="Estimate file space usage",
                category="filesystem",
                flags=(
                    FlagSpec(short="-s", long="--summarize", description="Total only"),
                    FlagSpec(
                        short="-h", long="--human-readable", description="Human readable sizes"
                    ),
                    FlagSpec(
                        short="-d",
                        long="--max-depth",
                        takes_value=True,
                        value_hint="N",
                        description="Limit directory depth",
                    ),
                ),
                examples=(
                    ExampleSpec(
                        command="du -sh *",
                        description="Show disk usage by folder",
                        source="tldr",
                    ),
                ),
            ),
        )
        repository.upsert_tool(
            conn,
            ToolSpec(
                name="7z",
                summary="High compression file archiver",
                category="archive",
                subcommands=(
                    SubcommandSpec(
                        name="l", summary="List contents of archive", source="help_probe"
                    ),
                ),
                examples=(
                    ExampleSpec(
                        command="7z l <archive.7z>",
                        description="List files in a 7z archive",
                        source="tldr",
                    ),
                ),
            ),
        )

    config.write_index_meta(
        embedding_dim=0,
        embedding_model="",
        embedding_provider="none",
        tool_count=2,
        flag_count=3,
        subcommand_count=1,
        example_count=2,
    )


def test_daemon_rpc_lifecycle_search_show_ask_reload_and_shutdown(
    isolated_env: dict[str, Path],
) -> None:
    _seed_test_index(isolated_env)
    sock_path = isolated_env["run"] / "test.sock"
    pid_path = isolated_env["run"] / "test.pid"
    lock_path = isolated_env["run"] / "test.lock"

    server = SemDaemonServer(
        socket_path=sock_path,
        pid_path=pid_path,
        lock_path=lock_path,
        idle_timeout_seconds=30,
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"install_signals": False}, daemon=True
    )
    thread.start()

    client = DaemonClient(socket_path=sock_path, timeout=5.0)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline and not client.is_running():
        time.sleep(0.05)
    assert client.is_running() is True

    status = client.status()
    assert status["running"] is True

    # 1. Search RPC
    search_resp = client.request("search", {"query": "disk usage by folder"}, auto_start=False)
    assert search_resp["status"] == "ok"
    assert search_resp["results"][0]["name"] == "du"

    # 2. Show RPC with flag filter
    show_resp = client.request("show", {"tool": "du", "flag_filter": "depth"}, auto_start=False)
    assert show_resp["status"] == "ok"
    assert len(show_resp["tool"]["flags"]) == 1
    assert show_resp["tool"]["flags"][0]["short"] == "-d"

    # 3. Ask RPC (Tier-0 deterministic example match + flag verification!)
    ask_resp = client.request("ask", {"query": "show disk usage by folder"}, auto_start=False)
    assert ask_resp["status"] == "ok"
    assert ask_resp["command"] == "du -sh *"
    assert ask_resp["verification"]["safety_level"] == "safe"
    assert {f["flag"] for f in ask_resp["verification"]["flags_explained"]} == {"-s", "-h"}

    # 4. Reload after inserting a brand-new tool directly into SQLite
    with schema.get_connection() as conn:
        repository.upsert_tool(conn, ToolSpec(name="ss", summary="Investigate network sockets"))
    assert client.notify_reload() is True
    ss_resp = client.request("show", {"tool": "ss"}, auto_start=False)
    assert ss_resp["status"] == "ok"
    assert ss_resp["tool"]["name"] == "ss"

    # 5. Clean shutdown unlinks socket
    assert client.stop() is True
    thread.join(timeout=3.0)
    assert not sock_path.exists()


def test_daemon_idle_auto_shutdown(isolated_env: dict[str, Path]) -> None:
    _seed_test_index(isolated_env)
    sock_path = isolated_env["run"] / "idle.sock"
    server = SemDaemonServer(
        socket_path=sock_path,
        pid_path=isolated_env["run"] / "idle.pid",
        lock_path=isolated_env["run"] / "idle.lock",
        idle_timeout_seconds=1,
    )
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"install_signals": False}, daemon=True
    )
    thread.start()

    client = DaemonClient(socket_path=sock_path)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline and not client.is_running():
        time.sleep(0.05)
    assert client.is_running() is True

    # Wait for idle timeout to trigger automatic clean shutdown
    thread.join(timeout=4.0)
    assert not thread.is_alive()
    assert not sock_path.exists()


def test_fast_porcelain_helper_returns_none_when_no_daemon(isolated_env: dict[str, Path]) -> None:
    assert try_fast_porcelain_ask("list files") is None
