"""Command-line interface for sempropos."""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sempropos.config import (
    SUPPORTED_SYNTHESIS_PROVIDERS,
    SUPPORTED_EMBEDDING_PROVIDERS,
    IntelligenceConfig,
    RoleConfig,
    intelligence_config_path,
    load_intelligence_config,
    save_intelligence_config,
)
from sempropos.index import (
    build_index,
    detect_package_db,
    get_connection,
    initialize_database,
    is_index_stale,
    read_last_indexed,
)

# Explicit direct-module imports to avoid __init__.py export issues
from sempropos.intelligence import (
    ProviderUnavailableError,
    ProviderConfigurationError,
    list_embedding_providers,
    list_synthesis_providers,
    get_provider_models,
    detect_synthesis_provider,
    build_prompt,
    synthesize,
)

from sempropos.retrieval import (
    expand_query,
    get_relevant_flags,
    query_bm25_ranking,
    query_semantic_matches,
    reciprocal_rank_fusion,
)

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Semantic command proposer based on local man pages.",
)
console = Console()

# ==================================================
# Internal helper functions for CLI modes
# ==================================================

def _load_candidates(tool_ids: list[int], query: str) -> list[dict]:
    candidates: list[dict] = []
    missing_tool_ids: list[int] = []

    with get_connection() as conn:
        for tool_id in tool_ids:
            row = conn.execute(
                "SELECT id, name, synopsis, description FROM tools WHERE id = ?",
                (tool_id,),
            ).fetchone()
            if row is None:
                missing_tool_ids.append(tool_id)
                continue

            examples = conn.execute(
                "SELECT command, context FROM examples WHERE tool_id = ? ORDER BY id",
                (tool_id,),
            ).fetchall()
            candidates.append(
                {
                    "tool_id": int(row["id"]),
                    "tool": row["name"],
                    "synopsis": row["synopsis"] or "",
                    "description": row["description"],
                    "flags": get_relevant_flags(int(row["id"]), query, top_k=6),
                    "examples": [
                        {"command": item["command"], "context": item["context"]}
                        for item in examples
                    ],
                }
            )

    if missing_tool_ids:
        console.print(
            "[yellow]Warning: some indexed tools are missing. Run `sempropos --update`[/yellow]",
            style="bold",
        )
    return candidates

def _print_tier0(candidates: list[dict], reason: str = "no_provider") -> None:
    if reason == "no_candidates":
        console.print(
            "[yellow]No relevant tools were found in the local index.[/yellow]"
        )
        return

    console.print(
        Panel(
            "[yellow]Synthesis backend unavailable. Showing matched man pages directly from local index:[/yellow]"
        )
    )

    if not candidates:
        console.print("[yellow]No tool matches found in the local index.[/yellow]")
        return

    for c in candidates:
        synopsis = f"[italic]{c['synopsis']}[/italic]\n" if c.get("synopsis") else ""

        flags_text = ""
        for flag in c.get("flags", [])[:3]:
            label = flag.get("flag") or "*"
            desc = flag.get("description", "").strip()
            flags_text += f"[bold cyan]{label}[/bold cyan]: {desc}\n"

        examples_text = ""
        for ex in c.get("examples", [])[:3]:
            examples_text += f"[bold green]> {ex['command']}[/bold green]\n"

        content = Text.from_markup(f"{synopsis}\n{flags_text}\n{examples_text}")
        console.print(
            Panel(
                content,
                title=f"[bold]{c['tool']}[/bold]",
                title_align="left",
                border_style="cyan",
            )
        )

# ==================================================
# modes: setup, configure, install/update, check
# ==================================================

