---
type: reference
title: CLI Surface and Query Dispatch
description: Commands, bare-query argv normalization, ask/show/search output modes, daemon/config/model commands, flags, exit codes, and dispatch behavior for the sem CLI.
tags: [cli, typer, dispatch, exit-codes]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-401c3a8b8dfa9b92b6d17737
    resource: repo://src/sempropos/cli.py
  - id: openwiki-source-dde87ad38c67b724ad33ca8c
    resource: repo://src/sempropos/intelligence/providers/_tier0.py
  - id: openwiki-source-8f55faa7a495acc24641ff9a
    resource: repo://src/sempropos/shell.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# CLI Surface and Query Dispatch

The CLI is a Typer application in `src/sempropos/cli.py`, entered through `sempropos.cli:main` (declared in `pyproject.toml`). It combines a management command suite with three query modes — **ask**, **show**, and **search** — plus a bare-sentence form that is normalized into `ask`.

## App configuration

The Typer app is created with:

- `add_completion=False`, because `sem init` supplies its own completions;
- `invoke_without_command=True` and `no_args_is_help=False`, so the callback can handle a bare query;
- `context_settings={"allow_extra_args": True, "ignore_unknown_options": True}`, so a natural-language sentence is not parsed as CLI options;
- hidden aliases `doc` for `show`, `apropos` for `search`, `check` for `doctor`, and `complete-tools` for shell completion.

The app registers the `model`, `daemon`, and `config` sub-apps.

## Commands

| Command | Purpose |
| --- | --- |
| `sem "query"` / `sem ask "query"` | Synthesize one verified shell command from a natural-language task |
| `sem show <tool> [subcommand]` | Structured reference card replacing `man` and `--help` |
| `sem search <query>` | Ranked tool discovery replacing `apropos`, with no LLM call |
| `sem install` | First-time setup: verify SQLite FTS5, write config, build the v3 index, warm the provider |
| `sem update` | Incremental refresh: re-index changed tools and prune uninstalled binaries |
| `sem doctor` | Index health, coverage, unindexed PATH binaries, providers, and daemon status |
| `sem model list/install/use/remove` | Manage curated local GGUF models |
| `sem daemon status/start/stop/restart` | Inspect or control the warm Unix-socket daemon |
| `sem config show/get/set/wizard` | Inspect or update `config.toml` |
| `sem configure` | Alias for `sem config wizard` |
| `sem providers` | List synthesis and embedding providers with availability |
| `sem init <shell>` | Print the `Ctrl+S` widget and tab-completion script |
| `sem version` | Print `sempropos.__version__` |
| `sem complete-tools` | Hidden: print indexed tool names for completion |

`install`, `update`, `doctor`, `model`, `daemon`, and `config` are covered further in [Operations and Troubleshooting](operations.md). Shell output is covered in [Shell Integration](shell-integration.md).

### ask options

`sem ask` accepts one or more positional query words and:

| Option | Effect |
| --- | --- |
| `-t, --tool <name>` | Scope retrieval and synthesis to one indexed tool |
| `-e, --explain` | Show a flag-by-flag explanation table plus unverified flags |
| `-n, --alternatives <1-5>` | Request up to five command candidates |
| `-c, --copy` | Copy the command with `wl-copy`, `xclip`, `xsel`, or `pbcopy` |
| `--backend <name>` | Override the configured synthesis backend |
| `--porcelain` | Print only the runnable command; send diagnostics to stderr |
| `--json` | Emit the full service response as JSON |
| `--no-daemon` | Bypass the warm daemon and run in-process |

### show options

`sem show <tool> [subcommand]` accepts `-f/--flags <keyword>` to filter flags, `--all-flags` to print every parsed flag, `--examples` for a summary-plus-examples card, `--json` for the canonical `ToolSpec`, and `--no-daemon`.

### search options

`sem search <query>` accepts `-n/--limit <1-50>`, `--category <name>`, `--json`, and `--no-daemon`.

## Bare-query normalization

Typer resolves the first token as a subcommand, so `sem "find large files"` would otherwise fail. `main()` therefore calls `app(_normalize_argv(sys.argv[1:]))`.

`_normalize_argv` scans from the start:

1. `--` stops scanning and the whole argv is routed to `ask` (`["ask", *argv]`).
2. Any option token is skipped; `--backend`, `--tool`, `-t`, `--alternatives`, and `-n` also skip their following value (`_VALUE_OPTIONS`).
3. The first non-option token ends the scan.
4. If that token is not a registered command or group (`install`, `update`, `doctor`, `check`, `show`, `doc`, `search`, `apropos`, `ask`, `model`, `daemon`, `config`, `configure`, `providers`, `init`, `version`, `complete-tools`, `--help`, `-h`), `ask` is prepended.
5. If the first token is a zero-argument command (`install`, `update`, `doctor`, `check`, `providers`, `version`, `configure`, `complete-tools`) and more non-option words follow, the input is treated as a query. Thus `sem check open ports` becomes `sem ask "check open ports"`.
6. If the first token is `show` or `doc` and more than two positional words follow, the input becomes `ask`. This keeps `sem show git commit` as a reference card while `sem show disk usage by folder` becomes a query.

Examples:

```bash
sem "list files in archive.7z"
sem ask "list files in archive.7z"
sem --backend ollama "list files in archive.7z"
sem check open ports            # ask, not the doctor command
sem show tar --flags extract    # show
```

The callback `cli_core` handles the bare form when no subcommand was invoked: it joins `ctx.args` into a query, calls `_call_service("ask", ...)`, and renders the response. With no query it prints help and exits 0.

## Dispatch and the warm daemon

Every query command calls `_call_service(method, params, no_daemon=...)`. If `daemon.enabled` is true and the daemon is reachable — or auto-start is enabled and it starts successfully — the CLI sends a JSON-RPC request over the Unix socket. If the daemon is disabled, unavailable, or bypassed with `--no-daemon`, the CLI runs `SemService` in-process.

`main()` has an additional porcelain fast path. When `--porcelain` is present, no `--no-daemon` or `--json` is set, and a daemon is already running, `try_fast_porcelain_ask` returns the command using only the stdlib client, without importing Typer, Rich, NumPy, or FastEmbed.

## Output modes

### ask — interactive

- The command appears in a Rich panel whose border color reflects the verification level: green for `safe`, yellow for `caution`, and red for `destructive`. The title includes the level when it is not safe.
- The explanation and provider/latency subtitle are shown when available.
- Safety warnings from `verify_command` are printed in a separate stderr panel.
- `--explain` adds a flag table and lists flags that were not found in the local `ToolSpec`.
- Alternatives are printed below the panel, and `--copy` reports success or a missing clipboard utility.
- When stdout is not a TTY, the command is printed as plain text so `cmd=$(sem "...")` stays clean.
- When synthesis is unavailable or returns no usable command, `_print_fallback_cards` prints up to four structured cards. Each card includes the summary, up to three subcommands, up to four flags, and up to three examples, with the tool's sources as a subtitle.

### show — reference card

`show` renders a header with the tool name, aliases, category, and summary, then sections for the focused subcommand, synopsis, subcommands, flags, examples, and source provenance. `--examples` trims the card to summary and examples. `--json` returns the canonical `ToolSpec` payload.

### search — discovery table

`search` renders a Rich table with columns **Tool**, **Category**, **Summary**, and **Hint** (the first example, a subcommand, or a flag). The table title includes the measured latency. An empty result prints a yellow message on stderr and exits 3.

### porcelain

- On success, stdout contains only the command followed by a newline; the process exits 0.
- Diagnostics and errors go to stderr; stdout stays empty on failure.
- Index and no-candidate failures exit 3; other synthesis failures exit 1. This is the contract the shell widgets rely on.

## Exit codes

Constants are defined at the top of `cli.py`: `EXIT_OK = 0`, `EXIT_ERROR = 1`, `EXIT_USAGE = 2`, `EXIT_NOT_FOUND = 3`.

| Situation | Exit code |
| --- | --- |
| Command produced | 0 |
| Tier-0 card fallback | 0 |
| Non-tier0 fallback cards | 1 |
| No candidates or missing/incompatible index in `ask`/`search` | 3 |
| `show` tool or subcommand not found | 3 |
| Empty `ask` or `search` query | 2 |
| Invalid option value (for example `--limit 0`) | 2 |
| `sem init` with an unsupported shell | 2 |
| `sem doctor` when the index is incomplete | 1 |
| `sem install` / `sem update` build failure | 1 |
| `sem model install` unknown tier | 2 |
| `sem model use` / `remove` target not found | 3 |
| `sem config get/set` malformed key | 2 |
| `sem config get/set` unknown key | 3 |
| `sem config set` unsupported provider | 2 |
| No arguments | 0 after printing help |

The interactive ask path intentionally exits 0 after a tier-0 card fallback so an offline user still sees useful retrieved ToolSpecs; scripts should use `--porcelain` or `--json` for strict status handling.

## Related pages

- [Architecture Overview](architecture.md)
- [Operations and Troubleshooting](operations.md)
- [Shell Integration](shell-integration.md)
