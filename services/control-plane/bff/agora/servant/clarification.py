"""Typed clarification cards and style habit facts outside the workshop.

AGORA-SERVANT-CLARIFY-20261127 (SD_AGORA_SERVANT_CORE_2026-11-27 §5, T3 in §12):

1. A servant turn may return a typed ``clarification`` card with a question, 2 to 4
   selectable options, a provisional default (``servant_default``), and why it matters
   (``why_it_matters`` / ``why_now``), matching the workshop ``next_decision`` shape of
   AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127 (``WorkshopNextDecisionV114``).
2. The servant asks only when the trader's choice changes the data selected or the result
   (``changes_data_or_result=True``); otherwise it states its provisional assumption and
   answers directly.
3. At most one clarification per turn, and at most two consecutive clarifications in a row
   (``MAX_CONSECUTIVE_CLARIFICATIONS = 2``) before the servant must answer with stated
   assumptions.
4. The trader's choice is recorded as a durable session event
   (``servant.clarification.answered``); choices on style dimensions (``horizon``,
   ``universe``, ``risk_tolerance``) are emitted as deterministic habit facts for
   AGORA-TRADER-HABIT-PROFILE-20261127.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..strategy_workshop.readiness import resolve_decision_choice

MAX_CLARIFICATIONS_PER_TURN = 1
MAX_CONSECUTIVE_CLARIFICATIONS = 2

STYLE_DIMENSIONS: Tuple[str, ...] = ("horizon", "universe", "risk_tolerance")
CLARIFICATION_DIMENSIONS: Tuple[str, ...] = (
    "horizon",
    "universe",
    "risk_tolerance",
    "data_scope",
    "other",
)

STYLE_TO_HABIT_FEATURE: Dict[str, str] = {
    "horizon": "holding_period",
    "universe": "rejected_candidates",
    "risk_tolerance": "position_size",
}

STYLE_TO_V10_BLOCK: Dict[str, str] = {
    "horizon": "entry_holding",
    "universe": "market_scope",
    "risk_tolerance": "sizing_leverage",
}

STYLE_TO_RESOLVES: Dict[str, Tuple[str, ...]] = {
    "horizon": ("entry_holding", "holding_period", "horizon"),
    "universe": ("market_scope", "universe"),
    "risk_tolerance": ("sizing_leverage", "risk_controls", "risk_tolerance"),
}

STYLE_LABELS_ZH: Dict[str, str] = {
    "horizon": "持有週期與進場時機",
    "universe": "研究對象與市場範圍",
    "risk_tolerance": "部位與風險承受度",
}

_HORIZON_TARGETS = frozenset({
    "horizon",
    "entry_holding",
    "holding_period",
    "entry_rules",
    "timeframe",
})
_UNIVERSE_TARGETS = frozenset({
    "universe",
    "market_scope",
    "universe_rule",
    "candidate_rule",
    "sector_scope",
})
_RISK_TARGETS = frozenset({
    "risk_tolerance",
    "sizing_leverage",
    "risk_controls",
    "risk_constraints",
    "position_size",
    "position_sizing",
    "stop_loss",
})

_HORIZON_KEYWORDS = (
    "持有週期",
    "交易週期",
    "持有期間",
    "短線",
    "波段",
    "長線",
    "當沖",
    "隔日沖",
    "horizon",
    "holding period",
)
_UNIVERSE_KEYWORDS = (
    "市場範圍",
    "研究對象",
    "選股池",
    "候選池範圍",
    "成分股",
    "權值股",
    "中小型股",
    "universe",
    "market scope",
)
_RISK_KEYWORDS = (
    "風險承受",
    "風險偏好",
    "風險容忍",
    "停損",
    "部位上限",
    "最大回撤",
    "槓桿",
    "risk tolerance",
    "drawdown",
)


def _one_line(value: Any, fallback: str = "") -> str:
    if value is None:
        return fallback
    text = " ".join(str(value).strip().split())
    return text if text else fallback


def _digest(*parts: str, size: int = 16) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:size]


def infer_style_dimension(
    *,
    explicit: Any = None,
    resolves: Optional[Sequence[Any]] = None,
    target_blocks: Optional[Sequence[Any]] = None,
    question: Any = None,
    options: Optional[Sequence[Any]] = None,
) -> Optional[str]:
    """Return ``horizon``, ``universe``, or ``risk_tolerance`` when the clarification
    is a trader style choice; return ``None`` for non-style data/query clarifications."""
    exp = _one_line(explicit).lower()
    if exp in STYLE_DIMENSIONS:
        return exp
    if exp == "data_scope":
        return None

    targets = {
        _one_line(item).lower()
        for seq in (resolves or (), target_blocks or ())
        for item in seq
        if _one_line(item)
    }
    if targets & _HORIZON_TARGETS:
        return "horizon"
    if targets & _UNIVERSE_TARGETS:
        return "universe"
    if targets & _RISK_TARGETS:
        return "risk_tolerance"

    text_parts = [_one_line(question).lower()]
    for opt in options or ():
        if isinstance(opt, Mapping):
            text_parts.append(_one_line(opt.get("label") or opt.get("text")).lower())
        elif isinstance(opt, str):
            text_parts.append(_one_line(opt).lower())
    haystack = " ".join(text_parts)
    if any(k in haystack for k in _HORIZON_KEYWORDS):
        return "horizon"
    if any(k in haystack for k in _UNIVERSE_KEYWORDS):
        return "universe"
    if any(k in haystack for k in _RISK_KEYWORDS):
        return "risk_tolerance"
    return None


def extract_provisional_assumption(raw: Any) -> Optional[str]:
    """Extract the servant's provisional assumption from a clarification payload."""
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray, Mapping)):
        raw = raw[0] if raw else None
    if not isinstance(raw, Mapping):
        return None
    for key in ("provisional_default", "provisional_assumption", "stated_assumption"):
        val = _one_line(raw.get(key))
        if val:
            return val
    sd = raw.get("servant_default")
    if isinstance(sd, Mapping):
        for key in ("provisional_assumption", "summary", "label"):
            val = _one_line(sd.get(key))
            if val:
                return val
    elif isinstance(sd, str) and _one_line(sd):
        return _one_line(sd)
    def_id = _one_line(raw.get("default_option_id")).lower()
    raw_opts = raw.get("options") if isinstance(raw.get("options"), Sequence) else []
    for item in raw_opts:
        if isinstance(item, Mapping):
            oid = _one_line(item.get("option_id") or item.get("id")).lower()
            if item.get("is_default") or (def_id and oid == def_id):
                label = _one_line(item.get("label") or item.get("text"))
                if label:
                    return f"預設採用：{label}"
    return None


