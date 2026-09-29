"""Command-line interface for sempropos (`sem`).

Management is exposed as subcommands (`install`, `update`, `check`, ...) rather
than `--flags`. A bare natural-language query still works:

    sem "list files in an archive"
    sem ask "list files in an archive"
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sempropos.config import (
    RECOMMENDED_SYNTHESIS_MODEL,
    SUPPORTED_EMBEDDING_PROVIDERS,
    SUPPORTED_SYNTHESIS_PROVIDERS,
    IntelligenceConfig,
    RoleConfig,
    intelligence_config_path,
    load_intelligence_config,
    save_intelligence_config,
)

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Semantic command proposer based on local man pages.",
    epilog=(
        "Examples:\n"
        '  sem "list files in an archive"\n'
        '  sem ask "find files larger than 100MB"\n'
        "  sem update"
    ),
)
console = Console()


# ==================================================
# Internal helpers
# ==================================================


def _load_candidates(tool_ids: list[int], query: str, quiet: bool = False) -> list[dict]:
    from sempropos.index import get_connection
    from sempropos.retrieval import get_relevant_flags

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

    if missing_tool_ids and not quiet:
        console.print(
            "[yellow]Warning: some indexed tools are missing. Run `sem update`.[/yellow]"
        )
    return candidates


def _short(text: str, limit: int) -> str:
    """Collapse whitespace and truncate long man-page text for display."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def _print_tier0(candidates: list[dict], reason: str = "no_provider") -> None:
    if reason == "no_candidates":
        console.print("[yellow]No relevant tools were found in the local index.[/yellow]")
        return

    console.print(
        Panel(
            "[yellow]Synthesis backend unavailable. "
            "Showing matched man pages directly from the local index:[/yellow]"
        )
    )

    if not candidates:
        console.print("[yellow]No tool matches found in the local index.[/yellow]")
        return

    for candidate in candidates:
        lines: list[str] = []

        synopsis = _short(candidate.get("synopsis") or "", 200)
        if synopsis:
            lines.append(f"[italic]{escape(synopsis)}[/italic]")

        for flag in (candidate.get("flags") or [])[:4]:
            label = escape(flag.get("flag") or "*")
            desc = _short(flag.get("description") or "", 120)
            lines.append(f"[bold cyan]{label}[/bold cyan] {escape(desc)}".rstrip())

        for example in (candidate.get("examples") or [])[:3]:
            command = _short(example.get("command") or "", 160)
            if command:
                lines.append(f"[bold green]> {escape(command)}[/bold green]")

        content = Text.from_markup("\n".join(lines))
        console.print(
            Panel(
                content,
                title=f"[bold]{escape(candidate['tool'])}[/bold]",
                title_align="left",
                border_style="cyan",
            )
        )


