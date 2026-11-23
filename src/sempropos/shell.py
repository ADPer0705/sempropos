"""Shell integration snippets for ``sem`` (Ctrl+S in-place transform + tab completions).

Evaluate once in your shell rc file::

    eval "$(sem init zsh)"     # ~/.zshrc
    eval "$(sem init bash)"    # ~/.bashrc
    sem init fish | source     # ~/.config/fish/config.fish
"""

from __future__ import annotations

_SUPPORTED_SHELLS: tuple[str, ...] = ("zsh", "bash", "fish")

_ZSH = "\n".join(
    [
        "# sem shell integration — Ctrl+S transforms the current line + tab completions",
        "if [[ -o interactive ]]; then",
        "  stty -ixon 2>/dev/null",
        "  _sem_transform() {",
        '    [[ -n "$BUFFER" ]] || return',
        "    local cmd err_file err_msg",
        "    err_file=$(mktemp 2>/dev/null || echo /tmp/.sem_err_$$)",
        '    if cmd=$(command sem --porcelain -- "$BUFFER" 2>"$err_file"); then',
        '      if [[ -n "$cmd" ]]; then',
        '        BUFFER="$cmd"',
        "        CURSOR=${#BUFFER}",
        "      fi",
        "    else",
        '      err_msg=$(head -n 1 "$err_file" 2>/dev/null)',
        '      [[ -n "$err_msg" ]] && zle -M "sem: $err_msg"',
        "    fi",
        '    rm -f "$err_file" 2>/dev/null',
        "    zle reset-prompt",
        "  }",
        "  zle -N _sem_transform",
        "  bindkey '^S' _sem_transform",
        "  _sem_complete() {",
        "    local -a cmds tools",
        "    cmds=(ask show doc search apropos doctor check install update model daemon config configure providers init version)",
        "    if (( CURRENT == 2 )); then",
        '      _describe "command" cmds',
        '    elif (( CURRENT == 3 )) && [[ "${words[2]}" == (show|doc) ]]; then',
        "      tools=(${(f)$(command sem complete-tools 2>/dev/null)})",
        '      _describe "tool" tools',
        "    fi",
        "  }",
        "  (( $+functions[compdef] )) && compdef _sem_complete sem",
        "fi",
    ]
)

_BASH = "\n".join(
    [
        "# sem shell integration — Ctrl+S transforms the current line + tab completions",
        "_sem_transform() {",
        '  [[ -n "$READLINE_LINE" ]] || return',
        "  local cmd",
        '  cmd=$(command sem --porcelain -- "$READLINE_LINE" 2>/dev/null) || return',
        '  if [[ -n "$cmd" ]]; then',
        '    READLINE_LINE="$cmd"',
        "    READLINE_POINT=${#READLINE_LINE}",
        "  fi",
        "}",
        "_sem_complete() {",
        '  local cur="${COMP_WORDS[COMP_CWORD]}"',
        '  local prev="${COMP_WORDS[COMP_CWORD-1]}"',
        "  if [[ $COMP_CWORD -eq 1 ]]; then",
        '    COMPREPLY=( $(compgen -W "ask show doc search apropos doctor check install update model daemon config configure providers init version" -- "$cur") )',
        '  elif [[ $COMP_CWORD -eq 2 && ( "$prev" == "show" || "$prev" == "doc" ) ]]; then',
        '    COMPREPLY=( $(compgen -W "$(command sem complete-tools 2>/dev/null)" -- "$cur") )',
        "  fi",
        "}",
        "if [[ $- == *i* ]]; then",
        "  stty -ixon 2>/dev/null",
        "  bind -x '\"\\C-s\": _sem_transform'",
        "  complete -F _sem_complete sem",
        "fi",
    ]
)

_FISH = "\n".join(
    [
        "# sem shell integration — Ctrl+S transforms the current line + tab completions",
        "function _sem_transform",
        "  set -l query (commandline -b)",
        '  test -n "$query"; or return',
        "  set -l cmd (command sem --porcelain -- $query 2>/dev/null)",
        '  if test $status -eq 0; and test -n "$cmd"',
        '    commandline -r -- "$cmd"',
        "  end",
        "  commandline -f repaint",
        "end",
        "stty -ixon 2>/dev/null",
        r"bind \cs _sem_transform",
        'complete -c sem -f -n "__fish_use_subcommand" -a "ask show doc search apropos doctor check install update model daemon config configure providers init version"',
        'complete -c sem -f -n "__fish_seen_subcommand_from show doc" -a "(command sem complete-tools 2>/dev/null)"',
    ]
)

_SCRIPTS: dict[str, str] = {"zsh": _ZSH, "bash": _BASH, "fish": _FISH}


def supported_shells() -> tuple[str, ...]:
    """Return the shells for which integration is available."""
    return _SUPPORTED_SHELLS


def integration_script(shell: str) -> str:
    """Return the shell snippet for ``shell``."""
    name = (shell or "").strip().lower()
    script = _SCRIPTS.get(name)
    if script is None:
        supported = ", ".join(_SUPPORTED_SHELLS)
        raise ValueError(f"Unsupported shell '{shell}'. Choose one of: {supported}.")
    return script
