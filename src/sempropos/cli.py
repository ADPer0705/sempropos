"""Professional tri-modal CLI for sempropos (``sem``).

Modes:
  - **Ask (command synthesis):** ``sem "find files larger than 100MB"`` or ``sem ask ...``
  - **Show (man/--help card):**  ``sem show tar`` / ``sem show git commit``
  - **Search (apropos):**        ``sem search "disk usage"``
  - **Operations:**              ``sem install``, ``update``, ``doctor``, ``model``,
                                 ``daemon``, ``config``, ``providers``, ``init``, ``version``
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import replace
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sempropos import __version__, config
from sempropos.utils import should_disable_color

# Exit codes following Unix conventions
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_FOUND = 3

app = typer.Typer(
    name="sem",
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
    help="Instant offline replacement for apropos, --help, and man pages.",
    epilog=(
        "Examples:\n"
        '  sem "find files larger than 100MB"\n'
        '  sem -e "delete files older than 30 days"\n'
        "  sem show tar --flags extract\n"
        '  sem search "open listening ports"\n'
        "  sem doctor"
    ),
)

model_app = typer.Typer(help="Manage curated local GGUF synthesis models.")
daemon_app = typer.Typer(help="Inspect or control the warm background daemon.")
config_app = typer.Typer(help="Inspect and update sem configuration.")

app.add_typer(model_app, name="model")
app.add_typer(daemon_app, name="daemon")
app.add_typer(config_app, name="config")

# Strictly separate primary stdout output from diagnostics on stderr.
_NO_COLOR = should_disable_color()
stdout_console = Console(no_color=_NO_COLOR, highlight=False)
stderr_console = Console(stderr=True, no_color=_NO_COLOR, highlight=False)


# ============================================================================
# Service & Daemon Dispatch Helper
# ============================================================================


def _call_service(
    method: str,
    params: dict[str, Any],
    *,
    no_daemon: bool = False,
) -> dict[str, Any]:
    """Execute a service method via the warm daemon (when enabled), falling back in-process."""
    cfg = config.load_config().daemon
    if cfg.enabled and not no_daemon:
        from sempropos.daemon.client import DaemonClient, DaemonUnavailableError

        client = DaemonClient()
        try:
            return client.request(method, params, auto_start=cfg.auto_start)
        except (DaemonUnavailableError, RuntimeError):
            pass

    from sempropos.service import SemService

    svc = SemService()
    try:
        if method == "ask":
            return svc.ask(
                str(params.get("query") or ""),
                tool_filter=params.get("tool_filter"),
                backend=params.get("backend"),
                alternatives=int(params.get("alternatives") or 1),
            )
        if method == "search":
            return svc.search(
                str(params.get("query") or ""),
                limit=int(params.get("limit") or 10),
                category=params.get("category"),
            )
        if method == "show":
            return svc.show(
                str(params.get("tool") or ""),
                subcommand=params.get("subcommand"),
                flag_filter=params.get("flag_filter"),
            )
        if method == "doctor":
            return svc.doctor()
        if method == "list_names":
            return {"names": svc.list_names()}
        raise ValueError(f"Unsupported method: {method}")
    finally:
        svc.close()


# ============================================================================
# Rendering Helpers
# ============================================================================


def _short(text: str, limit: int) -> str:
    collapsed = " ".join((text or "").split())
    if len(collapsed) > limit:
        return collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def _copy_to_clipboard(text: str) -> bool:
    """Best-effort copy to system clipboard (Wayland, X11, macOS)."""
    candidates = (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["pbcopy"],
    )
    for cmd in candidates:
        if shutil.which(cmd[0]):
            try:
                proc = subprocess.run(
                    cmd, input=text.encode("utf-8"), check=False, capture_output=True, timeout=2
                )
                if proc.returncode == 0:
                    return True
            except (OSError, subprocess.SubprocessError):
                continue
    return False


def _print_fallback_cards(candidates: list[dict[str, Any]], header: str) -> None:
    stderr_console.print(f"[yellow]{escape(header)}[/yellow]")
    if not candidates:
        stderr_console.print("[yellow]No matching tools found in the local index.[/yellow]")
        return

    for cand in candidates[:4]:
        lines: list[str] = []
        summary = _short(str(cand.get("summary") or ""), 160)
        if summary:
            lines.append(f"[dim]{escape(summary)}[/dim]")

        for sub in (cand.get("subcommands") or [])[:3]:
            sname = escape(str(sub.get("name") or ""))
            sdesc = _short(str(sub.get("summary") or ""), 100)
            lines.append(f"[bold magenta]{sname}[/bold magenta] {escape(sdesc)}".rstrip())

        for flag in (cand.get("flags") or [])[:4]:
            label = escape(str(flag.get("display") or flag.get("short") or "*"))
            desc = _short(str(flag.get("description") or ""), 110)
            lines.append(f"[bold cyan]{label}[/bold cyan] {escape(desc)}".rstrip())

        for ex in (cand.get("examples") or [])[:3]:
            cmd = _short(str(ex.get("command") or ""), 150)
            edesc = _short(str(ex.get("description") or ""), 90)
            if cmd:
                suffix = f"  [dim]# {escape(edesc)}[/dim]" if edesc else ""
                lines.append(f"[bold green]> {escape(cmd)}[/bold green]{suffix}")

        title = f"[bold]{escape(str(cand.get('name') or 'tool'))}[/bold]"
        sources = cand.get("sources") or []
        subtitle = f"[dim]{', '.join(str(s) for s in sources)}[/dim]" if sources else None
        stdout_console.print(
            Panel(
                Text.from_markup("\n".join(lines)),
                title=title,
                title_align="left",
                subtitle=subtitle,
                subtitle_align="right",
                border_style="cyan",
            )
        )


def _render_ask_response(  # noqa: C901
    resp: dict[str, Any],
    *,
    porcelain: bool,
    as_json: bool,
    explain: bool,
    copy_cmd: bool,
) -> int:
    status = str(resp.get("status") or "")
    command = resp.get("command")

    if as_json:
        typer.echo(json.dumps(resp, ensure_ascii=False, indent=2))
        if status == "ok":
            return EXIT_OK
        return EXIT_NOT_FOUND if status in ("index_error", "no_candidates") else EXIT_ERROR

    if porcelain:
        if status == "ok" and isinstance(command, str) and command.strip():
            typer.echo(command.strip())
            return EXIT_OK
        typer.echo(str(resp.get("error") or "No usable command generated."), err=True)
        return EXIT_NOT_FOUND if status in ("index_error", "no_candidates") else EXIT_ERROR

    if resp.get("stale"):
        stderr_console.print(
            "[yellow][sem] Installed tools changed since last index. Run `sem update`.[/yellow]"
        )

    if status == "index_error":
        stderr_console.print(
            f"[bold red]{escape(str(resp.get('error') or 'Index error'))}[/bold red]"
        )
        return EXIT_NOT_FOUND

    if status == "no_candidates":
        stderr_console.print(
            f"[yellow]{escape(str(resp.get('error') or 'No relevant tools found.'))}[/yellow]"
        )
        return EXIT_NOT_FOUND

    if status != "ok" or not isinstance(command, str) or not command.strip():
        _print_fallback_cards(
            resp.get("candidates") or [],
            str(resp.get("error") or "Showing matched tools from the local index:"),
        )
        return EXIT_OK if resp.get("provider") == "tier0" else EXIT_ERROR

    # Non-TTY plain output keeps `cmd=$(sem "...")` clean even without --porcelain.
    if not sys.stdout.isatty():
        typer.echo(command.strip())
        return EXIT_OK

    verification = resp.get("verification") or {}
    safety_level = str(verification.get("safety_level") or "safe")
    border = {"safe": "green", "caution": "yellow", "destructive": "red"}.get(safety_level, "green")

    body_lines = [f"[bold {border}]{escape(command.strip())}[/bold {border}]"]
    explanation = str(resp.get("explanation") or "").strip()
    if explanation:
        body_lines.append(f"[dim]{escape(explanation)}[/dim]")

    provider = str(resp.get("provider") or "")
    latency = resp.get("latency_ms")
    subtitle_bits = [provider] if provider else []
    if latency is not None:
        subtitle_bits.append(f"{latency}ms")

    stdout_console.print(
        Panel(
            Text.from_markup("\n".join(body_lines)),
            title=f"Command ({safety_level.upper()})" if safety_level != "safe" else "Command",
            title_align="left",
            subtitle=f"[dim]{' · '.join(subtitle_bits)}[/dim]" if subtitle_bits else None,
            subtitle_align="right",
            border_style=border,
        )
    )

    warnings = verification.get("warnings") or []
    if warnings:
        warn_style = "bold red" if safety_level == "destructive" else "bold yellow"
        warn_lines = [f"• {escape(str(w))}" for w in warnings]
        stderr_console.print(
            Panel(
                Text.from_markup("\n".join(warn_lines)),
                title=f"[{warn_style}]Safety Notice[/{warn_style}]",
                title_align="left",
                border_style="red" if safety_level == "destructive" else "yellow",
            )
        )

    if explain:
        flags_exp = verification.get("flags_explained") or []
        if flags_exp:
            tbl = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
            tbl.add_column("Flag", style="bold cyan", no_wrap=True)
            tbl.add_column("Meaning")
            for item in flags_exp:
                tbl.add_row(
                    str(item.get("flag") or ""), _short(str(item.get("description") or ""), 140)
                )
            stdout_console.print(tbl)
        unverified = verification.get("unverified_flags") or []
        if unverified:
            stderr_console.print(
                f"[dim]Unverified flags not in local ToolSpec: {', '.join(str(f) for f in unverified)}[/dim]"
            )

    alts = resp.get("alternatives") or []
    if alts:
        stdout_console.print("[bold]Alternatives:[/bold]")
        for alt in alts:
            stdout_console.print(f"  [green]{escape(str(alt))}[/green]")

    if copy_cmd:
        if _copy_to_clipboard(command.strip()):
            stderr_console.print("[dim]Copied command to clipboard.[/dim]")
        else:
            stderr_console.print(
                "[yellow]Clipboard utility not available (wl-copy/xclip/pbcopy).[/yellow]"
            )

    return EXIT_OK


def _render_tool_card_header(tool: dict[str, Any], focused: dict[str, Any] | None) -> None:
    name = str(tool.get("name") or "")
    title = f"[bold]{escape(name)}[/bold]"
    if focused:
        title += f" [bold magenta]{escape(str(focused.get('name') or ''))}[/bold magenta]"
    if tool.get("section"):
        title += f" [dim]({tool['section']})[/dim]"

    meta_parts: list[str] = []
    if tool.get("aliases"):
        meta_parts.append(f"aliases: {', '.join(str(a) for a in tool['aliases'])}")
    if tool.get("category"):
        meta_parts.append(f"category: {tool['category']}")
    sources = sorted({str(s.get("source")) for s in (tool.get("sources") or []) if s.get("source")})
    if sources:
        meta_parts.append(f"sources: {', '.join(sources)}")

    summary = str((focused or tool).get("summary") or tool.get("summary") or "")
    header_lines = [escape(summary)]
    if not focused and tool.get("description") and tool["description"].lower() != summary.lower():
        header_lines.append(f"[dim]{escape(_short(str(tool['description']), 260))}[/dim]")
    synopsis = (focused or tool).get("synopsis") or []
    if synopsis:
        header_lines.append("")
        header_lines.extend(f"[cyan]{escape(str(syn))}[/cyan]" for syn in synopsis[:3])

    stdout_console.print(
        Panel(
            Text.from_markup("\n".join(header_lines)),
            title=title,
            title_align="left",
            subtitle=f"[dim]{escape(' · '.join(meta_parts))}[/dim]" if meta_parts else None,
            subtitle_align="right",
            border_style="blue",
        )
    )


def _render_tool_card(
    tool: dict[str, Any],
    *,
    all_flags: bool,
    examples_only: bool,
) -> None:
    focused = tool.get("focused_subcommand")
    _render_tool_card_header(tool, focused)

    examples = (focused or tool).get("examples") or []
    if examples:
        stdout_console.print("\n[bold]Examples[/bold]")
        for ex in examples[:10]:
            cmd = str(ex.get("command") or "")
            desc = str(ex.get("description") or "")
            if desc:
                stdout_console.print(f"  [dim]# {escape(_short(desc, 120))}[/dim]")
            stdout_console.print(f"  [bold green]{escape(cmd)}[/bold green]")

    if examples_only:
        return

    subcommands = [] if focused else (tool.get("subcommands") or [])
    if subcommands:
        stdout_console.print(f"\n[bold]Subcommands[/bold] [dim]({len(subcommands)})[/dim]")
        stbl = Table(show_header=False, box=None, pad_edge=False)
        stbl.add_column("Sub", style="bold magenta", no_wrap=True)
        stbl.add_column("Summary")
        for sub in subcommands[:20]:
            stbl.add_row(str(sub.get("name") or ""), _short(str(sub.get("summary") or ""), 110))
        stdout_console.print(stbl)
        if len(subcommands) > 20:
            stdout_console.print(f"  [dim]… and {len(subcommands) - 20} more[/dim]")

    flags = (focused or tool).get("flags") or []
    if flags:
        limit = len(flags) if all_flags else 20
        stdout_console.print(
            f"\n[bold]Flags[/bold] [dim](showing {min(limit, len(flags))}/{len(flags)})[/dim]"
        )
        ftbl = Table(show_header=False, box=None, pad_edge=False)
        ftbl.add_column("Flag", style="bold cyan", no_wrap=True)
        ftbl.add_column("Description")
        for flag in flags[:limit]:
            ftbl.add_row(
                str(flag.get("display") or flag.get("short") or "*"),
                _short(str(flag.get("description") or ""), 130),
            )
        stdout_console.print(ftbl)
        if not all_flags and len(flags) > limit:
            stdout_console.print(
                f"  [dim]… {len(flags) - limit} more flags (use `--all-flags` or `--flags <keyword>`)[/dim]"
            )

    see_also = tool.get("see_also") or []
    if see_also:
        stdout_console.print(f"\n[dim]See also: {', '.join(str(x) for x in see_also[:12])}[/dim]")


# ============================================================================
# Tri-Modal Primary Commands: ask, show, search
# ============================================================================


@app.command()
def ask(
    query: list[str] = typer.Argument(..., help="Natural-language task description"),
    tool: str | None = typer.Option(
        None, "-t", "--tool", help="Scope synthesis to a specific tool"
    ),
    explain: bool = typer.Option(False, "-e", "--explain", help="Show flag-by-flag breakdown"),
    alternatives: int = typer.Option(
        1, "-n", "--alternatives", min=1, max=5, help="Number of command candidates"
    ),
    copy: bool = typer.Option(False, "-c", "--copy", help="Copy generated command to clipboard"),
    backend: str | None = typer.Option(None, "--backend", help="Override synthesis backend"),
    porcelain: bool = typer.Option(False, "--porcelain", help="Print only the runnable command"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON output"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Bypass the background daemon"),
) -> None:
    """Turn a natural-language task into a verified shell command."""
    full_query = " ".join(query).strip()
    if not full_query:
        typer.echo("Query cannot be empty.", err=True)
        raise typer.Exit(EXIT_USAGE)

    resp = _call_service(
        "ask",
        {
            "query": full_query,
            "tool_filter": tool,
            "backend": backend,
            "alternatives": alternatives,
        },
        no_daemon=no_daemon,
    )
    code = _render_ask_response(
        resp,
        porcelain=porcelain,
        as_json=as_json,
        explain=explain,
        copy_cmd=copy,
    )
    raise typer.Exit(code)


@app.command("show")
@app.command("doc", hidden=True)
def show_tool(
    tool_name: str = typer.Argument(..., help="Command or alias to inspect (e.g. tar, 7z, git)"),
    subcommand: str | None = typer.Argument(None, help="Optional subcommand (e.g. commit, reset)"),
    flags_filter: str | None = typer.Option(None, "-f", "--flags", help="Filter flags by keyword"),
    all_flags: bool = typer.Option(False, "--all-flags", help="Show every parsed flag"),
    examples_only: bool = typer.Option(False, "--examples", help="Show summary and examples only"),
    as_json: bool = typer.Option(False, "--json", help="Emit canonical ToolSpec JSON"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Bypass the background daemon"),
) -> None:
    """Instant structured reference card for a tool (replaces man and --help)."""
    resp = _call_service(
        "show",
        {"tool": tool_name, "subcommand": subcommand, "flag_filter": flags_filter},
        no_daemon=no_daemon,
    )
    if as_json:
        typer.echo(json.dumps(resp, ensure_ascii=False, indent=2))
        raise typer.Exit(EXIT_OK if resp.get("status") == "ok" else EXIT_NOT_FOUND)

    if resp.get("status") != "ok":
        stderr_console.print(f"[red]{escape(str(resp.get('error') or 'Tool not found'))}[/red]")
        raise typer.Exit(EXIT_NOT_FOUND)

    _render_tool_card(resp["tool"], all_flags=all_flags, examples_only=examples_only)


@app.command("search")
@app.command("apropos", hidden=True)
def search_tools(
    query: list[str] = typer.Argument(..., help="Search terms or natural-language intent"),
    limit: int = typer.Option(10, "-n", "--limit", min=1, max=50, help="Maximum tools to return"),
    category: str | None = typer.Option(None, "--category", help="Filter by tool category"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Bypass the background daemon"),
) -> None:
    """Instant ranked tool discovery (replaces apropos, 0ms LLM)."""
    full_query = " ".join(query).strip()
    if not full_query:
        typer.echo("Search query cannot be empty.", err=True)
        raise typer.Exit(EXIT_USAGE)

    resp = _call_service(
        "search",
        {"query": full_query, "limit": limit, "category": category},
        no_daemon=no_daemon,
    )
    if as_json:
        typer.echo(json.dumps(resp, ensure_ascii=False, indent=2))
        raise typer.Exit(EXIT_OK if resp.get("status") == "ok" else EXIT_NOT_FOUND)

    if resp.get("status") != "ok":
        stderr_console.print(f"[red]{escape(str(resp.get('error') or 'Index unavailable'))}[/red]")
        raise typer.Exit(EXIT_NOT_FOUND)

    results = resp.get("results") or []
    if not results:
        stderr_console.print(f"[yellow]No installed tools matched '{escape(full_query)}'.[/yellow]")
        raise typer.Exit(EXIT_NOT_FOUND)

    table = Table(title=f"Tools matching '{full_query}' ({resp.get('latency_ms', 0)}ms)")
    table.add_column("Tool", style="bold cyan", no_wrap=True)
    table.add_column("Category", style="magenta", no_wrap=True)
    table.add_column("Summary")
    table.add_column("Hint", style="green")

    for item in results:
        name = str(item.get("name") or "")
        aliases = item.get("aliases") or []
        label = f"{name} ({', '.join(aliases[:2])})" if aliases else name
        cat = str(item.get("category") or "—")
        summary = _short(str(item.get("summary") or ""), 72)

        hint = ""
        examples = item.get("examples") or []
        subs = item.get("subcommands") or []
        flags = item.get("flags") or []
        if examples:
            hint = _short(str(examples[0].get("command") or ""), 42)
        elif subs:
            hint = f"{name} {subs[0].get('name', '')}"
        elif flags:
            hint = str(flags[0].get("display") or "")
        table.add_row(label, cat, summary, hint)

    stdout_console.print(table)


@app.command("complete-tools", hidden=True)
def complete_tools() -> None:
    """Print indexed tool names one per line for shell tab-completion."""
    resp = _call_service("list_names", {}, no_daemon=False)
    for name in resp.get("names") or []:
        typer.echo(str(name))


# ============================================================================
# Lifecycle Commands: install, update, doctor (alias: check)
# ============================================================================


def _check_sqlite_fts5() -> None:
    try:
        conn = sqlite3.connect(":memory:")
        conn.execute(
            "CREATE VIRTUAL TABLE _fts_check USING fts5(content, tokenize='porter unicode61');"
        )
        conn.close()
    except sqlite3.OperationalError:
        stderr_console.print("[red]Error: SQLite FTS5 with porter tokenizer is unavailable.[/red]")
        raise typer.Exit(EXIT_ERROR)


def _suggest_initial_config(model_tier: str | None) -> config.AppConfig:
    from sempropos.intelligence import get_provider_models, list_synthesis_providers, models

    synth = config.SynthesisConfig(provider="tier0", model=None)
    if model_tier and model_tier.lower() != "none":
        spec = models.get_model_spec(model_tier)
        if spec is not None and models.resolve_model_path(spec.model_id) is not None:
            synth = config.SynthesisConfig(provider="local_gguf", model=spec.model_id)
    elif models.local_runtime_kind() is not None and models.installed_model_names():
        synth = config.SynthesisConfig(
            provider="local_gguf", model=models.installed_model_names()[0]
        )
    elif list_synthesis_providers().get("ollama"):
        try:
            installed = get_provider_models("ollama")
        except Exception:  # noqa: BLE001
            installed = []
        chosen = (
            config.RECOMMENDED_SYNTHESIS_MODEL
            if config.RECOMMENDED_SYNTHESIS_MODEL in installed
            else (installed[0] if installed else config.RECOMMENDED_SYNTHESIS_MODEL)
        )
        synth = config.SynthesisConfig(provider="ollama", model=chosen)

    return config.AppConfig(
        synthesis=synth,
        embedding=config.EmbeddingConfig(
            provider="fastembed_local", model=config.RECOMMENDED_EMBEDDING_MODEL
        ),
    )


def _print_build_report(action: str, report: Any) -> None:
    sources_str = ", ".join(f"{k}={v}" for k, v in sorted(report.source_counts.items()))
    stdout_console.print(
        f"[bold green]{action.capitalize()} completed:[/bold green] "
        f"{report.total_tools} tools "
        f"(+{report.updated_tools} updated, {report.unchanged_tools} unchanged, "
        f"-{report.removed_tools} pruned) | "
        f"{report.total_subcommands} subcommands · {report.total_flags} flags · "
        f"{report.total_examples} examples"
    )
    if sources_str:
        stderr_console.print(f"[dim]Sources: {sources_str}[/dim]")


@app.command()
def install(
    offline: bool = typer.Option(False, "--offline", help="Do not download tldr archive or models"),
    no_tldr: bool = typer.Option(False, "--no-tldr", help="Skip tldr-pages enrichment"),
    no_help_probe: bool = typer.Option(False, "--no-help-probe", help="Skip safe --help probing"),
    skip_embeddings: bool = typer.Option(
        False, "--skip-embeddings", help="Build lexical-only index"
    ),
    model_tier: str | None = typer.Option(
        None,
        "--model",
        help="Optional local GGUF model tier to download (tiny, balanced, quality, none)",
    ),
) -> None:
    """First-time setup: verify SQLite FTS5, write config, and build the structured v3 index."""
    from sempropos.daemon.client import DaemonClient
    from sempropos.index import build_index
    from sempropos.intelligence import models, warm_synthesis_provider

    stdout_console.print(Panel.fit("[bold blue]Welcome to sem setup![/bold blue]"))
    _check_sqlite_fts5()

    if model_tier and model_tier.lower() != "none" and not offline:
        stderr_console.print(f"[blue]Downloading local GGUF model ({model_tier})...[/blue]")
        try:
            models.download_model(model_tier)
        except Exception as exc:  # noqa: BLE001
            stderr_console.print(f"[yellow]Model download skipped: {exc}[/yellow]")

    if not config.config_path().exists():
        cfg = _suggest_initial_config(model_tier)
        if skip_embeddings:
            cfg = replace(cfg, embedding=config.EmbeddingConfig(provider="none", model=None))
        config.save_config(cfg)

    active = config.load_config()
    stderr_console.print(
        f"[dim]Synthesis: {active.synthesis.provider}"
        f"{f' ({active.synthesis.model})' if active.synthesis.model else ''} | "
        f"Embedding: {active.embedding.provider}[/dim]"
    )

    try:
        report = build_index(
            progress=sys.stderr.isatty(),
            force=True,
            offline=offline,
            enable_tldr=not no_tldr,
            probe_help=not no_help_probe,
            skip_embeddings=skip_embeddings,
        )
    except Exception as exc:  # noqa: BLE001
        stderr_console.print(f"[red]Install failed: {exc}[/red]")
        raise typer.Exit(EXIT_ERROR)

    _print_build_report("install", report)
    DaemonClient().notify_reload()

    warmed = warm_synthesis_provider()
    if warmed is not None:
        stderr_console.print(f"[dim]Synthesis backend '{warmed[0]}': {warmed[1]}.[/dim]")

    stdout_console.print(
        '\n[bold green]Setup complete![/bold green] Try: sem "show disk usage by folder"'
    )


@app.command()
def update(
    full: bool = typer.Option(False, "--full", help="Force a full rebuild from scratch"),
    offline: bool = typer.Option(False, "--offline", help="Do not perform network downloads"),
    no_tldr: bool = typer.Option(False, "--no-tldr", help="Skip tldr-pages enrichment"),
    no_help_probe: bool = typer.Option(False, "--no-help-probe", help="Skip safe --help probing"),
    skip_embeddings: bool = typer.Option(
        False, "--skip-embeddings", help="Skip vector embedding sync"
    ),
) -> None:
    """Incrementally refresh the index, re-indexing changed tools and pruning uninstalled ones."""
    from sempropos.daemon.client import DaemonClient
    from sempropos.index import build_index

    try:
        report = build_index(
            progress=sys.stderr.isatty(),
            force=full,
            offline=offline,
            enable_tldr=not no_tldr,
            probe_help=not no_help_probe,
            skip_embeddings=skip_embeddings,
        )
    except Exception as exc:  # noqa: BLE001
        stderr_console.print(f"[red]Update failed: {exc}[/red]")
        raise typer.Exit(EXIT_ERROR)

    _print_build_report("update", report)
    if DaemonClient().notify_reload():
        stderr_console.print("[dim]Notified running sem daemon to reload index.[/dim]")


@app.command("doctor")
@app.command("check", hidden=True)
def doctor(
    as_json: bool = typer.Option(False, "--json", help="Emit diagnostics as JSON"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Bypass the background daemon"),
) -> None:
    """Report index health, parse coverage, unindexed PATH tools, daemon, and providers."""
    from sempropos.daemon.client import DaemonClient

    report = _call_service("doctor", {}, no_daemon=no_daemon)
    report["daemon_status"] = DaemonClient().status()

    if as_json:
        typer.echo(json.dumps(report, ensure_ascii=False, indent=2))
        raise typer.Exit(EXIT_OK if report.get("index_complete") else EXIT_ERROR)

    complete = bool(report.get("index_complete"))
    stale = bool(report.get("stale"))
    stats = report.get("stats") or {}
    dstat = report["daemon_status"]

    table = Table(title=f"sem doctor (v{report.get('version', __version__)})")
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Value")

    table.add_row(
        "Index complete", "[green]yes[/green]" if complete else "[red]no (run `sem install`)[/red]"
    )
    table.add_row(
        "Index revision",
        f"{report.get('index_revision') or 'none'} (expected {report.get('expected_index_revision')})",
    )
    table.add_row("Last indexed", str(report.get("last_indexed") or "never"))
    table.add_row("Update needed", "[yellow]yes[/yellow]" if stale else "[green]no[/green]")
    if stats:
        table.add_row(
            "Indexed corpus",
            f"{stats.get('tool_count', 0)} tools · {stats.get('alias_count', 0)} aliases · "
            f"{stats.get('subcommand_count', 0)} subcommands · {stats.get('flag_count', 0)} flags · "
            f"{stats.get('example_count', 0)} examples",
        )
        table.add_row(
            "Parse coverage",
            f"flags: {stats.get('flag_coverage_pct', 0)}% · examples: {stats.get('example_coverage_pct', 0)}%",
        )
        src_counts = stats.get("source_counts") or {}
        if src_counts:
            table.add_row("Sources", ", ".join(f"{k}={v}" for k, v in sorted(src_counts.items())))

    unindexed_count = int(report.get("unindexed_binaries_count") or 0)
    unindexed_sample = report.get("unindexed_binaries_sample") or []
    sample_str = f" ({', '.join(unindexed_sample[:8])})" if unindexed_sample else ""
    table.add_row("Unindexed PATH bins", f"{unindexed_count}{sample_str}")

    synth_cfg = report.get("synthesis_config") or {}
    embed_cfg = report.get("embedding_config") or {}
    table.add_row(
        "Synthesis", f"{synth_cfg.get('provider')} ({synth_cfg.get('model') or 'default'})"
    )
    table.add_row(
        "Embedding",
        f"{embed_cfg.get('provider')} ({embed_cfg.get('model') or 'none'}, lazy={embed_cfg.get('lazy_fallback')})",
    )
    gguf = report.get("local_gguf") or {}
    table.add_row(
        "Local GGUF",
        f"runtime={gguf.get('runtime') or 'none'}, installed={gguf.get('installed_models') or []}",
    )
    table.add_row(
        "Warm daemon",
        (
            f"[green]running[/green] (pid={dstat.get('pid')}, reqs={dstat.get('requests_served', 0)}, "
            f"uptime={dstat.get('uptime_seconds', 0)}s)"
            if dstat.get("running")
            else "[dim]stopped (auto-starts on demand)[/dim]"
        ),
    )

    stdout_console.print(table)
    if not complete:
        raise typer.Exit(EXIT_ERROR)


# ============================================================================
# Model Management Subcommands: sem model <list|install|use|remove>
# ============================================================================


@model_app.command("list")
def model_list() -> None:
    """List curated local GGUF models and local installation status."""
    from sempropos.intelligence import models

    installed = set(models.installed_model_names())
    active = config.load_config().synthesis
    runtime = models.local_runtime_kind()

    table = Table(title=f"Local GGUF Models (runtime: {runtime or 'not installed'})")
    table.add_column("Tier", style="cyan", no_wrap=True)
    table.add_column("Model ID", style="magenta", no_wrap=True)
    table.add_column("Size", justify="right")
    table.add_column("Installed", justify="center")
    table.add_column("Description")

    for spec in models.list_catalog():
        is_inst = spec.model_id in installed
        is_active = active.provider == "local_gguf" and active.model in (
            spec.tier,
            spec.model_id,
            spec.filename,
        )
        status = (
            "[bold green]active[/bold green]"
            if is_active
            else ("[green]yes[/green]" if is_inst else "[dim]no[/dim]")
        )
        table.add_row(spec.tier, spec.model_id, f"{spec.size_mb} MB", status, spec.description)

    stdout_console.print(table)
    if runtime is None:
        stderr_console.print(
            "[dim]Tip: install `llama-server` or `uv pip install 'sempropos[local-llm]'` to run GGUF models locally.[/dim]"
        )


@model_app.command("install")
def model_install(
    tier: str = typer.Argument("balanced", help="Model tier or id: tiny, balanced, quality"),
    use: bool = typer.Option(True, "--use/--no-use", help="Activate this model in config.toml"),
) -> None:
    """Download a curated local GGUF model into the local data directory."""
    from sempropos.intelligence import models

    spec = models.get_model_spec(tier)
    if spec is None:
        stderr_console.print(f"[red]Unknown model '{escape(tier)}'. Run `sem model list`.[/red]")
        raise typer.Exit(EXIT_USAGE)

    stderr_console.print(f"[blue]Downloading {spec.model_id} (~{spec.size_mb} MB)...[/blue]")
    try:
        dest = models.download_model(tier)
    except Exception as exc:  # noqa: BLE001
        stderr_console.print(f"[red]Download failed: {exc}[/red]")
        raise typer.Exit(EXIT_ERROR)

    stdout_console.print(f"[green]Installed {spec.model_id} at {escape(str(dest))}[/green]")
    if use:
        cfg = config.load_config()
        updated = replace(
            cfg,
            synthesis=replace(cfg.synthesis, provider="local_gguf", model=spec.model_id),
        )
        config.save_config(updated)
        stdout_console.print(
            f"[dim]Activated synthesis provider: local_gguf ({spec.model_id}).[/dim]"
        )


@model_app.command("use")
def model_use(
    identifier: str = typer.Argument(..., help="Installed model tier, id, or GGUF path"),
) -> None:
    """Set an installed local GGUF model as the active synthesis backend."""
    from sempropos.daemon.client import DaemonClient
    from sempropos.intelligence import models

    resolved = models.resolve_model_path(identifier)
    if resolved is None:
        stderr_console.print(
            f"[red]Model '{escape(identifier)}' is not installed. Run `sem model install {escape(identifier)}`.[/red]"
        )
        raise typer.Exit(EXIT_NOT_FOUND)

    spec = models.get_model_spec(resolved.name)
    model_name = spec.model_id if spec else str(resolved)
    cfg = config.load_config()
    config.save_config(
        replace(cfg, synthesis=replace(cfg.synthesis, provider="local_gguf", model=model_name))
    )
    DaemonClient().notify_reload()
    stdout_console.print(f"[green]Active synthesis model set to local_gguf ({model_name}).[/green]")


@model_app.command("remove")
def model_remove(
    identifier: str = typer.Argument(..., help="Installed model tier, id, or filename"),
) -> None:
    """Remove a downloaded local GGUF model from disk."""
    from sempropos.intelligence import models

    removed = models.remove_model(identifier)
    if removed is None:
        stderr_console.print(
            f"[yellow]No managed model found matching '{escape(identifier)}'.[/yellow]"
        )
        raise typer.Exit(EXIT_NOT_FOUND)
    stdout_console.print(f"[green]Removed {escape(str(removed))}[/green]")


# ============================================================================
# Daemon Subcommands: sem daemon <start|stop|restart|status>
# ============================================================================


@daemon_app.command("status")
def daemon_status(
    as_json: bool = typer.Option(False, "--json", help="Emit daemon status as JSON"),
) -> None:
    """Show whether the warm background daemon is running."""
    from sempropos.daemon.client import DaemonClient

    stat = DaemonClient().status()
    if as_json:
        typer.echo(json.dumps(stat, ensure_ascii=False, indent=2))
        return

    if stat.get("running"):
        stdout_console.print(
            f"[green]Running[/green] (pid={stat.get('pid')}, uptime={stat.get('uptime_seconds')}s, "
            f"requests={stat.get('requests_served')}, socket={stat.get('socket')})"
        )
    else:
        stdout_console.print(f"[dim]Stopped[/dim] (socket={stat.get('socket')})")


@daemon_app.command("start")
def daemon_start() -> None:
    """Start the warm background daemon if not already running."""
    from sempropos.daemon.client import DaemonClient

    client = DaemonClient()
    if client.is_running():
        stdout_console.print("[green]Daemon is already running.[/green]")
        return
    if client.ensure_started():
        stdout_console.print("[green]Daemon started.[/green]")
        return
    stderr_console.print("[red]Failed to start sem daemon.[/red]")
    raise typer.Exit(EXIT_ERROR)


@daemon_app.command("stop")
def daemon_stop() -> None:
    """Stop the running warm background daemon."""
    from sempropos.daemon.client import DaemonClient

    if DaemonClient().stop():
        stdout_console.print("[green]Daemon stopped.[/green]")
    else:
        stdout_console.print("[dim]Daemon was not running.[/dim]")


@daemon_app.command("restart")
def daemon_restart() -> None:
    """Restart the warm background daemon."""
    from sempropos.daemon.client import DaemonClient

    client = DaemonClient()
    client.stop()
    if client.ensure_started():
        stdout_console.print("[green]Daemon restarted.[/green]")
        return
    stderr_console.print("[red]Failed to restart sem daemon.[/red]")
    raise typer.Exit(EXIT_ERROR)


# ============================================================================
# Configuration Subcommands: sem config <show|get|set|wizard> (+ configure)
# ============================================================================


def _coerce_value(raw: str, current: Any) -> Any:
    if isinstance(current, bool):
        low = raw.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"Expected boolean value, got '{raw}'")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    if raw.strip().lower() in ("none", "null", ""):
        return None
    return raw


@config_app.command("show")
def config_show() -> None:
    """Display the resolved configuration."""
    from dataclasses import asdict

    import tomli_w

    cfg = config.load_config()

    def strip_none(items: list[tuple[str, Any]]) -> dict[str, Any]:
        return {k: v for k, v in items if v is not None}

    typer.echo(tomli_w.dumps(asdict(cfg, dict_factory=strip_none)).rstrip())


@config_app.command("get")
def config_get(key: str = typer.Argument(..., help="Dotted key, e.g. synthesis.provider")) -> None:
    """Read a single configuration value (e.g. ``sem config get synthesis.provider``)."""
    parts = key.split(".", 1)
    if len(parts) != 2:
        stderr_console.print(
            "[red]Key must use section.field format (e.g. synthesis.provider).[/red]"
        )
        raise typer.Exit(EXIT_USAGE)
    section_name, field_name = parts
    cfg = config.load_config()
    section = getattr(cfg, section_name, None)
    if section is None or not hasattr(section, field_name):
        stderr_console.print(f"[red]Unknown configuration key '{escape(key)}'.[/red]")
        raise typer.Exit(EXIT_NOT_FOUND)
    val = getattr(section, field_name)
    typer.echo("" if val is None else str(val))


@config_app.command("set")
def config_set(
    key: str = typer.Argument(..., help="Dotted key, e.g. synthesis.provider"),
    value: str = typer.Argument(..., help="New value"),
) -> None:
    """Update a single configuration value cleanly without interactive prompts."""
    from sempropos.daemon.client import DaemonClient

    parts = key.split(".", 1)
    if len(parts) != 2:
        stderr_console.print("[red]Key must use section.field format (e.g. daemon.enabled).[/red]")
        raise typer.Exit(EXIT_USAGE)
    section_name, field_name = parts
    cfg = config.load_config()
    section = getattr(cfg, section_name, None)
    if section is None or not hasattr(section, field_name):
        stderr_console.print(f"[red]Unknown configuration key '{escape(key)}'.[/red]")
        raise typer.Exit(EXIT_NOT_FOUND)

    current = getattr(section, field_name)
    try:
        coerced = _coerce_value(value, current)
    except ValueError as exc:
        stderr_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(EXIT_USAGE)

    if (
        section_name == "synthesis"
        and field_name == "provider"
        and coerced not in config.SUPPORTED_SYNTHESIS_PROVIDERS
    ):
        stderr_console.print(
            f"[red]Unsupported synthesis provider '{coerced}'. "
            f"Choose from: {', '.join(config.SUPPORTED_SYNTHESIS_PROVIDERS)}[/red]"
        )
        raise typer.Exit(EXIT_USAGE)
    if (
        section_name == "embedding"
        and field_name == "provider"
        and coerced not in config.SUPPORTED_EMBEDDING_PROVIDERS
    ):
        stderr_console.print(
            f"[red]Unsupported embedding provider '{coerced}'. "
            f"Choose from: {', '.join(config.SUPPORTED_EMBEDDING_PROVIDERS)}[/red]"
        )
        raise typer.Exit(EXIT_USAGE)

    updated_section = replace(section, **{field_name: coerced})
    updated_cfg = replace(cfg, **{section_name: updated_section})
    config.save_config(updated_cfg)
    DaemonClient().notify_reload()
    stdout_console.print(f"[green]Set {escape(key)} = {escape(str(coerced))}[/green]")


@config_app.command("wizard")
@app.command("configure")
def configure_wizard() -> None:
    """Configure synthesis and embedding providers interactively."""
    import questionary

    from sempropos.intelligence import (
        ProviderConfigurationError,
        get_provider_models,
        list_embedding_providers,
        list_synthesis_providers,
    )

    avail_synth = [name for name, ok in list_synthesis_providers().items() if ok]
    if not avail_synth:
        stderr_console.print("[red]No synthesis providers available.[/red]")
        raise typer.Exit(EXIT_ERROR)

    default_synth = next(
        (p for p in ("local_gguf", "ollama", "openai_compatible", "tier0") if p in avail_synth),
        avail_synth[0],
    )
    synth_choice = questionary.select(
        "Choose a synthesis provider:", choices=avail_synth, default=default_synth
    ).ask()
    if synth_choice is None:
        raise typer.Exit(EXIT_ERROR)

    if synth_choice == "openai_compatible":
        base_url = questionary.text("Base URL:", default="https://api.openai.com/v1").ask()
        model_text = questionary.text("Model name:").ask()
        api_key_env = questionary.text("API key env var (optional):").ask()
        if base_url is None or model_text is None:
            raise typer.Exit(EXIT_ERROR)
        synth_cfg = config.SynthesisConfig(
            provider=synth_choice,
            model=model_text.strip() or None,
            base_url=base_url.strip() or None,
            api_key_env=(api_key_env or "").strip() or None,
        )
    else:
        try:
            models_list = get_provider_models(synth_choice)
        except ProviderConfigurationError as exc:
            stderr_console.print(f"[red]{exc}[/red]")
            raise typer.Exit(EXIT_ERROR)
        chosen_model = (
            questionary.select("Choose a synthesis model:", choices=models_list).ask()
            if models_list
            else ""
        )
        if chosen_model is None:
            raise typer.Exit(EXIT_ERROR)
        synth_cfg = config.SynthesisConfig(provider=synth_choice, model=chosen_model or None)

    avail_embed = [name for name, ok in list_embedding_providers().items() if ok]
    embed_choice = questionary.select(
        "Choose an embedding provider:",
        choices=avail_embed,
        default="fastembed_local" if "fastembed_local" in avail_embed else avail_embed[0],
    ).ask()
    if embed_choice is None:
        raise typer.Exit(EXIT_ERROR)

    embed_models = get_provider_models(embed_choice) if embed_choice != "none" else []
    chosen_embed = (
        questionary.select("Choose an embedding model:", choices=embed_models).ask()
        if embed_models
        else ""
    )
    if chosen_embed is None:
        raise typer.Exit(EXIT_ERROR)

    current = config.load_config()
    config.save_config(
        replace(
            current,
            synthesis=synth_cfg,
            embedding=config.EmbeddingConfig(provider=embed_choice, model=chosen_embed or None),
        )
    )
    stdout_console.print("[bold green]Configuration saved.[/bold green]")
    stderr_console.print("[dim]If embeddings changed, run `sem update` to sync vectors.[/dim]")


# ============================================================================
# Utility Commands: providers, init, version
# ============================================================================


@app.command()
def providers() -> None:
    """List supported synthesis and embedding providers and their availability."""
    from sempropos.intelligence import list_embedding_providers, list_synthesis_providers

    synth = list_synthesis_providers()
    embed = list_embedding_providers()

    table = Table(title="Intelligence Providers")
    table.add_column("Role", style="cyan")
    table.add_column("Provider", style="magenta")
    table.add_column("Available", justify="right")

    for name in config.SUPPORTED_SYNTHESIS_PROVIDERS:
        table.add_row(
            "Synthesis", name, "[green]yes[/green]" if synth.get(name) else "[red]no[/red]"
        )
    for name in config.SUPPORTED_EMBEDDING_PROVIDERS:
        table.add_row(
            "Embedding", name, "[green]yes[/green]" if embed.get(name) else "[red]no[/red]"
        )

    stdout_console.print(table)


@app.command()
def init(
    shell: str = typer.Argument(..., help="Shell to configure: zsh, bash, or fish"),
) -> None:
    """Print shell integration (Ctrl+S in-place transform + tab completions)."""
    from sempropos.shell import integration_script, supported_shells

    try:
        script = integration_script(shell)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        typer.echo(f"Supported shells: {', '.join(supported_shells())}", err=True)
        raise typer.Exit(EXIT_USAGE)
    typer.echo(script)


@app.command()
def version() -> None:
    """Print the sem version."""
    typer.echo(__version__)


# ============================================================================
# Bare-Query Routing & Fast-Path Entry Point
# ============================================================================


@app.callback()
def cli_core(
    ctx: typer.Context,
    tool: str | None = typer.Option(
        None, "-t", "--tool", help="Scope synthesis to a specific tool"
    ),
    explain: bool = typer.Option(False, "-e", "--explain", help="Show flag-by-flag breakdown"),
    alternatives: int = typer.Option(
        1, "-n", "--alternatives", min=1, max=5, help="Number of candidates"
    ),
    copy: bool = typer.Option(False, "-c", "--copy", help="Copy command to clipboard"),
    backend: str | None = typer.Option(None, "--backend", help="Override synthesis backend"),
    porcelain: bool = typer.Option(False, "--porcelain", help="Print only the command"),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
    no_daemon: bool = typer.Option(False, "--no-daemon", help="Bypass the background daemon"),
) -> None:
    """sem — instant offline replacement for apropos, --help, and man."""
    if ctx.invoked_subcommand is not None:
        return

    query = " ".join(ctx.args).strip()
    if query:
        resp = _call_service(
            "ask",
            {
                "query": query,
                "tool_filter": tool,
                "backend": backend,
                "alternatives": alternatives,
            },
            no_daemon=no_daemon,
        )
        code = _render_ask_response(
            resp,
            porcelain=porcelain,
            as_json=as_json,
            explain=explain,
            copy_cmd=copy,
        )
        raise typer.Exit(code)

    stdout_console.print(ctx.get_help())
    raise typer.Exit(EXIT_OK)


# Subcommands that take zero positional arguments; if followed by extra words
# (e.g. `sem check open ports` or `sem update packages`), route to `ask` instead!
_ZERO_ARG_COMMANDS = frozenset(
    {"install", "update", "doctor", "check", "providers", "version", "configure", "complete-tools"}
)

_VALUE_OPTIONS = frozenset({"--backend", "--tool", "-t", "--alternatives", "-n"})


def _registered_command_names() -> set[str]:
    names = {"--help", "-h"}
    for cmd in app.registered_commands:
        if cmd.name:
            names.add(cmd.name)
        elif cmd.callback is not None:
            names.add(cmd.callback.__name__.replace("_", "-"))
    for grp in app.registered_groups:
        if grp.name:
            names.add(grp.name)
    return names


def _normalize_argv(argv: list[str]) -> list[str]:
    """Route bare natural-language queries (including `sem check open ports`) cleanly to `ask`."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            # Everything after `--` is explicitly a natural-language query.
            return ["ask", *argv]
        if token.startswith("-"):
            index += 2 if token in _VALUE_OPTIONS else 1
            continue
        break

    if index >= len(argv):
        return argv

    first = argv[index]
    known = _registered_command_names()
    if first not in known:
        return ["ask", *argv]

    # Disambiguate multi-word bare queries starting with a zero-arg command verb:
    # `sem check` -> `check` subcommand; `sem check open ports` -> `ask "check open ports"`!
    if first in _ZERO_ARG_COMMANDS:
        rest = argv[index + 1 :]
        if any(not tok.startswith("-") for tok in rest):
            return ["ask", *argv]

    # `sem show tar` -> `show`; `sem show disk usage by folder` (>2 positional words) -> `ask`!
    if first in ("show", "doc"):
        positional_after = [tok for tok in argv[index + 1 :] if not tok.startswith("-")]
        if len(positional_after) > 2:
            return ["ask", *argv]

    return argv