def _query_mode(  # noqa: C901 — branchy dispatcher: index, retrieval, backend, output
    query: str, backend_override: str | None = None, porcelain: bool = False
) -> None:
    """Resolve a query to a command.

    With ``porcelain`` the command is written to stdout on its own (for shell
    widgets and scripting), diagnostics go to stderr, and any failure exits
    non-zero. Otherwise a rich panel is printed and fallbacks are shown.
    """
    from sempropos import config
    from sempropos.index import is_index_stale
    from sempropos.intelligence import (
        ProviderConfigurationError,
        build_prompt,
        detect_synthesis_provider,
        extract_command,
        synthesize,
    )
    from sempropos.intelligence.contracts import ProviderUnavailableError
    from sempropos.retrieval import (
        expand_query,
        hybrid_fusion,
        primary_tokens,
        query_bm25_ranking,
        query_semantic_matches,
        tokenize,
    )

    def fail(message: str, candidates: list[dict] | None = None) -> None:
        if porcelain:
            typer.echo(message, err=True)
            raise typer.Exit(1)
        console.print(f"[bold red]{message}[/bold red]")
        if candidates is not None:
            _print_tier0(candidates, reason="synthesis_unavailable")
        raise typer.Exit(0)

    # Fail fast with an actionable message instead of silently degrading.
    try:
        config.read_validated_index_meta()
    except RuntimeError as exc:
        fail(str(exc))

    if not porcelain and is_index_stale():
        console.print(
            "[yellow][sem] Package database has changed. "
            "Run `sem update` to refresh the index.[/yellow]"
        )

    try:
        tokens = expand_query(query)
        bm25_results = query_bm25_ranking(
            tokens,
            top_k=10,
            name_tokens=tokenize(query),
            primary_tokens=primary_tokens(query),
        )
        semantic_results = query_semantic_matches(query, top_k=10)
    except RuntimeError as exc:
        fail(str(exc))

    fused = hybrid_fusion(bm25_results, semantic_results)
    ranked_ids = (
        [tool_id for tool_id, _score in fused[:3]]
        or [tool_id for tool_id, _ in bm25_results[:3]]
        or [tool_id for tool_id, _ in semantic_results[:3]]
    )

    candidates = _load_candidates(ranked_ids, query, quiet=porcelain)
    if not candidates:
        if porcelain:
            typer.echo("No relevant tools were found in the local index.", err=True)
            raise typer.Exit(1)
        _print_tier0(candidates, reason="no_candidates")
        raise typer.Exit(0)

    try:
        selected_backend = backend_override or detect_synthesis_provider()[0]
    except ProviderUnavailableError as exc:
        fail(f"Backend unavailable: {exc}", candidates)

    if selected_backend == "tier0":
        fail("No synthesis backend is configured.", candidates)

    built_prompt = build_prompt(query, candidates)

    try:
        result = synthesize(built_prompt, backend=selected_backend)
    except ProviderConfigurationError as exc:
        fail(f"Configuration error: {exc}", candidates)
    except Exception as exc:  # noqa: BLE001
        fail(f"Backend error: {exc}", candidates)

    command = extract_command(result.command or result.raw_output)
    if command is None:
        command = _repair_command(
            built_prompt, result.command or result.raw_output, selected_backend
        )

    if command:
        if porcelain:
            typer.echo(command)
            raise typer.Exit(0)
        console.print(
            Panel(
                f"[bold green]{escape(command)}[/bold green]",
                border_style="green",
                title="Generated Command",
                title_align="left",
            )
        )
        raise typer.Exit(0)

    if porcelain:
        typer.echo("Model did not return a usable command.", err=True)
        raise typer.Exit(1)

    console.print(
        "[yellow]Model did not return a usable command; showing retrieved man pages.[/yellow]"
    )
    _print_tier0(candidates, reason="empty_response")


def _repair_command(built_prompt, raw: str, backend: str) -> str | None:
    """Ask the model once more when its first reply was not a runnable command.

    Returns the repaired command, or ``None``. The explicit ``NONE`` sentinel is
    respected and never repaired, so an honest "no tool fits" is not overridden.
    """
    from sempropos.intelligence import extract_command, synthesize
    from sempropos.intelligence.contracts import StructuredPrompt

    raw = (raw or "").strip()
    if not raw or raw.upper().strip(".!") == "NONE":
        return None

    repair_prompt = StructuredPrompt(
        system=built_prompt.system,
        context=built_prompt.context,
        query=(
            f"{built_prompt.query}\n"
            f"Your previous reply {raw!r} was not a runnable command. "
            "Reply with exactly one concrete shell command, or NONE."
        ),
    )
    try:
        retry = synthesize(repair_prompt, backend=backend)
    except Exception:  # noqa: BLE001
        return None
    return extract_command(retry.command or retry.raw_output)


def _suggest_synthesis_config() -> IntelligenceConfig:
    """Pick a reasonable default synthesis backend based on what is installed."""
    from sempropos.config import RECOMMENDED_EMBEDDING_MODEL
    from sempropos.intelligence import get_provider_models, list_synthesis_providers

    synthesis = RoleConfig(provider="tier0", model=None)
    if list_synthesis_providers().get("ollama"):
        try:
            installed = get_provider_models("ollama")
        except Exception:  # noqa: BLE001
            installed = []
        model = (
            RECOMMENDED_SYNTHESIS_MODEL
            if RECOMMENDED_SYNTHESIS_MODEL in installed
            else (installed[0] if installed else RECOMMENDED_SYNTHESIS_MODEL)
        )
        synthesis = RoleConfig(provider="ollama", model=model)

    return IntelligenceConfig(
        synthesis=synthesis,
        embedding=RoleConfig(provider="fastembed_local", model=RECOMMENDED_EMBEDDING_MODEL),
    )


def _ensure_default_config() -> None:
    if intelligence_config_path().exists():
        return
    save_intelligence_config(_suggest_synthesis_config())


def _run_build(force: bool) -> None:
    from sempropos.index import build_index

    action = "install" if force else "update"
    try:
        build_index(progress=True, force=force)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]{action} failed: {exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold green]{action.capitalize()} completed (index built).[/bold green]")

    from sempropos.intelligence import warm_synthesis_provider

    warmed = warm_synthesis_provider()
    if warmed is not None:
        provider_name, detail = warmed
        console.print(f"[dim]Synthesis backend '{provider_name}': {detail}.[/dim]")


