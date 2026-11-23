"""Unit tests for shell integration snippets (zsh, bash, fish)."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from sempropos.shell import integration_script, supported_shells


def test_supported_shells_and_safe_double_dash() -> None:
    assert set(supported_shells()) == {"zsh", "bash", "fish"}

    for sh in supported_shells():
        script = integration_script(sh)
        assert "--porcelain --" in script
        assert "complete-tools" in script
        assert "stty -ixon" in script

    with pytest.raises(ValueError, match="Unsupported shell"):
        integration_script("powershell")


def test_bash_and_zsh_syntax_valid() -> None:
    if shutil.which("bash"):
        res = subprocess.run(
            ["bash", "-n"],
            input=integration_script("bash"),
            text=True,
            capture_output=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr

    if shutil.which("zsh"):
        res = subprocess.run(
            ["zsh", "-n"],
            input=integration_script("zsh"),
            text=True,
            capture_output=True,
            check=False,
        )
        assert res.returncode == 0, res.stderr
