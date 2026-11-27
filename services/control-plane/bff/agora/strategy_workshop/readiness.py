"""Public Workshop readiness and V10 twelve-block completeness projection module.

Computes:
1. The AG-BE-SW readiness assessment (preliminary_research / full_validation /
   trading_room gates) and 7 generic readiness dimensions from a workshop
   session, its events, and its latest StrategyCompleteness snapshot.
2. The additive V10 twelve-block strategy completeness projection (in V10 order,
   with the five V10 states confirmed / inferred / missing / weak / conflict,
   one-line note, source citations, branch vs non-branch naming) and the next
   high-value decision with 2 to 4 selectable options and optional servant
   default (V10 §4, §4.1; AGORA-WORKSHOP-TWELVE-BLOCK-COMPLETENESS-20261127).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ._common import _clean_optional, _safe_card_id

__all__ = [
    "V10_BLOCK_STATES",
    "V10_STATE_LABELS_ZH",
    "V10_TWELVE_BLOCKS_SPEC",
    "SEVEN_READINESS_DIMENSIONS",
    "build_readiness_assessment",
    "_build_readiness_assessment",
    "detect_branch_strategy",
    "extract_workshop_decisions",
    "normalize_v10_block_state",
    "build_twelve_strategy_blocks",
    "build_seven_readiness_dimensions",
    "build_next_high_value_decision",
    "project_completeness_response",
    "resolve_decision_choice",
]

V10_BLOCK_STATES: Tuple[str, ...] = (
    "confirmed",
    "inferred",
    "missing",
    "weak",
    "conflict",
)

V10_STATE_LABELS_ZH: Dict[str, str] = {
    "confirmed": "已確認",
    "inferred": "助理推定",
    "missing": "尚缺",
    "weak": "薄弱",
    "conflict": "衝突",
}

SEVEN_READINESS_DIMENSIONS: Tuple[str, ...] = (
    "market_scope",
    "data_dependencies",
    "hypothesis",
    "evaluation_plan",
    "risk_constraints",
    "execution_profile",
    "governance",
)


@dataclass(frozen=True)
class V10BlockSpec:
    order: int
    v10_key: str
    branch_key: str
    generic_key: str
    strategy_map_key: str
    mapped_dimension: str
    branch_title_zh: str
    branch_title_en: str
    generic_title_zh: str
    generic_title_en: str
    aliases: Tuple[str, ...]


# V10 §4 exact 12-block order:
# 1. market scope
# 2. related-party and branch mapping
# 3. winner-branch scoring
# 4. branch migration and reverse flow
# 5. event lead
# 6. signal formation
# 7. entry and holding
# 8. add reduce and exit
# 9. sizing and leverage
# 10. cost liquidity and capacity
# 11. validation backtest and refutation
# 12. monitoring and update
V10_TWELVE_BLOCKS_SPEC: Tuple[V10BlockSpec, ...] = (
    V10BlockSpec(
        order=1,
        v10_key="market_scope",
        branch_key="market_scope",
        generic_key="market_scope",
        strategy_map_key="universe",
        mapped_dimension="market_scope",
        branch_title_zh="研究對象與市場範圍",
        branch_title_en="Market scope",
        generic_title_zh="研究對象與市場範圍",
        generic_title_en="Market scope",
        aliases=("market_scope", "universe", "universe_rule", "candidate_rule"),
    ),
    V10BlockSpec(
        order=2,
        v10_key="insider_branch_mapping",
        branch_key="insider_branch_mapping",
        generic_key="data_requirements",
        strategy_map_key="data_requirements",
        mapped_dimension="data_dependencies",
        branch_title_zh="關係人與分點映射",
        branch_title_en="Related-party and branch mapping",
        generic_title_zh="資料需求與來源映射",
        generic_title_en="Data requirements and source mapping",
        aliases=(
            "insider_branch_mapping",
            "related_party_branch_mapping",
            "data_requirements",
            "identity_mapping_role",
            "data_identity_mapping_role",
        ),
    ),
    V10BlockSpec(
        order=3,
        v10_key="winner_branch_scoring",
        branch_key="winner_branch_scoring",
        generic_key="hypothesis_scoring",
        strategy_map_key="hypothesis",
        mapped_dimension="data_dependencies",
        branch_title_zh="贏家分點評分",
        branch_title_en="Winner-branch scoring",
        generic_title_zh="核心假說與因子評分",
        generic_title_en="Core hypothesis and factor scoring",
        aliases=(
            "winner_branch_scoring",
            "hypothesis_scoring",
            "hypothesis",
            "hypothesis.summary",
            "feature_definition",
            "alpha_definition",
        ),
    ),
    V10BlockSpec(
        order=4,
        v10_key="migration_reverse_flow",
        branch_key="migration_reverse_flow",
        generic_key="regime_invalidation",
        strategy_map_key="regime_invalidation",
        mapped_dimension="data_dependencies",
        branch_title_zh="分點遷移與反向流",
        branch_title_en="Branch migration and reverse flow",
        generic_title_zh="市場狀態遷移與失效條件",
        generic_title_en="Regime transition and invalidation",
        aliases=(
            "migration_reverse_flow",
            "branch_migration_reverse_flow",
            "regime_invalidation",
            "regime",
            "invalidation",
            "define_branch_round_trip",
        ),
    ),
    V10BlockSpec(
        order=5,
        v10_key="event_lead",
        branch_key="event_lead",
        generic_key="event_lead",
        strategy_map_key="risk_controls",
        mapped_dimension="hypothesis",
        branch_title_zh="事件領先關聯",
        branch_title_en="Event lead",
        generic_title_zh="事件領先與風險過濾",
        generic_title_en="Event lead and risk filters",
        aliases=(
            "event_lead",
            "risk_controls",
            "entry_signal",
            "data_availability_cutoff",
            "data_pit",
            "pit",
        ),
    ),
    V10BlockSpec(
        order=6,
        v10_key="signal_formation",
        branch_key="signal_formation",
        generic_key="signal_formation",
        strategy_map_key="signal_definition",
        mapped_dimension="hypothesis",
        branch_title_zh="信號形成",
        branch_title_en="Signal formation",
        generic_title_zh="信號形成",
        generic_title_en="Signal formation",
        aliases=("signal_formation", "signal_definition", "entry_signal_confirmation"),
    ),
    V10BlockSpec(
        order=7,
        v10_key="entry_holding",
        branch_key="entry_holding",
        generic_key="entry_holding",
        strategy_map_key="entry_rules",
        mapped_dimension="evaluation_plan",
        branch_title_zh="進場與持有週期",
        branch_title_en="Entry and holding",
        generic_title_zh="進場與持有週期",
        generic_title_en="Entry and holding",
        aliases=("entry_holding", "entry_rules", "holding_period"),
    ),
    V10BlockSpec(
        order=8,
        v10_key="add_reduce_exit",
        branch_key="add_reduce_exit",
        generic_key="add_reduce_exit",
        strategy_map_key="exit_rules",
        mapped_dimension="evaluation_plan",
        branch_title_zh="加碼、減碼與出場",
        branch_title_en="Add, reduce, and exit",
        generic_title_zh="加碼、減碼與出場",
        generic_title_en="Add, reduce, and exit",
        aliases=("add_reduce_exit", "exit_rules", "exit_invalidation", "exit", "position_add_reduce"),
    ),
    V10BlockSpec(
        order=9,
        v10_key="sizing_leverage",
        branch_key="sizing_leverage",
        generic_key="sizing_leverage",
        strategy_map_key="position_sizing",
        mapped_dimension="risk_constraints",
        branch_title_zh="部位與槓桿",
        branch_title_en="Sizing and leverage",
        generic_title_zh="部位與槓桿",
        generic_title_en="Sizing and leverage",
        aliases=(
            "sizing_leverage",
            "position_sizing",
            "risk_constraints",
            "risk_leverage",
            "leverage_limit",
            "risk.max_loss",
            "max_loss",
        ),
    ),
    V10BlockSpec(
        order=10,
        v10_key="cost_liquidity_capacity",
        branch_key="cost_liquidity_capacity",
        generic_key="cost_liquidity_capacity",
        strategy_map_key="cost_liquidity_capacity",
        mapped_dimension="execution_profile",
        branch_title_zh="成本、流動性與容量",
        branch_title_en="Cost, liquidity, and capacity",
        generic_title_zh="成本、流動性與容量",
        generic_title_en="Cost, liquidity, and capacity",
        aliases=("cost_liquidity_capacity", "execution_cost", "liquidity"),
    ),
    V10BlockSpec(
        order=11,
        v10_key="validation_backtest_refutation",
        branch_key="validation_backtest_refutation",
        generic_key="validation_backtest_refutation",
        strategy_map_key="validation_plan",
        mapped_dimension="execution_profile",
        branch_title_zh="驗證、回測與反證",
        branch_title_en="Validation, backtest, and refutation",
        generic_title_zh="驗證、回測與反證",
        generic_title_en="Validation, backtest, and refutation",
        aliases=("validation_backtest_refutation", "validation_plan", "validation_oos", "oos_validation"),
    ),
    V10BlockSpec(
        order=12,
        v10_key="monitoring_update",
        branch_key="monitoring_update",
        generic_key="monitoring_update",
        strategy_map_key="governance_constraints",
        mapped_dimension="governance",
        branch_title_zh="監控與策略更新",
        branch_title_en="Monitoring and update",
        generic_title_zh="監控與策略更新",
        generic_title_en="Monitoring and update",
        aliases=("monitoring_update", "governance_constraints", "governance"),
    ),
)

_BRANCH_ONLY_KEYS = frozenset({
    "insider_branch_mapping",
    "related_party_branch_mapping",
    "winner_branch_scoring",
    "migration_reverse_flow",
    "branch_migration_reverse_flow",
})

_EXPLICIT_V10_KEYS = frozenset({
    "market_scope",
    "insider_branch_mapping",
    "related_party_branch_mapping",
    "winner_branch_scoring",
    "migration_reverse_flow",
    "branch_migration_reverse_flow",
    "event_lead",
    "signal_formation",
    "entry_holding",
    "add_reduce_exit",
    "sizing_leverage",
    "validation_backtest_refutation",
    "monitoring_update",
})

_STRATEGY_MAP_ONLY_KEYS = frozenset({
    "universe",
    "data_requirements",
    "signal_definition",
    "entry_rules",
    "exit_rules",
    "position_sizing",
    "risk_controls",
    "validation_plan",
    "regime_invalidation",
    "governance_constraints",
})

_BRANCH_KEYWORDS: Tuple[str, ...] = (
    "分點",
    "券商分點",
    "贏家分點",
    "關係人持股",
    "主力分點",
    "winner branch",
    "winner-branch",
    "winner_branch",
    "broker branch",
    "broker-branch",
    "broker_branch",
    "insider_branch",
    "related-party and branch",
    "branch mapping",
    "branch migration",
    "branch scoring",
)


def _one_line(text: Any, fallback: str = "") -> str:
    cleaned = " ".join(str(text or "").split()).strip()
    return cleaned if cleaned else fallback


def _raw_state_dict(snapshot: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not snapshot:
        return {}
    raw = snapshot.get("state_map_json") or {}
    if isinstance(raw, Mapping) and isinstance(raw.get("state_map"), Mapping):
        raw = raw["state_map"]
    return dict(raw) if isinstance(raw, Mapping) else {}


def _lookup_template_option_label(
    resolves: Sequence[str],
    decision_id: str,
    option_id: str,
    *,
    is_branch_strategy: Optional[bool] = None,
) -> str:
    """Resolve the canonical static template label for a known option_id without
    storing option labels in event payload_refs_json.
    """
    oid = str(option_id or "").strip().lower()
    if not oid or oid in {"freeform", "opt_freeform"}:
        return "自訂裁示"
    spec: Optional[V10BlockSpec] = None
    for target in resolves or []:
        spec = _spec_for_target(target)
        if spec is not None:
            break
    if spec is None and decision_id:
        cleaned_id = decision_id.removeprefix("decision_")
        spec = _spec_for_target(cleaned_id)
    if spec is None:
        return ""
    template = _DECISION_TEMPLATES.get(spec.order)
    if not isinstance(template, Mapping):
        return ""
    if is_branch_strategy is not None:
        branch_modes = [bool(is_branch_strategy)]
    elif any(str(r) in _BRANCH_ONLY_KEYS for r in resolves or []) or any(
        k in str(decision_id) for k in _BRANCH_ONLY_KEYS
    ):
        branch_modes = [True, False]
    elif any(
        str(r) in {"data_requirements", "hypothesis_scoring", "regime_invalidation"}
        for r in resolves or []
    ):
        branch_modes = [False, True]
    else:
        branch_modes = [True, False]
    for bmode in branch_modes:
        tuples_key = "options_branch" if bmode else "options_generic"
        for opt_tuple_id, label, _desc in template.get(tuples_key) or []:
            if str(opt_tuple_id).strip().lower() == oid:
                return str(label)
    return ""


def extract_workshop_decisions(
    events: Optional[Sequence[Mapping[str, Any]]] = None,
    messages_content: Optional[Sequence[str]] = None,
    *,
    is_branch_strategy: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Extract answered trader decisions from workshop events and message texts."""
    decisions: List[Dict[str, Any]] = []
    by_msg_no: Dict[int, Dict[str, Any]] = {}
    by_id_pair: Dict[Tuple[str, str], Dict[str, Any]] = {}
    seen_keys: set[Tuple[str, str, int]] = set()
    msg_index = 0
    for event in events or []:
        if not isinstance(event, Mapping):
            continue
        ev_type = str(event.get("event_type") or "")
        if ev_type == "message":
            msg_index += 1
        refs = event.get("payload_refs_json")
        decision_id = ""
        option_id = ""
        option_label = ""
        question = ""
        response_kind = "option"
        resolves: List[str] = []
        is_decision = ev_type == "decision"
        if isinstance(refs, list):
            for item in refs:
                text = str(item or "").strip()
                if text == "kind:workshop_decision":
                    is_decision = True
                elif text == "kind:workshop_decision_freeform":
                    is_decision = True
                    response_kind = "freeform"
                elif text.startswith("decision_id:") or text.startswith("decision:"):
                    is_decision = True
                    decision_id = text.split(":", 1)[1].strip()
                elif text.startswith("option_id:") or text.startswith("option:"):
                    option_id = text.split(":", 1)[1].strip()
                    if option_id.lower() in {"freeform", "opt_freeform"}:
                        response_kind = "freeform"
                elif text.startswith("option_label:"):
                    option_label = text.split(":", 1)[1].strip()
                elif text.startswith("question:"):
                    question = text.split(":", 1)[1].strip()
                elif text.startswith("resolves:"):
                    target = text.split(":", 1)[1].strip()
                    if target:
                        resolves.extend(part.strip() for part in target.split(",") if part.strip())
        elif isinstance(refs, Mapping):
            if refs.get("decision_id") or refs.get("option_id"):
                is_decision = True
                decision_id = str(refs.get("decision_id") or "")
                option_id = str(refs.get("option_id") or "")
                if option_id.lower() in {"freeform", "opt_freeform"}:
                    response_kind = "freeform"
                option_label = str(refs.get("option_label") or "")
                question = str(refs.get("question") or "")
                raw_res = refs.get("resolves") or []
                if isinstance(raw_res, list):
                    resolves = [str(r).strip() for r in raw_res if str(r).strip()]
        summary_text = str(event.get("redacted_summary") or "")
        if not is_decision and summary_text.startswith("Workshop decision"):
            is_decision = True
        if is_decision and (decision_id or option_id or option_label):
            seq_no = int(event.get("sequence_no") or msg_index or (len(decisions) + 1))
            eff_msg_no = msg_index if msg_index > 0 else seq_no
            eff_decision_id = decision_id or f"decision-{seq_no}"
            eff_option_id = option_id or "selected"
            dedup = (eff_decision_id, eff_option_id, eff_msg_no)
            if dedup not in seen_keys:
                seen_keys.add(dedup)
                entry: Dict[str, Any] = {
                    "event_id": str(event.get("event_id") or f"seq-{seq_no}"),
                    "sequence_no": seq_no,
                    "message_number": eff_msg_no,
                    "decision_id": eff_decision_id,
                    "option_id": eff_option_id,
                    "option_label": option_label,
                    "response_kind": response_kind,
                    "question": question,
                    "resolves": resolves,
                    "created_at": event.get("created_at"),
                }
                decisions.append(entry)
                by_msg_no[eff_msg_no] = entry
                by_id_pair[(eff_decision_id, eff_option_id)] = entry

    for idx, raw_msg in enumerate(messages_content or [], start=1):
        text = str(raw_msg or "").strip()
        if not (text.startswith("[策略決策]") or text.startswith("[Workshop Decision]")):
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        header = lines[0]
        decision_id = ""
        option_id = ""
        response_kind = "option"
        resolves = []
        for token in header.split():
            if token.startswith("decision_id="):
                decision_id = token.split("=", 1)[1].strip()
            elif token.startswith("option_id="):
                option_id = token.split("=", 1)[1].strip()
                if option_id.lower() in {"freeform", "opt_freeform"}:
                    response_kind = "freeform"
            elif token.startswith("kind="):
                kval = token.split("=", 1)[1].strip().lower()
                if kval == "freeform":
                    response_kind = "freeform"
            elif token.startswith("resolves="):
                resolves = [p.strip() for p in token.split("=", 1)[1].split(",") if p.strip()]
        option_label = ""
        question = ""
        for line in lines[1:]:
            if line.startswith("問題：") or line.startswith("Question:"):
                question = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif ")：" in line:
                option_label = line.split(")：", 1)[1].strip()
            elif "）：" in line:
                option_label = line.split("）：", 1)[1].strip()
            elif "):" in line:
                option_label = line.split("):", 1)[1].strip()
        eff_decision_id = decision_id or f"decision-{idx}"
        eff_option_id = option_id or "selected"
        existing = by_msg_no.get(idx) or by_id_pair.get((eff_decision_id, eff_option_id))
        if existing is not None:
            if option_label:
                existing["option_label"] = option_label
            if question:
                existing["question"] = question
            if resolves and not existing.get("resolves"):
                existing["resolves"] = resolves
            if response_kind == "freeform":
                existing["response_kind"] = "freeform"
            continue
        dedup = (eff_decision_id, eff_option_id, idx)
        if dedup not in seen_keys and (decision_id or option_id):
            seen_keys.add(dedup)
            decisions.append({
                "event_id": f"seq-{idx}",
                "sequence_no": idx,
                "message_number": idx,
                "decision_id": eff_decision_id,
                "option_id": eff_option_id,
                "option_label": option_label,
                "response_kind": response_kind,
                "question": question,
                "resolves": resolves,
                "created_at": None,
            })

    for entry in decisions:
        if not entry.get("option_label"):
            resolved_lbl = _lookup_template_option_label(
                entry.get("resolves") or [],
                str(entry.get("decision_id") or ""),
                str(entry.get("option_id") or ""),
                is_branch_strategy=is_branch_strategy,
            )
            if resolved_lbl:
                entry["option_label"] = resolved_lbl
            elif entry.get("response_kind") == "freeform" or str(entry.get("option_id") or "").lower() in {"freeform", "opt_freeform"}:
                entry["option_label"] = "自訂裁示"
            else:
                entry["option_label"] = str(entry.get("option_id") or "已確認選項")
    return decisions


