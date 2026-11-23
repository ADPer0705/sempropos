"""Unit tests for tldr, completions, help_probe, discovery, merger, staleness, and incremental builder."""

from __future__ import annotations

import gzip
import os
import stat
import zipfile
from pathlib import Path

import numpy as np
import pytest

from sempropos import config
from sempropos.index import builder, repository, schema, staleness
from sempropos.sources.completions import parse_fish_completion, parse_zsh_completion
from sempropos.sources.discovery import DiscoveryContext, discover_path_binaries
from sempropos.sources.help_probe import is_safe_to_probe, parse_help_output
from sempropos.sources.merger import ManBundle, ToolSourceBundle, merge_tool_bundle
from sempropos.sources.roff_parser import parse_roff_source
from sempropos.sources.tldr import extract_tldr_fragments, parse_tldr_markdown

_UV_HELP = """An extremely fast Python package manager.

Usage: uv [OPTIONS] <COMMAND>

Commands:
  run        Run a command or script
  init       Create a new project
  add        Add dependencies to the project
  venv       Create a virtual environment
  pip        Manage Python packages with a pip-compatible interface

Cache options:
  -n, --no-cache               Avoid reading from or writing to the cache
      --cache-dir <CACHE_DIR>  Path to the cache directory
"""

_SEVENZ_HELP = """
7-Zip 26.06 (x64) : Copyright (c) 1999-2026 Igor Pavlov : 2026-11-06
 64-bit locale=en_US.UTF-8 Threads:32 OPEN_MAX:4096

Usage: 7z <command> [<switches>...] <archive_name> [<file_names>...] [@listfile]

<Commands>
  a : Add files to archive
  d : Delete files from archive
  e : Extract files from archive (without using directory names)
  l : List contents of archive
  t : Test integrity of archive
  x : eXtract files with full paths

<Switches>
  -an : disable archive_name field
  -bd : disable progress indicator
  -o{Directory} : set Output directory
"""

_TLDR_TAR = """# tar

> Archiving utility.
> Often combined with a compression method, such as `gzip` or `bzip2`.
> More information: <https://www.gnu.org/software/tar>.

- Create an archive from files:

`tar cf {{path/to/target.tar}} {{path/to/file1 path/to/file2 ...}}`

- Extract a gzipped archive into a target directory:

`tar xzf {{path/to/source.tar.gz}} -C {{path/to/directory}}`
"""

_TLDR_GIT_COMMIT = """# git commit

> Commit files to the repository.

- Commit staged files with a message:

`git commit -m "{{message}}"`
"""


def test_help_probe_parses_clap_and_7z_and_blocks_unsafe(tmp_path: Path) -> None:
    uv_frag = parse_help_output("uv", _UV_HELP, "/usr/bin/uv")
    assert "Python package manager" in uv_frag.summary
    assert any(s.name == "venv" for s in uv_frag.subcommands)
    assert any(f.long == "--cache-dir" and f.takes_value for f in uv_frag.flags)

    z_frag = parse_help_output("7z", _SEVENZ_HELP, "/usr/bin/7z")
    assert z_frag.synopsis and "7z <command>" in z_frag.synopsis[0]
    sub_names = {s.name for s in z_frag.subcommands}
    assert {"a", "l", "x", "e"}.issubset(sub_names)
    assert any(f.short == "-bd" for f in z_frag.flags)

    fake_bin = tmp_path / "safe_tool"
    fake_bin.write_text("#!/bin/sh\necho ok\n")
    fake_bin.chmod(0o755)
    assert is_safe_to_probe("safe_tool", fake_bin) is True
    assert is_safe_to_probe("rm", fake_bin) is False
    assert is_safe_to_probe("reboot", fake_bin) is False
    assert is_safe_to_probe("mkfs.ext4", fake_bin) is False

    # Setuid binary must be rejected.
    suid_bin = tmp_path / "suid_tool"
    suid_bin.write_text("x")
    suid_bin.chmod(0o755 | stat.S_ISUID)
    if suid_bin.stat().st_mode & stat.S_ISUID:
        assert is_safe_to_probe("suid_tool", suid_bin) is False

    # A rogue script creating files in cwd during --help must NEVER pollute caller cwd.
    rogue = tmp_path / "rogue_tool"
    rogue.write_text("#!/bin/sh\ntouch leaked.log\necho 'Usage: rogue_tool [OPTIONS]'\n")
    rogue.chmod(0o755)
    from sempropos.sources.help_probe import run_help_probe

    out = run_help_probe(rogue)
    assert out is not None and "Usage: rogue_tool" in out
    assert not (Path.cwd() / "leaked.log").exists()


