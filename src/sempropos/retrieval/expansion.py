"""Directional, weighted query expansion for CLI retrieval.

Unlike symmetric synonym cliques (which cause runaway transitive drift),
each term expands *directionally* to domain vocabulary with an explicit weight
in ``(0, 1]``. Porter stemming in SQLite FTS5 handles inflectional morphology.
"""

from __future__ import annotations

from sempropos.retrieval.query import analyze_query, tokenize

# Directional term -> [(expanded_term, weight)]
_DIRECTIONAL_EXPANSIONS: dict[str, tuple[tuple[str, float], ...]] = {
    "folder": (("directory", 0.85), ("dir", 0.55)),
    "folders": (("directory", 0.85), ("dir", 0.55)),
    "directory": (("folder", 0.5), ("files", 0.4)),
    "disk": (("filesystem", 0.65), ("space", 0.65), ("storage", 0.55), ("block", 0.4)),
    "usage": (("space", 0.6), ("size", 0.6), ("estimate", 0.45)),
    "size": (("space", 0.55), ("bytes", 0.5), ("usage", 0.45)),
    "port": (("socket", 0.85), ("tcp", 0.6), ("udp", 0.6), ("listening", 0.6)),
    "ports": (("socket", 0.85), ("tcp", 0.6), ("udp", 0.6), ("listening", 0.6)),
    "network": (("socket", 0.7), ("interface", 0.65), ("tcp", 0.55), ("packet", 0.5)),
    "archive": (("compress", 0.6), ("extract", 0.6), ("tar", 0.5), ("zip", 0.5)),
    "unzip": (("extract", 0.75), ("decompress", 0.7), ("archive", 0.5)),
    "extract": (("archive", 0.6), ("decompress", 0.6), ("unpack", 0.55)),
    "compress": (("archive", 0.6), ("zip", 0.5), ("gzip", 0.5)),
    "delete": (("remove", 0.75), ("unlink", 0.5), ("erase", 0.45)),
    "remove": (("delete", 0.65), ("unlink", 0.5)),
    "search": (("find", 0.7), ("grep", 0.65), ("match", 0.6), ("pattern", 0.55)),
    "find": (("search", 0.7), ("locate", 0.55)),
    "kill": (("terminate", 0.75), ("signal", 0.7), ("process", 0.55)),
    "process": (("pid", 0.65), ("task", 0.45), ("running", 0.45)),
    "service": (("systemd", 0.75), ("unit", 0.6), ("daemon", 0.55)),
    "logs": (("journal", 0.75), ("syslog", 0.55), ("messages", 0.45)),
    "download": (("fetch", 0.65), ("http", 0.55), ("url", 0.55), ("transfer", 0.5)),
    "upload": (("transfer", 0.65), ("copy", 0.5), ("remote", 0.5)),
    "copy": (("duplicate", 0.45), ("transfer", 0.45), ("sync", 0.45)),
    "sync": (("mirror", 0.65), ("copy", 0.55), ("remote", 0.5)),
    "permissions": (("mode", 0.75), ("chmod", 0.7), ("access", 0.5)),
    "owner": (("ownership", 0.8), ("chown", 0.7), ("group", 0.5)),
    "replace": (("substitute", 0.8), ("sed", 0.6), ("transform", 0.5)),
    "text": (("pattern", 0.55), ("lines", 0.55), ("string", 0.5)),
    "json": (("jq", 0.8), ("filter", 0.45), ("parse", 0.45)),
    "video": (("ffmpeg", 0.75), ("media", 0.65), ("transcode", 0.6)),
    "audio": (("ffmpeg", 0.7), ("sound", 0.55), ("media", 0.55)),
    "image": (("picture", 0.55), ("photo", 0.55), ("convert", 0.55), ("resize", 0.5)),
    "encrypt": (("cipher", 0.7), ("gpg", 0.65), ("openssl", 0.6), ("decrypt", 0.55)),
    "hash": (("checksum", 0.85), ("digest", 0.75), ("sha256", 0.65)),
    "schedule": (("cron", 0.8), ("timer", 0.7), ("periodic", 0.55)),
    "container": (("docker", 0.75), ("podman", 0.75), ("image", 0.45)),
    "commit": (("git", 0.8), ("repository", 0.6)),
    "branch": (("git", 0.75), ("checkout", 0.6)),
    "venv": (("virtual", 0.75), ("environment", 0.7), ("python", 0.6)),
    "package": (("install", 0.55), ("dependency", 0.55)),
}


def primary_tokens(query: str) -> list[str]:
    """Return stopword-filtered primary tokens from ``query``."""
    return list(analyze_query(query).primary_tokens)


def expand_weighted(tokens: list[str] | tuple[str, ...]) -> dict[str, float]:
    """Return ``{expanded_term: max_weight}`` excluding terms already in ``tokens``."""
    primary_set = {t.lower() for t in tokens}
    weighted: dict[str, float] = {}
    for tok in tokens:
        for syn, weight in _DIRECTIONAL_EXPANSIONS.get(tok.lower(), ()):
            if syn not in primary_set:
                weighted[syn] = max(weighted.get(syn, 0.0), weight)
    return weighted


def expand_query(query: str) -> list[str]:
    """Return primary tokens followed by directional expansion terms (highest weight first)."""
    prim = primary_tokens(query)
    expanded = expand_weighted(prim)
    extras = [term for term, _w in sorted(expanded.items(), key=lambda kv: (-kv[1], kv[0]))]
    return [*prim, *extras]


__all__ = ["tokenize", "primary_tokens", "expand_weighted", "expand_query"]
