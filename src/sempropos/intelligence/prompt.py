"""Prompt construction, JSON schema constraints, and resilient output parsing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sempropos.intelligence.contracts import StructuredPrompt

COMMAND_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool": {"type": "string", "description": "Primary CLI tool selected from context"},
        "command": {
            "type": "string",
            "description": "Single runnable shell command on one line (empty if none=true)",
        },
        "explanation": {
            "type": "string",
            "description": "One concise sentence explaining what the command does",
        },
        "alternatives": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional alternative commands when requested",
        },
        "none": {
            "type": "boolean",
            "description": "True only when no tool in the context can accomplish the task",
        },
    },
    "required": ["tool", "command", "explanation", "none"],
    "additionalProperties": False,
}

SYSTEM_INSTRUCTION = (
    "You are a precise offline shell command synthesizer. Choose the single best tool from the "
    "provided local ToolSpec context and emit a JSON object matching the schema: "
    '{"tool": "<name>", "command": "<one runnable command>", "explanation": "<short explanation>", '
    '"alternatives": [], "none": false}. '
    "Prefer flags, subcommands, and patterns from the context, and never invent tools not listed "
    "in the context. Never echo a usage/synopsis line (e.g. `du [OPTION]...`); always emit a "
    "concrete runnable command with realistic placeholders when needed. If no listed tool fits "
    'the task, return {"tool": "", "command": "", "explanation": "", "alternatives": [], "none": true}.'
)

MAX_PROMPT_WORDS = 680
MAX_TOOLS = 5
MAX_SUBCOMMANDS_PER_TOOL = 4
MAX_FLAGS_PER_TOOL = 8
MAX_EXAMPLES_PER_TOOL = 4

_PROSE_MARKERS = (
    "i'm sorry",
    "i am sorry",
    "i cannot",
    "i can't",
    "here is",
    "here's",
    "the command",
    "you can use",
    "explanation",
)

_FENCE_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\s*\n(.*?)```", re.DOTALL)


@dataclass(frozen=True)
class ParsedSynthesis:
    """Normalized structured command extracted from either JSON or raw text output."""

    command: str | None
    tool: str | None = None
    explanation: str = ""
    alternatives: tuple[str, ...] = ()
    explicit_none: bool = False


def _sanitize_single_command(raw_cmd: str) -> str | None:
    """Validate and normalize a single candidate command line."""
    if not raw_cmd:
        return None
    text = raw_cmd.strip()

    # Handle fenced code blocks properly (including ```bash\ncmd\n```!).
    fence_match = _FENCE_BLOCK_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()
    elif "```" in text:
        text = text.replace("```", "").strip()

    # Unwrap inline backticks `cmd`
    if len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        text = text[1:-1].strip()

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None

    command = lines[0]
    while command[:1] in {"$", "#", ">"}:
        command = command[1:].strip()

    if not command or command.upper().strip(".!") == "NONE":
        return None

    lowered = command.lower()
    if any(marker in lowered for marker in _PROSE_MARKERS):
        return None

    # Reject usage/synopsis echoes like `du [OPTION]... FILE...`
    if re.search(r"\[[A-Z][A-Z0-9_.-]*\]", command):
        return None
    if re.search(r"\b(OPTION|OPTIONS|FLAGS)\b", command):
        return None

    if "```" in command or len(command) > 500:
        return None

    return command


def extract_command(raw_output: str) -> str | None:
    """Extract a single runnable shell command from JSON or plain model output."""
    return parse_synthesis_output(raw_output).command


def _try_parse_json_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None

    fence_match = _FENCE_BLOCK_RE.search(text)
    if fence_match:
        text = fence_match.group(1).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None

    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    return parsed if isinstance(parsed, dict) else None


def parse_synthesis_output(raw_output: str) -> ParsedSynthesis:
    """Parse structured JSON synthesis output, falling back gracefully to raw text."""
    if not raw_output or not raw_output.strip():
        return ParsedSynthesis(command=None)

    stripped = raw_output.strip()
    if stripped.upper().strip(".!") == "NONE":
        return ParsedSynthesis(command=None, explicit_none=True)

    obj = _try_parse_json_object(stripped)
    if obj is not None:
        if bool(obj.get("none")):
            return ParsedSynthesis(
                command=None,
                tool=str(obj.get("tool") or "") or None,
                explanation=str(obj.get("explanation") or "").strip(),
                explicit_none=True,
            )
        cmd = _sanitize_single_command(str(obj.get("command") or ""))
        tool = str(obj.get("tool") or "").strip() or None
        explanation = str(obj.get("explanation") or "").strip()
        raw_alts = obj.get("alternatives")
        alts: list[str] = []
        if isinstance(raw_alts, list):
            for item in raw_alts:
                clean_alt = _sanitize_single_command(str(item))
                if clean_alt and clean_alt != cmd and clean_alt not in alts:
                    alts.append(clean_alt)
        return ParsedSynthesis(
            command=cmd,
            tool=tool,
            explanation=explanation,
            alternatives=tuple(alts),
            explicit_none=False,
        )

    return ParsedSynthesis(command=_sanitize_single_command(stripped))


def _word_count(text: str) -> int:
    return len(text.split())


def _format_flag(flag: dict[str, Any]) -> str:
    short = flag.get("short_flag") or flag.get("flag")
    long = flag.get("long_flag")
    forms = [str(f) for f in (short, long) if f]
    if not forms:
        forms = ["*"]
    # Avoid duplicating when flag == long_flag
    forms = list(dict.fromkeys(forms))
    head = ", ".join(forms)
    if flag.get("takes_value"):
        hint = str(flag.get("value_hint") or "value")
        head = f"{head} <{hint}>"
    description = str(flag.get("description") or "").strip()[:180]
    return f"  {head} — {description}"


def context_to_str(context: list[dict[str, Any]]) -> str:
    """Render structured ToolSpec candidates into compact prompt context."""
    lines: list[str] = []
    for candidate in context:
        tool = str(candidate.get("tool") or "unknown")
        summary = str(candidate.get("summary") or "").strip()
        header = f"=== {tool}: {summary} ===" if summary else f"=== {tool} ==="
        lines.append(header)

        synopsis = str(candidate.get("synopsis") or "").strip()
        if synopsis:
            lines.append(f"Usage: {synopsis[:200]}")

        subcommands = candidate.get("subcommands") or []
        if subcommands:
            lines.append("Subcommands:")
            for sub in subcommands:
                sname = str(sub.get("name") or "")
                ssum = str(sub.get("summary") or "").strip()[:120]
                lines.append(f"  {sname} — {ssum}" if ssum else f"  {sname}")

        flags = candidate.get("flags") or []
        if flags:
            lines.append("Flags:")
            for flag in flags:
                lines.append(_format_flag(flag))

        examples = candidate.get("examples") or []
        if examples:
            lines.append("Examples:")
            for ex in examples:
                cmd = str(ex.get("command") or "").strip()
                ctx = str(ex.get("context") or "").strip()[:100]
                if cmd:
                    lines.append(f"  {cmd}  # {ctx}" if ctx else f"  {cmd}")
        lines.append("")

    return "\n".join(lines).strip()


def _truncate_candidates(
    candidates: list[dict[str, Any]],
    max_tools: int,
    max_subs: int,
    max_flags: int,
    max_examples: int,
) -> list[dict[str, Any]]:
    truncated: list[dict[str, Any]] = []
    for cand in candidates[:max_tools]:
        copy = dict(cand)
        if "subcommands" in copy:
            copy["subcommands"] = list(copy["subcommands"])[:max_subs]
        if "flags" in copy:
            copy["flags"] = list(copy["flags"])[:max_flags]
        if "examples" in copy:
            copy["examples"] = list(copy["examples"])[:max_examples]
        truncated.append(copy)
    return truncated


def build_prompt(
    query: str,
    candidates: list[dict[str, Any]],
    *,
    alternatives: int = 1,
) -> StructuredPrompt:
    """Build a token-budgeted structured synthesis prompt from candidate ToolSpecs."""
    max_tools = MAX_TOOLS
    max_subs = MAX_SUBCOMMANDS_PER_TOOL
    max_flags = MAX_FLAGS_PER_TOOL
    max_examples = MAX_EXAMPLES_PER_TOOL

    extra = (
        f" Also provide up to {alternatives - 1} distinct useful alternatives in `alternatives`."
        if alternatives > 1
        else ""
    )
    normalized_query = f"{(query or '').strip()}{extra}"

    while True:
        truncated = _truncate_candidates(candidates, max_tools, max_subs, max_flags, max_examples)
        formatted = context_to_str(truncated)
        total_words = (
            _word_count(SYSTEM_INSTRUCTION) + _word_count(normalized_query) + _word_count(formatted)
        )
        prompt = StructuredPrompt(
            system=SYSTEM_INSTRUCTION,
            context=truncated,
            query=normalized_query,
            json_schema=COMMAND_JSON_SCHEMA,
        )
        if total_words <= MAX_PROMPT_WORDS:
            return prompt

        if max_examples > 1:
            max_examples -= 1
        elif max_flags > 4:
            max_flags -= 1
        elif max_subs > 2:
            max_subs -= 1
        elif max_tools > 1:
            max_tools -= 1
            max_subs = MAX_SUBCOMMANDS_PER_TOOL
            max_flags = MAX_FLAGS_PER_TOOL
            max_examples = MAX_EXAMPLES_PER_TOOL
        else:
            return prompt