def _spec_for_target(target: str) -> Optional[V10BlockSpec]:
    cleaned = str(target or "").strip()
    if not cleaned:
        return None
    base = cleaned.split(".", 1)[0]
    for spec in V10_TWELVE_BLOCKS_SPEC:
        if cleaned in (spec.v10_key, spec.branch_key, spec.generic_key, spec.strategy_map_key) or cleaned in spec.aliases:
            return spec
        if base in (spec.v10_key, spec.branch_key, spec.generic_key, spec.strategy_map_key) or base in spec.aliases:
            return spec
    return None


def _project_winner_branch_state_map(state_map: Dict[str, str]) -> Dict[str, str]:
    has_explicit_v10 = any(k in state_map for k in _EXPLICIT_V10_KEYS)
    has_strategy_map = any(k in state_map for k in _STRATEGY_MAP_ONLY_KEYS)
    if not has_explicit_v10 and not has_strategy_map:
        return state_map

    projected = dict(state_map)

    def to_generic_grade(grade: str) -> str:
        if grade in {"confirmed", "complete", "satisfied", "not_applicable"}:
            return "complete"
        if grade in {
            "inferred_needs_confirmation",
            "inferred",
            "weak",
            "conflicting",
            "conflict",
            "partial",
        }:
            return "partial"
        return "missing"

    def to_specific_grade(grade: str) -> str:
        if grade in {"confirmed", "complete", "satisfied"}:
            return "confirmed"
        if grade in {"inferred_needs_confirmation", "inferred", "partial"}:
            return "inferred_needs_confirmation"
        if grade == "weak":
            return "weak"
        if grade in {"conflicting", "conflict"}:
            return "conflicting"
        return "missing"

    if has_explicit_v10:
        # Map Winner Branch / V10 blocks directly to specific StrategySpec fields
        insider_val = state_map.get("insider_branch_mapping") or state_map.get("related_party_branch_mapping")
        if insider_val is not None:
            projected["data_identity_mapping_role"] = to_specific_grade(insider_val)
            projected["identity_mapping_role"] = to_specific_grade(insider_val)
        migration_val = state_map.get("migration_reverse_flow") or state_map.get("branch_migration_reverse_flow")
        if migration_val is not None:
            projected["exit"] = to_specific_grade(migration_val)
            projected["exit_invalidation"] = to_specific_grade(migration_val)
        if "event_lead" in state_map:
            projected["entry_signal"] = to_specific_grade(state_map["event_lead"])
        if "signal_formation" in state_map:
            projected["entry_signal_confirmation"] = to_specific_grade(state_map["signal_formation"])
        if "entry_holding" in state_map:
            projected["holding_period"] = to_specific_grade(state_map["entry_holding"])
        if "add_reduce_exit" in state_map:
            projected["exit_invalidation"] = to_specific_grade(state_map["add_reduce_exit"])
        if "sizing_leverage" in state_map:
            projected["risk_constraints"] = to_specific_grade(state_map["sizing_leverage"])
            projected["position_sizing"] = to_specific_grade(state_map["sizing_leverage"])
        if "cost_liquidity_capacity" in state_map:
            projected["execution_cost"] = to_specific_grade(state_map["cost_liquidity_capacity"])
            projected["liquidity"] = to_specific_grade(state_map["cost_liquidity_capacity"])
        if "validation_backtest_refutation" in state_map:
            projected["validation_oos"] = to_specific_grade(state_map["validation_backtest_refutation"])

        mapping = {
            "market_scope": ["market_scope"],
            "data_dependencies": ["insider_branch_mapping", "winner_branch_scoring", "migration_reverse_flow"],
            "hypothesis": ["event_lead", "signal_formation"],
            "evaluation_plan": ["entry_holding", "add_reduce_exit"],
            "risk_constraints": ["sizing_leverage"],
            "execution_profile": ["cost_liquidity_capacity", "validation_backtest_refutation"],
            "governance": ["monitoring_update"],
        }
        for dim, blocks in mapping.items():
            grades = [to_generic_grade(state_map[b]) for b in blocks if b in state_map]
            if not grades:
                if dim not in state_map:
                    projected[dim] = "missing"
            elif all(g == "complete" for g in grades):
                projected[dim] = "complete"
            elif all(g == "missing" for g in grades):
                projected[dim] = "missing"
            else:
                projected[dim] = "partial"
        return projected

    # StrategyMap 12-field projection (from runner.completeness_snapshot_from_result)
    if "universe" in state_map:
        projected.setdefault("universe_rule", to_specific_grade(state_map["universe"]))
    if "data_requirements" in state_map:
        projected.setdefault("data_identity_mapping_role", to_specific_grade(state_map["data_requirements"]))
        projected.setdefault("identity_mapping_role", to_specific_grade(state_map["data_requirements"]))
    if "signal_definition" in state_map:
        projected.setdefault("entry_signal", to_specific_grade(state_map["signal_definition"]))
        projected.setdefault("entry_signal_confirmation", to_specific_grade(state_map["signal_definition"]))
    if "entry_rules" in state_map:
        projected.setdefault("holding_period", to_specific_grade(state_map["entry_rules"]))
    if "exit_rules" in state_map:
        projected.setdefault("exit", to_specific_grade(state_map["exit_rules"]))
        projected.setdefault("exit_invalidation", to_specific_grade(state_map["exit_rules"]))
    if "position_sizing" in state_map:
        projected.setdefault("position_sizing", to_specific_grade(state_map["position_sizing"]))
    if "risk_controls" in state_map:
        projected.setdefault("risk_constraints", to_specific_grade(state_map["risk_controls"]))
    elif "position_sizing" in state_map:
        projected.setdefault("risk_constraints", to_specific_grade(state_map["position_sizing"]))
    if "cost_liquidity_capacity" in state_map:
        projected.setdefault("execution_cost", to_specific_grade(state_map["cost_liquidity_capacity"]))
        projected.setdefault("liquidity", to_specific_grade(state_map["cost_liquidity_capacity"]))
    if "validation_plan" in state_map:
        projected.setdefault("validation_oos", to_specific_grade(state_map["validation_plan"]))

    smap_mapping = {
        "market_scope": ["universe"],
        "data_dependencies": ["data_requirements", "regime_invalidation"],
        "hypothesis": ["hypothesis", "signal_definition"],
        "evaluation_plan": ["entry_rules", "exit_rules"],
        "risk_constraints": ["position_sizing", "risk_controls"],
        "execution_profile": ["cost_liquidity_capacity", "validation_plan"],
        "governance": ["governance_constraints"],
    }
    for dim, blocks in smap_mapping.items():
        grades = [to_generic_grade(state_map[b]) for b in blocks if b in state_map]
        if not grades:
            if dim not in projected:
                projected[dim] = "missing"
        elif all(g == "complete" for g in grades):
            projected[dim] = "complete"
        elif all(g == "missing" for g in grades):
            projected[dim] = "missing"
        else:
            projected[dim] = "partial"

    return projected


