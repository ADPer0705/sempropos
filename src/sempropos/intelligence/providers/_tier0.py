"""Tier-0 zero-LLM deterministic fallback synthesis provider.

When no LLM is configured or available, Tier-0 inspects retrieved structured
ToolSpec examples for a clean high-overlap template match and otherwise yields
cleanly so the CLI displays rich structured ToolSpec cards.
"""

from __future__ import annotations

from sempropos.intelligence.contracts import (
    StructuredPrompt,
    SynthesisOptions,
    SynthesisResult,
)
from sempropos.retrieval.query import analyze_query


def is_available() -> bool:
    """Tier-0 is always available offline with zero external dependencies."""
    return True


def get_available_models() -> list[str]:
    return ["deterministic-tier0"]


def synthesize(
    prompt: StructuredPrompt,
    model: str | None = None,
    options: SynthesisOptions | None = None,
) -> SynthesisResult:
    """Return the best high-overlap verified example template when available."""
    analyzed = analyze_query(prompt.query)
    q_terms = set(analyzed.primary_tokens)

    best_cmd = ""
    best_desc = ""
    best_tool: str | None = None
    best_overlap = 0.0

    for rank_idx, candidate in enumerate(prompt.context[:3]):
        tool = str(candidate.get("tool") or "")
        examples = candidate.get("examples") or []
        for ex in examples:
            cmd = str(ex.get("command") or "").strip()
            desc = str(ex.get("context") or "").strip()
            if not cmd:
                continue
            ex_terms = set(analyze_query(f"{tool} {desc} {cmd}").primary_tokens)
            overlap = len(q_terms & ex_terms) / max(len(q_terms), 1)
            # Small bonus for top-ranked candidate tool
            score = overlap + (0.15 if rank_idx == 0 else 0.0)
            if score > best_overlap:
                best_overlap = score
                best_cmd = cmd
                best_desc = desc
                best_tool = tool

    if best_overlap >= 0.55 and best_cmd:
        return SynthesisResult(
            provider="tier0",
            command=best_cmd,
            raw_output=best_cmd,
            tool=best_tool,
            explanation=best_desc,
            metadata={"mode": "example_match", "overlap": round(best_overlap, 3)},
        )

    return SynthesisResult(provider="tier0", metadata={"mode": "card_fallback"})
