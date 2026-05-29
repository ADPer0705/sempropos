"""Prompt construction for command synthesis."""

from __future__ import annotations

from sempropos.intelligence.contracts import StructuredPrompt

SYSTEM_INSTRUCTION = "You are a CLI command synthesizer. Output ONLY the exact shell command or pipeline. No explanation. No markdown. No preamble."

MAX_PROMPT_WORDS = 600
MAX_TOOLS = 3
MAX_FLAGS_PER_TOOL = 6
MAX_EXAMPLES_PER_TOOL = 4


def _word_count(text: str) -> int:
    """Return a conservative word-budget count for prompt sizing."""
    return len(text.split())


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


def context_to_str(context: list[dict]) -> str:
    """
    Convert the structured context list into a human-readable string.
    Note: The context list should already be truncated to budget before calling this.
    """
    lines: list[str] = []
    lines.append("")
    
    for candidate in context:
        tool = candidate.get("tool", "unknown")
        lines.append(f"--- {tool} ---")

        synopsis = (candidate.get("synopsis") or "").strip()
        if synopsis:
            lines.append(f"Synopsis: {synopsis}")

        flags = candidate.get("flags") or []
        if flags:
            lines.append("Relevant flags:")
            for flag in flags:
                lines.append(_format_flag(flag))

        examples = candidate.get("examples") or []
        if examples:
            lines.append("Examples:")
            for example in examples:
                command = (example.get("command") or "").strip()
                if command:
                    lines.append(f"  {command}")

        lines.append("---------")
        lines.append("")

    return "\n".join(lines)


def _truncate_candidates(
    candidates: list[dict], max_tools: int, max_flags: int, max_examples: int
) -> list[dict]:
    """Deep copy and truncate the candidate data structure to fit constraints."""
    truncated = []
    for c in candidates[:max_tools]:
        new_c = c.copy()
        if "flags" in new_c:
            new_c["flags"] = new_c["flags"][:max_flags]
        if "examples" in new_c:
            new_c["examples"] = new_c["examples"][:max_examples]
        truncated.append(new_c)
    return truncated


def build_prompt(
    query: str,
    candidates: list[dict],
) -> StructuredPrompt:
    """Build the prompt body while budgeting the token usage."""
    max_tools = MAX_TOOLS
    max_flags = MAX_FLAGS_PER_TOOL
    max_examples = MAX_EXAMPLES_PER_TOOL

    normalized_query = (query or "").strip() + "\n\nCommand:"

    while True:
        # 1. Truncate the raw data structure
        truncated_context = _truncate_candidates(candidates, max_tools, max_flags, max_examples)
        
        # 2. Build the actual string to calculate word limits
        formatted_context = context_to_str(truncated_context)
        
        total_words = _word_count(SYSTEM_INSTRUCTION) + _word_count(normalized_query) + _word_count(formatted_context)

        # 3. Create the prompt object
        prompt = StructuredPrompt(
            system=SYSTEM_INSTRUCTION,
            query=normalized_query,
            context=truncated_context, # Store the pure list of dicts!
        )

        if total_words <= MAX_PROMPT_WORDS:
            return prompt

        if max_examples > 0:
            max_examples -= 1
            continue

        if max_flags > 3:
            max_flags -= 1
            continue

        if max_tools > 1:
            max_tools -= 1
            max_flags = MAX_FLAGS_PER_TOOL
            max_examples = MAX_EXAMPLES_PER_TOOL
            continue

        if max_tools == 1 and max_flags == 3 and max_examples == 0:
            return prompt