def _state_map_from_snapshot(
    snapshot: Optional[Dict[str, Any]],
    events: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, str]:
    if not snapshot:
        return {}
    raw = _raw_state_dict(snapshot)
    if not raw:
        return {}
    result: Dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, Mapping):
            state = value.get("state") or value.get("grade") or value.get("status")
        else:
            state = value
        if key and state is not None:
            result[str(key)] = str(state)

    for decision in extract_workshop_decisions(events):
        for target in decision.get("resolves") or []:
            spec = _spec_for_target(target)
            if spec is not None:
                for key in (spec.v10_key, spec.branch_key, spec.generic_key, spec.strategy_map_key):
                    if key in result:
                        result[key] = "confirmed"
            if target in result:
                result[target] = "confirmed"
    return _project_winner_branch_state_map(result)


def detect_branch_strategy(
    *,
    snapshot: Optional[Mapping[str, Any]] = None,
    session: Optional[Mapping[str, Any]] = None,
    events: Optional[Sequence[Mapping[str, Any]]] = None,
    reconstruction: Optional[Mapping[str, Any]] = None,
) -> bool:
    """Return True when the workshop strategy belongs to the broker-branch family."""
    raw_map = _raw_state_dict(snapshot)
    if any(k in raw_map for k in _BRANCH_ONLY_KEYS):
        return True

    fragments: List[str] = []
    if isinstance(session, Mapping):
        for k in ("title", "strategy_id", "active_strategy_spec_registry_id"):
            if session.get(k):
                fragments.append(str(session[k]))
    if isinstance(snapshot, Mapping):
        if snapshot.get("strategy_version_id"):
            fragments.append(str(snapshot["strategy_version_id"]))
        nq = snapshot.get("next_question_json")
        if isinstance(nq, Mapping):
            for k in ("question_id", "decision_id", "question", "text", "why_now"):
                if nq.get(k):
                    fragments.append(str(nq[k]))
            for item in nq.get("resolves") or []:
                if str(item) in _BRANCH_ONLY_KEYS:
                    return True
                fragments.append(str(item))
        for val in raw_map.values():
            if isinstance(val, Mapping):
                for k in ("summary", "note", "title", "name"):
                    if val.get(k):
                        fragments.append(str(val[k]))
                for item in val.get("missing_definitions") or []:
                    fragments.append(str(item))
    if isinstance(reconstruction, Mapping):
        if reconstruction.get("is_branch_strategy") is True or reconstruction.get("strategy_family") == "winner_branch":
            return True
        smap = reconstruction.get("strategy_map")
        if isinstance(smap, Mapping):
            for block in smap.values():
                if isinstance(block, Mapping) and block.get("summary"):
                    fragments.append(str(block["summary"]))
        for list_key in ("explicit_facts", "inferences", "assumptions", "contradictions"):
            for item in reconstruction.get(list_key) or []:
                fragments.append(str(item))
        nbq = reconstruction.get("next_best_question")
        if isinstance(nbq, Mapping):
            for k in ("question_id", "text", "why_now"):
                if nbq.get(k):
                    fragments.append(str(nbq[k]))
    for event in events or []:
        if isinstance(event, Mapping) and event.get("redacted_summary"):
            fragments.append(str(event["redacted_summary"]))

    corpus = " ".join(fragments).lower()
    return any(keyword in corpus for keyword in _BRANCH_KEYWORDS)


def normalize_v10_block_state(
    raw_value: Any,
    *,
    has_summary: bool = False,
    has_missing_definitions: bool = False,
    has_conflict: bool = False,
    has_inference: bool = False,
) -> str:
    """Normalize a raw status/grade into one of the five V10 states:
    confirmed | inferred | missing | weak | conflict.
    """
    if has_conflict:
        return "conflict"
    token = str(raw_value or "").strip().lower()
    if token in {"confirmed", "complete", "satisfied"}:
        return "confirmed"
    if token in {"conflict", "conflicting", "contradiction", "contradictory"}:
        return "conflict"
    if token in {"inferred", "inferred_needs_confirmation", "provisional", "assumed"}:
        return "inferred"
    if token == "weak":
        return "weak"
    if token == "partial":
        if has_missing_definitions and not has_inference:
            return "weak"
        if has_summary or has_inference:
            return "inferred"
        return "weak"
    return "missing"


def _event_lookup_by_message_number(
    events: Optional[Sequence[Mapping[str, Any]]],
) -> Tuple[Dict[int, Mapping[str, Any]], List[Mapping[str, Any]]]:
    by_msg_no: Dict[int, Mapping[str, Any]] = {}
    ordered: List[Mapping[str, Any]] = []
    msg_no = 0
    for ev in events or []:
        if not isinstance(ev, Mapping):
            continue
        ordered.append(ev)
        if ev.get("event_type") in {"message", "decision"}:
            msg_no += 1
            by_msg_no[msg_no] = ev
    return by_msg_no, ordered


def _build_block_citations(
    *,
    raw_entry: Any,
    recon_block: Optional[Mapping[str, Any]],
    state: str,
    note: str,
    events: Optional[Sequence[Mapping[str, Any]]],
    snapshot: Optional[Mapping[str, Any]],
    matched_decision: Optional[Mapping[str, Any]] = None,
) -> List[Dict[str, Any]]:
    by_msg_no, ordered_events = _event_lookup_by_message_number(events)
    citations: List[Dict[str, Any]] = []
    seen_refs: set[str] = set()

    def add_citation(item: Dict[str, Any]) -> None:
        ref_id = str(item.get("ref_id") or "")
        if not ref_id or ref_id in seen_refs:
            return
        seen_refs.add(ref_id)
        citations.append(_clean_optional(item))

    if matched_decision is not None:
        add_citation({
            "ref_type": "workshop_decision",
            "ref_id": str(matched_decision.get("event_id") or f"seq-{matched_decision.get('sequence_no', 1)}"),
            "message_number": int(matched_decision.get("message_number") or 1),
            "sequence_no": int(matched_decision.get("sequence_no") or 1),
            "decision_id": matched_decision.get("decision_id"),
            "option_id": matched_decision.get("option_id"),
            "summary": _one_line(
                f"交易員裁示 ({matched_decision.get('option_id')}): {matched_decision.get('option_label')}",
                "Workshop decision event",
            ),
        })

    for source in (raw_entry, recon_block):
        if not isinstance(source, Mapping):
            continue
        for key in ("source_citations", "citations"):
            raw_cites = source.get(key)
            if isinstance(raw_cites, list):
                for cite in raw_cites:
                    if isinstance(cite, Mapping) and cite.get("ref_id"):
                        add_citation({
                            "ref_type": str(cite.get("ref_type") or "workshop_event"),
                            "ref_id": str(cite["ref_id"]),
                            "message_number": cite.get("message_number"),
                            "sequence_no": cite.get("sequence_no"),
                            "summary": _one_line(cite.get("summary"), note),
                        })
                    elif isinstance(cite, str) and cite.strip():
                        add_citation({
                            "ref_type": "workshop_event",
                            "ref_id": cite.strip(),
                            "summary": note,
                        })
        details = source.get("details") if isinstance(source.get("details"), Mapping) else source
        msg_nums = details.get("message_numbers") if isinstance(details, Mapping) else None
        if isinstance(msg_nums, list):
            for num in msg_nums:
                if isinstance(num, int) and num >= 1:
                    ev = by_msg_no.get(num)
                    if ev is not None:
                        ref_id = str(ev.get("event_id") or ev.get("private_content_ref") or f"seq-{ev.get('sequence_no') or num}")
                        add_citation({
                            "ref_type": "workshop_event",
                            "ref_id": ref_id,
                            "message_number": num,
                            "sequence_no": int(ev.get("sequence_no") or num),
                            "summary": _one_line(ev.get("redacted_summary"), f"Workshop message #{num}"),
                        })
                    else:
                        add_citation({
                            "ref_type": "conversation_message",
                            "ref_id": f"msg-{num}",
                            "message_number": num,
                            "sequence_no": num,
                            "summary": f"Conversation message #{num}",
                        })

    if not citations and state != "missing":
        if ordered_events:
            latest_ev = ordered_events[-1]
            seq_no = int(latest_ev.get("sequence_no") or len(ordered_events))
            ref_id = str(latest_ev.get("event_id") or latest_ev.get("private_content_ref") or f"seq-{seq_no}")
            add_citation({
                "ref_type": "workshop_event",
                "ref_id": ref_id,
                "message_number": len(by_msg_no) or 1,
                "sequence_no": seq_no,
                "summary": _one_line(latest_ev.get("redacted_summary"), f"Workshop event #{seq_no}"),
            })
        elif isinstance(snapshot, Mapping) and snapshot.get("snapshot_id"):
            add_citation({
                "ref_type": "completeness_snapshot",
                "ref_id": str(snapshot["snapshot_id"]),
                "summary": note,
            })

    return citations