def _check_environment() -> None:
    import sqlite3

    try:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE VIRTUAL TABLE test USING fts5(content);")
        conn.close()
    except sqlite3.OperationalError:
        console.print("[red]Error: FTS5 is not available in your SQLite installation.[/red]")
        raise typer.Exit(1)

    try:
        subprocess.run(
            ["man", "--version"],
            check=True,
            capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("[red]Error: 'man' command is not available in PATH.[/red]")
        raise typer.Exit(1)


# ==================================================
# Commands
# ==================================================


@app.command()
def install() -> None:
    """First-time setup: check requirements, write config, and build the index."""
    console.print(Panel.fit("[bold blue]Welcome to sem setup![/bold blue]"))
    console.print("Checking system requirements...")
    _check_environment()
    console.print("[green]System requirements met.[/green]\n")

    _ensure_default_config()
    settings = load_intelligence_config()
    console.print(
        f"[dim]Synthesis: {settings.synthesis.provider}"
        f"{f' ({settings.synthesis.model})' if settings.synthesis.model else ''} | "
        f"Embedding: {settings.embedding.provider or 'fastembed_local'}[/dim]\n"
    )

    console.print("[bold blue]Building index...[/bold blue] (this may take a while)")
    _run_build(force=True)
    console.print("\n[bold green]Setup complete![/bold green] Try: sem \"list files\"")


@app.command()
def update() -> None:
    """Refresh the index with newly installed tools (incremental)."""
    _run_build(force=False)


@app.command()
def check() -> None:
    """Check index freshness and completeness."""
    from sempropos import config
    from sempropos.index import detect_package_db, is_index_stale, read_last_indexed

    last = read_last_indexed()
    pkg_db = detect_package_db()
    stale = is_index_stale()
    complete = config.is_index_complete()

    console.print(f"Index complete: {'[green]yes[/green]' if complete else '[red]no[/red]'}")
    console.print(f"Last indexed: {last.isoformat() if last else 'never'}")
    console.print(f"Package DB: {pkg_db or 'unknown'}")
    if pkg_db:
        mtime = datetime.fromtimestamp(Path(pkg_db).stat().st_mtime)
        console.print(f"Package DB mtime: {mtime.isoformat()}")
    console.print(f"Update needed: {'[red]yes[/red]' if stale else '[green]no[/green]'}")

    if not complete:
        raise typer.Exit(1)


@app.command()
def configure() -> None:
    """Configure the synthesis and embedding providers interactively."""
    import questionary

    from sempropos.intelligence import (
        ProviderConfigurationError,
        get_provider_models,
        list_embedding_providers,
        list_synthesis_providers,
    )

    console.print("[bold blue]Configuring intelligence providers...[/bold blue]\n")

    avail_synth = [name for name, ok in list_synthesis_providers().items() if ok]
    if not avail_synth:
        console.print("[red]No synthesis providers available. Exiting.[/red]")
        raise typer.Exit(1)

    default_synth = (
        "ollama" if "ollama" in avail_synth else ("openai_compatible" if "openai_compatible" in avail_synth else "tier0")
    )
    synth_choice = questionary.select(
        "Choose a synthesis provider:",
        choices=avail_synth,
        default=default_synth,
    ).ask()
    if synth_choice is None:
        raise typer.Exit(1)

    if synth_choice == "openai_compatible":
        base_url = questionary.text(
            "Base URL (OpenAI-compatible /chat/completions root):",
            default="https://api.openai.com/v1",
        ).ask()
        if base_url is None:
            raise typer.Exit(1)
        model_text = questionary.text(
            "Model name (as the endpoint expects it):"
        ).ask()
        if model_text is None:
            raise typer.Exit(1)
        api_key_env = questionary.text(
            "API key environment variable name (blank to use keyring/plaintext):"
        ).ask()
        synth_config = RoleConfig(
            provider=synth_choice,
            model=model_text.strip() or None,
            base_url=base_url or None,
            api_key_env=(api_key_env or None),
        )
    else:
        try:
            synth_models = get_provider_models(synth_choice)
        except ProviderConfigurationError as exc:
            console.print(f"\n[bold red]Configuration Action Required:[/bold red] {exc}")
            raise typer.Exit(1)

        synth_model_choice = (
            questionary.select("Choose a model for synthesis:", choices=synth_models).ask()
            if synth_models
            else ""
        )
        if synth_model_choice is None:
            raise typer.Exit(1)
        synth_config = RoleConfig(provider=synth_choice, model=synth_model_choice or None)

    avail_embed = [name for name, ok in list_embedding_providers().items() if ok]
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
    except ProviderConfigurationError as exc:
        console.print(f"\n[bold red]Configuration Action Required:[/bold red] {exc}")
        raise typer.Exit(1)

    embed_model_choice = (
        questionary.select("Choose a model for embedding:", choices=embed_models).ask()
        if embed_models
        else ""
    )
    if embed_model_choice is None:
        raise typer.Exit(1)

    new_config = IntelligenceConfig(
        synthesis=synth_config,
        embedding=RoleConfig(provider=embed_choice, model=embed_model_choice or None),
    )
    save_intelligence_config(new_config)
    console.print("\n[bold green]Configuration saved successfully![/bold green]")

    console.print(
        "[dim]If the embedding provider or model changed, run `sem update` "
        "to re-embed the index.[/dim]"
    )


@app.command()
def providers() -> None:
    """List supported synthesis and embedding providers and their status."""
    from sempropos.intelligence import list_embedding_providers, list_synthesis_providers

    synthesis = list_synthesis_providers()
    embedding = list_embedding_providers()

    table = Table(title="Intelligence Providers")
    table.add_column("Type", style="cyan")
    table.add_column("Provider", style="magenta")
    table.add_column("Status", justify="right")

    for name in SUPPORTED_SYNTHESIS_PROVIDERS:
        table.add_row(
            "Synthesis", name, "[green]Yes[/green]" if synthesis.get(name) else "[red]No[/red]"
        )
    for name in SUPPORTED_EMBEDDING_PROVIDERS:
        table.add_row(
            "Embedding", name, "[green]Yes[/green]" if embedding.get(name) else "[red]No[/red]"
        )

    console.print(table)


@app.command()
def version() -> None:
    """Print the sem version."""
    from sempropos import __version__

    console.print(__version__)


@app.command()
def init(
    shell: str = typer.Argument(..., help="Shell to configure: zsh, bash, or fish"),
) -> None:
    """Print shell integration for Ctrl+S.

    Add it to your shell startup file, e.g.:

        eval "$(sem init zsh)"
    """
    from sempropos.shell import integration_script, supported_shells

    try:
        script = integration_script(shell)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        typer.echo(f"Supported shells: {', '.join(supported_shells())}", err=True)
        raise typer.Exit(1)
    # Printed verbatim so it can be eval'd.
    typer.echo(script)


@app.command()
def ask(
    query: list[str] = typer.Argument(..., help="Natural language task description"),
    backend: str = typer.Option(None, "--backend", help="Force a synthesis backend"),
    porcelain: bool = typer.Option(
        False,
        "--porcelain",
        help="Print only the command (for shell integration and scripting)",
    ),
) -> None:
    """Turn a natural-language task into a shell command."""
    full_query = " ".join(query).strip()
    if not full_query:
        typer.echo("Query cannot be empty.", err=True)
        raise typer.Exit(1)
    _query_mode(full_query, backend_override=backend, porcelain=porcelain)


# ==================================================
# Bare-query entry point
# ==================================================


@app.callback()
def cli_core(
    ctx: typer.Context,
    backend: str = typer.Option(
        None, "--backend", help="Force a specific synthesis backend for this query"
    ),
    porcelain: bool = typer.Option(
        False,
        "--porcelain",
        help="Print only the command (for shell integration and scripting)",
    ),
) -> None:
    """sem — ask for a command in plain English."""
    # A registered subcommand handles itself.
    if ctx.invoked_subcommand is not None:
        return

    # Anything left over is treated as a natural-language query:
    #   sem "list files in an archive"
    query = " ".join(ctx.args).strip()
    if query:
        _query_mode(query, backend_override=backend, porcelain=porcelain)
        raise typer.Exit(0)

    console.print(ctx.get_help())
    raise typer.Exit(1)


def main() -> None:
    app(_normalize_argv(sys.argv[1:]))


# Subcommands that must not be mistaken for the first word of a query.
_KNOWN_COMMANDS = {
    "install",
    "update",
    "check",
    "configure",
    "providers",
    "init",
    "version",
    "ask",
    "--help",
    "-h",
}

# Options that consume a following value (used when scanning for the query).
_VALUE_OPTIONS = {"--backend"}


def _normalize_argv(argv: list[str]) -> list[str]:
    """Route a bare natural-language query to the `ask` command.

    Typer/Click insist on resolving the first token as a subcommand, so
    ``sem "list files"`` would otherwise fail with "No such command".
    Inserting ``ask`` keeps both the bare and explicit forms working.
    """
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            index += 1
            break
        if token.startswith("-"):
            index += 2 if token in _VALUE_OPTIONS else 1
            continue
        break

    if index < len(argv) and argv[index] not in _KNOWN_COMMANDS:
        return ["ask", *argv]
    return argv


if __name__ == "__main__":
    main()
