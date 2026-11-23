"""Integration tests for the tri-modal CLI (`ask`, `show`, `search`, `doctor`, `config`, `model`)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sempropos import config
from sempropos.cli import _normalize_argv, app
from sempropos.index import repository, schema
from sempropos.model import ExampleSpec, FlagSpec, SubcommandSpec, ToolSpec

runner = CliRunner()


def _seed_cli_index(isolated_env: dict[str, Path]) -> None:
    config.save_config(
        config.AppConfig(
            synthesis=config.SynthesisConfig(provider="tier0"),
            embedding=config.EmbeddingConfig(provider="none", model=None),
            daemon=config.DaemonConfig(enabled=False, auto_start=False),
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
                    FlagSpec(short="-s", long="--summarize", description="Display only a total"),
                    FlagSpec(
                        short="-h", long="--human-readable", description="Human readable sizes"
                    ),
                    FlagSpec(
                        short="-d",
                        long="--max-depth",
                        takes_value=True,
                        value_hint="N",
                        description="Limit directory traversal depth",
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
                name="ss",
                summary="Investigate network sockets and open ports",
                category="network",
                examples=(
                    ExampleSpec(
                        command="ss -tulnp",
                        description="Check open listening ports",
                        source="tldr",
                    ),
                ),
            ),
        )
        repository.upsert_tool(
            conn,
            ToolSpec(
                name="git",
                summary="Distributed version control system",
                category="vcs",
                subcommands=(
                    SubcommandSpec(
                        name="commit",
                        summary="Record changes to the repository",
                        examples=(
                            ExampleSpec(
                                command='git commit -m "<message>"',
                                description="Commit staged files",
                                source="tldr",
                                subcommand="commit",
                            ),
                        ),
                        source="tldr",
                    ),
                ),
            ),
        )
    config.write_index_meta(
        embedding_dim=0,
        embedding_model="",
        embedding_provider="none",
        tool_count=3,
        flag_count=3,
        subcommand_count=1,
        example_count=3,
    )


def test_normalize_argv_disambiguates_multiword_verb_queries() -> None:
    # Single zero-arg subcommand stays a subcommand
    assert _normalize_argv(["check"]) == ["check"]
    assert _normalize_argv(["check", "--json"]) == ["check", "--json"]
    assert _normalize_argv(["doctor"]) == ["doctor"]
    assert _normalize_argv(["show", "tar"]) == ["show", "tar"]
    assert _normalize_argv(["search", "disk", "usage"]) == ["search", "disk", "usage"]

    # Multi-word natural language starting with `check` or `update` MUST route to `ask`!
    assert _normalize_argv(["check", "open", "ports"]) == ["ask", "check", "open", "ports"]
    assert _normalize_argv(["update", "system", "packages"]) == [
        "ask",
        "update",
        "system",
        "packages",
    ]
    assert _normalize_argv(["list", "files"]) == ["ask", "list", "files"]
    assert _normalize_argv(["--porcelain", "--", "-name flag"]) == [
        "ask",
        "--porcelain",
        "--",
        "-name flag",
    ]


def test_cli_tri_modal_search_show_ask_and_porcelain(isolated_env: dict[str, Path]) -> None:
    _seed_cli_index(isolated_env)

    # 1. `sem search` (apropos replacement) with --json
    s_res = runner.invoke(app, ["search", "disk usage by folder", "--json", "--no-daemon"])
    assert s_res.exit_code == 0, s_res.output
    s_data = json.loads(s_res.stdout)
    assert s_data["results"][0]["name"] == "du"

    # 2. `sem show` (man/--help replacement) with --flags filter and subcommand focus
    show_res = runner.invoke(app, ["show", "du", "--flags", "depth", "--json", "--no-daemon"])
    assert show_res.exit_code == 0
    show_data = json.loads(show_res.stdout)
    assert len(show_data["tool"]["flags"]) == 1
    assert show_data["tool"]["flags"][0]["short"] == "-d"

    sub_res = runner.invoke(app, ["show", "git", "commit", "--json", "--no-daemon"])
    assert sub_res.exit_code == 0
    assert json.loads(sub_res.stdout)["tool"]["focused_subcommand"]["name"] == "commit"

    missing_res = runner.invoke(app, ["show", "definitely_missing_xyz", "--no-daemon"])
    assert missing_res.exit_code == 3

    # 3. `sem ask` porcelain + JSON + multi-word `check open ports` routing
    porc_res = runner.invoke(
        app, _normalize_argv(["--porcelain", "--no-daemon", "show", "disk", "usage"])
    )
    # Note: `show` is a valid multi-arg subcommand, so explicit bare query uses `ask` or quotes
    porc_ask = runner.invoke(
        app, _normalize_argv(["--porcelain", "--no-daemon", "check", "open", "ports"])
    )
    assert porc_ask.exit_code == 0, porc_ask.output
    assert porc_ask.stdout.strip() == "ss -tulnp"

    json_ask = runner.invoke(app, ["ask", "show disk usage by folder", "--json", "--no-daemon"])
    assert json_ask.exit_code == 0
    ask_data = json.loads(json_ask.stdout)
    assert ask_data["command"] == "du -sh *"
    assert ask_data["verification"]["safety_level"] == "safe"
    assert porc_res.exit_code in (0, 2, 3)


def test_cli_doctor_config_model_and_init(isolated_env: dict[str, Path]) -> None:
    _seed_cli_index(isolated_env)

    doc_res = runner.invoke(app, ["doctor", "--json", "--no-daemon"])
    assert doc_res.exit_code == 0, doc_res.output
    doc_data = json.loads(doc_res.stdout)
    assert doc_data["index_complete"] is True
    assert doc_data["stats"]["tool_count"] == 3

    # Scriptable `sem config get / set / show`
    set_res = runner.invoke(app, ["config", "set", "daemon.idle_timeout_seconds", "600"])
    assert set_res.exit_code == 0
    get_res = runner.invoke(app, ["config", "get", "daemon.idle_timeout_seconds"])
    assert get_res.exit_code == 0
    assert get_res.stdout.strip() == "600"

    show_cfg = runner.invoke(app, ["config", "show"])
    assert show_cfg.exit_code == 0
    assert "idle_timeout_seconds = 600" in show_cfg.stdout

    # `sem model list` and `sem init zsh`
    m_res = runner.invoke(app, ["model", "list"])
    assert m_res.exit_code == 0
    assert "balanced" in m_res.stdout

    init_res = runner.invoke(app, ["init", "zsh"])
    assert init_res.exit_code == 0
    assert "_sem_transform" in init_res.stdout