def normalize_clarification_card(
    raw: Any,
    *,
    seed_id: str = "",
) -> Optional[Dict[str, Any]]:
    """Normalize a model clarification payload into the exact ``WorkshopNextDecisionV114``
    shape (AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127).

    Returns ``None`` if the payload does not change data/result
    (``changes_data_or_result is False``) or lacks a question, ``why_it_matters``,
    or 2 to 4 valid options.
    """
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray, Mapping)):
        raw = raw[0] if raw else None
    if not isinstance(raw, Mapping):
        return None
    if raw.get("changes_data_or_result") is False:
        return None

    question = _one_line(raw.get("question") or raw.get("text"))
    why_now = _one_line(raw.get("why_it_matters") or raw.get("why_now"))
    if not question or not why_now:
        return None

    raw_options = raw.get("options")
    if not isinstance(raw_options, Sequence) or isinstance(raw_options, (str, bytes, bytearray)):
        return None

    requested_default_id = _one_line(raw.get("default_option_id"))
    raw_sd = raw.get("servant_default")
    if not requested_default_id and isinstance(raw_sd, Mapping):
        requested_default_id = _one_line(raw_sd.get("option_id"))

    options: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(list(raw_options)[:4]):
        letter = chr(ord("a") + idx)
        fallback_id = f"opt_{letter}"
        if isinstance(item, Mapping):
            opt_id = _one_line(item.get("option_id") or item.get("id"), fallback_id)
            label = _one_line(item.get("label") or item.get("text"))
            desc = _one_line(item.get("description")) or None
            is_def = bool(item.get("is_default", False))
        elif isinstance(item, str):
            opt_id = fallback_id
            label = _one_line(item)
            desc = None
            is_def = False
        else:
            continue
        if not label:
            continue
        if opt_id in seen_ids:
            opt_id = f"{opt_id}_{idx + 1}"
        seen_ids.add(opt_id)
        options.append({
            "option_id": opt_id,
            "id": opt_id,
            "label": label,
            "text": label,
            "description": desc,
            "is_default": is_def,
        })

    if len(options) < 2:
        return None

    chosen_default_idx: Optional[int] = None
    if requested_default_id:
        req_lower = requested_default_id.lower()
        for idx, opt in enumerate(options):
            if opt["option_id"].lower() == req_lower or f"opt_{req_lower}" == opt["option_id"].lower():
                chosen_default_idx = idx
                break
    if chosen_default_idx is None:
        for idx, opt in enumerate(options):
            if opt["is_default"]:
                chosen_default_idx = idx
                break
    if chosen_default_idx is None:
        chosen_default_idx = len(options) - 1

    for idx, opt in enumerate(options):
        opt["is_default"] = idx == chosen_default_idx

    default_opt = options[chosen_default_idx]
    default_option_id = str(default_opt["option_id"])

    summary_text = _one_line(raw.get("provisional_default") or raw.get("provisional_assumption"))
    if not summary_text and isinstance(raw_sd, Mapping):
        summary_text = _one_line(raw_sd.get("summary") or raw_sd.get("provisional_assumption"))
    elif not summary_text and isinstance(raw_sd, str):
        summary_text = _one_line(raw_sd)
    if not summary_text:
        summary_text = _one_line(default_opt.get("description")) or f"預設採用：{default_opt['label']}"

    servant_default = {
        "option_id": default_option_id,
        "label": str(default_opt["label"]),
        "summary": summary_text,
        "provisional_assumption": summary_text,
    }

    style_dim = infer_style_dimension(
        explicit=raw.get("style_dimension"),
        resolves=raw.get("resolves") if isinstance(raw.get("resolves"), Sequence) else None,
        target_blocks=raw.get("target_blocks") if isinstance(raw.get("target_blocks"), Sequence) else None,
        question=question,
        options=options,
    )

    resolves: List[str] = []
    for key in ("resolves", "target_blocks"):
        seq = raw.get(key)
        if isinstance(seq, Sequence) and not isinstance(seq, (str, bytes, bytearray)):
            for item in seq:
                clean = _one_line(item)
                if clean and clean not in resolves:
                    resolves.append(clean)
    if style_dim in STYLE_TO_RESOLVES:
        for extra in STYLE_TO_RESOLVES[style_dim]:
            if extra not in resolves:
                resolves.append(extra)
    if not resolves:
        resolves = ["data_requirements", "signal_formation"]

    target_blocks: List[str] = []
    raw_tb = raw.get("target_blocks")
    if isinstance(raw_tb, Sequence) and not isinstance(raw_tb, (str, bytes, bytearray)):
        for item in raw_tb:
            clean = _one_line(item)
            if clean and clean not in target_blocks:
                target_blocks.append(clean)
    if style_dim in STYLE_TO_V10_BLOCK and STYLE_TO_V10_BLOCK[style_dim] not in target_blocks:
        target_blocks.insert(0, STYLE_TO_V10_BLOCK[style_dim])
    if not target_blocks:
        target_blocks = [resolves[0]]

    decision_id = _one_line(
        raw.get("decision_id") or raw.get("question_id"),
        f"clarify-{_digest(seed_id, question, size=12)}",
    )

    return {
        "decision_id": decision_id,
        "question_id": decision_id,
        "question": question,
        "text": question,
        "why_now": why_now,
        "why_it_matters": why_now,
        "resolves": resolves,
        "target_blocks": target_blocks,
        "options": options,
        "answer_options": [str(opt["label"]) for opt in options],
        "default_option_id": default_option_id,
        "servant_default": servant_default,
        "score_total": float(raw.get("score_total") or 85.0),
        "freeform_allowed": bool(raw.get("freeform_allowed", True)),
        "defer_allowed": bool(raw.get("defer_allowed", True)),
        "style_dimension": style_dim,
        "changes_data_or_result": True,
    }


