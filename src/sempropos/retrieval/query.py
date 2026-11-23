"""Structured natural-language query analysis for CLI tool retrieval."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Common natural-language filler words that dilute OR-based FTS queries.
# Short Unix command names (ls, du, df, cp, mv, rm, ss, ps, ip, jq, 7z, uv, rg, fd, nc, dd, tr, wc, id, ln, vi)
# are intentionally NEVER included here.
STOPWORDS = frozenset(
    {
        "a",
        "about",
        "all",
        "an",
        "and",
        "any",
        "are",
        "as",
        "at",
        "be",
        "between",
        "by",
        "can",
        "cli",
        "command",
        "commands",
        "currently",
        "do",
        "does",
        "each",
        "every",
        "for",
        "from",
        "get",
        "give",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "linux",
        "make",
        "me",
        "my",
        "need",
        "of",
        "on",
        "one",
        "only",
        "or",
        "out",
        "please",
        "run",
        "shell",
        "should",
        "some",
        "terminal",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "this",
        "those",
        "to",
        "tool",
        "unix",
        "use",
        "using",
        "want",
        "way",
        "what",
        "when",
        "where",
        "which",
        "with",
        "without",
        "would",
    }
)

# File extension / artifact suffix -> canonical tool boosts.
_EXTENSION_TOOL_MAP: dict[str, tuple[str, ...]] = {
    "7z": ("7z", "7za", "7zr", "bsdtar"),
    "tar": ("tar",),
    "tgz": ("tar", "gzip"),
    "tbz2": ("tar", "bzip2"),
    "txz": ("tar", "xz"),
    "gz": ("gzip", "gunzip", "zcat", "tar"),
    "bz2": ("bzip2", "bunzip2", "tar"),
    "xz": ("xz", "unxz", "tar"),
    "zst": ("zstd", "unzstd", "tar"),
    "zip": ("unzip", "zip", "7z"),
    "rar": ("unrar", "7z"),
    "json": ("jq",),
    "jsonl": ("jq",),
    "yaml": ("yq",),
    "yml": ("yq",),
    "pdf": ("pdftotext", "pdfinfo", "qpdf", "gs"),
    "mp4": ("ffmpeg", "ffprobe"),
    "mkv": ("ffmpeg", "ffprobe"),
    "mov": ("ffmpeg", "ffprobe"),
    "mp3": ("ffmpeg", "ffprobe"),
    "wav": ("ffmpeg", "sox"),
    "flac": ("ffmpeg", "flac"),
    "png": ("magick", "convert", "ffmpeg"),
    "jpg": ("magick", "convert", "ffmpeg"),
    "jpeg": ("magick", "convert", "ffmpeg"),
    "webp": ("magick", "convert", "cwebp"),
    "svg": ("inkscape", "rsvg-convert", "magick"),
    "deb": ("dpkg", "apt"),
    "rpm": ("rpm", "dnf"),
    "service": ("systemctl", "journalctl"),
    "timer": ("systemctl",),
    "sql": ("sqlite3", "psql", "mysql"),
    "sqlite": ("sqlite3",),
    "db": ("sqlite3",),
}

# Compound natural-language intent rules: (required_token_groups, boosted_tools)
# Each rule matches when EVERY group has at least one token/phrase present in the query.
_COMPOUND_INTENT_RULES: tuple[tuple[tuple[tuple[str, ...], ...], tuple[tuple[str, float], ...]], ...] = (
    # "show disk usage by folder" / "directory size" -> du
    (
        (("disk", "space", "storage", "size"), ("usage", "size", "folder", "folders", "directory", "directories", "dir")),
        (("du", 1.35), ("df", 0.55)),
    ),
    # "free disk space" / "mounted filesystems" -> df
    (
        (("free", "available", "mounted", "filesystem", "filesystems"), ("disk", "space", "storage", "partition", "partitions")),
        (("df", 1.35), ("lsblk", 0.7), ("du", 0.4)),
    ),
    # "open ports" / "listening tcp ports" / "listening sockets" -> ss
    (
        (("port", "ports", "socket", "sockets"), ("open", "listening", "listen", "tcp", "udp", "active", "established", "connections")),
        (("ss", 1.45), ("lsof", 0.8), ("netstat", 0.7)),
    ),
    # "files older than 30 days" / "files larger than 100MB" -> find (even with "delete"!)
    (
        (("file", "files", "directory", "directories", "folder", "folders"), ("older", "newer", "larger", "bigger", "smaller", "modified", "mtime", "size", "days", "weeks", "empty")),
        (("find", 1.55), ("fd", 0.75)),
    ),
    # "undo last commit" / "stash changes" / "git branch" -> git
    (
        (("commit", "commits", "branch", "branches", "stash", "rebase", "cherry", "merge", "diff", "staged"),),
        (("git", 1.3),),
    ),
    # "restart nginx service" / "check service status" -> systemctl
    (
        (("service", "services", "daemon", "unit"), ("start", "stop", "restart", "reload", "enable", "disable", "status", "failed")),
        (("systemctl", 1.45), ("journalctl", 0.55)),
    ),
    # "service logs" / "systemd journal" -> journalctl
    (
        (("log", "logs", "journal"), ("service", "systemd", "unit", "boot", "kernel")),
        (("journalctl", 1.35), ("dmesg", 0.6)),
    ),
    # "search recursively" / "ignoring gitignore" / "pattern in files" -> rg / grep
    (
        (("search", "grep", "match", "find"), ("recursively", "recursive", "gitignore", "pattern", "regex", "text", "string", "lines")),
        (("rg", 1.25), ("grep", 1.1)),
    ),
    # "pretty print json" / "parse json" / "extract field from json" -> jq
    (
        (("json",), ("pretty", "format", "parse", "extract", "filter", "field", "key", "query")),
        (("jq", 1.5),),
    ),
    # "copy files over ssh" / "sync directory" / "preserving permissions" -> rsync / scp
    (
        (("copy", "sync", "transfer", "upload", "mirror"), ("ssh", "remote", "server", "host", "permissions", "incremental")),
        (("rsync", 1.35), ("scp", 1.1)),
    ),
    # "make file executable" / "change permissions" -> chmod
    (
        (("permission", "permissions", "executable", "mode"), ("change", "set", "make", "grant", "add", "remove")),
        (("chmod", 1.4), ("chown", 0.5)),
    ),
    # "change owner" / "change ownership" -> chown
    (
        (("owner", "ownership", "group"), ("change", "set", "transfer")),
        (("chown", 1.4),),
    ),
    # "running processes" / "process list" / "memory usage by process" -> ps / top
    (
        (("process", "processes", "pid"), ("list", "running", "snapshot", "show", "all", "tree", "memory", "cpu")),
        (("ps", 1.2), ("pgrep", 0.8), ("top", 0.6)),
    ),
    # "kill process by name" / "stop process" -> pkill / kill
    (
        (("kill", "terminate", "signal"), ("process", "pid", "name", "port")),
        (("pkill", 1.0), ("kill", 1.0), ("killall", 0.7)),
    ),
    # "download file from url" / "http post request" -> curl / wget
    (
        (("http", "https", "url", "api", "endpoint", "header", "headers"), ("request", "post", "get", "download", "fetch", "send", "curl")),
        (("curl", 1.3), ("wget", 0.8)),
    ),
    # "replace text in place" / "substitute string in file" -> sed
    (
        (("replace", "substitute"), ("text", "string", "word", "pattern", "line", "lines", "file", "inplace", "place")),
        (("sed", 1.35), ("awk", 0.5)),
    ),
    # "extract column" / "sum numbers in column" -> awk / cut
    (
        (("column", "columns", "field", "fields", "delimiter", "csv"), ("extract", "print", "sum", "split", "select")),
        (("awk", 1.2), ("cut", 1.1)),
    ),
)

_EXT_RE = re.compile(r"\.([a-z0-9]{2,7})\b")
_TAR_COMPOUND_EXT_RE = re.compile(r"\.tar\.(gz|bz2|xz|zst|lzma)\b")
_FLAG_MENTION_RE = re.compile(r"(?:^|\s)(--[a-zA-Z0-9][a-zA-Z0-9_-]*|-[a-zA-Z0-9])\b")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_+.-]*")


@dataclass(frozen=True)
class AnalyzedQuery:
    """Structured features extracted from a raw user query."""

    raw: str
    raw_tokens: tuple[str, ...]
    primary_tokens: tuple[str, ...]
    flag_tokens: tuple[str, ...] = ()
    artifact_boosts: dict[str, float] = field(default_factory=dict)
    intent_tool_boosts: dict[str, float] = field(default_factory=dict)

    @property
    def has_strong_hint(self) -> bool:
        return bool(self.artifact_boosts or self.intent_tool_boosts)


def tokenize(text: str) -> list[str]:
    """Lowercase and tokenize text into alphanumeric terms."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _singular_variant(token: str) -> str | None:
    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        if token.endswith("ies") and len(token) > 4:
            return f"{token[:-3]}y"
        return token[:-1]
    return None


