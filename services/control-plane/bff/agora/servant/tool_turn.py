"""One servant turn with read-only data tools (AGORA-SERVANT-DATA-TOOLS-20261127, SD §4.3).

The turn runs in ``agora-interaction-worker`` (AD-AG-05). Each round is one structured provider
call that either asks for tools or answers. At most ``MAX_TOOL_ROUNDS`` tool rounds run, then
the model must answer.

The model never supplies a number (11/16 rule):

* it shows a value only by writing a fact reference ``{{f3}}``; the BFF renders the fact's
  value and unit from the tool result;
* any other number in its text (other than a stock code or date the facts or the trader already
  carry) is removed and recorded in ``number_guard``.

When no tool covers the request the answer lists the missing data; it never answers from model
memory as if that were data. No persona and no meeting is consulted in this turn (SD §10).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from .data_tools import (
    MAX_CALLS_PER_ROUND,
    MAX_FACTS_PER_ANSWER,
    MAX_TOOL_ROUNDS,
    ServantDataTools,
    ToolContext,
    ToolResult,
    seeded_tool_calls,
)

TURN_SCHEMA_VERSION = "agora.servant.tool_turn.v1"
PACKET_MARKER = "SERVANT_TURN_PACKET_JSON:"
HISTORY_CONTENT_MAX_CHARS = 1000
ANSWER_MAX_CHARS = 3000
REDACTED_NUMBER = "〔未引用數字〕"

_ARGUMENT_PROPERTIES: Dict[str, Any] = {
    "symbol": {"type": "string", "maxLength": 16},
    "symbols": {"type": "array", "maxItems": 20, "items": {"type": "string", "maxLength": 16}},
    "keyword": {"type": "string", "maxLength": 80},
    "date_from": {"type": "string", "maxLength": 10},
    "date_to": {"type": "string", "maxLength": 10},
    "since": {"type": "string", "maxLength": 10},
    "side": {"type": "string", "enum": ["buy", "sell", "both"]},
    "strategy_id": {"type": "string", "maxLength": 120},
    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
    "question": {"type": "string", "maxLength": 500},
}

# Only keywords the OpenClaw adapter validator enforces (pinned by its contract test).
SERVANT_TURN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "tool_calls", "answer_text", "cited_fact_ids", "missing_data"],
    "properties": {
        "action": {"type": "string", "enum": ["call_tools", "answer"]},
        "tool_calls": {
            "type": "array",
            "maxItems": MAX_CALLS_PER_ROUND,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool", "arguments"],
                "properties": {
                    "tool": {"type": "string", "minLength": 1, "maxLength": 64},
                    "arguments": {"type": "object", "additionalProperties": False, "properties": _ARGUMENT_PROPERTIES},
                },
            },
        },
        "answer_text": {"type": "string", "maxLength": ANSWER_MAX_CHARS},
        "cited_fact_ids": {
            "type": "array", "maxItems": MAX_FACTS_PER_ANSWER, "items": {"type": "string", "maxLength": 16},
        },
        "missing_data": {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 200}},
    },
}

INSTRUCTIONS = (
    "你是交易員在 Agora 的交易僕人。你只能用下列唯讀工具查資料，工具結果就是 facts。\n"
    "規則：\n"
    "1. 你自己不得寫出任何數字。要顯示某個數值，請寫 {{fact_id}}（例如 {{f3}}），系統會換成該 fact 的值與單位。"
    "股票代號可以直接寫。\n"
    "2. 需要資料就回 action=call_tools，tool_calls 最多 4 個；資料足夠或 must_answer_now=true 時回 action=answer。\n"
    "3. 沒有工具能提供的資料，寫進 missing_data，並在回答中說明缺什麼；不得用你的記憶當作資料回答。\n"
    "4. cited_fact_ids 列出回答用到的 fact id。\n"
    "5. 不要諮詢任何 persona，也不要開會。用繁體中文回答。\n"
)

_FACT_REF = re.compile(r"\{\{\s*(f\d{1,3})\s*\}\}")
_NUMBER = re.compile(r"\d+(?:[.,:/\-]\d+)*%?")
_UNIT_LABELS = {"shares": "股", "TWD": "元", "symbols": "檔", "score": "分"}


@dataclass
class TurnOutcome:
    answer: str
    provider_status: str
    facts: List[Dict[str, Any]] = field(default_factory=list)
    tool_trace: List[Dict[str, Any]] = field(default_factory=list)
    missing_data: List[str] = field(default_factory=list)
    tool_rounds: int = 0
    provider_calls: int = 0
    number_guard: Dict[str, Any] = field(default_factory=dict)
    answer_mode: str = "model"
    error: Optional[Dict[str, Any]] = None

    def metadata(self) -> Dict[str, Any]:
        return {
            "schema_version": TURN_SCHEMA_VERSION,
            "tool_rounds": self.tool_rounds,
            "provider_calls": self.provider_calls,
            "tool_calls": self.tool_trace,
            "missing_data": self.missing_data,
            "number_guard": self.number_guard,
            "answer_mode": self.answer_mode,
            "persona_calls": 0,
        }


def format_fact_value(fact: Mapping[str, Any]) -> str:
    value = fact.get("value")
    if value is None:
        return "（無值）"
    if isinstance(value, bool):
        text = str(value)
    elif isinstance(value, int):
        text = f"{value:,}"
    elif isinstance(value, float):
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    unit = _UNIT_LABELS.get(str(fact.get("unit") or ""), "")
    return f"{text}{unit}" if unit and fact.get("unit") not in {"text", "decision", "draft"} else text


def _allowed_number_tokens(facts: Sequence[Mapping[str, Any]], trader_text: str, extra: Sequence[str]) -> set:
    allowed = set(_NUMBER.findall(trader_text or ""))
    for fact in facts:
        for key in ("symbol",):
            if fact.get(key):
                allowed.add(str(fact[key]))
        as_of = str(fact.get("as_of") or "")
        if as_of:
            allowed.add(as_of[:10])
    allowed.update(str(x) for x in extra if x)
    return allowed


def render_answer(
    text: str, facts_by_id: Mapping[str, Mapping[str, Any]], *, allowed_tokens: set
) -> tuple[str, List[str], Dict[str, Any]]:
    """Replace fact references with BFF-rendered values and strip numbers the model supplied."""
    used: List[str] = []
    unknown_refs: List[str] = []
    pieces: List[str] = []
    violations: List[str] = []
    cursor = 0

    def guard(segment: str) -> str:
        def repl(match: "re.Match[str]") -> str:
            token = match.group(0)
            if token in allowed_tokens or token.rstrip("%") in allowed_tokens:
                return token
            violations.append(token)
            return REDACTED_NUMBER

        return _NUMBER.sub(repl, segment)

    for match in _FACT_REF.finditer(text or ""):
        pieces.append(guard(text[cursor : match.start()]))
        fact_id = match.group(1)
        fact = facts_by_id.get(fact_id)
        if fact is None:
            unknown_refs.append(fact_id)
            pieces.append(REDACTED_NUMBER)
        else:
            used.append(fact_id)
            pieces.append(format_fact_value(fact))
        cursor = match.end()
    pieces.append(guard((text or "")[cursor:]))
    rendered = "".join(pieces).strip()
    report = {
        "model_numbers_removed": violations[:20],
        "removed_count": len(violations),
        "unknown_fact_refs": unknown_refs[:20],
        "passed": not violations and not unknown_refs,
    }
    return rendered, list(dict.fromkeys(used)), report


def facts_only_answer(facts: Sequence[Mapping[str, Any]], missing: Sequence[str]) -> str:
    """Deterministic answer from the facts alone (provider unavailable or silent)."""
    lines: List[str] = []
    if facts:
        lines.append("以下是工具查到的資料（數字均來自資料來源）：")
        for fact in facts[:20]:
            source = fact.get("connector_id") or fact.get("owner") or "?"
            lines.append(
                f"- {fact.get('label')}：{format_fact_value(fact)}"
                f"（來源 {source}，資料時間 {fact.get('as_of') or '未知'}，{fact.get('freshness')}）"
            )
        if len(facts) > 20:
            lines.append(f"- 其餘 {len(facts) - 20} 筆見引用清單。")
    if missing:
        lines.append(missing_data_sentence(missing))
    return "\n".join(lines)


def missing_data_sentence(missing: Sequence[str]) -> str:
    return "缺少的資料：" + "；".join(missing) + "。我不會用模型記憶當作資料回答。"


def _packet(
    *,
    trader_text: str,
    history: Sequence[Mapping[str, Any]],
    ctx: ToolContext,
    catalog: Sequence[Mapping[str, Any]],
    facts: Sequence[Mapping[str, Any]],
    trace: Sequence[Mapping[str, Any]],
    rounds_used: int,
    must_answer_now: bool,
) -> str:
    packet = {
        "trader_message": trader_text,
        "history": [
            {"role": m.get("role"), "content": str(m.get("content") or "")[:HISTORY_CONTENT_MAX_CHARS]}
            for m in history[:-1]
        ],
        "attachment_refs": list((history[-1].get("attachment_refs") if history else None) or []),
        "command_context": ctx.command_context,
        "tools": list(catalog),
        "facts": [
            {k: f.get(k) for k in ("id", "label", "value", "unit", "as_of", "freshness", "symbol")}
            for f in facts
        ],
        "tool_results": list(trace),
        "tool_rounds_used": rounds_used,
        "tool_rounds_left": max(0, MAX_TOOL_ROUNDS - rounds_used),
        "must_answer_now": must_answer_now,
    }
    return INSTRUCTIONS + "\n" + PACKET_MARKER + "\n" + json.dumps(packet, ensure_ascii=False, default=str)


def run_tool_turn(
    *,
    tools: ServantDataTools,
    ctx: ToolContext,
    history: Sequence[Mapping[str, Any]],
    invoke: Callable[[str], Mapping[str, Any]],
) -> TurnOutcome:
    """Run the tool rounds and the answer of one turn.

    ``invoke(prompt)`` performs one structured provider call and returns the validated JSON
    object; it raises on provider failure.
    """
    trader_text = next((str(m.get("content") or "") for m in reversed(history) if m.get("role") == "user"), "")
    facts: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []
    missing: List[str] = []
    rounds = 0
    provider_calls = 0

    def execute(calls: Sequence[Mapping[str, Any]]) -> None:
        for call in list(calls)[:MAX_CALLS_PER_ROUND]:
            result: ToolResult = tools.run(str(call.get("tool") or ""), call.get("arguments") or {}, ctx)
            for fact in result.facts:
                if len(facts) >= MAX_FACTS_PER_ANSWER:
                    break
                fact["id"] = f"f{len(facts) + 1}"
                facts.append(fact)
            trace.append(result.summary())
            if result.missing:
                missing.append(result.missing)

    seeds = seeded_tool_calls(ctx.command_context)
    if seeds:
        execute(seeds)
        rounds = 1

    plan: Optional[Mapping[str, Any]] = None
    error: Optional[Dict[str, Any]] = None
    while True:
        must_answer = rounds >= MAX_TOOL_ROUNDS
        prompt = _packet(
            trader_text=trader_text, history=history, ctx=ctx, catalog=tools.catalog(), facts=facts,
            trace=trace, rounds_used=rounds, must_answer_now=must_answer,
        )
        provider_calls += 1
        try:
            plan = invoke(prompt)
        except Exception as exc:  # reported; facts already read are still shown
            error = _provider_error(exc)
            plan = None
            break
        if plan.get("action") == "call_tools" and plan.get("tool_calls") and not must_answer:
            execute(plan.get("tool_calls") or [])
            rounds += 1
            continue
        break

    for item in (plan or {}).get("missing_data") or []:
        text = str(item).strip()
        if text:
            missing.append(text)
    missing = list(dict.fromkeys(missing))

    facts_by_id = {f["id"]: f for f in facts}
    raw_answer = str((plan or {}).get("answer_text") or "").strip()
    if plan is not None and raw_answer:
        allowed = _allowed_number_tokens(facts, trader_text, ctx.command_context.get("selected_symbols") or [])
        answer, used, guard = render_answer(raw_answer, facts_by_id, allowed_tokens=allowed)
        cited = set(used) | {str(i) for i in (plan.get("cited_fact_ids") or []) if str(i) in facts_by_id}
        if missing and "缺少的資料" not in answer:
            answer = f"{answer}\n\n{missing_data_sentence(missing)}"
        mode = "model"
        status = "completed"
    elif facts or missing:
        answer = facts_only_answer(facts, missing)
        cited = {f["id"] for f in facts}
        guard = {"model_numbers_removed": [], "removed_count": 0, "unknown_fact_refs": [], "passed": True}
        mode = "facts_only"
        status = "completed"
    else:
        answer, cited, guard, mode = "", set(), {}, "none"
        status = "degraded" if error else "completed"
    for fact in facts:
        fact["cited"] = fact["id"] in cited
    return TurnOutcome(
        answer=answer,
        provider_status=status,
        facts=facts,
        tool_trace=trace,
        missing_data=missing,
        tool_rounds=rounds,
        provider_calls=provider_calls,
        number_guard=guard,
        answer_mode=mode,
        error=error,
    )


def _provider_error(exc: Exception) -> Dict[str, Any]:
    code = getattr(exc, "error_code", None)
    if code is not None:
        return {
            "code": "OPENCLAW_UPSTREAM_DEGRADED",
            "source_error_code": code,
            "status_code": getattr(exc, "status_code", None),
            "message": getattr(exc, "message", str(exc)),
        }
    return {"code": "SERVANT_TURN_INTERNAL", "message": type(exc).__name__}


__all__ = [
    "PACKET_MARKER",
    "REDACTED_NUMBER",
    "SERVANT_TURN_SCHEMA",
    "TurnOutcome",
    "facts_only_answer",
    "format_fact_value",
    "render_answer",
    "run_tool_turn",
]