def clarification_from_message(message: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """Return the clarification card stored on an assistant message, if any."""
    if message.get("role") != "assistant":
        return None
    meta = message.get("metadata") if isinstance(message.get("metadata"), Mapping) else {}
    tool_turn = meta.get("tool_turn") if isinstance(meta.get("tool_turn"), Mapping) else {}
    card = meta.get("clarification") or tool_turn.get("clarification")
    return dict(card) if isinstance(card, Mapping) and card.get("question") else None


def count_consecutive_clarifications(
    messages: Sequence[Mapping[str, Any]],
    *,
    before_message_id: Optional[str] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Count trailing consecutive assistant turns that returned a clarification card.

    Returns ``(streak_count, cards_in_streak_chronological)``. A normal assistant answer
    resets the streak to 0.
    """
    upto = len(messages)
    if before_message_id:
        for idx, msg in enumerate(messages):
            if msg.get("id") == before_message_id:
                upto = idx
                break
    assistant_msgs = [
        m for m in messages[:upto]
        if isinstance(m, Mapping) and m.get("role") == "assistant"
    ]
    streak_cards: List[Dict[str, Any]] = []
    for msg in reversed(assistant_msgs):
        card = clarification_from_message(msg)
        if card is not None:
            streak_cards.append(card)
        else:
            break
    streak_cards.reverse()
    return len(streak_cards), streak_cards


def latest_pending_clarification(messages: Sequence[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the latest unanswered clarification card in the session, if any."""
    answered_ids: set[str] = set()
    for msg in messages:
        if not isinstance(msg, Mapping):
            continue
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), Mapping) else {}
        choice = meta.get("clarification_choice")
        if isinstance(choice, Mapping) and choice.get("decision_id"):
            answered_ids.add(str(choice["decision_id"]))
    for msg in reversed(messages):
        if not isinstance(msg, Mapping):
            continue
        card = clarification_from_message(msg)
        if card is not None:
            if str(card.get("decision_id") or "") not in answered_ids:
                return card
            return None
        if msg.get("role") == "assistant":
            return None
    return None