def analyze_query(query: str) -> AnalyzedQuery:
    """Analyze a natural-language query into filtered terms, artifacts, and intent hints."""
    cleaned = (query or "").strip()
    lowered = cleaned.lower()

    raw_tokens_list = tokenize(lowered)
    # Filter stopwords, but keep everything if filtering would leave the query empty.
    filtered = [tok for tok in raw_tokens_list if tok not in STOPWORDS]
    if not filtered:
        filtered = list(raw_tokens_list)

    # Deduplicate while preserving order and adding safe singular forms for exact matching.
    primary_list: list[str] = []
    seen: set[str] = set()
    for tok in filtered:
        if tok not in seen:
            primary_list.append(tok)
            seen.add(tok)
        singular = _singular_variant(tok)
        if singular and singular not in seen and singular not in STOPWORDS:
            primary_list.append(singular)
            seen.add(singular)

    # Extract explicit flag mentions (e.g. "-name", "--max-depth")
    flag_tokens = tuple(dict.fromkeys(m.group(1) for m in _FLAG_MENTION_RE.finditer(cleaned)))

    # Extract file-extension / artifact hints (`.7z`, `.tar.gz`, `.json`, `.service`, ...)
    artifact_boosts: dict[str, float] = {}
    if _TAR_COMPOUND_EXT_RE.search(lowered):
        artifact_boosts["tar"] = max(artifact_boosts.get("tar", 0.0), 1.8)
    for match in _EXT_RE.finditer(lowered):
        ext = match.group(1)
        tools = _EXTENSION_TOOL_MAP.get(ext)
        if tools:
            for idx, tool_name in enumerate(tools):
                weight = 1.8 if idx == 0 else max(0.7, 1.3 - 0.3 * idx)
                artifact_boosts[tool_name] = max(artifact_boosts.get(tool_name, 0.0), weight)

    # Evaluate compound intent rules against the full normalized token set.
    token_bag = set(raw_tokens_list) | set(primary_list)
    intent_boosts: dict[str, float] = {}
    for groups, targets in _COMPOUND_INTENT_RULES:
        if all(any(term in token_bag for term in group) for group in groups):
            for tool_name, weight in targets:
                intent_boosts[tool_name] = max(intent_boosts.get(tool_name, 0.0), weight)

    return AnalyzedQuery(
        raw=cleaned,
        raw_tokens=tuple(dict.fromkeys(raw_tokens_list)),
        primary_tokens=tuple(primary_list),
        flag_tokens=flag_tokens,
        artifact_boosts=artifact_boosts,
        intent_tool_boosts=intent_boosts,
    )
