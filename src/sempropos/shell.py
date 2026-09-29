"""Shell integration snippets for `sem`.

Each snippet binds ``Ctrl+S`` to a widget that treats the current command line
as a natural-language query, calls ``sem --porcelain`` for a command, and
replaces the line in place. The snippets are printed by ``sem init <shell>`` and
evaluated by the shell, for example::

    eval "$(sem init zsh)"

`stty -ixon` is required so the terminal does not swallow ``Ctrl+S`` as its
XOFF/flow-control character.
"""

from __future__ import annotations

_SUPPORTED_SHELLS: tuple[str, ...] = ("zsh", "bash", "fish")

_ZSH = '\n'.join(
    [
        "# sem shell integration — Ctrl+S transforms the current line",
        "if [[ -o interactive ]]; then",
        "  stty -ixon 2>/dev/null",
        "  _sem_transform() {",
        '    [[ -n "$BUFFER" ]] || return',
        "    local cmd",
        '    cmd=$(command sem --porcelain "$BUFFER" 2>/dev/null) || { zle reset-prompt; return }',
        '    if [[ -n "$cmd" ]]; then',
        '      BUFFER="$cmd"',
        "      CURSOR=${#BUFFER}",
        "    fi",
        "    zle reset-prompt",
        "  }",
        "  zle -N _sem_transform",
        "  bindkey '^S' _sem_transform",
        "fi",
    ]
)

_BASH = '\n'.join(
    [
        "# sem shell integration — Ctrl+S transforms the current line",
        "_sem_transform() {",
        '  [[ -n "$READLINE_LINE" ]] || return',
        "  local cmd",
        '  cmd=$(command sem --porcelain "$READLINE_LINE" 2>/dev/null) || return',
        '  if [[ -n "$cmd" ]]; then',
        '    READLINE_LINE="$cmd"',
        "    READLINE_POINT=${#READLINE_LINE}",
        "  fi",
        "}",
        'if [[ $- == *i* ]]; then',
        "  stty -ixon 2>/dev/null",
        "  bind -x '\"\\C-s\": _sem_transform'",
        "fi",
    ]
)

_FISH = '\n'.join(
    [
        "# sem shell integration — Ctrl+S transforms the current line",
        "function _sem_transform",
        "  set -l query (commandline -b)",
        '  test -n "$query"; or return',
        "  set -l cmd (command sem --porcelain -- $query 2>/dev/null)",
        '  test -n "$cmd"; and commandline -r -- "$cmd"',
        "  commandline -f repaint",
        "end",
        "stty -ixon 2>/dev/null",
        r"bind \cs _sem_transform",
    ]
)

_SCRIPTS: dict[str, str] = {"zsh": _ZSH, "bash": _BASH, "fish": _FISH}


def supported_shells() -> tuple[str, ...]:
    """Return the shells for which integration is available."""
    return _SUPPORTED_SHELLS


def integration_script(shell: str) -> str:
    """Return the shell snippet for *shell*.

    Raises:
        ValueError: If the shell is not supported.
    """
    name = (shell or "").strip().lower()
    script = _SCRIPTS.get(name)
    if script is None:
        supported = ", ".join(_SUPPORTED_SHELLS)
        raise ValueError(f"Unsupported shell '{shell}'. Choose one of: {supported}.")
    return script
