from __future__ import annotations

from sempropos.synthesis.prompt import MAX_PROMPT_WORDS, _truncate_words, build_prompt


def test_prompt_stays_under_token_budget() -> None:
    candidates = [
        {
            "tool": "7z",
            "synopsis": "7z <command> [<switches>] <archive_name> [<files>]",
            "flags": [
                {
                    "flag": "l",
                    "long_flag": None,
                    "takes_value": False,
                    "value_hint": None,
                    "description": "List archive contents",
                },
                {
                    "flag": "e",
                    "long_flag": None,
                    "takes_value": False,
                    "value_hint": None,
                    "description": "Extract files",
                },
            ],
            "examples": [
                {"command": "7z l archive.7z", "context": None},
                {"command": "7z e archive.7z", "context": None},
            ],
        },
        {
            "tool": "tar",
            "synopsis": "tar [OPTION...] [FILE]...",
            "flags": [
                {
                    "flag": "-tf",
                    "long_flag": "--list",
                    "takes_value": True,
                    "value_hint": "archive",
                    "description": "List archive entries",
                }
            ],
            "examples": [{"command": "tar -tf archive.tar", "context": None}],
        },
    ]

    prompt = build_prompt("list files in archive.7z", candidates)
    assert "Command:" in prompt
    assert len(prompt.split()) <= 600


def test_prompt_handles_empty_candidates() -> None:
    prompt = build_prompt("echo hello", [])
    assert "Task: echo hello" in prompt
    assert prompt.endswith("Command:")


def test_prompt_reduces_examples_before_hard_truncate() -> None:
    many_examples = [
        {
            "command": f"tool --example-{idx} alpha beta gamma delta epsilon",
            "context": None,
        }
        for idx in range(30)
    ]
    candidates = [
        {
            "tool": "tool",
            "synopsis": "tool [options]",
            "flags": [
                {
                    "flag": "--long-option",
                    "long_flag": None,
                    "takes_value": True,
                    "value_hint": "value",
                    "description": "extremely verbose description field for prompt shaping",
                }
                for _ in range(10)
            ],
            "examples": many_examples,
        }
    ]

    prompt = build_prompt("run very detailed task", candidates)
    assert "Command:" in prompt
    assert len(prompt.split()) <= MAX_PROMPT_WORDS
    # Ensure prompt still has section structure after reduction.
    assert "Relevant flags:" in prompt
    assert "Examples:" in prompt


def test_hard_truncation_keeps_command_marker() -> None:
    long_text = " ".join(["token"] * 700) + " Command:"
    truncated = _truncate_words(long_text, 600)

    assert len(truncated.split()) <= 600
    assert "Command:" in truncated.split()
