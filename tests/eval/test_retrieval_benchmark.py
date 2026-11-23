"""Golden retrieval evaluation suite (35 realistic CLI tasks + distractor stress tests).

Verifies that structured ToolSpec indexing + Porter-stemmed weighted BM25 +
artifact/intent analysis resolves every known v0.3.0 ranking failure mode.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sempropos.index import repository, schema
from sempropos.model import ExampleSpec, FlagSpec, SubcommandSpec, ToolSpec
from sempropos.retrieval import query_bm25_ranking


def _build_realistic_corpus() -> list[ToolSpec]:
    """Build a 30+ tool realistic Linux corpus including notorious ranking distractors."""
    return [
        ToolSpec(
            name="find",
            summary="Search for files in a directory hierarchy",
            category="filesystem",
            tags=("files", "directory", "search"),
            flags=(
                FlagSpec(short="-size", takes_value=True, value_hint="N", description="File uses N units of space"),
                FlagSpec(short="-mtime", takes_value=True, value_hint="N", description="File modified N*24 hours ago"),
                FlagSpec(short="-name", takes_value=True, value_hint="PATTERN", description="Match filename pattern"),
                FlagSpec(short="-delete", description="Delete matching files"),
                FlagSpec(short="-type", takes_value=True, value_hint="TYPE", description="File type f or d"),
            ),
            examples=(
                ExampleSpec(command="find . -type f -size +100M", description="Find files larger than 100MB", source="tldr"),
                ExampleSpec(command="find /tmp -type f -mtime +30 -delete", description="Delete files older than 30 days", source="tldr"),
                ExampleSpec(command="find . -name '*.md'", description="Find markdown files", source="tldr"),
            ),
        ),
        # Classic distractors that beat `find` in v0.3.0!
        ToolSpec(name="userdel", summary="Delete a user account and related files", category="system"),
        ToolSpec(name="mdel", summary="Delete an MSDOS file", category="filesystem"),
        ToolSpec(name="systemd-tmpfiles", summary="Creates, deletes and cleans up volatile and temporary files", category="process"),
        ToolSpec(
            name="du",
            summary="Estimate file space usage",
            description="Summarize disk usage of files and directories recursively.",
            category="filesystem",
            tags=("disk", "size", "directory"),
            flags=(
                FlagSpec(short="-h", long="--human-readable", description="Print sizes in human readable format"),
                FlagSpec(short="-s", long="--summarize", description="Display only a total for each argument"),
                FlagSpec(short="-d", long="--max-depth", takes_value=True, value_hint="N", description="Limit directory depth"),
            ),
            examples=(
                ExampleSpec(command="du -sh *", description="Show human-readable disk usage by directory", source="tldr"),
            ),
        ),
        ToolSpec(
            name="df",
            summary="Report file system disk space usage",
            category="filesystem",
            tags=("disk", "filesystem"),
            flags=(FlagSpec(short="-h", long="--human-readable", description="Print sizes in powers of 1024"),),
            examples=(ExampleSpec(command="df -h", description="Display free disk space on mounted filesystems", source="tldr"),),
        ),
        ToolSpec(
            name="ls",
            summary="List directory contents",
            category="filesystem",
            flags=(
                FlagSpec(short="-a", long="--all", description="Do not ignore entries starting with . (hidden files)"),
                FlagSpec(short="-l", description="Use a long listing format"),
                FlagSpec(short="-h", long="--human-readable", description="Print human readable file sizes"),
            ),
            examples=(ExampleSpec(command="ls -la", description="List all files including hidden files", source="tldr"),),
        ),
        ToolSpec(
            name="ss",
            summary="Another utility to investigate sockets",
            description="Dump socket statistics for TCP, UDP, and UNIX sockets.",
            category="network",
            tags=("network", "socket", "port"),
            flags=(
                FlagSpec(short="-t", long="--tcp", description="Display TCP sockets"),
                FlagSpec(short="-u", long="--udp", description="Display UDP sockets"),
                FlagSpec(short="-l", long="--listening", description="Display listening sockets"),
                FlagSpec(short="-n", long="--numeric", description="Do not resolve service names"),
                FlagSpec(short="-p", long="--processes", description="Show process using socket"),
            ),
            examples=(ExampleSpec(command="ss -tulnp", description="Show all listening TCP and UDP ports with processes", source="tldr"),),
        ),
        ToolSpec(
            name="tar",
            summary="Archiving utility",
            category="archive",
            flags=(
                FlagSpec(short="-x", long="--extract", description="Extract files from an archive"),
                FlagSpec(short="-c", long="--create", description="Create a new archive"),
                FlagSpec(short="-z", long="--gzip", description="Filter archive through gzip"),
                FlagSpec(short="-C", long="--directory", takes_value=True, value_hint="DIR", description="Change to directory before extraction"),
                FlagSpec(short="-t", long="--list", description="List the contents of an archive"),
            ),
            examples=(
                ExampleSpec(command="tar -xzf archive.tar.gz -C /target", description="Extract tar.gz archive to directory", source="tldr"),
            ),
        ),
        ToolSpec(
            name="7z",
            summary="File archiver with a high compression ratio",
            category="archive",
            subcommands=(
                SubcommandSpec(name="l", summary="List contents of archive", source="help_probe"),
                SubcommandSpec(name="x", summary="Extract files with full paths", source="help_probe"),
                SubcommandSpec(name="a", summary="Add files to archive", source="help_probe"),
            ),
            examples=(ExampleSpec(command="7z l archive.7z", description="List files in a 7z archive", source="tldr"),),
        ),
        ToolSpec(
            name="unzip",
            summary="List, test and extract compressed files in a ZIP archive",
            category="archive",
            examples=(ExampleSpec(command="unzip -l file.zip", description="List contents of a zip file", source="tldr"),),
        ),
        ToolSpec(
            name="git",
            summary="Distributed version control system",
            category="vcs",
            subcommands=(
                SubcommandSpec(name="reset", summary="Reset current HEAD to undo commits", source="tldr"),
                SubcommandSpec(name="revert", summary="Create a new commit that undoes an existing commit", source="tldr"),
                SubcommandSpec(name="stash", summary="Stash uncommitted changes in a dirty working directory", source="tldr"),
                SubcommandSpec(name="rebase", summary="Reapply commits on top of another base tip", source="tldr"),
                SubcommandSpec(name="log", summary="Show commit logs", source="tldr"),
            ),
            examples=(
                ExampleSpec(command="git reset --soft HEAD~1", description="Undo last commit keeping changes staged", source="tldr", subcommand="reset"),
            ),
        ),
        ToolSpec(
            name="rg",
            summary="Recursively search the current directory for lines matching a pattern",
            category="text",
            flags=(
                FlagSpec(short="-u", long="--unrestricted", description="Do not respect .gitignore files"),
                FlagSpec(long="--no-ignore", description="Do not respect ignore files like .gitignore"),
                FlagSpec(short="-i", long="--ignore-case", description="Search case insensitively"),
            ),
            examples=(ExampleSpec(command="rg --no-ignore 'TODO'", description="Search recursively ignoring gitignore", source="tldr"),),
        ),
        ToolSpec(
            name="grep",
            summary="Print lines that match patterns",
            category="text",
            flags=(
                FlagSpec(short="-r", long="--recursive", description="Search directories recursively"),
                FlagSpec(short="-i", long="--ignore-case", description="Ignore case distinctions"),
                FlagSpec(short="-n", long="--line-number", description="Prefix each line with line number"),
            ),
        ),
        ToolSpec(
            name="jq",
            summary="Command-line JSON processor",
            category="text",
            examples=(ExampleSpec(command="jq '.' data.json", description="Pretty-print a JSON file", source="tldr"),),
        ),
        ToolSpec(
            name="systemctl",
            summary="Control the systemd system and service manager",
            category="process",
            subcommands=(
                SubcommandSpec(name="restart", summary="Stop and then start one or more service units", source="tldr"),
                SubcommandSpec(name="status", summary="Show runtime status of service units", source="tldr"),
                SubcommandSpec(name="enable", summary="Enable service units at boot", source="tldr"),
            ),
            examples=(ExampleSpec(command="systemctl restart nginx", description="Restart a systemd service", source="tldr"),),
        ),
        ToolSpec(
            name="journalctl",
            summary="Query the systemd journal logs",
            category="process",
            flags=(
                FlagSpec(short="-u", long="--unit", takes_value=True, value_hint="UNIT", description="Show logs for a systemd service unit"),
                FlagSpec(short="-f", long="--follow", description="Follow live journal output"),
            ),
            examples=(ExampleSpec(command="journalctl -u nginx -f", description="Follow logs for a systemd service", source="tldr"),),
        ),
        ToolSpec(
            name="uv",
            summary="An extremely fast Python package and project manager",
            category="package",
            subcommands=(
                SubcommandSpec(name="venv", summary="Create a Python virtual environment", source="help_probe"),
                SubcommandSpec(name="add", summary="Add dependencies to a project", source="help_probe"),
                SubcommandSpec(name="sync", summary="Update the project environment", source="help_probe"),
            ),
        ),
        ToolSpec(
            name="podman",
            aliases=("docker",),
            summary="Simple management tool for pods, containers and images",
            category="container",
            subcommands=(
                SubcommandSpec(name="ps", summary="List running containers", source="tldr"),
                SubcommandSpec(name="run", summary="Run a command in a new container", source="tldr"),
                SubcommandSpec(name="logs", summary="Fetch the logs of a container", source="tldr"),
            ),
        ),
        ToolSpec(
            name="rsync",
            summary="Fast, versatile, remote and local file-copying and synchronization tool",
            category="network",
            flags=(
                FlagSpec(short="-a", long="--archive", description="Archive mode preserving permissions, symlinks, timestamps"),
                FlagSpec(short="-v", long="--verbose", description="Increase verbosity"),
                FlagSpec(short="-z", long="--compress", description="Compress file data during transfer"),
            ),
            examples=(ExampleSpec(command="rsync -avz src/ user@host:dst/", description="Sync files over SSH preserving permissions", source="tldr"),),
        ),
        ToolSpec(
            name="scp",
            summary="OpenSSH secure file copy over SSH",
            category="network",
            examples=(ExampleSpec(command="scp file.txt user@host:/tmp/", description="Copy file to remote host over SSH", source="tldr"),),
        ),
        ToolSpec(
            name="ssh",
            summary="OpenSSH remote login client",
            category="network",
            flags=(
                FlagSpec(short="-L", takes_value=True, value_hint="BIND", description="Local port forwarding tunnel"),
                FlagSpec(short="-p", takes_value=True, value_hint="PORT", description="Port to connect to on remote host"),
            ),
        ),
        ToolSpec(
            name="curl",
            summary="Transfer a URL and make HTTP requests",
            category="network",
            flags=(
                FlagSpec(short="-X", long="--request", takes_value=True, value_hint="METHOD", description="Specify HTTP request method"),
                FlagSpec(short="-H", long="--header", takes_value=True, value_hint="HEADER", description="Pass custom header"),
                FlagSpec(short="-d", long="--data", takes_value=True, value_hint="DATA", description="HTTP POST data"),
            ),
        ),
        ToolSpec(
            name="chmod",
            summary="Change file mode bits and permissions",
            category="filesystem",
            examples=(ExampleSpec(command="chmod +x script.sh", description="Make a file executable", source="tldr"),),
        ),
        ToolSpec(
            name="chown",
            summary="Change file owner and group ownership",
            category="filesystem",
        ),
        ToolSpec(
            name="sed",
            summary="Stream editor for filtering and transforming text",
            category="text",
            flags=(FlagSpec(short="-i", long="--in-place", description="Edit files in place"),),
            examples=(ExampleSpec(command="sed -i 's/old/new/g' file.txt", description="Replace text in place in a file", source="tldr"),),
        ),
        ToolSpec(
            name="awk",
            summary="Pattern scanning and text column processing language",
            category="text",
            flags=(FlagSpec(short="-F", takes_value=True, value_hint="SEP", description="Field separator delimiter"),),
            examples=(ExampleSpec(command="awk -F, '{print $2}' data.csv", description="Extract second column from CSV", source="tldr"),),
        ),
        ToolSpec(
            name="ps",
            summary="Report a snapshot of the current running processes",
            category="process",
            examples=(ExampleSpec(command="ps aux", description="List all running processes", source="tldr"),),
        ),
        ToolSpec(
            name="pkill",
            summary="Signal or kill processes based on name",
            category="process",
        ),
        ToolSpec(
            name="ffmpeg",
            summary="FFmpeg multimedia video and audio converter",
            category="media",
            examples=(ExampleSpec(command="ffmpeg -i input.mp4 output.mp3", description="Extract audio from mp4 video", source="tldr"),),
        ),
        ToolSpec(
            name="sha256sum",
            summary="Compute and check SHA256 message digest checksum",
            category="security",
        ),
        ToolSpec(
            name="crontab",
            summary="Maintain crontab files to schedule periodic jobs",
            category="process",
        ),
    ]


# (query, expected_top1_set, expected_top3_set)
_BENCHMARK_CASES: list[tuple[str, set[str], set[str]]] = [
    ("find files larger than 100MB", {"find"}, {"find"}),
    ("find markdown files", {"find"}, {"find"}),
    ("find markdowns", {"find"}, {"find"}),
    ("delete files older than 30 days", {"find"}, {"find"}),
    ("show disk usage by folder", {"du"}, {"du"}),
    ("directory size human readable", {"du"}, {"du"}),
    ("check free disk space on mounted filesystems", {"df"}, {"df"}),
    ("list all files including hidden files", {"ls"}, {"ls"}),
    ("list open listening tcp ports", {"ss"}, {"ss"}),
    ("show listening sockets and processes", {"ss"}, {"ss"}),
    ("list files in archive.7z", {"7z"}, {"7z"}),
    ("extract files from backup.tar.gz to folder", {"tar"}, {"tar"}),
    ("list contents of data.zip", {"unzip"}, {"unzip", "7z"}),
    ("undo last git commit", {"git"}, {"git"}),
    ("stash uncommitted changes", {"git"}, {"git"}),
    ("search recursively ignoring gitignore", {"rg"}, {"rg", "grep"}),
    ("grep pattern recursively with line numbers", {"grep"}, {"grep", "rg"}),
    ("pretty print data.json", {"jq"}, {"jq"}),
    ("extract field from json", {"jq"}, {"jq"}),
    ("restart nginx service", {"systemctl"}, {"systemctl"}),
    ("check nginx.service status", {"systemctl"}, {"systemctl"}),
    ("follow systemd service logs", {"journalctl"}, {"journalctl"}),
    ("create python virtual environment with uv", {"uv"}, {"uv"}),
    ("list running docker containers", {"podman"}, {"podman"}),
    ("sync directory over ssh preserving permissions", {"rsync"}, {"rsync", "scp"}),
    ("copy file over ssh", {"scp", "rsync"}, {"scp", "rsync"}),
    ("ssh local port forwarding tunnel", {"ssh"}, {"ssh"}),
    ("send http post request with json header", {"curl"}, {"curl"}),
    ("make script executable", {"chmod"}, {"chmod"}),
    ("change file owner and group", {"chown"}, {"chown"}),
    ("replace string in place in file", {"sed"}, {"sed"}),
    ("extract second column from csv", {"awk"}, {"awk"}),
    ("list all running processes", {"ps"}, {"ps"}),
    ("kill process by name", {"pkill"}, {"pkill"}),
    ("convert video.mp4 to audio", {"ffmpeg"}, {"ffmpeg"}),
    ("compute sha256 checksum of file", {"sha256sum"}, {"sha256sum"}),
    ("schedule periodic cron job", {"crontab"}, {"crontab"}),
]


@pytest.mark.parametrize(("query", "expected_top1", "expected_top3"), _BENCHMARK_CASES)
def test_golden_retrieval_benchmark(
    isolated_env: dict[str, Path],
    query: str,
    expected_top1: set[str],
    expected_top3: set[str],
) -> None:
    schema.initialize_database()
    id_to_name: dict[int, str] = {}
    with schema.get_connection() as conn:
        for spec in _build_realistic_corpus():
            tid, _ = repository.upsert_tool(conn, spec)
            id_to_name[tid] = spec.name

        ranked = query_bm25_ranking(query, top_k=5, conn=conn)
        names = [id_to_name[tid] for tid, _ in ranked]

    assert names, f"No results returned for query: {query!r}"
    assert names[0] in expected_top1, (
        f"Query {query!r}: expected top-1 in {expected_top1}, got {names[:5]}"
    )
    assert set(names[:3]) & expected_top3, (
        f"Query {query!r}: expected top-3 to intersect {expected_top3}, got {names[:5]}"
    )