def _try_porcelain_fast_path(argv: list[str]) -> int | None:
    """Bypass Typer/Rich startup for warm-daemon ``--porcelain`` (Ctrl+S) calls."""
    if "--porcelain" not in argv or "--no-daemon" in argv or "--json" in argv:
        return None
    if "--help" in argv or "-h" in argv:
        return None

    tool_filter: str | None = None
    backend: str | None = None
    query_parts: list[str] = []

    i = 0
    after_dd = False
    while i < len(argv):
        tok = argv[i]
        if after_dd:
            query_parts.append(tok)
            i += 1
            continue
        if tok == "--":
            after_dd = True
            i += 1
            continue
        if tok == "--porcelain":
            i += 1
            continue
        if tok == "ask" and not query_parts:
            i += 1
            continue
        if tok in ("-t", "--tool") and i + 1 < len(argv):
            tool_filter = argv[i + 1]
            i += 2
            continue
        if tok == "--backend" and i + 1 < len(argv):
            backend = argv[i + 1]
            i += 2
            continue
        if tok.startswith("-"):
            # Let full Typer CLI handle unfamiliar flags.
            return None
        query_parts.append(tok)
        i += 1

    query = " ".join(query_parts).strip()
    if not query:
        return None

    from sempropos.daemon.client import try_fast_porcelain_ask

    fast = try_fast_porcelain_ask(query, tool_filter=tool_filter, backend=backend)
    if fast is None:
        return None
    code, out, err = fast
    if out:
        sys.stdout.write(out + "\n")
    if err:
        sys.stderr.write(err + "\n")
    return code


def main() -> None:
    argv = sys.argv[1:]
    fast_code = _try_porcelain_fast_path(argv)
    if fast_code is not None:
        raise SystemExit(fast_code)
    app(_normalize_argv(argv))


if __name__ == "__main__":
    main()
