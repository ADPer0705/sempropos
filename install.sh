#!/usr/bin/env bash

set -euo pipefail

APP_NAME="sempropos"
LOCAL_BIN_DIR="$HOME/.local/bin"

log() {
  echo "[${APP_NAME}-install] $*"
}

ensure_python() {
  if ! command -v python3 >/dev/null 2>&1; then
    log "Error: python3 is required."
    exit 1
  fi
}

ensure_pipx() {
  if command -v pipx >/dev/null 2>&1; then
    return 0pytest -q
Ran /mnt/s/workshop/sempropos/.venv/bin/python -m pyte...
I’ve confirmed the code fixes are good; I’ll do one final run with the same activation style you used (source .venv/bin/activate && pytest) so the result matches your workflow exactly.

Ran source .venv/bin/activate && pytest -q
Read changed files in sempropos


  fi

  log "pipx not found. Installing pipx in user space..."
  if python3 -m pip install --user pipx; then
    :
  elif python3 -m pip install --user --break-system-packages pipx; then
    :
  else
    log "Error: failed to install pipx via python3 -m pip."
    log "Install pipx with your distro package manager and rerun this script."
    exit 1
  fi

  python3 -m pipx ensurepath >/dev/null || true
}

refresh_path() {
  export PATH="$PATH:$LOCAL_BIN_DIR"
}

install_sempropos() {
  local source_ref="sempropos"

  if [[ -f "./pyproject.toml" && -d "./src" ]]; then
    source_ref="."
    log "Installing sempropos from local source with pipx..."
  else
    log "Installing sempropos from PyPI with pipx..."
  fi

  pipx install "$source_ref" --force
}

run_bootstrap_install() {
  local exe=""
  if command -v sempropos >/dev/null 2>&1; then
    exe="$(command -v sempropos)"
  elif [[ -x "$LOCAL_BIN_DIR/sempropos" ]]; then
    exe="$LOCAL_BIN_DIR/sempropos"
  else
    log "Error: sempropos executable not found after pipx install."
    exit 1
  fi

  log "Running sempropos --install (downloads llama-cli/model and builds index)..."
  "$exe" --install
}

print_success() {
  log "Success. Try: sempropos \"find all large files\""

  case ":$PATH:" in
    *":$LOCAL_BIN_DIR:"*)
      ;;
    *)
      echo
      log "Add $LOCAL_BIN_DIR to your PATH if needed:"
      echo "export PATH=\"$LOCAL_BIN_DIR:\$PATH\""
      ;;
  esac
}

main() {
  log "Bootstrapping sempropos environment..."
  ensure_python
  ensure_pipx
  refresh_path
  install_sempropos
  run_bootstrap_install
  print_success
}

main "$@"
