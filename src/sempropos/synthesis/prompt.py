"""Prompt construction for command synthesis."""

from __future__ import annotations

SYSTEM_INSTRUCTION = (
    "You are a CLI command synthesizer. Output ONLY the exact shell command "
    "or pipeline. No explanation. No markdown. No preamble."
)

MAX_PROMPT_WORDS = 600


def _word_count(text: str) -> int:
    """Return a conservative word-budget count for prompt sizing."""
    return len(text.split())


def _truncate_words(text: str, limit: int) -> str:
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


def _format_flag(flag: dict) -> str:
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


def _build_with_limits(
    query: str,
    candidates: list[dict],
    max_flags: int,
    max_examples: int,
) -> str:
    """Build the prompt body while limiting flags and examples per tool."""
    lines: list[str] = [SYSTEM_INSTRUCTION, f"Task: {query}", ""]

    for candidate in candidates[:3]:
        tool = candidate.get("tool", "unknown")
        lines.append(f"--- {tool}(1) ---")

        synopsis = (candidate.get("synopsis") or "").strip()
        if synopsis:
            lines.append(f"Synopsis: {synopsis}")

        flags = candidate.get("flags") or []
        if flags:
            lines.append("Relevant flags:")
            for flag in flags[:max_flags]:
                lines.append(_format_flag(flag))

        examples = candidate.get("examples") or []
        if examples:
            lines.append("Examples:")
            for example in examples[:max_examples]:
                command = (example.get("command") or "").strip()
                if command:
                    lines.append(f"  {command}")

        lines.append("---------")
        lines.append("")

    lines.append("Command:")
    return "\n".join(lines)


def build_prompt(query: str, candidates: list[dict]) -> str:
    """Build synthesis prompt and keep it under the target token budget."""
    max_flags = 6
    max_examples = 4
    normalized_query = (query or "").strip()

    while True:
        prompt = _build_with_limits(
            normalized_query,
            candidates,
            max_flags=max_flags,
            max_examples=max_examples,
        )
        if _word_count(prompt) <= MAX_PROMPT_WORDS:
            return prompt

        if max_examples > 0:
            max_examples -= 1
            continue
        if max_flags > 3:
            max_flags -= 1
            continue

        return _truncate_words(prompt, MAX_PROMPT_WORDS)
