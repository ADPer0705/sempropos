---
type: guide
title: Shell Integration
description: The zsh, bash, and fish Ctrl+S widgets, tab completions, the stty requirement, and the --porcelain contract that makes in-place command replacement safe.
tags: [shell, zsh, bash, fish, porcelain]
verified:
  - by: openwiki/0.9.0
    at: 2026-11-23T21:48:04.805Z
sources:
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-401c3a8b8dfa9b92b6d17737
    resource: repo://src/sempropos/cli.py
  - id: openwiki-source-8f55faa7a495acc24641ff9a
    resource: repo://src/sempropos/shell.py
generated: { by: "opencode", at: "2026-11-21T06:12:23.249Z" }
---

# Shell Integration

`src/sempropos/shell.py` generates snippets that bind **Ctrl+S** to transform the current command line in place and register tab completion for `sem`. The user types a natural-language task, presses Ctrl+S, and the line is replaced with a generated command — never executed.

## Installing

```bash
# zsh or bash
eval "$(sem init zsh)"
eval "$(sem init bash)"

# fish
sem init fish | source
```

`sem init <shell>` prints the snippet verbatim so it can be evaluated or sourced. Supported names are `zsh`, `bash`, and `fish` (`supported_shells()`). An unsupported name prints `Unsupported shell '<name>'. Choose one of: ...` plus the supported list to stderr and exits 2 (the CLI usage-error code).

## The `--porcelain` contract

All widgets call `sem --porcelain -- <query>`. The contract they depend on:

- the generated command is the only thing written to stdout;
- warnings and errors go to stderr;
- a non-zero exit means no replacement should happen;
- an empty stdout result also leaves the line unchanged.

The widgets also guard on a non-empty current line, so pressing Ctrl+S on an empty prompt does nothing. Failures leave the user's input intact.

## zsh

The zsh snippet runs only in an interactive shell (`[[ -o interactive ]]`) and:

1. calls `stty -ixon` (errors suppressed) so the terminal does not consume Ctrl+S as XOFF;
2. defines `_sem_transform`, which returns on an empty `$BUFFER`, captures stderr in a temporary file, and calls `command sem --porcelain -- "$BUFFER"`;
3. on success with a non-empty result, assigns it to `BUFFER` and moves `CURSOR` to the end;
4. on failure, reads the first stderr line and displays it with `zle -M "sem: $err_msg"`;
5. removes the temporary file and calls `zle reset-prompt`;
6. registers the widget with `zle -N _sem_transform` and binds `^S` with `bindkey`;
7. defines `_sem_complete` for command names and, after `show`/`doc`, tool names from `sem complete-tools`, registering it with `compdef sem` when `compdef` exists.

## bash

The bash snippet:

1. defines `_sem_transform`, which returns on an empty `$READLINE_LINE` and calls `command sem --porcelain -- "$READLINE_LINE"`, discarding stderr;
2. on success with a non-empty result, writes it back to `READLINE_LINE` and sets `READLINE_POINT` to the end;
3. defines `_sem_complete`, completing command names at position 1 and indexed tool names after `show`/`doc`;
4. in interactive shells (`[[ $- == *i* ]]`), runs `stty -ixon`, binds the function with `bind -x '"\C-s": _sem_transform'`, and registers `complete -F _sem_complete sem`.

## fish

The fish snippet:

1. defines `_sem_transform`, which reads the buffer with `commandline -b` and returns when it is empty;
2. calls `command sem --porcelain -- $query`, so a leading dash in natural language is not parsed as an option;
3. replaces the line with `commandline -r -- "$cmd"` only when the command succeeded and returned a non-empty value;
4. repaints with `commandline -f repaint`;
5. runs `stty -ixon` and binds `\cs` to the function;
6. registers `complete -c sem` rules for command names and for `complete-tools` output after `show`/`doc`.

## Keybinding notes

- `stty -ixon` is required because many terminals reserve Ctrl+S for XOFF/flow control. If the terminal still intercepts the key, rebind a different key by editing the `bindkey`/`bind -x`/`bind` line.
- Ctrl+R (history) and Ctrl+T (files) are intentionally left to [fzf](https://github.com/junegunn/fzf); `sem` only adds Ctrl+S.
- The replacement is inserted into the prompt, so the user can review and edit it before running.
- Only zsh surfaces the first stderr line in the editor; bash and fish discard diagnostics and simply leave the line unchanged.

## Related pages

- [CLI Surface and Query Dispatch](cli.md)
- [Quickstart](quickstart.md)
