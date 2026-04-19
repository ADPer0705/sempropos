"""
Utilities and helper functions for sempropos.

This module provides shared, reusable helper logic for command parsing,
prompt construction, text manipulation, and timestamp normalization.
"""

from __future__ import annotations

from datetime import datetime, timezone

# ============================================================================
# Command and Prompt Parsing
# ============================================================================


def extract_command(text: str) -> str:
    """
    Extract the shell command from LLM synthesis output.

    Looks for lines prefixed with "Command:" or backtick-wrapped content,
    returning the first non-empty, cleaned line.
    """
    for line in text.splitlines():
        cleaned = line.strip().strip("`")
        if not cleaned:
            continue
        if cleaned.lower().startswith("command:"):
            cleaned = cleaned.split(":", maxsplit=1)[1].strip()
        if cleaned:
            return cleaned
    return text.strip()


def build_final_prompt(base_prompt: str, model_hint: str) -> str:
    """Apply model-specific prompt suffixes if needed."""
    lowered = model_hint.lower()
    if "qwen3" in lowered:
        return base_prompt + "\n/no_think"
    return base_prompt


def word_count(text: str) -> int:
    """Return a conservative word-budget count for prompt sizing."""
    return len(text.split())


def truncate_words(text: str, limit: int) -> str:
    """Trim text to a fixed word budget while preserving stable formatting."""
    if limit <= 0:
        return ""
    words = text.split()
    if len(words) <= limit:
        return text

    truncated_words = words[:limit]
    if "Command:" in truncated_words:
        return " ".join(truncated_words)

    if limit == 1:
        return "Command:"

    return " ".join(words[: limit - 1] + ["Command:"])


def format_flag(flag: dict) -> str:
    """Render one flag record into the prompt's human-readable flag line."""
    forms = [flag.get("flag") or "*"]
    if flag.get("long_flag"):
        forms.append(flag["long_flag"])

    head = ", ".join(forms)
    if flag.get("takes_value"):
        hint = flag.get("value_hint") or "value"
        head = f"{head} <{hint}>"

    description = (flag.get("description") or "").strip()
    return f"  {head} - {description}"


def format_key_flags(flags: list[dict], max_items: int = 3) -> str:
    """Render a compact, human-readable summary of important flags."""
    if not flags:
        return "none"

    items: list[str] = []
    for flag in flags[:max_items]:
        label = flag.get("flag") or "*"
        description = (flag.get("description") or "").strip()
        if description:
            items.append(f"{label} ({description})")
        else:
            items.append(label)
    return ", ".join(items)


def to_utc_timestamp(value: datetime) -> float:
    """Convert datetime to epoch seconds in UTC consistently."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).timestamp()
    return value.astimezone(timezone.utc).timestamp()
