"""Rule-based query expansion.

Expansion adds *synonyms* to improve recall, but they are treated as a weaker
signal than the user's own words (see :mod:`sempropos.retrieval.bm25`). Keeping
the primary tokens separate prevents an ambiguous verb such as "scan" from
dragging in an entire unrelated synonym group.
"""

from __future__ import annotations

import re

# Base mapping
_BASE_SYNONYMS: dict[str, list[str]] = {
    "see": ["list", "view", "show", "display", "print", "read"],
    "files": ["contents", "entries", "members", "paths"],
    "archive": ["compress", "extract", "zip", "tar", "gz", "7z", "bz2", "xz"],
    "delete": ["remove", "rm", "erase", "unlink", "clean"],
    "find": ["search", "locate", "grep", "filter"],
    "network": ["socket", "tcp", "udp", "interface", "packet", "port", "http"],
    "kill": ["terminate", "stop", "signal", "process", "pid"],
    "disk": ["partition", "mount", "filesystem", "df", "du", "block"],
    "user": ["account", "passwd", "group", "permission", "sudo"],
    "monitor": ["watch", "top", "stat", "trace", "profile", "log"],
    "convert": ["transform", "encode", "decode", "transcode", "format"],
    "download": ["fetch", "get", "pull", "curl", "wget", "request"],
    "send": ["transfer", "push", "upload", "scp", "rsync", "copy"],
    "text": ["string", "line", "grep", "sed", "awk", "parse"],
    "image": ["photo", "picture", "png", "jpg", "jpeg", "svg", "resize"],
    "run": ["execute", "launch", "start", "spawn", "exec"],
    "schedule": ["cron", "timer", "at", "systemd", "interval"],
    "encrypt": ["decrypt", "cipher", "gpg", "ssl", "tls", "hash", "sign"],
}

# Suffixes that must not be crudely singularized ("status" -> "statu").
_NO_STRIP_SUFFIXES = ("ss", "us", "is")


def _build_symmetric_network() -> dict[str, list[str]]:
    """Dynamically build a bi-directional synonym lookup network."""
    network: dict[str, set[str]] = {}
    for base, syns in _BASE_SYNONYMS.items():
        if base not in network:
            network[base] = set()
        network[base].update(syns)
        for syn in syns:
            if syn not in network:
                network[syn] = set()
            network[syn].add(base)
            network[syn].update(s for s in syns if s != syn)
    return {k: list(v) for k, v in network.items()}


# Computed once when the module loads
SYNONYMS = _build_symmetric_network()


def tokenize(text: str) -> list[str]:
    """Lowercase and tokenize a user query into alphanumeric terms."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _variants(token: str) -> list[str]:
    """Return a token plus its naive singular form, when it is safe to derive."""
    variants = [token]
    if len(token) > 3 and token.endswith("s") and not token.endswith(_NO_STRIP_SUFFIXES):
        variants.append(token[:-1])
    return variants


def primary_tokens(query: str) -> list[str]:
    """Return the strong tokens (the user's words plus singular forms)."""
    ordered: list[str] = []
    seen: set[str] = set()
    for token in tokenize(query):
        for variant in _variants(token):
            if variant not in seen:
                ordered.append(variant)
                seen.add(variant)
    return ordered


def expand_query(query: str) -> list[str]:
    """Tokenize query and add symmetric synonym expansions."""
    ordered: list[str] = []
    seen: set[str] = set()

    for token in tokenize(query):
        for variant in _variants(token):
            if variant not in seen:
                ordered.append(variant)
                seen.add(variant)

            for synonym in SYNONYMS.get(variant, []):
                if synonym not in seen:
                    ordered.append(synonym)
                    seen.add(synonym)

    return ordered