def configure_mode() -> None:
    console.print("[bold blue]Configuring intelligence providers...[/bold blue]\n")

    avail_synth = [str(p) for p, ok in list_synthesis_providers().items() if ok]
    if not avail_synth:
        console.print("[red]No synthesis providers available. Exiting.[/red]")
        raise typer.Exit(1)

    synth_choice = questionary.select(
        "Choose a synthesis provider:",
        choices=avail_synth,
        default="tier0" if "tier0" in avail_synth else None,
    ).ask()
    if synth_choice is None:
        raise typer.Exit(1)

    # Safely get models and trap ProviderConfigurationError if pip install is missing
    try:
        synth_models = get_provider_models(synth_choice)
    except ProviderConfigurationError as e:
        console.print(f"\n[bold red]Configuration Action Required:[/bold red] {e}")
        raise typer.Exit(1)

    synth_model_choice = (
        questionary.select("Choose a model for synthesis:", choices=synth_models).ask()
        if synth_models
        else ""
    )
    if synth_model_choice is None:
        raise typer.Exit(1)

    avail_embed = [str(p) for p, ok in list_embedding_providers().items() if ok]
    if not avail_embed:
        console.print("[red]No embedding providers available. Exiting.[/red]")
        raise typer.Exit(1)

    embed_choice = questionary.select(
        "Choose an embedding provider:",
        choices=avail_embed,
        default="fastembed_local" if "fastembed_local" in avail_embed else None,
    ).ask()
    if embed_choice is None:
        raise typer.Exit(1)

    try:
        embed_models = get_provider_models(embed_choice)
    except ProviderConfigurationError as e:
        console.print(f"\n[bold red]Configuration Action Required:[/bold red] {e}")
        raise typer.Exit(1)

    embed_model_choice = (
        questionary.select("Choose a model for embedding:", choices=embed_models).ask()
        if embed_models
        else ""
    )
    if embed_model_choice is None:
        raise typer.Exit(1)

    new_config = IntelligenceConfig(
        synthesis=RoleConfig(provider=synth_choice, model=synth_model_choice),
        embedding=RoleConfig(provider=embed_choice, model=embed_model_choice),
    )
    save_intelligence_config(new_config)
    console.print("\n[bold green]Configuration saved successfully![/bold green]")