def test_tldr_markdown_and_zip_subcommand_attachment(tmp_path: Path) -> None:
    parsed = parse_tldr_markdown(_TLDR_TAR, "tar")
    assert parsed.summary == "Archiving utility."
    assert len(parsed.examples) == 2
    assert "<path/to/source.tar.gz>" in parsed.examples[1].command

    archive = tmp_path / "tldr-pages.en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("pages/common/tar.md", _TLDR_TAR)
        zf.writestr("pages/common/git.md", "# git\n> Distributed version control.\n")
        zf.writestr("pages/common/git-commit.md", _TLDR_GIT_COMMIT)
        zf.writestr("pages/common/uninstalled.md", "# uninstalled\n> Not on machine.\n")

    ctx = DiscoveryContext(
        binaries={"tar": Path("/usr/bin/tar"), "git": Path("/usr/bin/git")},
        symlink_aliases={},
    )
    frags = extract_tldr_fragments(ctx, source_paths=[archive])
    assert "tar" in frags
    assert "git" in frags
    assert "uninstalled" not in frags
    assert any(s.name == "commit" and len(s.examples) == 1 for s in frags["git"].subcommands)


def test_completions_static_fish_and_zsh_parsers() -> None:
    fish_src = """
complete -c git -n "__fish_use_subcommand" -a commit -d "Record changes to the repository"
complete -c git -s C -r -d "Run as if git was started in path"
complete -c git -l version -d "Print git version"
"""
    fish_frag = parse_fish_completion(fish_src, "git", "/usr/share/fish/completions/git.fish")
    assert any(s.name == "commit" for s in fish_frag.subcommands)
    assert any(f.short == "-C" and f.takes_value for f in fish_frag.flags)
    assert any(f.long == "--version" for f in fish_frag.flags)

    zsh_src = """
_arguments \\
  '-h[Show help message]' \\
  '--output=[Write output to file]:FILE:_files'
"""
    zsh_frag = parse_zsh_completion(zsh_src, "mytool", "/usr/share/zsh/site-functions/_mytool")
    assert any(f.short == "-h" and "Show help" in f.description for f in zsh_frag.flags)
    assert any(f.long == "--output" and f.takes_value for f in zsh_frag.flags)


def test_discovery_resolves_symlink_aliases_and_ignores_multicall(tmp_path: Path) -> None:
    podman = tmp_path / "podman"
    podman.write_text("#!/bin/sh\n")
    podman.chmod(0o755)

    docker = tmp_path / "docker"
    docker.symlink_to(podman)

    busybox = tmp_path / "busybox"
    busybox.write_text("#!/bin/sh\n")
    busybox.chmod(0o755)
    ls_link = tmp_path / "ls"
    ls_link.symlink_to(busybox)

    ctx = discover_path_binaries(str(tmp_path))
    assert ctx.symlink_aliases.get("docker") == "podman"
    assert "ls" not in ctx.symlink_aliases


def test_merger_combines_man_tldr_and_help() -> None:
    man_doc = parse_roff_source(
        ".TH TAR 1\n.SH NAME\ntar \\- an archiving utility\n.SH OPTIONS\n.TP\n.B \\-x, \\-\\-extract\nExtract files.\n",
        "tar",
    )
    bundle = ToolSourceBundle(
        name="tar",
        binary_path="/usr/bin/tar",
        aliases={"gtar"},
        man=ManBundle(doc=man_doc, section=1, source="man_roff", location="/man/tar.1.gz"),
        tldr=extract_tldr_fragments(
            DiscoveryContext(binaries={"tar": Path("/usr/bin/tar")}, symlink_aliases={}),
            source_paths=[],
        ).get("tar"),
    )
    spec = merge_tool_bundle(bundle)
    assert spec is not None
    assert spec.category == "archive"
    assert spec.aliases == ("gtar",)
    assert any(f.short == "-x" and f.long == "--extract" for f in spec.flags)