def build_style_habit_fact(
    *,
    choice: Mapping[str, Any],
    tenant_id: str,
    user_id: str,
    session_id: str,
    message_id: str,
    created_at: str,
    strategy_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Emit a deterministic habit fact when the trader's clarification choice is a style
    choice (``horizon``, ``universe``, or ``risk_tolerance``) for
    AGORA-TRADER-HABIT-PROFILE-20261127."""
    style_dim = infer_style_dimension(
        explicit=choice.get("style_dimension"),
        resolves=choice.get("resolves") if isinstance(choice.get("resolves"), Sequence) else None,
        target_blocks=choice.get("target_blocks") if isinstance(choice.get("target_blocks"), Sequence) else None,
        question=choice.get("question"),
        options=[{"label": choice.get("option_label")}],
    )
    if style_dim not in STYLE_DIMENSIONS:
        return None
    decision_id = str(choice.get("decision_id") or "")
    option_id = str(choice.get("option_id") or "")
    option_label = str(choice.get("option_label") or "")
    fact_id = f"hfact-{_digest(tenant_id, user_id, session_id, message_id, decision_id, option_id, size=16)}"
    source_id = f"servant-clarification:{session_id}:{message_id}:{decision_id}"
    query_hash = "sha256:" + _digest(tenant_id, user_id, style_dim, decision_id, option_id, size=32)
    return {
        "fact_id": fact_id,
        "id": fact_id,
        "kind": "trader_style_choice",
        "source": "servant_clarification",
        "tool": "servant_clarification",
        "style_dimension": style_dim,
        "feature_key": STYLE_TO_HABIT_FEATURE[style_dim],
        "target_block": STYLE_TO_V10_BLOCK[style_dim],
        "label": f"交易風格選擇（{STYLE_LABELS_ZH[style_dim]}）",
        "question": str(choice.get("question") or ""),
        "decision_id": decision_id,
        "option_id": option_id,
        "option_label": option_label,
        "value": option_label,
        "unit": "style_choice",
        "used_servant_default": bool(choice.get("used_servant_default", False)),
        "source_id": source_id,
        "owner": "agora",
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "session_id": str(session_id),
        "message_id": str(message_id),
        "strategy_id": strategy_id,
        "as_of": created_at,
        "freshness": "fresh",
        "query_hash": query_hash,
        "cited": False,
    }


def resolve_servant_clarification_choice(
    *,
    messages: Sequence[Mapping[str, Any]],
    explicit_choice: Optional[Mapping[str, Any]],
    content: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
    message_id: str,
    created_at: str,
    strategy_id: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]], str]:
    """Resolve an explicit ``clarification_choice`` or an implicit option selection against
    the session's pending clarification card.

    Raises ``ValueError`` if ``explicit_choice`` was supplied by the caller but no matching
    clarification card exists or the option is invalid (mapped to HTTP 422 by the route).
    Returns ``(clarification_choice_event, habit_fact_or_none, effective_content)``.
    """
    pending = latest_pending_clarification(messages)
    target_card: Optional[Dict[str, Any]] = pending

    if explicit_choice is not None:
        req_id = _one_line(explicit_choice.get("decision_id") or explicit_choice.get("question_id"))
        if req_id:
            # Find the matching clarification card in session history.
            matched: Optional[Dict[str, Any]] = None
            for msg in reversed(messages):
                if not isinstance(msg, Mapping):
                    continue
                card = clarification_from_message(msg)
                if card and str(card.get("decision_id") or "") == req_id:
                    matched = card
                    break
            if matched is None:
                raise ValueError(f"Unknown clarification decision_id {req_id!r} in session {session_id!r}")
            target_card = matched
        if target_card is None:
            raise ValueError(f"No pending clarification in session {session_id!r}")
        resolved = resolve_decision_choice(
            next_decision=target_card,
            decision_id=explicit_choice.get("decision_id"),
            question_id=explicit_choice.get("question_id"),
            option_id=explicit_choice.get("option_id"),
            selected_option_id=explicit_choice.get("selected_option_id"),
            option_label=explicit_choice.get("option_label"),
            custom_response=explicit_choice.get("custom_response"),
            use_servant_default=bool(explicit_choice.get("use_servant_default", False)),
            resolves=explicit_choice.get("resolves"),
        )
    else:
        if target_card is None:
            return None, None, content
        clean_content = _one_line(content)
        if not clean_content:
            return None, None, content
        # Implicit match: only resolve when content matches one of the pending options
        # by option_id, label, or single-letter option prefix ("A", "B", "opt_a", etc.).
        try:
            resolved = resolve_decision_choice(
                next_decision=target_card,
                option_id=clean_content,
            )
        except ValueError:
            try:
                resolved = resolve_decision_choice(
                    next_decision=target_card,
                    option_label=clean_content,
                )
            except ValueError:
                return None, None, content

    style_dim = infer_style_dimension(
        explicit=target_card.get("style_dimension"),
        resolves=resolved.get("resolves"),
        target_blocks=resolved.get("target_blocks"),
        question=resolved.get("question"),
        options=target_card.get("options"),
    )
    choice_event: Dict[str, Any] = {
        "event_type": "servant.clarification.answered",
        "decision_id": resolved["decision_id"],
        "question_id": resolved["question_id"],
        "question": resolved["question"],
        "response_kind": resolved["response_kind"],
        "option_id": resolved["option_id"],
        "option_label": resolved["option_label"],
        "used_servant_default": resolved["used_servant_default"],
        "resolves": resolved["resolves"],
        "target_blocks": resolved["target_blocks"],
        "style_dimension": style_dim,
    }
    habit_fact = build_style_habit_fact(
        choice=choice_event,
        tenant_id=tenant_id,
        user_id=user_id,
        session_id=session_id,
        message_id=message_id,
        created_at=created_at,
        strategy_id=strategy_id,
    )
    eff_content = _one_line(content) or str(resolved.get("private_response_text") or resolved["option_label"])
    return choice_event, habit_fact, eff_content


def extract_clarification_habit_facts_from_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    strategy_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Return all style habit facts emitted by clarification choices in ``messages``."""
    facts: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, Mapping) or msg.get("role") != "user":
            continue
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), Mapping) else {}
        fact = meta.get("habit_fact")
        if not isinstance(fact, Mapping) or fact.get("style_dimension") not in STYLE_DIMENSIONS:
            continue
        if strategy_id and fact.get("strategy_id") and str(fact.get("strategy_id")) != str(strategy_id):
            continue
        facts.append(dict(fact))
    return facts