def _default_block_note(title_zh: str, state: str, missing_defs: Sequence[str]) -> str:
    if missing_defs:
        joined = "、".join(_one_line(item) for item in missing_defs if _one_line(item))
        if state == "conflict":
            return f"{title_zh}存在衝突待裁示：{joined}"
        if state in {"weak", "inferred"}:
            return f"{title_zh}部分待確認：{joined}"
        return f"{title_zh}尚缺：{joined}"
    if state == "confirmed":
        return f"交易員已明確定義{title_zh}。"
    if state == "inferred":
        return f"助理由對話推定{title_zh}暫定方案，待交易員確認。"
    if state == "weak":
        return f"{title_zh}目前僅有初步方向，證據或參數仍薄弱。"
    if state == "conflict":
        return f"{title_zh}前後條件存在衝突，需交易員裁示。"
    return f"尚未定義{title_zh}。"


def build_twelve_strategy_blocks(
    *,
    snapshot: Optional[Mapping[str, Any]],
    session: Optional[Mapping[str, Any]] = None,
    events: Optional[Sequence[Mapping[str, Any]]] = None,
    reconstruction: Optional[Mapping[str, Any]] = None,
    is_branch_strategy: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Build the 12 V10 strategy completeness blocks in V10 order (1..12)."""
    branch_mode = (
        is_branch_strategy
        if is_branch_strategy is not None
        else detect_branch_strategy(
            snapshot=snapshot, session=session, events=events, reconstruction=reconstruction
        )
    )
    raw_map = _raw_state_dict(snapshot)
    recon_map = (
        dict(reconstruction.get("strategy_map") or {})
        if isinstance(reconstruction, Mapping) and isinstance(reconstruction.get("strategy_map"), Mapping)
        else {}
    )
    contradictions = [
        _one_line(item)
        for item in ((reconstruction or {}).get("contradictions") or [])
        if _one_line(item)
    ]
    decisions = extract_workshop_decisions(events, is_branch_strategy=branch_mode)
    decisions_by_order: Dict[int, Dict[str, Any]] = {}
    for dec in decisions:
        for target in dec.get("resolves") or []:
            matched_spec = _spec_for_target(target)
            if matched_spec is not None:
                decisions_by_order[matched_spec.order] = dec

    blocks: List[Dict[str, Any]] = []
    for spec in V10_TWELVE_BLOCKS_SPEC:
        lookup_keys: List[str] = []
        primary_keys = (
            (spec.v10_key, spec.branch_key, spec.strategy_map_key, spec.generic_key)
            if branch_mode
            else (spec.generic_key, spec.strategy_map_key, spec.v10_key, spec.branch_key)
        )
        for key in (*primary_keys, *spec.aliases):
            if key not in lookup_keys:
                lookup_keys.append(key)

        raw_entry: Any = None
        for key in lookup_keys:
            if key in raw_map:
                raw_entry = raw_map[key]
                break

        recon_block = None
        for key in (spec.strategy_map_key, spec.v10_key, spec.generic_key):
            candidate = recon_map.get(key)
            if isinstance(candidate, Mapping):
                recon_block = candidate
                break

        raw_status: Any = "missing"
        summary_text = ""
        missing_defs: List[str] = []
        has_conflict = False
        has_inference = False
        custom_title = ""

        if isinstance(raw_entry, Mapping):
            raw_status = (
                raw_entry.get("state")
                or raw_entry.get("v10_state")
                or raw_entry.get("grade")
                or raw_entry.get("status")
                or "missing"
            )
            summary_text = _one_line(raw_entry.get("note") or raw_entry.get("summary"))
            if not branch_mode and raw_entry.get("title"):
                candidate_title = _one_line(raw_entry.get("title"))
                if not any(kw in candidate_title.lower() for kw in ("分點", "branch")):
                    custom_title = candidate_title
            for mkey in ("missing_definitions", "unresolved", "gaps"):
                items = raw_entry.get(mkey)
                if isinstance(items, str) and items.strip():
                    missing_defs.append(_one_line(items))
                elif isinstance(items, list):
                    missing_defs.extend(_one_line(x) for x in items if _one_line(x))
            details = raw_entry.get("details")
            if isinstance(details, Mapping):
                if details.get("open_question"):
                    missing_defs.append(_one_line(details["open_question"]))
                if details.get("contradiction") or details.get("conflict"):
                    has_conflict = True
                if details.get("inferred") or details.get("provisional"):
                    has_inference = True
        elif raw_entry is not None:
            raw_status = str(raw_entry)

        if isinstance(recon_block, Mapping):
            if raw_entry is None:
                raw_status = recon_block.get("state") or recon_block.get("status") or "missing"
            if not summary_text:
                summary_text = _one_line(recon_block.get("summary"))
            rdetails = recon_block.get("details")
            if isinstance(rdetails, Mapping):
                for mkey in ("missing_definitions", "unresolved"):
                    items = rdetails.get(mkey)
                    if isinstance(items, str) and items.strip():
                        val = _one_line(items)
                        if val not in missing_defs:
                            missing_defs.append(val)
                    elif isinstance(items, list):
                        for x in items:
                            val = _one_line(x)
                            if val and val not in missing_defs:
                                missing_defs.append(val)
                if rdetails.get("open_question"):
                    val = _one_line(rdetails["open_question"])
                    if val and val not in missing_defs:
                        missing_defs.append(val)
                if rdetails.get("contradiction") or rdetails.get("conflict"):
                    has_conflict = True

        if contradictions and spec.strategy_map_key in " ".join(contradictions).lower():
            has_conflict = True

        matched_decision = decisions_by_order.get(spec.order)
        if matched_decision is not None:
            state = "confirmed"
            decision_note = _one_line(
                f"已裁示（{matched_decision['option_id']}）：{matched_decision['option_label']}"
            )
            already_has_decision = (
                f"交易員裁示（{matched_decision['option_id']}）" in summary_text
                or f"已裁示（{matched_decision['option_id']}）" in summary_text
                or decision_note in summary_text
            )
            if not summary_text:
                summary_text = decision_note
            elif not already_has_decision:
                summary_text = f"{summary_text}；{decision_note}"
            missing_defs = []
        else:
            state = normalize_v10_block_state(
                raw_status,
                has_summary=bool(summary_text),
                has_missing_definitions=bool(missing_defs),
                has_conflict=has_conflict,
                has_inference=has_inference,
            )

        title_zh = (
            spec.branch_title_zh
            if branch_mode
            else (custom_title or spec.generic_title_zh)
        )
        title_en = spec.branch_title_en if branch_mode else spec.generic_title_en
        block_key = spec.branch_key if branch_mode else spec.generic_key

        if summary_text and missing_defs and state in {"weak", "inferred", "conflict"}:
            missing_str = "、".join(missing_defs)
            if missing_str not in summary_text:
                note = _one_line(f"{summary_text}（待確認：{missing_str}）")
            else:
                note = summary_text
        elif summary_text:
            note = summary_text
        else:
            note = _default_block_note(title_zh, state, missing_defs)

        citations = _build_block_citations(
            raw_entry=raw_entry,
            recon_block=recon_block,
            state=state,
            note=note,
            events=events,
            snapshot=snapshot,
            matched_decision=matched_decision,
        )

        blocks.append({
            "order": spec.order,
            "block_id": block_key,
            "block_key": block_key,
            "block_name": block_key,
            "v10_block_id": spec.v10_key,
            "strategy_map_key": spec.strategy_map_key,
            "mapped_dimension": spec.mapped_dimension,
            "name": title_zh,
            "title": title_zh,
            "label_en": title_en,
            "state": state,
            "state_label_zh": V10_STATE_LABELS_ZH[state],
            "note": note,
            "summary": note,
            "missing_definitions": list(missing_defs),
            "source_citations": citations,
            "citations": citations,
        })

    return blocks


def build_seven_readiness_dimensions(
    snapshot: Optional[Mapping[str, Any]],
    events: Optional[Sequence[Mapping[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Build the 7 backward-compatible readiness dimensions from a snapshot."""
    state_map = _state_map_from_snapshot(dict(snapshot) if isinstance(snapshot, Mapping) else None, events=events)
    dimensions: List[Dict[str, Any]] = []
    for dim in SEVEN_READINESS_DIMENSIONS:
        raw_grade = state_map.get(dim, "missing")
        if raw_grade in {"confirmed", "complete", "satisfied"}:
            grade = "complete"
        elif raw_grade in {"partial", "inferred", "inferred_needs_confirmation", "weak", "conflict", "conflicting"}:
            grade = "partial"
        else:
            grade = "missing"
        dimensions.append({
            "dimension": dim,
            "grade": grade,
            "gaps": [] if grade == "complete" else [f"{dim} is {grade}"],
            "required_actions": [],
        })
    return dimensions


# V10 §4.1 decision option templates keyed by V10 block order (branch vs generic).
_DECISION_TEMPLATES: Dict[int, Dict[str, Any]] = {
    1: {
        "question_branch": "請裁示此分點策略的研究標的範圍與流動性門檻：",
        "question_generic": "請裁示此策略的研究標的池範圍與資料頻率：",
        "why_now": "標的池與流動性門檻會直接決定候選母體、資料需求與回測基準。",
        "options_branch": [
            ("opt_a", "A. 台股上市櫃高流動性標的（排除低成交量小型股）", "聚焦日均成交額充足之上市櫃股票，降低滑價與流動性失真。"),
            ("opt_b", "B. 全市場掃描但依市值與日均量分層評估", "完整捕捉中小型股分點異動，並於回測中分層檢驗容量限制。"),
            ("opt_c", "C. 指定重點產業或關係人申報活躍標的池", "鎖定特定產業族群與近期有內部人申報變動之標的。"),
            ("opt_d", "D. 由僕人先以上市櫃日頻資料與流動性下限建立基準池（預設）", "先排除極低流動性標的建立可交易母體，並保留分層對照。"),
        ],
        "options_generic": [
            ("opt_a", "A. 大型高流動性龍頭標的池（日頻資料）", "聚焦大型權值或科技龍頭標的，交易成本低且容量充足。"),
            ("opt_b", "B. 跨市場/跨產業寬基成分股標的池", "涵蓋多產業成分股以分散單一因子與產業集中風險。"),
            ("opt_c", "C. 依成交量與波動度動態篩選之候選池", "定期依流動性與波動度門檻更新可交易標的母體。"),
            ("opt_d", "D. 由僕人先以日頻資料與流動性門檻建立基準標的池（預設）", "採用對話已提及標的搭配日頻還原資料作為第一版研究範圍。"),
        ],
        "default_summary": "我先以日頻資料與流動性門檻建立可交易基準標的池，避免低流動性噪音干擾初步回測。",
    },
    2: {
        "question_branch": "請裁示「關係人與分點映射」在策略中的角色與證據門檻：",
        "question_generic": "請裁示核心資料來源的對齊方式與可得時間（Point-in-Time）規則：",
        "why_now": "資料映射與可得時間定義直接影響訊號是否含前視偏誤（Look-ahead Bias）。",
        "options_branch": [
            ("opt_a", "A. 關係人持股變化與分點買賣超時序對應為必要條件", "僅在關係人申報變動與分點異常買賣同時滿足時才列入候選。"),
            ("opt_b", "B. 關係人對應機率作為贏家分點之加權支持證據", "建立機率映射（Match Probability），高對應度者給予額外權重。"),
            ("opt_c", "C. 同券商與歷史共現分點群組搭配大額交易雙重過濾", "結合跨期間重複共現與集中成交識別潛在關聯分點。"),
            ("opt_d", "D. 由僕人先建立關係人↔分點機率映射作為加權證據（預設）", "僅標示可能對應程度與支持/反向證據，不做確定身份斷言。"),
        ],
        "options_generic": [
            ("opt_a", "A. 僅使用收盤與正式公告後可得之 Point-in-Time 資料", "嚴格以公告時間戳記對齊，完全排除盤中或事後修正偏誤。"),
            ("opt_b", "B. 價格成交量搭配籌碼/情緒輔助指標雙重確認", "要求主訊號與輔助資料源同時到位才觸發進場評估。"),
            ("opt_c", "C. 以核心價量資料為主，外部指標僅作風險過濾", "降低資料源缺漏風險，外部指標異常時僅降載部位。"),
            ("opt_d", "D. 由僕人先以嚴格 Point-in-Time 日頻資料對齊建立基準（預設）", "所有輸入欄位皆以交易日收盤後確認可得時點對齊。"),
        ],
        "default_summary": "我先以機率映射與嚴格 Point-in-Time 資料截止時點作為基準，確保無前視偏誤。",
    },
    3: {
        "question_branch": "你希望「贏家分點」的主要評分目標偏向哪一種？",
        "question_generic": "你希望策略核心假說與候選標的的主要評分目標偏向哪一種？",
        "why_now": "主要評分目標會直接改變候選排序、持有週期設計與回測檢驗指標。",
        "options_branch": [
            ("opt_a", "A. 分點買入後 20～60 日的持續超額報酬", "重視分點建倉後中波段（20d/60d）成本後超額報酬表現。"),
            ("opt_b", "B. 分點在多次交易中的獲利一致性", "重視跨股票、跨期間與不同市場狀態下的勝率與穩定度。"),
            ("opt_c", "C. 分點對重大訊息的領先能力", "重視異常買進後 3～6 個月內重大基本面事件之領先關聯。"),
            ("opt_d", "D. 上述三項形成綜合分數，由僕人先提出權重", "同時建立盈利導向、資訊領先導向與可交易綜合分數三個版本對照。"),
        ],
        "options_generic": [
            ("opt_a", "A. 20～60 日波段風險調整後超額報酬（Sharpe / Alpha）", "以中期波段延續性與成本後超額報酬作為核心排序目標。"),
            ("opt_b", "B. 跨市場狀態（Regime）的獲利穩定性與低回撤", "優先挑選在不同波動環境下表現一致、回撤受控的訊號。"),
            ("opt_c", "C. 單次訊號勝率與期望值（Expected Value）最大化", "聚焦高勝率、高賠率之顯著訊號事件，降低出手頻率。"),
            ("opt_d", "D. 上述三項形成綜合評分，由僕人先提出基準權重（預設）", "先建立報酬、穩定度與成本後期望值的綜合評分並提供變體比較。"),
        ],
        "default_summary": "我先建立 5d、20d、60d、120d 四個 horizon，並以 20d / 60d 綜合評分作為主要交易與部位建立基準。",
    },
    4: {
        "question_branch": "請裁示「分點遷移與反向流（跨分點匯撥/出貨）」的辨識與過濾規則：",
        "question_generic": "請裁示市場狀態遷移與策略失效條件（Regime Invalidation）的處理方式：",
        "why_now": "遷移與失效規則決定何時應調降單點訊號信賴度或暫停策略。",
        "options_branch": [
            ("opt_a", "A. 買入後無買進紀錄之其他分點突然大量賣出即視為疑似轉倉並剔除", "嚴格防範 A 分點買進、匯撥至 B 分點出貨的假買盤訊號。"),
            ("opt_b", "B. 建立同券商/共現分點 Cluster，以統一淨流量（Net Unified Flow）折算分數", "若群組統一淨流量低於原始買超 50%，自動調降贏家分點信賴值。"),
            ("opt_c", "C. 僅追蹤單一分點完整買進與賣出回合（Round-trip）", "以單一分點歷史獨立進出績效為主，不合併其他分點流量。"),
            ("opt_d", "D. 由僕人建立分點 Flow Graph 並以 Cluster-adjusted 分數併行比較（預設）", "同時保留單點原始分數與 Cluster 調整後分數，於回測中直接對照差異。"),
        ],
        "options_generic": [
            ("opt_a", "A. 市場波動指標（如 VIX > 30）超標時整體曝險自動減半", "在高波動恐慌環境下主動降載，保護組合免於尾端風險。"),
            ("opt_b", "B. 滾動樣本外夏普或相對基準表現跌破門檻時暫停新進場", "當策略近期表現連續落後基準時視為狀態失效並暫停。"),
            ("opt_c", "C. 結合趨勢與波動雙重狀態過濾，空頭狀態僅保留強訊號", "依市場多空狀態動態調整入選門檻與持倉上限。"),
            ("opt_d", "D. 由僕人先以高波動降載 + 連續兩季落後基準作為失效防護（預設）", "建立明確可檢驗的狀態降載與失效停機條件。"),
        ],
        "default_summary": "我先建立調整前與調整後雙版本規則，將異常反向流與高波動狀態納入風險折減。",
    },
    5: {
        "question_branch": "請裁示異常分點交易對照未來重大事件的「事件領先窗口」與檢驗基準：",
        "question_generic": "請裁示事件驅動與風險過濾條件（如財報前夕或重大事件窗口）的處理規則：",
        "why_now": "事件窗口設定會影響資訊領先代理分數的統計顯著性與交易避險規則。",
        "options_branch": [
            ("opt_a", "A. 對照異常交易後 30～90 日內之重大訊息、財報與營收變化", "聚焦季度財報與短期重大公告之資訊領先關聯。"),
            ("opt_b", "B. 對照異常交易後 90～180 日內之併購、私募、處分資產與內部人申報", "捕捉中長期結構性公司事件之領先佈局特徵。"),
            ("opt_c", "C. 同時檢驗 90d/180d 並以同產業隨機分點基準率（Placebo）校正", "加入隨機分點對照與多重檢定懲罰，排除事後巧合。"),
            ("opt_d", "D. 由僕人先以 90d/180d 雙窗口搭配隨機分點基準率建立資訊領先分數（預設）", "僅作為統計領先代理指標，並標註樣本數與信賴區間。"),
        ],
        "options_generic": [
            ("opt_a", "A. 財報或重大公告前 2 個交易日暫停新進場以避開跳空風險", "避免財報開獎不確定性造成的單日大幅跳空虧損。"),
            ("opt_b", "B. 結合正面基本面/法人買賣超事件作為動能訊號的確認條件", "要求價格訊號與基本面/籌碼事件同向才進場。"),
            ("opt_c", "C. 單日組合虧損或市場波動超標時觸發冷卻期暫停新交易", "以組合層級風控門檻限制連續逆風期的交易頻率。"),
            ("opt_d", "D. 由僕人先加入財報前避險與組合日虧損冷卻期作為預設過濾（預設）", "兼顧事件跳空防護與組合回撤控制。"),
        ],
        "default_summary": "我先以 90d/180d 事件窗口並搭配同市場基準率與事件避險規則進行檢驗。",
    },
    6: {
        "question_branch": "請裁示贏家分點異動轉化為「可交易進場信號」的確認條件：",
        "question_generic": "請裁示策略「信號形成與排序過濾」的具體計算與門檻：",
        "why_now": "信號形成規則是產生候選清單與執行回測的直接必要條件。",
        "options_branch": [
            ("opt_a", "A. 高分贏家分點單日或連日異常買超且股價尚未大幅乖離", "在贏家分點剛開始異常建倉、價格尚未反映時切入。"),
            ("opt_b", "B. 贏家分點買超 + Cluster 統一淨流量為正 + 關係人對應支持", "要求分點群組流向一致且無反向出貨跡象才形成信號。"),
            ("opt_c", "C. 綜合評分排名前段且成本後 20d 期望值（EV）大於門檻", "以機率校準後之成本後正期望值作為最終信號門檻。"),
            ("opt_d", "D. 由僕人同時建立直接分點信號、Cluster 調整信號與綜合 Ensemble 對照（預設）", "一次產生多個可比較策略變體（V1～V5）進行回測評斷。"),
        ],
        "options_generic": [
            ("opt_a", "A. 多週期報酬加權排序（如 60d/20d 加權並扣除短期過熱反轉）取前段做多", "兼顧中期趨勢強度與短期均值回歸過濾。"),
            ("opt_b", "B. 均線或突破指標交叉確認並搭配成交量放大過濾", "以明確趨勢突破搭配量能確認作為進場信號。"),
            ("opt_c", "C. 多因子綜合排名加上市場波動度（如 VIX）動態門檻", "依市場環境調整信號入選標準。"),
            ("opt_d", "D. 由僕人先以核心排序指標搭配短期反轉與波動度過濾建立基準信號（預設）", "建立基準參數並於回測中檢驗不同週期敏感度。"),
        ],
        "default_summary": "我先以核心評分加上反向流/短期過熱過濾建立基準信號，並保留變體供回測對照。",
    },
    7: {
        "question_branch": "請裁示信號觸發後的「進場方式與目標持有週期（Horizon）」：",
        "question_generic": "請裁示信號觸發後的「進場委託方式與目標持有週期」：",
        "why_now": "持有週期與進場價格限制會直接影響成交率、滑價成本與報酬衰減曲線。",
        "options_branch": [
            ("opt_a", "A. 短波段持有（5～20 個交易日），信號次日開盤限價進場", "聚焦分點異常買進後的短期動能爆發窗口。"),
            ("opt_b", "B. 中波段持有（20～60 個交易日），分批確認分點續買後進場", "對齊分點主力完整建倉至拉抬之主力波段週期。"),
            ("opt_c", "C. 事件領先持有（60～120 個交易日或至重大事件公告落地）", "針對具資訊領先特徵之分點，持有至基本面事件反應完畢。"),
            ("opt_d", "D. 由僕人同時評估 5d、20d、60d、120d 四個持有期，並以 20d/60d 為主（預設）", "完整比較不同持有期之報酬、風險與容量表現。"),
        ],
        "options_generic": [
            ("opt_a", "A. 次一交易日開盤限價進場（如前收盤上浮 0.3%），未成交則放棄當期", "控制追價滑價成本，避免在大幅跳空時買在短線高點。"),
            ("opt_b", "B. 信號確認當日收盤或次日開盤市價/TWAP 分批進場", "確保高成交率以完整追蹤模型排名組合。"),
            ("opt_c", "C. 固定再平衡週期（每週或雙週）搭配 20～40 個交易日持有上限", "按固定節奏輪動換股，兼顧趨勢延續與資金效率。"),
            ("opt_d", "D. 由僕人先以次日限價進場搭配 20d/40d 持有週期建立基準（預設）", "同時在回測中對照不同持有天數與進場滑價假設。"),
        ],
        "default_summary": "我先建立 5d、20d、60d、120d 多持有期對照，並以 20d/60d 波段作為基準持有設定。",
    },
    8: {
        "question_branch": "請裁示持倉期間的「加碼、減碼與出場（含分點反向流出貨）」規則：",
        "question_generic": "請裁示持倉期間的「加碼、減碼、停損與再平衡出場」規則：",
        "why_now": "出場與失效減碼規則是風險控制與回測損益模擬的高優先必備條件。",
        "options_branch": [
            ("opt_a", "A. 贏家分點或關聯 Cluster 出現大量賣出（反向流）時立即減碼或全數出場", "以籌碼邏輯一致為原則，主力出貨即退場。"),
            ("opt_b", "B. 訊號二次確認（分點續買 + 價格突破）後加碼，跌破固定停損（如 -8%）出場", "分批建立部位並以明確價格停損保護下檔。"),
            ("opt_c", "C. 持有期屆滿（如 20d/60d）或事件落地後依報酬衰減分批停利出場", "結合時間到期與事件兌現雙重出場機制。"),
            ("opt_d", "D. 由僕人先採「二次確認加碼 + 分點反向流減碼 + 固定停損」複合規則（預設）", "兼顧順勢加碼、籌碼轉弱退場與硬性停損保護。"),
        ],
        "options_generic": [
            ("opt_a", "A. 定期再平衡掉出排名前段即出場，並設單一標的固定停損（如 -8%）", "紀律淘汰動能轉弱標的並嚴格限制單筆最大虧損。"),
            ("opt_b", "B. 持有超過指定天數（如 40 日）仍無獲利即時間停損出場，獲利部位採移動停利", "提升資金周轉效率，讓獲利部位延續趨勢。"),
            ("opt_c", "C. 訊號強度二次確認後分批加碼，組合回撤超標時統一減碼降載", "動態調整單檔與整體部位以控制回撤。"),
            ("opt_d", "D. 由僕人先結合排名淘汰、固定百分比停損與時間停損建立出場基準（預設）", "完整涵蓋動能轉弱、價格停損與時間停損三種出場情境。"),
        ],
        "default_summary": "我先採用「訊號二次確認加碼、反向流/排名轉弱減碼、固定百分比停損」作為出場基準。",
    },
    9: {
        "question_branch": "請選擇此策略的「部位規模、總曝險與槓桿限制」方案（V10 §9）：",
        "question_generic": "請選擇此策略的「部位配置、集中度上限與風險預算」方案：",
        "why_now": "部位與槓桿上限是進入完整驗證與交易操盤室的必要風控關卡。",
        "options_branch": [
            ("opt_a", "A. 保守方案（初始單檔 1%、二次確認增至 2%，最多 5 檔，總曝險 8%，無槓桿）", "適合初步驗證階段，嚴格控制單檔與總體風險曝險。"),
            ("opt_b", "B. 平衡方案（初始單檔 1.5%、確認後加至 3%，最多 8 檔，總曝險 15%，最高槓桿 1.2×）", "兼顧報酬彈性與分散度，並限制同產業/同群組集中度。"),
            ("opt_c", "C. 積極方案（初始單檔 2%、三項通過加至 4%，最多 10 檔，總曝險 25%，最高槓桿 1.5×）", "在高信賴度訊號通過流動性檢驗時給予較高部位上限。"),
            ("opt_d", "D. 由僕人先採平衡方案並加上同產業 Cluster 曝險上限（每群組最多 2 檔，預設）", "避免候選股集中於同一產業造成共同因子曝險過高。"),
        ],
        "options_generic": [
            ("opt_a", "A. 保守等權方案（單檔上限 10%，總曝險上限 40%，無槓桿，最大回撤 10%）", "低波動配置，剩餘資金停泊於現金或短期國庫券工具。"),
            ("opt_b", "B. 標準等權方案（單檔上限 20%，總曝險上限 60%，無槓桿，最大回撤 15%）", "均衡配置前段入選標的，兼顧報酬貢獻與回撤控管。"),
            ("opt_c", "C. 訊號加權方案（依評分強弱配置 10%～25%，總曝險上限 80%，含產業集中度限制）", "讓高分標的獲得較高權重，同時限制單一市場/產業比例。"),
            ("opt_d", "D. 由僕人先採標準等權方案（單檔 ≤20%、總曝險 ≤60%、最大回撤 15%）作為基準（預設）", "符合多數波段策略之穩健風控基準，並可於回測中比較不同權重。"),
        ],
        "default_summary": "我先以平衡部位方案並加上同產業/同群組集中度上限作為預設風控配置。",
    },
    10: {
        "question_branch": "請裁示回測與期望值計算中的「交易成本、滑價與容量限制」假設：",
        "question_generic": "請裁示回測與執行評估中的「手續費、稅費、滑價與資金容量」假設：",
        "why_now": "成本與流動性懲罰決定紙上報酬能否轉化為真實可交易的正期望值（EV）。",
        "options_branch": [
            ("opt_a", "A. 台股標準成本（手續費 0.1425% 折讓、證交稅 0.3%）加計 5～10 bp 衝擊滑價", "反映一般實盤交易成本與中大型股委託滑價。"),
            ("opt_b", "B. 加入低流動性懲罰（Liquidity Penalty）與單日成交量佔比上限（≤3%）", "確保贏家分點訊號不會僅集中在無法進出的小型冷門股。"),
            ("opt_c", "C. 嚴格容量壓力測試（高滑價 20 bp 與不同資金規模下的報酬衰減檢驗）", "評估大部位資金進場對訊號超額報酬的侵蝕程度。"),
            ("opt_d", "D. 由僕人套用標準手續費/證交稅、滑價與流動性懲罰計算成本後 EV（預設）", "所有回測與期望值一律呈現成本後（Post-cost）結果。"),
        ],
        "options_generic": [
            ("opt_a", "A. 標準交易成本（台股手續費折讓 + 0.3% 證交稅；美股每股成本）與 5 bp 滑價", "符合主流券商費率與大型股限價委託之合理滑價。"),
            ("opt_b", "B. 保守高成本壓力測試（雙倍滑價 10～15 bp 與換手率上限約束）", "檢驗高換手率下策略是否仍具穩健正報酬。"),
            ("opt_c", "C. 設定策略總資金容量上限與單筆委託佔日均量比例限制", "確保策略在目標資金規模下不產生顯著市場衝擊。"),
            ("opt_d", "D. 由僕人先套用標準費率、5 bp 滑價與容量上限進行成本後評估（預設）", "以成本後夏普與淨期望值作為策略評斷標準。"),
        ],
        "default_summary": "我先套用標準手續費、證交稅、滑價與流動性容量限制，所有指標皆以成本後呈現。",
    },
    11: {
        "question_branch": "請裁示策略進入操盤室前的「驗證、回測與反證（Refutation）」檢驗設計：",
        "question_generic": "請裁示策略的「樣本外驗證（OOS）、敏感度分析與反證檢驗」方案：",
        "why_now": "必須通過樣本外與反證基準檢驗，才能區分真實交易價值與事後資料探勘。",
        "options_branch": [
            ("opt_a", "A. 滾動樣本外驗證（Walk-forward Rolling OOS）搭配多持有期（5d/20d/60d/120d）比較", "檢驗贏家分點名單在未見樣本期間是否仍具預測力。"),
            ("opt_b", "B. 隨機分點基準（Random Branch Placebo）對照與多重檢定懲罰（False Discovery Penalty）", "證明贏家分點表現顯著優於同市場隨機抽樣分點。"),
            ("opt_c", "C. 五個策略變體（V1 直接信號～V5 Ensemble）跨市場狀態與流動性分組完整回測", "直接比較有無關係人確認、有無 Cluster 調整之實質差異。"),
            ("opt_d", "D. 由僕人完整執行 Rolling OOS、隨機分點 Placebo 與 V1～V5 變體比較（預設）", "一次產出成本後夏普、最大回撤、命中率、容量與 OOS 穩定度對照表。"),
        ],
        "options_generic": [
            ("opt_a", "A. 樣本內/樣本外時間切分（In-sample / Out-of-sample）與滾動 Walk-forward 驗證", "確認策略在最新樣本外期間仍維持正夏普與可控回撤。"),
            ("opt_b", "B. 核心參數敏感度測試（如不同觀察期 40/60/90 日）與等權基準對照", "確保績效非單一參數過度擬合（Overfitting）之結果。"),
            ("opt_c", "C. 跨多頭、空頭與高波動市場狀態（Regime）之分組表現與壓力測試", "檢驗策略在不利市場環境下的防禦力。"),
            ("opt_d", "D. 由僕人執行樣本外驗證、參數敏感度與等權基準反證對照（預設）", "全面檢驗成本後夏普、最大回撤、換手率與基準超額報酬。"),
        ],
        "default_summary": "我先規劃滾動樣本外回測、隨機/等權基準反證與多變體敏感度比較。",
    },
    12: {
        "question_branch": "請裁示策略定案後的「監控指標、自動更新與交易操盤室部署模式」：",
        "question_generic": "請裁示策略的「日常監控、版本更新頻率與紙上交易（Paper）治理規則」：",
        "why_now": "明確的監控與治理規則是完成策略完整度並加入交易操盤室的最後一塊拼圖。",
        "options_branch": [
            ("opt_a", "A. 每日更新贏家分點排名與 Cluster 淨流量，先以紙上交易（Paper）執行", "在操盤室即時監控新進場候選與已持倉標的之分點異動。"),
            ("opt_b", "B. 主候選版本（如 V4）進操盤室紙上交易，Ensemble 版本（如 V5）同步放 Shadow 對照", "雙軌驗證主策略與對照模型在即時盤勢下的一致性。"),
            ("opt_c", "C. 設定分點遷移模型穩定度與事件領先信賴值監控，異常時觸發人工覆核", "當分點結構改變或資料延遲時主動告警並暫停自動建議。"),
            ("opt_d", "D. 由僕人先配置主策略紙上交易 + Shadow 對照雙軌監控，所有執行皆需交易員確認（預設）", "保持 Agora 無直接下單權限之安全邊界，由交易員在操盤室裁示。"),
        ],
        "options_generic": [
            ("opt_a", "A. 先於紙上交易（Paper）帳本運行並每日監控訊號、部位與風險指標", "所有實盤或跨階段晉升均需另外經過人工核准。"),
            ("opt_b", "B. 同步啟動 Shadow 影子追蹤，比對規則建議與交易員實際裁示之績效歸因", "持續追蹤干預效果並隨策略演進更新操盤室視圖。"),
            ("opt_c", "C. 設定資料新鮮度與績效衰減告警，連續落後基準時自動提醒檢討", "確保資料源延遲或市場結構改變時能即時介入。"),
            ("opt_d", "D. 由僕人先建立紙上交易 + Shadow 對照監控，並保留交易員每筆確認關卡（預設）", "遵循治理邊界，任何部位建立或調整皆由交易員確認後送出。"),
        ],
        "default_summary": "我先將策略配置為操盤室紙上交易與 Shadow 對照雙軌監控，所有交易裁示均保留由你確認。",
    },
}

# V10 priority order for selecting which unconfirmed block to ask about next:
# Score/hypothesis & signal first, then migration/invalidation, entry/holding, exit, sizing, mapping, event lead, etc.
_DECISION_PRIORITY_ORDERS: Tuple[int, ...] = (3, 6, 4, 7, 8, 9, 2, 5, 1, 10, 11, 12)


def _normalize_options_list(
    raw_options: Any,
    *,
    default_option_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_options, list):
        return []
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(raw_options[:4]):
        letter = chr(ord("a") + idx)
        fallback_id = f"opt_{letter}"
        if isinstance(item, Mapping):
            opt_id = _one_line(item.get("option_id") or item.get("id"), fallback_id)
            label = _one_line(item.get("label") or item.get("text") or item.get("title"), opt_id)
            desc = _one_line(item.get("description") or item.get("summary") or item.get("rationale"))
            is_def = bool(item.get("is_default")) or (default_option_id is not None and opt_id == default_option_id)
            normalized.append(_clean_optional({
                "option_id": opt_id,
                "id": opt_id,
                "label": label,
                "text": label,
                "description": desc or None,
                "is_default": is_def,
            }))
        elif isinstance(item, str) and item.strip():
            label = _one_line(item)
            opt_id = fallback_id
            is_def = (default_option_id is not None and opt_id == default_option_id)
            normalized.append({
                "option_id": opt_id,
                "id": opt_id,
                "label": label,
                "text": label,
                "is_default": is_def,
            })
    if len(normalized) >= 2 and not any(opt.get("is_default") for opt in normalized):
        if default_option_id:
            for opt in normalized:
                if opt["option_id"] == default_option_id:
                    opt["is_default"] = True
                    break
        if not any(opt.get("is_default") for opt in normalized):
            normalized[-1]["is_default"] = True
    return normalized if 2 <= len(normalized) <= 4 else []


def build_next_high_value_decision(
    *,
    snapshot: Optional[Mapping[str, Any]],
    blocks: Optional[Sequence[Mapping[str, Any]]] = None,
    session: Optional[Mapping[str, Any]] = None,
    events: Optional[Sequence[Mapping[str, Any]]] = None,
    reconstruction: Optional[Mapping[str, Any]] = None,
    is_branch_strategy: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build the next high-value decision with a question, 2-4 selectable options,
    and an optional servant default (V10 §4.1).
    """
    branch_mode = (
        is_branch_strategy
        if is_branch_strategy is not None
        else detect_branch_strategy(
            snapshot=snapshot, session=session, events=events, reconstruction=reconstruction
        )
    )
    effective_blocks = (
        list(blocks)
        if blocks is not None
        else build_twelve_strategy_blocks(
            snapshot=snapshot,
            session=session,
            events=events,
            reconstruction=reconstruction,
            is_branch_strategy=branch_mode,
        )
    )
    blocks_by_order = {int(b["order"]): b for b in effective_blocks}
    answered_ids = {
        str(d.get("decision_id") or "")
        for d in extract_workshop_decisions(events)
        if d.get("decision_id")
    }

    raw_nq: Dict[str, Any] = {}
    if isinstance(snapshot, Mapping) and isinstance(snapshot.get("next_question_json"), Mapping):
        raw_nq = dict(snapshot["next_question_json"])
    elif isinstance(reconstruction, Mapping) and isinstance(reconstruction.get("next_best_question"), Mapping):
        raw_nq = dict(reconstruction["next_best_question"])

    raw_q_id = _one_line(raw_nq.get("decision_id") or raw_nq.get("question_id") or raw_nq.get("id"))
    raw_resolves = [
        str(r).strip()
        for r in (raw_nq.get("target_blocks") or raw_nq.get("resolves") or [])
        if str(r).strip()
    ]

    # Check whether raw_nq is still unresolved or was already answered by a decision event.
    raw_specs = [s for s in (_spec_for_target(r) for r in raw_resolves) if s is not None]
    raw_already_answered = (
        (bool(raw_q_id) and raw_q_id in answered_ids)
        or (
            bool(raw_specs)
            and all(blocks_by_order.get(s.order, {}).get("state") == "confirmed" for s in raw_specs)
        )
    )

    target_spec: Optional[V10BlockSpec] = None
    if not raw_already_answered and raw_specs:
        target_spec = next(
            (s for s in raw_specs if blocks_by_order.get(s.order, {}).get("state") != "confirmed"),
            raw_specs[0],
        )

    if target_spec is None:
        # Pick the highest-priority unconfirmed block (conflict > missing > weak > inferred).
        state_rank = {"conflict": 0, "missing": 1, "weak": 2, "inferred": 3}
        unconfirmed_orders = [
            order
            for order in _DECISION_PRIORITY_ORDERS
            if blocks_by_order.get(order, {}).get("state") != "confirmed"
        ]
        if unconfirmed_orders:
            unconfirmed_orders.sort(
                key=lambda o: (
                    state_rank.get(str(blocks_by_order[o].get("state")), 4),
                    _DECISION_PRIORITY_ORDERS.index(o),
                )
            )
            chosen_order = unconfirmed_orders[0]
            target_spec = next(s for s in V10_TWELVE_BLOCKS_SPEC if s.order == chosen_order)
        else:
            target_spec = next(s for s in V10_TWELVE_BLOCKS_SPEC if s.order == 11)

    template = _DECISION_TEMPLATES[target_spec.order]
    target_block_key = target_spec.branch_key if branch_mode else target_spec.generic_key

    if not raw_already_answered and raw_q_id:
        decision_id = raw_q_id
    else:
        decision_id = f"decision_{target_block_key}"

    if not raw_already_answered and _one_line(raw_nq.get("question") or raw_nq.get("text") or raw_nq.get("prompt")):
        question_text = _one_line(raw_nq.get("question") or raw_nq.get("text") or raw_nq.get("prompt"))
    else:
        question_text = template["question_branch"] if branch_mode else template["question_generic"]

    if not raw_already_answered and _one_line(raw_nq.get("why_now") or raw_nq.get("why_it_matters") or raw_nq.get("reason")):
        why_now = _one_line(raw_nq.get("why_now") or raw_nq.get("why_it_matters") or raw_nq.get("reason"))
    else:
        why_now = template["why_now"]

    if not raw_already_answered and raw_resolves:
        resolves = list(raw_resolves)
    else:
        resolves = [target_block_key, target_spec.strategy_map_key]
    target_blocks: List[str] = []
    for r in resolves:
        spec = _spec_for_target(r)
        bkey = (spec.branch_key if branch_mode else spec.generic_key) if spec is not None else r
        if bkey not in target_blocks:
            target_blocks.append(bkey)
    if target_block_key not in target_blocks:
        target_blocks.insert(0, target_block_key)

    raw_default_id = _one_line(
        raw_nq.get("default_option_id")
        or (raw_nq.get("servant_default") or {}).get("option_id")
        if isinstance(raw_nq.get("servant_default"), Mapping)
        else raw_nq.get("default_option_id")
    ) or None

    options = (
        _normalize_options_list(
            raw_nq.get("options") or raw_nq.get("answer_options"),
            default_option_id=raw_default_id,
        )
        if not raw_already_answered
        else []
    )
    if not options:
        raw_tuples = template["options_branch"] if branch_mode else template["options_generic"]
        options = [
            {
                "option_id": opt_id,
                "id": opt_id,
                "label": label,
                "text": label,
                "description": desc,
                "is_default": idx == len(raw_tuples) - 1,
            }
            for idx, (opt_id, label, desc) in enumerate(raw_tuples)
        ]

    default_opt = next((opt for opt in options if opt.get("is_default")), options[-1])
    default_option_id = str(default_opt["option_id"])

    raw_servant_default = raw_nq.get("servant_default") if not raw_already_answered else None
    if isinstance(raw_servant_default, Mapping) and raw_servant_default.get("summary"):
        servant_default_summary = _one_line(raw_servant_default.get("summary"))
    elif isinstance(raw_servant_default, str) and raw_servant_default.strip():
        servant_default_summary = _one_line(raw_servant_default)
    else:
        servant_default_summary = template["default_summary"]

    servant_default = {
        "option_id": default_option_id,
        "label": str(default_opt["label"]),
        "summary": servant_default_summary,
        "provisional_assumption": servant_default_summary,
    }

    return {
        "decision_id": decision_id,
        "question_id": decision_id,
        "question": question_text,
        "text": question_text,
        "why_now": why_now,
        "why_it_matters": why_now,
        "resolves": resolves,
        "target_blocks": target_blocks,
        "options": options,
        "answer_options": [str(opt["label"]) for opt in options],
        "default_option_id": default_option_id,
        "servant_default": servant_default,
        "score_total": float(raw_nq.get("score_total") or raw_nq.get("score") or 85.0),
        "freeform_allowed": bool(raw_nq.get("freeform_allowed", True)),
        "defer_allowed": bool(raw_nq.get("defer_allowed", True)),
    }


def resolve_decision_choice(
    *,
    next_decision: Mapping[str, Any],
    decision_id: Optional[str] = None,
    question_id: Optional[str] = None,
    option_id: Optional[str] = None,
    selected_option_id: Optional[str] = None,
    option_label: Optional[str] = None,
    custom_response: Optional[str] = None,
    use_servant_default: bool = False,
    resolves: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Validate and resolve a trader's decision submission against the current next_decision."""
    eff_decision_id = _one_line(
        decision_id
        or question_id
        or next_decision.get("decision_id")
        or next_decision.get("question_id"),
        "decision_latest",
    )
    options = list(next_decision.get("options") or [])
    requested_option_id = _one_line(option_id or selected_option_id)
    requested_option_label = _one_line(option_label)
    requested_custom = _one_line(custom_response)

    chosen_option: Optional[Dict[str, Any]] = None
    used_default = False
    is_freeform = False
    response_kind = "option"
    private_response_text = ""

    if requested_custom:
        if requested_option_id and requested_option_id.lower() not in {"freeform", "opt_freeform"}:
            raise ValueError(
                f"Cannot combine option_id {requested_option_id!r} with custom_response for decision {eff_decision_id!r}"
            )
        if requested_option_label:
            raise ValueError(
                f"Cannot combine option_label with custom_response for decision {eff_decision_id!r}"
            )
        if not bool(next_decision.get("freeform_allowed", True)):
            raise ValueError(
                f"Freeform custom_response is not allowed for decision {eff_decision_id!r}"
            )
        is_freeform = True
        response_kind = "freeform"
        final_option_id = "freeform"
        final_option_label = "自訂裁示（已加密儲存）"
        private_response_text = requested_custom
    elif requested_option_id:
        req_lower = requested_option_id.lower()
        if req_lower in {"freeform", "opt_freeform"}:
            raise ValueError(
                f"Freeform option_id requires custom_response for decision {eff_decision_id!r}"
            )
        for opt in options:
            oid = str(opt.get("option_id") or opt.get("id") or "").strip()
            lbl = str(opt.get("label") or opt.get("text") or "").strip()
            if (
                oid.lower() == req_lower
                or f"opt_{req_lower}" == oid.lower()
                or lbl.lower() == req_lower
                or (len(req_lower) == 1 and lbl.lower().startswith(f"{req_lower}."))
            ):
                chosen_option = dict(opt)
                break
        if chosen_option is None:
            raise ValueError(f"Unknown option_id {requested_option_id!r} for decision {eff_decision_id!r}")
        canonical_label = _one_line(
            chosen_option.get("label") or chosen_option.get("text"),
            str(chosen_option.get("option_id") or chosen_option.get("id") or "opt_selected"),
        )
        if requested_option_label and requested_option_label.lower() != canonical_label.lower():
            raise ValueError(
                f"option_label {requested_option_label!r} does not match option_id {requested_option_id!r} for decision {eff_decision_id!r}"
            )
        final_option_id = str(chosen_option.get("option_id") or chosen_option.get("id") or "opt_selected")
        final_option_label = canonical_label
        private_response_text = canonical_label
        if chosen_option.get("is_default"):
            used_default = True
    elif requested_option_label:
        req_lbl_lower = requested_option_label.lower()
        for opt in options:
            oid = str(opt.get("option_id") or opt.get("id") or "").strip()
            lbl = str(opt.get("label") or opt.get("text") or "").strip()
            if (
                lbl.lower() == req_lbl_lower
                or oid.lower() == req_lbl_lower
                or (len(req_lbl_lower) == 1 and lbl.lower().startswith(f"{req_lbl_lower}."))
            ):
                chosen_option = dict(opt)
                break
        if chosen_option is None:
            raise ValueError(f"Unknown option_label {requested_option_label!r} for decision {eff_decision_id!r}")
        final_option_id = str(chosen_option.get("option_id") or chosen_option.get("id") or "opt_selected")
        final_option_label = _one_line(
            chosen_option.get("label") or chosen_option.get("text"),
            final_option_id,
        )
        private_response_text = final_option_label
        if chosen_option.get("is_default"):
            used_default = True
    elif use_servant_default:
        used_default = True
        def_id = _one_line(next_decision.get("default_option_id"))
        chosen_option = next(
            (dict(opt) for opt in options if str(opt.get("option_id")) == def_id or opt.get("is_default")),
            dict(options[-1]) if options else None,
        )
        if chosen_option is None:
            raise ValueError(f"No servant default option available for decision {eff_decision_id!r}")
        final_option_id = str(chosen_option.get("option_id") or chosen_option.get("id") or "opt_d")
        final_option_label = _one_line(
            chosen_option.get("label") or chosen_option.get("text"),
            final_option_id,
        )
        private_response_text = final_option_label
    else:
        raise ValueError("One of option_id, selected_option_id, option_label, custom_response, or use_servant_default=true is required")

    eff_resolves = [str(r).strip() for r in (resolves or []) if str(r).strip()]
    if not eff_resolves:
        for key in ("target_blocks", "resolves"):
            for item in next_decision.get(key) or []:
                val = str(item).strip()
                if val and val not in eff_resolves:
                    eff_resolves.append(val)
    for item in list(eff_resolves):
        spec = _spec_for_target(item)
        if spec is not None:
            for extra in (spec.v10_key, spec.strategy_map_key, spec.generic_key):
                if extra not in eff_resolves:
                    eff_resolves.append(extra)

    return {
        "decision_id": eff_decision_id,
        "question_id": eff_decision_id,
        "question": _one_line(next_decision.get("question") or next_decision.get("text"), "策略高價值決策"),
        "response_kind": response_kind,
        "is_freeform": is_freeform,
        "option_id": final_option_id,
        "option_label": final_option_label,
        "private_response_text": private_response_text,
        "used_servant_default": used_default,
        "resolves": eff_resolves,
        "target_blocks": [
            r for r in eff_resolves
            if any(r in (s.v10_key, s.branch_key, s.generic_key) for s in V10_TWELVE_BLOCKS_SPEC)
        ] or eff_resolves,
    }


def project_completeness_response(
    *,
    snapshot: Optional[Dict[str, Any]],
    session: Optional[Mapping[str, Any]] = None,
    events: Optional[Sequence[Mapping[str, Any]]] = None,
    reconstruction: Optional[Mapping[str, Any]] = None,
    readiness: Optional[Mapping[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Enrich a completeness snapshot with the 12 V10 blocks, 5 states, notes,
    source citations, and the next high-value decision while preserving all
    existing snapshot fields and the 7 readiness dimensions.
    """
    if snapshot is None:
        return None
    _assess_blocking_items, _assess_readiness, compute_overall_grade = _readiness_helpers()
    branch_mode = detect_branch_strategy(
        snapshot=snapshot, session=session, events=events, reconstruction=reconstruction
    )
    blocks = build_twelve_strategy_blocks(
        snapshot=snapshot,
        session=session,
        events=events,
        reconstruction=reconstruction,
        is_branch_strategy=branch_mode,
    )
    next_decision = build_next_high_value_decision(
        snapshot=snapshot,
        blocks=blocks,
        session=session,
        events=events,
        reconstruction=reconstruction,
        is_branch_strategy=branch_mode,
    )
    state_map = _state_map_from_snapshot(snapshot, events=events)
    dimensions = build_seven_readiness_dimensions(snapshot, events=events)
    ready_gates = [
        gate["gate"]
        for gate in (readiness or {}).get("gates", [])
        if isinstance(gate, Mapping) and gate.get("state") == "ready"
    ]
    raw_nq = snapshot.get("next_question_json")
    enriched_nq = {**dict(raw_nq), **next_decision} if isinstance(raw_nq, Mapping) else dict(next_decision)

    return {
        **snapshot,
        "strategy_family": "winner_branch" if branch_mode else "generic",
        "is_branch_strategy": branch_mode,
        "overall_grade": compute_overall_grade(state_map) if state_map else "incomplete",
        "research_ready": "preliminary_research" in ready_gates,
        "dimensions": dimensions,
        "blocks": blocks,
        "twelve_blocks": blocks,
        "next_decision": next_decision,
        "next_question_json": enriched_nq,
    }


def _explicit_blockers(snapshot: Optional[Dict[str, Any]]) -> List[str]:
    if not snapshot:
        return []
    raw = snapshot.get("blocking_items_json") or []
    if not isinstance(raw, list):
        return [str(raw)]
    result: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            result.append(str(item.get("reason") or item.get("field") or item))
        else:
            result.append(str(item))
    return [item for item in result if item]


def _readiness_helpers() -> tuple[Callable[[Any], Any], Callable[[Any], Any], Callable[[Any], str]]:
    try:
        from services.research.strategy_spec.completeness import (  # type: ignore
            assess_blocking_items,
            assess_readiness,
            compute_overall_grade,
        )
    except Exception:
        def assess_blocking_items(state_map: Dict[str, str]) -> List[Dict[str, str]]:
            return [
                {
                    "field": key,
                    "reason": f"Field '{key}' is {state}; blocks research gate.",
                    "gate": "research",
                }
                for key, state in state_map.items()
                if state in {"missing", "conflicting"}
            ]

        def assess_readiness(blocking_items: List[Any]) -> tuple[bool, bool, bool]:
            return (not blocking_items, not blocking_items, not blocking_items)

        def compute_overall_grade(state_map: Dict[str, str]) -> str:
            return "complete" if state_map and not assess_blocking_items(state_map) else "partial"

    return assess_blocking_items, assess_readiness, compute_overall_grade


def _blocking_attr(item: Any, attr: str) -> str:
    if isinstance(item, dict):
        return str(item.get(attr) or "")
    return str(getattr(item, attr, "") or "")


def _requirement(
    requirement_id: str,
    title: str,
    state: str,
    *,
    hardness: str = "hard",
    summary: Optional[str] = None,
) -> Dict[str, Any]:
    return _clean_optional({
        "requirement_id": requirement_id,
        "title": title,
        "hardness": hardness,
        "state": state,
        "summary": summary,
    })


def _gate_payload(
    *,
    gate_name: str,
    gate_state: str,
    requirements: List[Dict[str, Any]],
    blockers: List[str],
    assessed_at: str,
) -> Dict[str, Any]:
    blocking_ids = [
        req["requirement_id"]
        for req in requirements
        if req.get("state") in {"missing", "partial", "stale"}
        and req.get("hardness") == "hard"
    ]
    return _clean_optional({
        "gate": gate_name,
        "state": gate_state,
        "requirements": requirements,
        "blocking_requirement_ids": blocking_ids,
        "conditional_assumptions": blockers if gate_state == "conditional" else [],
        "evaluated_at": assessed_at,
    })


def _build_evidence_refs(
    events: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for event in events:
        ref_id = event.get("private_content_ref") or event.get("event_id")
        if not ref_id:
            continue
        refs.append(_clean_optional({
            "ref_type": "evidence_item",
            "ref_id": ref_id,
            "summary": event.get("redacted_summary") or f"Workshop event {event.get('sequence_no')}",
            "data_cutoff": event.get("created_at"),
        }))
    if snapshot and snapshot.get("snapshot_id"):
        refs.append(_clean_optional({
            "ref_type": "evidence_bundle",
            "ref_id": snapshot["snapshot_id"],
            "summary": "Latest StrategyCompleteness snapshot",
            "data_cutoff": snapshot.get("created_at"),
        }))
    return refs


def build_readiness_assessment(
    *,
    session: Dict[str, Any],
    events: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
    assessed_at: str,
    assessment_version: Optional[int] = None,
) -> Dict[str, Any]:
    assess_blocking_items, assess_readiness, _compute_overall_grade = _readiness_helpers()
    state_map = _state_map_from_snapshot(snapshot, events=events)
    hard_blockers = _explicit_blockers(snapshot)
    blocking_items = list(assess_blocking_items(state_map)) if state_map else []
    research_ready, validation_ready, trading_room_ready = assess_readiness(blocking_items)

    strategy_id = (
        session.get("strategy_id")
        or session.get("active_strategy_spec_registry_id")
        or f"unbound-{session['workshop_id']}"
    )
    workshop_version_id = (
        session.get("selected_version_id")
        or (snapshot or {}).get("strategy_version_id")
        or strategy_id
    )
    registry_id = (
        session.get("active_strategy_spec_registry_id")
        or session.get("strategy_id")
        or strategy_id
    )
    has_state = bool(state_map)
    has_events = bool(events)
    has_strategy_ref = bool(session.get("strategy_id") or session.get("active_strategy_spec_registry_id"))
    explicit_blocked = bool(hard_blockers)

    gate_blockers = {
        "preliminary_research": [
            _blocking_attr(item, "reason")
            for item in blocking_items
            if _blocking_attr(item, "gate") == "research"
        ],
        "full_validation": [
            _blocking_attr(item, "reason")
            for item in blocking_items
            if _blocking_attr(item, "gate") == "validation"
        ],
        "trading_room": [
            _blocking_attr(item, "reason")
            for item in blocking_items
            if _blocking_attr(item, "gate") == "trading_room"
        ],
    }

    if explicit_blocked and not blocking_items:
        gate_blockers = {key: list(hard_blockers) for key in gate_blockers}
        research_ready = validation_ready = trading_room_ready = False

    preliminary_state = "ready" if research_ready and has_state else (
        "conditional" if has_events or has_strategy_ref else "not_assessed"
    )
    if gate_blockers["preliminary_research"]:
        preliminary_state = "blocked"

    validation_state = "ready" if validation_ready and has_state else (
        "conditional" if preliminary_state == "ready" else "not_assessed"
    )
    if gate_blockers["full_validation"] or preliminary_state == "blocked":
        validation_state = "blocked"

    trading_state = "ready" if trading_room_ready and has_state and has_strategy_ref else (
        "conditional" if trading_room_ready and has_state else "not_assessed"
    )
    if gate_blockers["trading_room"] or validation_state == "blocked":
        trading_state = "blocked"

    gates = [
        _gate_payload(
            gate_name="preliminary_research",
            gate_state=preliminary_state,
            assessed_at=assessed_at,
            blockers=gate_blockers["preliminary_research"],
            requirements=[
                _requirement(
                    "state_map_present",
                    "Strategy completeness state map is present",
                    "satisfied" if has_state else "missing",
                    hardness="soft",
                ),
                _requirement(
                    "workshop_evidence_present",
                    "Workshop event evidence exists in scoped store",
                    "satisfied" if has_events else "missing",
                    hardness="hard",
                ),
                _requirement(
                    "research_blockers_clear",
                    "No research gate hard blockers",
                    "satisfied" if not gate_blockers["preliminary_research"] else "missing",
                    summary="; ".join(gate_blockers["preliminary_research"]) or None,
                ),
            ],
        ),
        _gate_payload(
            gate_name="full_validation",
            gate_state=validation_state,
            assessed_at=assessed_at,
            blockers=gate_blockers["full_validation"],
            requirements=[
                _requirement(
                    "preliminary_research_ready",
                    "Preliminary research gate is ready",
                    "satisfied" if preliminary_state == "ready" else "partial",
                ),
                _requirement(
                    "validation_blockers_clear",
                    "No validation gate hard blockers",
                    "satisfied" if not gate_blockers["full_validation"] else "missing",
                    summary="; ".join(gate_blockers["full_validation"]) or None,
                ),
            ],
        ),
        _gate_payload(
            gate_name="trading_room",
            gate_state=trading_state,
            assessed_at=assessed_at,
            blockers=gate_blockers["trading_room"],
            requirements=[
                _requirement(
                    "strategy_registry_ref_present",
                    "Strategy Registry reference exists in scoped workshop session",
                    "satisfied" if has_strategy_ref else "missing",
                ),
                _requirement(
                    "full_validation_ready",
                    "Full validation gate is ready",
                    "satisfied" if validation_state == "ready" else "partial",
                ),
                _requirement(
                    "trading_room_blockers_clear",
                    "No Trading Room hard blockers",
                    "satisfied" if not gate_blockers["trading_room"] else "missing",
                    summary="; ".join(gate_blockers["trading_room"]) or None,
                ),
            ],
        ),
    ]

    highest_ready_gate = None
    for gate_name in ("trading_room", "full_validation", "preliminary_research"):
        gate = next(item for item in gates if item["gate"] == gate_name)
        if gate["state"] == "ready":
            highest_ready_gate = gate_name
            break

    version = assessment_version or 1
    return _clean_optional({
        "spec_version": "1.0",
        "assessment_id": f"ready_{_safe_card_id(session['workshop_id'], version)}",
        "workshop_id": session["workshop_id"],
        "strategy_id": strategy_id,
        "workshop_version_id": workshop_version_id,
        "strategy_spec_registry_id": registry_id,
        "assessment_version": version,
        "gates": gates,
        "highest_ready_gate": highest_ready_gate,
        "staleness_reasons": [] if has_state else ["strategy_completeness_snapshot_missing"],
        "assessed_at": assessed_at,
        "evidence_refs": _build_evidence_refs(events, snapshot),
    })


# Backward compatibility alias for callers that historically imported the
# underscore-prefixed name.
_build_readiness_assessment = build_readiness_assessment