def test_builder_incremental_upsert_prune_and_id_keyed_embeddings(
    isolated_env: dict[str, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    man1 = tmp_path / "man" / "man1"
    man1.mkdir(parents=True)

    for tool_name, desc in (("alpha", "compress files"), ("beta", "inspect sockets")):
        exe = bin_dir / tool_name
        exe.write_text("#!/bin/sh\necho 'Usage: tool'\n")
        exe.chmod(0o755)
        roff = f".TH {tool_name.upper()} 1\n.SH NAME\n{tool_name} \\- {desc}\n.SH OPTIONS\n.TP\n.B \\-v\nVerbose output.\n"
        (man1 / f"{tool_name}.1.gz").write_bytes(gzip.compress(roff.encode("utf-8")))

    embed_calls: list[list[str]] = []

    def fake_embed(texts: list[str], **_kwargs: object) -> np.ndarray:
        embed_calls.append(list(texts))
        # Deterministic 4-dim unit vectors
        out = np.zeros((len(texts), 4), dtype=np.float32)
        for i, txt in enumerate(texts):
            out[i, i % 4] = 1.0
            out[i, 3] = float(len(txt) % 7 + 1)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return (out / norms).astype(np.float32)

    monkeypatch.setattr(
        builder, "detect_embedding_provider", lambda: ("fastembed_local", config.EmbeddingConfig())
    )
    monkeypatch.setattr(builder, "embed_texts", fake_embed)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("MANPATH", str(tmp_path / "man"))

    # Disable tldr download/probe for deterministic offline unit run.
    config.save_config(
        config.AppConfig(
            embedding=config.EmbeddingConfig(provider="fastembed_local", model="test-model"),
            indexing=config.IndexingConfig(
                enable_tldr=False, enable_completions=False, probe_help=False
            ),
        )
    )

    # First build indexes both alpha and beta.
    report1 = builder.build_index(progress=False, force=True, offline=True)
    assert report1.total_tools == 2
    assert report1.updated_tools == 2
    assert len(embed_calls) == 1
    assert len(embed_calls[0]) == 2

    # Second incremental run with zero changes must re-embed ZERO rows!
    embed_calls.clear()
    report2 = builder.build_index(progress=False, force=False, offline=True)
    assert report2.total_tools == 2
    assert report2.updated_tools == 0
    assert report2.removed_tools == 0
    assert embed_calls == []

    # Remove `alpha` and update `beta`: alpha pruned, only beta re-embedded!
    (bin_dir / "alpha").unlink()
    new_beta = ".TH BETA 1\n.SH NAME\nbeta \\- inspect network sockets and ports\n.SH OPTIONS\n.TP\n.B \\-l\nListening sockets.\n"
    (man1 / "beta.1.gz").write_bytes(gzip.compress(new_beta.encode("utf-8")))

    report3 = builder.build_index(progress=False, force=False, offline=True)
    assert report3.total_tools == 1
    assert report3.removed_tools == 1
    assert report3.updated_tools == 1
    assert len(embed_calls) == 1 and len(embed_calls[0]) == 1

    ids = np.load(config.tool_embedding_ids_path())
    vecs = np.load(config.tool_embeddings_path())
    assert len(ids) == 1 and vecs.shape == (1, 4)

    with schema.get_connection() as conn:
        assert repository.get_tool(conn, "alpha") is None
        beta = repository.get_tool(conn, "beta")
        assert beta is not None
        assert any(f.short == "-l" for f in beta.flags)

    # Staleness marker written properly
    assert staleness.read_last_indexed() is not None
    assert os.path.exists(config.index_meta_path())
