"""Strategy completeness assessment and Next-Best-Question (NBQ) scoring.

Implements A1 NBQ scoring spec and strategy completeness assessment:
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/
    A1_next_best_question_scoring_spec.md
  services/control-plane/specs/agora/strategy_completeness.schema.json

Policy version: QuestionScoringPolicy.v1
Used by: integrations/openclaw/skills/agora/strategy_completeness/skill.py
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

QUESTION_SCORING_POLICY_VERSION = "QuestionScoringPolicy.v1"

# ---------------------------------------------------------------------------
# Field states (C1 skill SPEC Output.stateMap)
# ---------------------------------------------------------------------------

FieldState = str

OPEN_STATES: frozenset[str] = frozenset({
    "missing",
    "weak",
    "conflicting",
    "inferred_needs_confirmation",
})

# ---------------------------------------------------------------------------
# Field importance table — A1 §4.1
# ---------------------------------------------------------------------------

FIELD_IMPORTANCE: Dict[str, float] = {
    # exit / invalidation (1.00)
    "exit_invalidation": 1.00,
    "exit": 1.00,
    "invalidation": 1.00,
    # risk / leverage / portfolio constraints (0.95)
    "risk_constraints": 0.95,
    "risk_leverage": 0.95,
    "risk.max_loss": 0.95,
    "max_loss": 0.95,
    "leverage_limit": 0.95,
    "portfolio_constraints": 0.95,
    # entry / signal confirmation (0.90)
    "entry_signal": 0.90,
    "entry_signal_confirmation": 0.90,
    # data availability / PIT / identity mapping (0.90)
    "data_availability_cutoff": 0.90,
    "data_pit": 0.90,
    "pit": 0.90,
    "identity_mapping_role": 0.90,
    "data_identity_mapping_role": 0.90,
    # position sizing / add / reduce (0.85)
    "position_sizing": 0.85,
    "position_add_reduce": 0.85,
    "holding_period": 0.85,
    "hedge_ratio": 0.85,
    "hedge_definition": 0.85,
    # universe / candidate rule (0.80)
    "universe_rule": 0.80,
    "candidate_rule": 0.80,
    # feature / alpha definition (0.80)
    "feature_definition": 0.80,
    "alpha_definition": 0.80,
    # execution / cost / liquidity (0.80)
    "execution_cost": 0.80,
    "liquidity": 0.80,
    # validation / OOS (0.75)
    "validation_oos": 0.75,
    "oos_validation": 0.75,
    # regime (0.70)
    "regime": 0.70,
    # hypothesis wording (0.55)
    "hypothesis_wording": 0.55,
    "hypothesis": 0.55,
    # display / dashboard preference (0.30)
    "display_preference": 0.30,
    "dashboard_preference": 0.30,
}

DEFAULT_FIELD_IMPORTANCE: float = 0.60


def get_field_importance(field_name: str) -> float:
    return FIELD_IMPORTANCE.get(field_name, DEFAULT_FIELD_IMPORTANCE)


# ---------------------------------------------------------------------------
# Downstream blocking weights — A1 §4.2
# ---------------------------------------------------------------------------

FIELD_DOWNSTREAM_WEIGHT: Dict[str, float] = {
    # Blocks compliance / PIT / safety (1.00)
    "data_availability_cutoff": 1.00,
    "data_pit": 1.00,
    "pit": 1.00,
    "risk.max_loss": 1.00,
    "max_loss": 1.00,
    "leverage_limit": 1.00,
    # Blocks ResearchPlan / backtest (0.90)
    "exit_invalidation": 0.90,
    "exit": 0.90,
    "invalidation": 0.90,
    "entry_signal": 0.90,
    "entry_signal_confirmation": 0.90,
    "identity_mapping_role": 0.90,
    "data_identity_mapping_role": 0.90,
    "holding_period": 0.90,
    # Blocks strategy version (0.80)
    "position_sizing": 0.80,
    "position_add_reduce": 0.80,
    "hedge_ratio": 0.80,
    "hedge_definition": 0.80,
    "risk_constraints": 0.80,
    "risk_leverage": 0.80,
    # Blocks trading room (0.75)
    "universe_rule": 0.75,
    "candidate_rule": 0.75,
    "execution_cost": 0.75,
    "liquidity": 0.75,
    # Only affects result interpretation (0.45)
    "validation_oos": 0.45,
    "oos_validation": 0.45,
    "regime": 0.45,
    # Only affects UI display (0.20)
    "display_preference": 0.20,
    "dashboard_preference": 0.20,
}

DEFAULT_DOWNSTREAM_WEIGHT: float = 0.60


def get_downstream_weight(field_name: str) -> float:
    return FIELD_DOWNSTREAM_WEIGHT.get(field_name, DEFAULT_DOWNSTREAM_WEIGHT)


# ---------------------------------------------------------------------------
# Risk impact — A1 §4.3
# ---------------------------------------------------------------------------

_HIGH_RISK_FIELDS: frozenset[str] = frozenset({
    "data_availability_cutoff", "data_pit", "pit",
    "exit_invalidation", "exit", "invalidation",
    "risk.max_loss", "max_loss", "leverage_limit",
    "identity_mapping_role", "data_identity_mapping_role",
    "holding_period",
})

_MEDIUM_RISK_FIELDS: frozenset[str] = frozenset({
    "position_sizing", "position_add_reduce",
    "risk_constraints", "risk_leverage",
    "hedge_ratio", "hedge_definition",
    "entry_signal", "entry_signal_confirmation",
})

_LOW_RISK_FIELDS: frozenset[str] = frozenset({
    "universe_rule", "candidate_rule",
    "feature_definition", "alpha_definition",
    "validation_oos", "oos_validation",
    "regime",
})


def get_risk_impact(field_name: str) -> float:
    if field_name in _HIGH_RISK_FIELDS:
        return 0.90
    if field_name in _MEDIUM_RISK_FIELDS:
        return 0.70
    if field_name in _LOW_RISK_FIELDS:
        return 0.45
    return 0.30


# ---------------------------------------------------------------------------
# Mandatory override fields — A1 §6
# ---------------------------------------------------------------------------

# Missing or conflicting triggers mandatory queue immediately
_MANDATORY_FIELDS: frozenset[str] = frozenset({
    "data_availability_cutoff", "data_pit", "pit",
    "exit_invalidation", "exit", "invalidation",
    "risk.max_loss", "max_loss", "leverage_limit",
    "risk_constraints",
})

# Only conflicting state triggers mandatory
_MANDATORY_IF_CONFLICTING: frozenset[str] = frozenset({
    "holding_period",
    "entry_signal",
    "entry_signal_confirmation",
    "position_sizing",
})

# A1 §6: mandatory priority order (lower number = higher priority)
_MANDATORY_PRIORITY: Dict[str, int] = {
    # compliance / privacy > PIT / look-ahead > risk / leverage > exit / invalidation
    "data_availability_cutoff": 1, "data_pit": 1, "pit": 1,
    "risk.max_loss": 2, "max_loss": 2, "leverage_limit": 2,
    "exit_invalidation": 3, "exit": 3, "invalidation": 3,
    "risk_constraints": 4,
}


def is_mandatory(field_name: str, state: FieldState) -> bool:
    if field_name in _MANDATORY_FIELDS and state in ("missing", "conflicting"):
        return True
    if field_name in _MANDATORY_IF_CONFLICTING and state == "conflicting":
        return True
    return False


# ---------------------------------------------------------------------------
# Tool-derivable and provisional fields — A1 §3 eligibility + §5 penalties
# ---------------------------------------------------------------------------

# These fields can be answered by tools; never presented to trader as questions
_TOOL_DERIVABLE_FIELDS: frozenset[str] = frozenset({
    "liquidity",
    "execution_cost",
    "display_preference",
    "dashboard_preference",
})

# These fields can safely use provisional tool estimates instead of asking
_PROVISIONAL_DEFAULT_FIELDS: frozenset[str] = frozenset({
    "hedge_ratio",
    "hedge_definition",
})

# Low-level engineering/tool details — not for trader decisions (A1 §3 User-level gate)
_LOW_LEVEL_FIELDS: frozenset[str] = frozenset({
    "model_choice",
    "tool_selection",
    "framework_choice",
    "data_format",
    "api_name",
    "signal_schema_version",
})


def is_tool_derivable(field_name: str) -> bool:
    return field_name in _TOOL_DERIVABLE_FIELDS or field_name in _LOW_LEVEL_FIELDS


def can_use_provisional(field_name: str) -> bool:
    return field_name in _PROVISIONAL_DEFAULT_FIELDS


# ---------------------------------------------------------------------------
# Eligibility gate — A1 §3
# ---------------------------------------------------------------------------

def _check_eligibility(
    field_name: str,
    state: FieldState,
    recent_questions: Sequence[str],
) -> Optional[str]:
    """Return a suppression reason if the candidate fails any eligibility gate, else None."""
    if state not in OPEN_STATES:
        return None  # not an open field; caller already filtered
    if is_tool_derivable(field_name):
        return "derivable_by_tool"
    if field_name in _LOW_LEVEL_FIELDS:
        return "low_level_not_for_trader"
    if field_name in recent_questions:
        return "recently_asked"
    return None


# ---------------------------------------------------------------------------
# Question candidate dataclass
# ---------------------------------------------------------------------------

@dataclass
class QuestionCandidate:
    question_id: str
    field: str
    text: str
    state: FieldState
    target_fields: List[str]
    answer_mode: str = "free_text"
    options: List[str] = field(default_factory=list)
    why_now: str = ""
    mandatory: bool = False

    # A1 §4 base-score factors [0, 1]
    information_gain: float = 0.0
    downstream_blocking_weight: float = 0.0
    risk_impact: float = 0.0
    research_cost_reduction: float = 0.10
    user_relevance: float = 0.50

    # A1 §5 penalties [0, 1]
    already_answered_penalty: float = 0.0
    low_level_question_penalty: float = 0.0
    cognitive_burden_penalty: float = 0.0
    premature_optimization_penalty: float = 0.0

    def final_score(self) -> float:
        """A1 §4 formula: final_score = clamp(100 × (base - penalty), 0, 100)."""
        base = (
            0.30 * self.information_gain
            + 0.25 * self.downstream_blocking_weight
            + 0.20 * self.risk_impact
            + 0.10 * self.research_cost_reduction
            + 0.15 * self.user_relevance
        )
        penalty = (
            0.35 * self.already_answered_penalty
            + 0.25 * self.low_level_question_penalty
            + 0.20 * self.cognitive_burden_penalty
            + 0.20 * self.premature_optimization_penalty
        )
        return max(0.0, min(100.0, 100.0 * (base - penalty)))


# ---------------------------------------------------------------------------
# Default question text templates (servant generates richer text in production)
# ---------------------------------------------------------------------------

_QUESTION_TEMPLATES: Dict[str, str] = {
    "exit_invalidation": (
        "請裁示此策略的失效條件與出場規則："
        "何時認定策略信號失效，應在什麼條件下平倉？"
    ),
    "exit": (
        "請裁示此策略的出場規則與失效條件。"
    ),
    "invalidation": (
        "請裁示此策略的信號失效條件：何種市場狀況代表信號不再有效？"
    ),
    "data_availability_cutoff": (
        "請確認資料可得時間（PIT）：此策略使用的資料何時能被確認為已公開，"
        "以避免前視偏誤（look-ahead bias）？"
    ),
    "data_pit": (
        "請確認資料可得時間（PIT）定義，以確保回測不含前視偏誤。"
    ),
    "pit": (
        "請確認 PIT（Point-in-Time）資料截止定義。"
    ),
    "identity_mapping_role": (
        "請裁示身份映射的必要條件與加權證據："
        "關係人持股分點資料在此策略中是必要條件，還是加權支持證據？"
    ),
    "data_identity_mapping_role": (
        "請裁示身份映射資料的角色："
        "這是策略結論的必要條件，還是加權支持證據？"
    ),
    "position_sizing": (
        "請裁示部位規模規則："
        "單檔最大部位上限與整體策略總曝險上限為何？"
    ),
    "risk.max_loss": (
        "請裁示最大損失上限（Max Loss）："
        "此策略的風險預算與可接受最大損失為何？"
    ),
    "max_loss": (
        "請裁示最大損失上限：此策略能承受的最大損失是多少？"
    ),
    "risk_constraints": (
        "請裁示風險約束：槓桿上限、集中度限制與最大回撤容忍度為何？"
    ),
    "risk_leverage": (
        "請裁示槓桿限制與風險上限。"
    ),
    "holding_period": (
        "請確認持倉期間：策略目標持倉期為何？"
        "（前文陳述存在衝突，需要裁示。）"
    ),
    "hedge_ratio": (
        "請裁示 hedge ratio 定義方法：是否允許由統計工具（如 OLS 或 Kalman filter）"
        "估算 hedge ratio？若允許，系統將採暫定值並標示為 provisional。"
    ),
    "entry_signal": (
        "請裁示入場信號確認規則：哪些條件組合構成有效入場信號？"
    ),
    "entry_signal_confirmation": (
        "請裁示入場信號確認規則。"
    ),
    "universe_rule": (
        "請裁示候選股票池過濾規則：哪些標準決定標的是否進入候選池？"
    ),
    "candidate_rule": (
        "請裁示候選標的篩選規則。"
    ),
}


def _build_question_text(field_name: str, state: FieldState) -> str:
    return _QUESTION_TEMPLATES.get(
        field_name,
        f"請裁示關於 {field_name} 的設計決策（目前狀態：{state}）。",
    )


def _build_why_now(field_name: str, state: FieldState) -> str:
    if is_mandatory(field_name, state):
        return f"此欄位（{field_name}）缺失或衝突將阻擋後續步驟，為強制必問項。"
    return f"此欄位（{field_name}）目前狀態為 {state}，確認後可解除後續研究阻擋。"


def _get_answer_mode(field_name: str) -> str:
    _choice_fields = frozenset({
        "identity_mapping_role", "data_identity_mapping_role",
        "holding_period", "hedge_ratio",
    })
    return "single_choice" if field_name in _choice_fields else "free_text"


# ---------------------------------------------------------------------------
# Candidate builder
# ---------------------------------------------------------------------------

_STATE_UNCERTAINTY_REDUCTION: Dict[str, float] = {
    "missing": 1.0,
    "conflicting": 0.8,
    "weak": 0.7,
    "inferred_needs_confirmation": 0.5,
}


def _build_candidate(field_name: str, state: FieldState) -> QuestionCandidate:
    importance = get_field_importance(field_name)
    uncertainty = _STATE_UNCERTAINTY_REDUCTION.get(state, 0.5)
    research_cost = 0.30 if is_mandatory(field_name, state) else 0.10

    return QuestionCandidate(
        question_id=f"q_{field_name.replace('.', '_')}",
        field=field_name,
        text=_build_question_text(field_name, state),
        state=state,
        target_fields=[field_name],
        answer_mode=_get_answer_mode(field_name),
        why_now=_build_why_now(field_name, state),
        mandatory=is_mandatory(field_name, state),
        information_gain=importance * uncertainty,
        downstream_blocking_weight=get_downstream_weight(field_name),
        risk_impact=get_risk_impact(field_name),
        research_cost_reduction=research_cost,
        user_relevance=0.50,
    )


# ---------------------------------------------------------------------------
# Blocking items
# ---------------------------------------------------------------------------

_RESEARCH_BLOCKERS: frozenset[str] = frozenset({
    "exit_invalidation", "exit", "invalidation",
    "data_availability_cutoff", "data_pit", "pit",
    "entry_signal", "entry_signal_confirmation",
    "identity_mapping_role", "data_identity_mapping_role",
})

_VALIDATION_BLOCKERS: frozenset[str] = frozenset({
    "risk.max_loss", "max_loss", "risk_constraints",
    "risk_leverage", "leverage_limit", "position_sizing",
})

_TRADING_ROOM_BLOCKERS: frozenset[str] = frozenset({
    "universe_rule", "candidate_rule",
    "execution_cost", "liquidity",
})


@dataclass
class BlockingItem:
    field: str
    reason: str
    gate: str  # "research" | "validation" | "trading_room"


def _blocking_gate(field_name: str, state: FieldState) -> Optional[str]:
    if state not in ("missing", "conflicting"):
        return None
    if field_name in _RESEARCH_BLOCKERS:
        return "research"
    if field_name in _VALIDATION_BLOCKERS:
        return "validation"
    if field_name in _TRADING_ROOM_BLOCKERS:
        return "trading_room"
    return None


def assess_blocking_items(state_map: Mapping[str, FieldState]) -> List[BlockingItem]:
    """Return all hard-blocking items from the state map."""
    result: List[BlockingItem] = []
    for fname, state in state_map.items():
        gate = _blocking_gate(fname, state)
        if gate:
            result.append(BlockingItem(
                field=fname,
                reason=f"Field '{fname}' is {state}; blocks {gate} gate.",
                gate=gate,
            ))
    return result


def assess_readiness(
    blocking_items: List[BlockingItem],
) -> tuple[bool, bool, bool]:
    """Return (research_ready, validation_ready, trading_room_ready)."""
    research_ok = not any(b.gate == "research" for b in blocking_items)
    validation_ok = research_ok and not any(b.gate == "validation" for b in blocking_items)
    trading_ok = validation_ok and not any(b.gate == "trading_room" for b in blocking_items)
    return research_ok, validation_ok, trading_ok


def compute_overall_grade(state_map: Mapping[str, FieldState]) -> str:
    """Derive overall_grade from the state map."""
    open_fields = [f for f, s in state_map.items() if s in OPEN_STATES]
    mandatory_open = [
        f for f in open_fields
        if is_mandatory(f, state_map[f])
    ]
    if not open_fields:
        return "complete"
    if mandatory_open:
        return "incomplete"
    if len(open_fields) <= 2:
        return "mostly_complete"
    return "partial"


# ---------------------------------------------------------------------------
# NBQ result types
# ---------------------------------------------------------------------------

@dataclass
class NextBestQuestion:
    question_id: str
    text: str
    target_fields: List[str]
    score: float
    mandatory: bool
    why_now: str
    answer_mode: str = "free_text"
    options: List[str] = field(default_factory=list)
    policy_version: str = QUESTION_SCORING_POLICY_VERSION


@dataclass
class SuppressedQuestion:
    question_id: str
    field: str
    reason: str


@dataclass
class ProvisionalAssumption:
    field: str
    assumed_value: str
    why: str


# ---------------------------------------------------------------------------
# NBQ selection — A1 §4–7
# ---------------------------------------------------------------------------

NBQ_SCORE_THRESHOLD: float = 55.0


def select_next_best_question(
    state_map: Mapping[str, FieldState],
    *,
    recent_questions: Sequence[str] = (),
    user_relevance_overrides: Optional[Mapping[str, float]] = None,
) -> tuple[Optional[NextBestQuestion], List[SuppressedQuestion], List[ProvisionalAssumption]]:
    """Apply A1 eligibility, score open fields, and select the Next-Best-Question.

    Returns (nbq, suppressed, provisional).
    nbq is None when:
      - No eligible open fields remain → caller should build research plan.
      - Best regular-queue score < 55 → caller should proceed with provisional values.
    """
    suppressed: List[SuppressedQuestion] = []
    provisional: List[ProvisionalAssumption] = []
    candidates: List[QuestionCandidate] = []

    for fname, state in state_map.items():
        if state not in OPEN_STATES:
            continue

        # Eligibility gate
        suppression_reason = _check_eligibility(fname, state, recent_questions)
        if suppression_reason:
            suppressed.append(SuppressedQuestion(
                question_id=f"q_{fname.replace('.', '_')}",
                field=fname,
                reason=suppression_reason,
            ))
            continue

        # Provisional-default fields: use estimate instead of asking trader
        if can_use_provisional(fname) and state in ("missing", "weak"):
            provisional.append(ProvisionalAssumption(
                field=fname,
                assumed_value="tool_estimate_pending",
                why=f"{fname} can be estimated by a tool; using provisional default.",
            ))
            continue

        c = _build_candidate(fname, state)
        if user_relevance_overrides and fname in user_relevance_overrides:
            c.user_relevance = user_relevance_overrides[fname]
        candidates.append(c)

    if not candidates:
        return None, suppressed, provisional

    # Mandatory queue (A1 §6): bypass normal scoring, sorted by priority
    mandatory_queue = [c for c in candidates if c.mandatory]
    regular_queue = [c for c in candidates if not c.mandatory]

    if mandatory_queue:
        mandatory_queue.sort(key=lambda c: _MANDATORY_PRIORITY.get(c.field, 5))
        best = mandatory_queue[0]
    else:
        scored = sorted(regular_queue, key=lambda c: c.final_score(), reverse=True)
        best = scored[0]
        if best.final_score() < NBQ_SCORE_THRESHOLD:
            return None, suppressed, provisional

    return (
        NextBestQuestion(
            question_id=best.question_id,
            text=best.text,
            target_fields=best.target_fields,
            score=round(best.final_score(), 2),
            mandatory=best.mandatory,
            why_now=best.why_now,
            answer_mode=best.answer_mode,
            options=list(best.options),
        ),
        suppressed,
        provisional,
    )
