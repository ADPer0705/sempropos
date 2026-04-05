from __future__ import annotations

from sempropos.synthesis.prompt import build_prompt


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