def setup_mode() -> None:
    console.print(Panel.fit("[bold blue]Welcome to sempropos setup![/bold blue]"))
    console.print("Checking system requirements...")

    try:
        import sqlite3

        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE test USING fts5(content);")
        conn.close()
    except sqlite3.OperationalError:
        console.print(
            "[red]Error: FTS5 is not available in your SQLite installation.[/red]"
        )
        raise typer.Exit(1)

    try:
        subprocess.run(
            ["man", "--version"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[red]Error: 'man' command is not available in PATH.[/red]")
        raise typer.Exit(1)

    console.print("[green]System requirements met.[/green]\n")
    configure_mode()

    console.print(
        "\n[bold blue]Building initial index...[/bold blue] (This may take a moment)"
    )
    try:
        # FORCE override deletes the old db preventing version block
        build_index(progress=True, force=True)
        console.print("[bold green]Index built successfully![/bold green]")
    except Exception as e:
        console.print(f"[red]Error: Failed to build index. {e}[/red]")
        raise typer.Exit(1)

    console.print(
        "\n[bold green]Setup completed successfully![/bold green] You can now query your system."
    )

def _query_mode(query: str, backend_override: str | None = None) -> None:
    initialize_database()

    if is_index_stale():
        console.print(
            "[yellow][sempropos] Package database has changed. Run `sempropos --update` to refresh index.[/yellow]"
        )

    try:
        tokens = expand_query(query)
        bm25_results = query_bm25_ranking(tokens, top_k=10)
        semantic_results = query_semantic_matches(query, top_k=10)
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    fused = reciprocal_rank_fusion([bm25_results, semantic_results], k=60)
    ranked_ids = (
        [tool_id for tool_id, _score in fused[:3]]
        or [tool_id for tool_id, _ in bm25_results[:3]]
        or [tool_id for tool_id, _ in semantic_results[:3]]
    )

    candidates = _load_candidates(ranked_ids, query)
    if not candidates:
        _print_tier0(candidates, reason="no_candidates")
        raise typer.Exit(0)

    try:
        selected_backend = backend_override or detect_synthesis_provider()[0]
    except ProviderUnavailableError as exc:
        console.print(f"[red]Backend unavailable: {exc}[/red]")
        _print_tier0(candidates, reason="synthesis_unavailable")
        raise typer.Exit(0)

    if selected_backend == "tier0":
        _print_tier0(candidates, reason="no_provider")
        raise typer.Exit(0)

    built_prompt = build_prompt(query, candidates)

    try:
        # Synthesize now correctly extracts from the Dataclass
        result = synthesize(built_prompt, backend=selected_backend)
        response = result.command.strip() if result.command else result.raw_output.strip()
    except ProviderConfigurationError as exc:
        console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        _print_tier0(candidates, reason="synthesis_unavailable")
        raise typer.Exit(0)
    except Exception as exc:
        console.print(f"[red]Backend error: {exc}[/red]")
        _print_tier0(candidates, reason="synthesis_unavailable")
        raise typer.Exit(0)

    if response:
        console.print(
            Panel(
                f"[bold green]{response}[/bold green]",
                border_style="green",
                title="Generated Command",
                title_align="left",
            )
        )
        raise typer.Exit(0)

    _print_tier0(candidates, reason="empty_response")

@app.callback()
def cli_core(
    ctx: typer.Context,
    query: list[str] = typer.Argument(None, help="Natural language task description"),
    setup: bool = typer.Option(
        False, "--setup", help="Run the first-time setup wizard"
    ),
    configure: bool = typer.Option(
        False, "--configure", help="Configure intelligence providers interactively"
    ),
    install: bool = typer.Option(False, "--install", help="Build index and embeddings"),
    update: bool = typer.Option(False, "--update", help="Refresh index and embeddings"),
    check: bool = typer.Option(False, "--check", help="Check index staleness"),
    backend: str = typer.Option(
        None, "--backend", help="Force a specific synthesis backend for this invocation"
    ),
    list_providers: bool = typer.Option(
        False,
        "--list-providers",
        help="List supported synthesis and embedding providers",
    ),
    show_config: bool = typer.Option(
        False, "--show-config", help="Print current intelligence configuration"
    ),
    version: bool = typer.Option(False, "--version", help="Print version and exit"),
):
    flags = [
        setup,
        configure,
        install,
        update,
        check,
        list_providers,
        show_config,
        version,
    ]
    if sum(flags) > 1:
        console.print(
            "[red]Error: Use at most one management mode per invocation.[/red]"
        )
        raise typer.Exit(1)

    if any(flags) and query:
        console.print(
            "[red]Error: Query arguments cannot be combined with management modes.[/red]"
        )
        raise typer.Exit(1)

    if version:
        from sempropos import __version__

        console.print(__version__)
        raise typer.Exit(0)

    if setup:
        setup_mode()
        raise typer.Exit(0)
    if configure:
        configure_mode()
        raise typer.Exit(0)
    if list_providers:
        synthesis = list_synthesis_providers()
        embedding = list_embedding_providers()

        table = Table(title="Intelligence Providers")
        table.add_column("Type", style="cyan")
        table.add_column("Provider", style="magenta")
        table.add_column("Status", justify="right")

        for name in SUPPORTED_SYNTHESIS_PROVIDERS:
            table.add_row(
                "Synthesis",
                name,
                "[green]Yes[/green]" if synthesis.get(name) else "[red]No[/red]",
            )
        for name in SUPPORTED_EMBEDDING_PROVIDERS:
            table.add_row(
                "Embedding",
                name,
                "[green]Yes[/green]" if embedding.get(name) else "[red]No[/red]",
            )

        console.print(table)
        raise typer.Exit(0)
    if show_config:
        settings = load_intelligence_config()
        console.print(f"[cyan]Config path:[/cyan] {intelligence_config_path()}")
        console.print(f"[cyan]Synthesis provider:[/cyan] {settings.synthesis.provider}")
        console.print(f"[cyan]Embedding provider:[/cyan] {settings.embedding.provider}")
        raise typer.Exit(0)
    if install or update:
        action = "install" if install else "update"
        try:
            # Install forces a wipe. Update is incremental.
            build_index(progress=True, force=bool(install))
            console.print(
                f"[bold green]{action.capitalize()} completed (index built).[/bold green]"
            )
        except Exception as exc:
            console.print(f"[red]{action} failed: {exc}[/red]")
            raise typer.Exit(1)
        raise typer.Exit(0)
    if check:
        last = read_last_indexed()
        pkg_db = detect_package_db()
        stale = is_index_stale()
        console.print(f"Last indexed: {last.isoformat() if last else 'never'}")
        console.print(f"Package DB: {pkg_db or 'unknown'}")
        if pkg_db:
            mtime = datetime.fromtimestamp(Path(pkg_db).stat().st_mtime)
            console.print(f"Package DB mtime: {mtime.isoformat()}")
        console.print(
            f"Update needed: {'[red]yes[/red]' if stale else '[green]no[/green]'}"
        )
        raise typer.Exit(0)

    if query:
        full_query = " ".join(query).strip()
        if not full_query:
            console.print("[red]Query cannot be empty.[/red]")
            raise typer.Exit(1)
        _query_mode(full_query, backend_override=backend)
        raise typer.Exit(0)

    console.print(ctx.get_help())
    raise typer.Exit(1)

def main():
    app()

if __name__ == "__main__":
    main()