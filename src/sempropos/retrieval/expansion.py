"""Rule-based query expansion."""

from __future__ import annotations

import re


SYNONYMS: dict[str, list[str]] = {
    "see": ["list", "view", "show", "display", "print", "read"],
    "files": ["contents", "entries", "members", "paths"],
    "archive": ["compress", "extract", "zip", "tar", "gz", "7z", "bz2", "xz"],
    "delete": ["remove", "rm", "erase", "unlink", "clean"],
    "find": ["search", "locate", "grep", "scan", "filter"],
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


def _tokenize(text: str) -> list[str]:
    """Lowercase and tokenize a user query into alphanumeric terms."""
    return re.findall(r"[a-z0-9]+", text.lower())


def expand(query: str) -> list[str]:
    """Tokenize query and add static synonym expansions."""
    ordered: list[str] = []
    seen: set[str] = set()

    for token in _tokenize(query):
        if token not in seen:
            ordered.append(token)
            seen.add(token)

        for synonym in SYNONYMS.get(token, []):
            if synonym not in seen:
                ordered.append(synonym)
                seen.add(synonym)

    return ordered