def collect_clarification_habit_facts(
    owner: Any,
    *,
    tenant_id: str,
    user_id: str,
    strategy_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Collect style habit facts (horizon, universe, risk_tolerance) emitted across the
    trader's servant sessions in the Agora owner (for AGORA-TRADER-HABIT-PROFILE-20261127)."""
    if owner is None:
        return []
    if session_id:
        getter = getattr(owner, "get_servant_session", None)
        session = getter(session_id, tenant_id=tenant_id, user_id=user_id) if callable(getter) else None
        if not isinstance(session, Mapping):
            return []
        return extract_clarification_habit_facts_from_messages(
            session.get("messages") or [], strategy_id=strategy_id
        )

    store = getattr(owner, "store", owner)
    lister = getattr(store, "list_servant_user_messages", None)
    if callable(lister):
        user_msgs = lister(tenant_id, user_id)
        return extract_clarification_habit_facts_from_messages(user_msgs, strategy_id=strategy_id)

    list_sessions = getattr(store, "list_sessions", None)
    if not callable(list_sessions):
        return []
    facts: List[Dict[str, Any]] = []
    for session in list_sessions():
        if not isinstance(session, Mapping):
            continue
        if str(session.get("tenantId") or "") != str(tenant_id) or str(session.get("userId") or "") != str(user_id):
            continue
        facts.extend(
            extract_clarification_habit_facts_from_messages(
                session.get("messages") or [], strategy_id=strategy_id
            )
        )
    facts.sort(key=lambda f: (str(f.get("as_of") or ""), str(f.get("fact_id") or "")))
    return facts


def extract_clarification_choices_from_messages(
    messages: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Return all clarification choice events recorded in ``messages``."""
    choices: List[Dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, Mapping) or msg.get("role") != "user":
            continue
        meta = msg.get("metadata") if isinstance(msg.get("metadata"), Mapping) else {}
        choice = meta.get("clarification_choice")
        if isinstance(choice, Mapping) and choice.get("decision_id"):
            choices.append(dict(choice))
    return choices


def format_clarification_answer(card: Mapping[str, Any]) -> str:
    """Human-readable text stored on ``message.content`` when a turn returns a clarification card."""
    question = _one_line(card.get("question") or card.get("text"))
    why = _one_line(card.get("why_it_matters") or card.get("why_now"))
    sd = card.get("servant_default") if isinstance(card.get("servant_default"), Mapping) else {}
    default_summary = _one_line(sd.get("provisional_assumption") or sd.get("summary") or sd.get("label"))
    option_lines = [
        f"- {opt.get('label')}"
        for opt in (card.get("options") or [])
        if isinstance(opt, Mapping) and opt.get("label")
    ]
    parts = [question]
    if why:
        parts.append(f"為何重要：{why}")
    if option_lines:
        parts.append("\n".join(option_lines))
    if default_summary:
        parts.append(f"推定預設：{default_summary}")
    return "\n".join(parts)


def format_stated_assumptions_prefix(assumptions: Sequence[str]) -> str:
    clean = [_one_line(a) for a in assumptions if _one_line(a)]
    if not clean:
        return ""
    return "（推定假設：" + "；".join(dict.fromkeys(clean)) + "）"


__all__ = [
    "CLARIFICATION_DIMENSIONS",
    "MAX_CLARIFICATIONS_PER_TURN",
    "MAX_CONSECUTIVE_CLARIFICATIONS",
    "STYLE_DIMENSIONS",
    "STYLE_LABELS_ZH",
    "STYLE_TO_HABIT_FEATURE",
    "STYLE_TO_RESOLVES",
    "STYLE_TO_V10_BLOCK",
    "build_style_habit_fact",
    "clarification_from_message",
    "collect_clarification_habit_facts",
    "count_consecutive_clarifications",
    "extract_clarification_choices_from_messages",
    "extract_clarification_habit_facts_from_messages",
    "extract_provisional_assumption",
    "format_clarification_answer",
    "format_stated_assumptions_prefix",
    "infer_style_dimension",
    "latest_pending_clarification",
    "normalize_clarification_card",
    "resolve_servant_clarification_choice",
]
