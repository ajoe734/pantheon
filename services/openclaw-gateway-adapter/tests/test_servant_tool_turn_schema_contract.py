"""Cross-service contract: the Agora servant tool-turn schema vs. the adapter validator.

AGORA-SERVANT-DATA-TOOLS-20261127. Each servant tool round is one ``/structured`` call with
``SERVANT_TURN_SCHEMA``. The real schema from the BFF runs through the real adapter validator
and ``jsonschema``, so a keyword the adapter cannot enforce fails here, not on a paid answer.
"""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import jsonschema
import pytest

ADAPTER_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for _path in (ADAPTER_DIR, REPO_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from assistant_openclaw_provider import (  # noqa: E402
    OpenClawProviderError,
    _validate_extraction_arguments,
    unsupported_schema_keyword_paths,
)
from services.control_plane.bff.agora.servant.tool_turn import SERVANT_TURN_SCHEMA  # noqa: E402

CALL_TOOLS = {
    "action": "call_tools",
    "tool_calls": [
        {"tool": "broker_branch_flow", "arguments": {"symbols": ["2330"], "side": "both", "date_from": "2026-11-20"}},
        {"tool": "news", "arguments": {"keyword": "台積電", "since": "2026-11-25"}},
    ],
    "answer_text": "",
    "cited_fact_ids": [],
    "missing_data": [],
}
ANSWER = {
    "action": "answer",
    "tool_calls": [],
    "answer_text": "2330 最新交易日買超第一名淨買 {{f1}}。",
    "cited_fact_ids": ["f1"],
    "missing_data": ["內部人申報持股"],
}


def _adapter_accepts(answer) -> bool:
    try:
        _validate_extraction_arguments(answer, SERVANT_TURN_SCHEMA)
    except OpenClawProviderError as error:
        assert error.error_code == "OPENCLAW_TOOL_ARGS_SCHEMA_MISMATCH"
        return False
    return True


def test_schema_uses_only_adapter_enforced_keywords():
    assert unsupported_schema_keyword_paths(SERVANT_TURN_SCHEMA) == []


@pytest.mark.parametrize("answer", [CALL_TOOLS, ANSWER])
def test_valid_rounds_are_accepted_on_both_sides(answer):
    assert _adapter_accepts(answer) is True
    assert jsonschema.Draft7Validator(SERVANT_TURN_SCHEMA).is_valid(answer) is True


def _mutate(base, path, value):
    answer = copy.deepcopy(base)
    target = answer
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return answer


@pytest.mark.parametrize(
    "answer",
    [
        _mutate(ANSWER, ["action"], "place_order"),
        _mutate(CALL_TOOLS, ["tool_calls"], CALL_TOOLS["tool_calls"] * 3),  # more than 4 calls a round
        _mutate(CALL_TOOLS, ["tool_calls", 0, "arguments", "side"], "net"),
        _mutate(CALL_TOOLS, ["tool_calls", 0, "arguments", "persona_id"], "persona-x"),  # no free arguments
        _mutate(ANSWER, ["answer_text"], "x" * 3001),
        {k: v for k, v in ANSWER.items() if k != "missing_data"},
    ],
)
def test_invalid_rounds_are_refused_on_both_sides(answer):
    assert _adapter_accepts(answer) is False
    assert jsonschema.Draft7Validator(SERVANT_TURN_SCHEMA).is_valid(answer) is False
