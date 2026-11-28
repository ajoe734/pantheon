"""Deterministic shadow-arm settlement per decision (Design A4 & §12.3/§12.5).

Settles the four shadow comparison arms (``human_actual``, ``private_servant``,
``base_strategy``, ``committee_variant``) for an ``AgoraDecisionLock`` using a
single shared ``data_cutoff``, ``CostModelConfig``, and ``observation_window``
over governed market data and paper/broker fills.

Standing rules:
- No language model computes any number.
- ``pantheon_paper_proxy`` is never labelled as an actual fill (A4 §1).
- Missing cutoff, missing instrument, missing entry open price, or missing
  observation-window price fails closed and leaves the arm unsettled with an
  explicit ``unavailable_reason``.
- Higher-authority fills create a new outcome referencing
  ``supersedes_outcome_id`` rather than overwriting prior outcomes in place (A4 §3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services.execution.lean_runtime.taiwan_cash_costs import (
    COMMISSION_RATE as DEFAULT_COMMISSION_RATE,
    EQUITY_TAX_RATE as DEFAULT_EQUITY_TAX_RATE,
    ETF_TAX_RATE as DEFAULT_ETF_TAX_RATE,
)

CANONICAL_SHADOW_ARMS: Tuple[str, ...] = (
    "human_actual",
    "private_servant",
    "base_strategy",
    "committee_variant",
)
TRADER_IMITATION_ARM = "trader_imitation"
OPTIONAL_ALT_SHADOW_ARMS: Tuple[str, ...] = (TRADER_IMITATION_ARM,)

SETTLEMENT_SOURCES: Tuple[str, ...] = (
    "verified_broker_fill",
    "imported_broker_fill",
    "pantheon_paper_proxy",
    "manual_declared_outcome",
    "declared_no_trade",
)

SETTLEMENT_SOURCE_PRIORITY: Mapping[str, int] = {
    "verified_broker_fill": 1,
    "imported_broker_fill": 2,
    "pantheon_paper_proxy": 3,
    "manual_declared_outcome": 4,
    "declared_no_trade": 5,
}

A4_DISPLAY_LABELS: Mapping[str, str] = {
    "verified_broker_fill": "交易員實際成交（已驗證）",
    "imported_broker_fill": "交易員成交（匯入／待完整驗證）",
    "pantheon_paper_proxy": "交易員決策（紙上代理）",
    "manual_declared_outcome": "交易員手動回報結果",
    "declared_no_trade": "交易員選擇不交易",
}

A4_DEFAULT_CONFIDENCE: Mapping[str, float] = {
    "verified_broker_fill": 1.00,
    "imported_broker_fill": 0.80,
    "pantheon_paper_proxy": 0.75,
    "manual_declared_outcome": 0.40,
    "declared_no_trade": 1.00,
}

ACTUAL_FILL_SOURCES = frozenset({"verified_broker_fill", "imported_broker_fill"})

OBSERVATION_WINDOW_SESSIONS: Mapping[str, int] = {
    "1d": 1,
    "3d": 3,
    "5d": 5,
    "10d": 10,
    "20d": 20,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _finite_positive(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) and num > 0.0 else None


def _finite_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if math.isfinite(num) else None


def _normalize_symbol(symbol: Any) -> str:
    raw = _clean(symbol).upper()
    for suffix in (".TWSE", ".TPEX", ".TW", ".TWO"):
        if raw.endswith(suffix):
            return raw[: -len(suffix)]
    return raw


def _session_date(value: Any) -> str:
    text = _clean(value)
    return text[:10] if len(text) >= 10 else text


def _parse_iso_dt(value: Any) -> Optional[datetime]:
    text = _clean(value)
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class CostModelConfig:
    """Shared transaction cost model applied identically to every shadow arm."""

    model_id: str = "taiwan_cash_statutory_v1"
    commission_rate: float = DEFAULT_COMMISSION_RATE
    equity_tax_rate: float = DEFAULT_EQUITY_TAX_RATE
    etf_tax_rate: float = DEFAULT_ETF_TAX_RATE
    slippage_bps: float = 5.0
    default_notional: float = 100_000.0
    liquidate_at_window_end: bool = True

    def is_etf(self, symbol: str) -> bool:
        return _normalize_symbol(symbol).startswith("00")

    def tax_rate_for(self, symbol: str) -> float:
        return self.etf_tax_rate if self.is_etf(symbol) else self.equity_tax_rate

    def roundtrip_cost_fraction(
        self,
        symbol: str,
        *,
        direction: int,
        weight: float,
        liquidate_at_end: Optional[bool] = None,
    ) -> float:
        if direction == 0 or weight <= 0.0:
            return 0.0
        liquidate = self.liquidate_at_window_end if liquidate_at_end is None else liquidate_at_end
        slip = (self.slippage_bps / 10_000.0) * abs(weight)
        tax = self.tax_rate_for(symbol)
        # Entry cost: commission + slippage; exit cost (when liquidated or selling): commission + sell tax + slippage
        entry_cost = abs(weight) * self.commission_rate + slip
        if not liquidate:
            sell_tax = abs(weight) * tax if direction < 0 else 0.0
            return entry_cost + sell_tax
        exit_cost = abs(weight) * (self.commission_rate + tax) + slip
        return entry_cost + exit_cost


@dataclass(frozen=True)
class SettledArmRecord:
    """Settled or unsettled state of one shadow comparison arm for a decision."""

    decision_id: str
    arm: str
    settlement_source: Optional[str]
    outcome: Optional[Dict[str, Any]]
    settled_at: Optional[str]
    unavailable_reason: Optional[str] = None
    outcome_id: Optional[str] = None
    supersedes_outcome_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "arm": self.arm,
            "settlement_source": self.settlement_source,
            "outcome": dict(self.outcome) if isinstance(self.outcome, Mapping) else None,
            "settled_at": self.settled_at,
            "unavailable_reason": self.unavailable_reason,
        }
        if self.outcome_id is not None:
            payload["outcome_id"] = self.outcome_id
        if self.supersedes_outcome_id is not None:
            payload["supersedes_outcome_id"] = self.supersedes_outcome_id
        return payload


@dataclass(frozen=True)
class ShadowDecisionSettlement:
    """Complete 4-arm settlement record for one AgoraDecisionLock."""

    decision_id: str
    tenant_id: str
    agora_user_id: Optional[str]
    account_id: Optional[str]
    broker_ids: Tuple[str, ...]
    target_instrument: Optional[str]
    decision_time: str
    data_cutoff: Optional[str]
    cost_model: str
    observation_window: str
    arms: Tuple[SettledArmRecord, ...]
    settled_at: str
    trader_identity: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "tenant_id": self.tenant_id,
            "agora_user_id": self.agora_user_id,
            "account_id": self.account_id,
            "broker_ids": list(self.broker_ids),
            "trader_identity": dict(self.trader_identity),
            "target_instrument": self.target_instrument,
            "decision_time": self.decision_time,
            "data_cutoff": self.data_cutoff,
            "cost_model": self.cost_model,
            "observation_window": self.observation_window,
            "settled_at": self.settled_at,
            "arms": [arm.to_dict() for arm in self.arms],
        }


def map_action_to_direction_and_weight(
    action: Any,
    *,
    quantity: Any = None,
    weight: Any = None,
    reference_price: Optional[float] = None,
    default_notional: float = 100_000.0,
) -> Tuple[int, float, str]:
    """Map an A4 or Trading Room action/suggestion into (direction, weight, canonical_action)."""
    raw = _clean(action).lower()
    if not raw:
        return 0, 0.0, ""

    if raw in {"enter", "buy", "long", "approve", "bullish", "overweight"}:
        direction = 1
        canonical = "enter"
        default_w = 1.0
    elif raw in {"add", "increase", "scale_in"}:
        direction = 1
        canonical = "add"
        default_w = 1.25
    elif raw in {"reduce", "trim", "scale_out", "underweight"}:
        direction = 1
        canonical = "reduce"
        default_w = 0.5
    elif raw in {"exit", "sell", "short", "close", "bearish"}:
        direction = -1
        canonical = "exit"
        default_w = 1.0
    elif raw in {"hold", "keep", "maintain", "review"}:
        direction = 1
        canonical = "hold"
        default_w = 1.0
    elif raw in {"reject", "no_trade", "defer", "flat", "skip", "pass", "none", "shadow"}:
        return 0, 0.0, "no_trade"
    else:
        return 0, 0.0, raw

    explicit_w = _finite_positive(weight)
    if explicit_w is not None:
        resolved_w = min(3.0, max(0.05, explicit_w))
    else:
        qty = _finite_positive(quantity)
        if qty is not None and reference_price is not None and reference_price > 0 and default_notional > 0:
            resolved_w = min(3.0, max(0.05, (qty * reference_price) / default_notional))
        else:
            resolved_w = default_w
    return direction, resolved_w, canonical


map_action_to_exposure = map_action_to_direction_and_weight


def _extract_lock_arms(
    lock: Mapping[str, Any],
    *,
    imitation_candidate: Optional[Mapping[str, Any]] = None,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Tuple[str, ...]]:
    """Extract normalized arm specs and unavailable reasons from C4 or A4 decision locks."""
    raw_arms = lock.get("arms") if isinstance(lock.get("arms"), Mapping) else {}
    opt_arms = lock.get("optional_alt_versions") if isinstance(lock.get("optional_alt_versions"), Mapping) else {}
    unavail_reasons: Dict[str, str] = {}

    raw_unavail = lock.get("unavailable_arms")
    if isinstance(raw_unavail, Mapping):
        for k, v in raw_unavail.items():
            if _clean(k):
                unavail_reasons[_clean(k)] = _clean(v) or "arm_unavailable"
    elif isinstance(raw_unavail, Iterable) and not isinstance(raw_unavail, (str, bytes)):
        for item in raw_unavail:
            if isinstance(item, Mapping) and _clean(item.get("arm")):
                unavail_reasons[_clean(item["arm"])] = _clean(
                    item.get("reason") or item.get("unavailable_reason") or "arm_unavailable"
                )

    # Alias institutional_view -> committee_variant
    if "institutional_view" in unavail_reasons and "committee_variant" not in unavail_reasons:
        unavail_reasons["committee_variant"] = unavail_reasons["institutional_view"]

    arms_out: Dict[str, Dict[str, Any]] = {}

    # 1. human_actual
    human_raw = raw_arms.get("human_actual")
    if isinstance(human_raw, Mapping):
        if human_raw.get("available") is False:
            unavail_reasons.setdefault(
                "human_actual",
                _clean(human_raw.get("unavailable_reason") or human_raw.get("reason") or "human_actual_unavailable"),
            )
        else:
            arms_out["human_actual"] = {
                "action": _clean(
                    human_raw.get("action")
                    or human_raw.get("decision")
                    or lock.get("decision")
                    or lock.get("trading_room_verdict")
                ),
                "quantity": human_raw.get("quantity", lock.get("requested_quantity")),
                "weight": human_raw.get("weight", lock.get("requested_weight")),
                "price": human_raw.get("price", lock.get("requested_price_constraint")),
                "execution_observation_kind": _clean(
                    human_raw.get("execution_observation_kind") or lock.get("execution_observation_kind")
                ),
            }
    elif "human_actual" not in unavail_reasons:
        top_action = _clean(lock.get("decision") or lock.get("trading_room_verdict"))
        if top_action:
            arms_out["human_actual"] = {
                "action": top_action,
                "quantity": lock.get("requested_quantity"),
                "weight": lock.get("requested_weight"),
                "price": lock.get("requested_price_constraint"),
                "execution_observation_kind": _clean(lock.get("execution_observation_kind")),
            }
        else:
            unavail_reasons["human_actual"] = "missing_human_decision"

    # 2. private_servant, base_strategy, committee_variant
    for arm_name, fallback_key in (
        ("private_servant", None),
        ("base_strategy", None),
        ("committee_variant", "institutional_view"),
    ):
        arm_raw = raw_arms.get(arm_name)
        if arm_raw is None and fallback_key is not None:
            arm_raw = raw_arms.get(fallback_key)

        if arm_name in unavail_reasons:
            continue
        if arm_raw is None:
            unavail_reasons[arm_name] = f"no_{arm_name}_recorded"
            continue
        if isinstance(arm_raw, Mapping):
            if arm_raw.get("available") is False:
                unavail_reasons[arm_name] = _clean(
                    arm_raw.get("unavailable_reason") or arm_raw.get("reason") or f"{arm_name}_unavailable"
                )
                continue
            suggestion = _clean(
                arm_raw.get("suggestion")
                or arm_raw.get("suggested_action")
                or arm_raw.get("action")
                or arm_raw.get("decision")
                or arm_raw.get("verdict")
                or arm_raw.get("stance")
            )
            if not suggestion:
                unavail_reasons[arm_name] = _clean(
                    arm_raw.get("unavailable_reason") or arm_raw.get("reason") or f"{arm_name}_missing_suggestion"
                )
                continue
            arms_out[arm_name] = {
                "action": suggestion,
                "quantity": arm_raw.get("quantity", arm_raw.get("suggested_size")),
                "weight": arm_raw.get("weight", arm_raw.get("suggested_weight")),
                "price": arm_raw.get("price"),
                "persona_id": _clean(arm_raw.get("persona_id") or arm_raw.get("servant_persona_id")) or None,
                "strategy_ref": _clean(
                    arm_raw.get("strategy_ref") or arm_raw.get("strategy_version_id") or arm_raw.get("strategy_id")
                )
                or None,
                "meeting_id": _clean(arm_raw.get("meeting_id") or arm_raw.get("session_id")) or None,
            }
        else:
            unavail_reasons[arm_name] = f"{arm_name}_invalid_payload"

    # 3. optional_alt_versions: trader_imitation (T10)
    cand = imitation_candidate or (
        lock.get("trader_imitation_candidate")
        if isinstance(lock.get("trader_imitation_candidate"), Mapping)
        else None
    )
    imit_raw = (
        raw_arms.get(TRADER_IMITATION_ARM)
        if TRADER_IMITATION_ARM in raw_arms
        else opt_arms.get(TRADER_IMITATION_ARM)
    )
    has_imitation_arm = (
        TRADER_IMITATION_ARM in raw_arms
        or TRADER_IMITATION_ARM in opt_arms
        or TRADER_IMITATION_ARM in unavail_reasons
        or cand is not None
    )
    arm_order: Tuple[str, ...] = (
        CANONICAL_SHADOW_ARMS + (TRADER_IMITATION_ARM,)
        if has_imitation_arm
        else CANONICAL_SHADOW_ARMS
    )

    if has_imitation_arm and TRADER_IMITATION_ARM not in unavail_reasons:
        if isinstance(imit_raw, Mapping) and (
            imit_raw.get("policy_weights") or imit_raw.get("policy") or imit_raw.get("weights")
        ) and not _clean(
            imit_raw.get("action") or imit_raw.get("suggestion") or imit_raw.get("suggested_action")
        ):
            cand = imit_raw
        if cand is not None and (
            not isinstance(imit_raw, Mapping)
            or imit_raw.get("available") is False
            or not _clean(
                imit_raw.get("action")
                or imit_raw.get("suggestion")
                or imit_raw.get("suggested_action")
            )
        ):
            from services.agora.shadow_mode import predict_trader_imitation_action

            snap = (
                lock.get("decision_time_snapshot")
                if isinstance(lock.get("decision_time_snapshot"), Mapping)
                else {}
            )
            pred = predict_trader_imitation_action(cand, snapshot=snap, event=lock)
            if pred.get("available") is True:
                imit_raw = pred
            elif imit_raw is None:
                imit_raw = pred

        if imit_raw is None:
            unavail_reasons[TRADER_IMITATION_ARM] = "no_trader_imitation_recorded"
        elif isinstance(imit_raw, Mapping):
            if imit_raw.get("available") is False:
                unavail_reasons[TRADER_IMITATION_ARM] = _clean(
                    imit_raw.get("unavailable_reason")
                    or imit_raw.get("reason")
                    or "trader_imitation_unavailable"
                )
            else:
                suggestion = _clean(
                    imit_raw.get("suggestion")
                    or imit_raw.get("suggested_action")
                    or imit_raw.get("action")
                    or imit_raw.get("decision")
                    or imit_raw.get("verdict")
                    or imit_raw.get("stance")
                )
                if not suggestion:
                    unavail_reasons[TRADER_IMITATION_ARM] = _clean(
                        imit_raw.get("unavailable_reason")
                        or imit_raw.get("reason")
                        or "trader_imitation_missing_suggestion"
                    )
                else:
                    arms_out[TRADER_IMITATION_ARM] = {
                        "action": suggestion,
                        "quantity": imit_raw.get("quantity", imit_raw.get("suggested_size")),
                        "weight": imit_raw.get("weight", imit_raw.get("suggested_weight")),
                        "price": imit_raw.get("price"),
                        "candidate_id": _clean(imit_raw.get("candidate_id")) or None,
                        "candidate_version": imit_raw.get("candidate_version"),
                        "strategy_ref": _clean(
                            imit_raw.get("strategy_ref") or imit_raw.get("strategy_id")
                        )
                        or None,
                        "confidence": imit_raw.get("confidence"),
                    }
        else:
            unavail_reasons[TRADER_IMITATION_ARM] = "trader_imitation_invalid_payload"

    return arms_out, unavail_reasons, arm_order


def _match_fills_for_arm(
    decision_id: str,
    arm_name: str,
    paper_fills: Sequence[Mapping[str, Any]],
) -> List[Mapping[str, Any]]:
    matched: List[Mapping[str, Any]] = []
    aliases = {arm_name}
    if arm_name == "human_actual":
        aliases.update({"trader", "human", ""})
    elif arm_name == "committee_variant":
        aliases.add("institutional_view")

    for fill in paper_fills or ():
        if not isinstance(fill, Mapping):
            continue
        fid = _clean(fill.get("decision_id") or fill.get("human_decision_id"))
        if fid != decision_id:
            continue
        farm = _clean(fill.get("arm") or fill.get("arm_name"))
        if farm in aliases or (not farm and arm_name == "human_actual"):
            matched.append(fill)
    return matched


def _resolve_settlement_source(
    arm_name: str,
    arm_spec: Mapping[str, Any],
    canonical_action: str,
    matched_fills: Sequence[Mapping[str, Any]],
) -> Tuple[str, Optional[Mapping[str, Any]], bool]:
    """Resolve the highest-priority settlement source per A4 §3."""
    if canonical_action == "no_trade":
        return "declared_no_trade", None, True

    best_fill: Optional[Mapping[str, Any]] = None
    best_source: Optional[str] = None
    best_rank = 999

    for fill in matched_fills:
        src = _clean(
            fill.get("settlement_source")
            or fill.get("execution_observation_kind")
            or ("verified_broker_fill" if fill.get("verified_broker") else "pantheon_paper_proxy")
        )
        if src not in SETTLEMENT_SOURCE_PRIORITY:
            src = "pantheon_paper_proxy"
        rank = SETTLEMENT_SOURCE_PRIORITY[src]
        if rank < best_rank:
            best_rank = rank
            best_source = src
            best_fill = fill

    if best_source is not None and best_fill is not None:
        verified = bool(
            best_fill.get("verified")
            if best_fill.get("verified") is not None
            else (best_source == "verified_broker_fill")
        )
        return best_source, best_fill, verified

    if arm_name == "human_actual":
        declared = _clean(arm_spec.get("execution_observation_kind"))
        if declared == "declared_no_trade":
            return "declared_no_trade", None, True
        if declared in SETTLEMENT_SOURCE_PRIORITY:
            return declared, None, (declared == "verified_broker_fill")

    return "pantheon_paper_proxy", None, False


def _outcome_id(decision_id: str, arm_name: str, settlement_source: str, cutoff: str, window: str) -> str:
    digest = hashlib.sha256(
        f"{decision_id}|{arm_name}|{settlement_source}|{cutoff}|{window}".encode("utf-8")
    ).hexdigest()[:16]
    return f"out-{decision_id}-{arm_name}-{digest}"


def settle_decision_lock(
    lock: Mapping[str, Any],
    *,
    market_rows: Sequence[Mapping[str, Any]],
    paper_fills: Sequence[Mapping[str, Any]] = (),
    cost_model: Optional[CostModelConfig] = None,
    observation_window: str = "5d",
    window_sessions: Optional[int] = None,
    settled_at: Optional[str] = None,
    prior_settlement: Optional[Mapping[str, Any] | ShadowDecisionSettlement] = None,
    imitation_candidate: Optional[Mapping[str, Any]] = None,
) -> ShadowDecisionSettlement:
    """Deterministically settle all shadow arms of a decision lock (including trader_imitation)."""
    decision_id = (
        _clean(lock.get("decision_id"))
        or _clean(lock.get("human_decision_id"))
        or _clean(lock.get("record_id"))
    )
    if not decision_id:
        raise ValueError("decision lock requires decision_id or human_decision_id")

    tenant_id = _clean(lock.get("tenant_id")) or "default"
    trader_identity = (
        dict(lock["trader_identity"])
        if isinstance(lock.get("trader_identity"), Mapping)
        else {"tenant_id": tenant_id, "user_id": _clean(lock.get("agora_user_id") or lock.get("user_id"))}
    )
    agora_user_id = (
        _clean(lock.get("agora_user_id"))
        or _clean(lock.get("user_id"))
        or _clean(trader_identity.get("agora_user_id") or trader_identity.get("user_id"))
        or None
    )
    account_id = _clean(lock.get("account_id")) or None
    raw_brokers = lock.get("broker_ids")
    broker_ids = (
        tuple(str(b).strip() for b in raw_brokers if str(b).strip())
        if isinstance(raw_brokers, Iterable) and not isinstance(raw_brokers, (str, bytes))
        else ()
    )
    decision_time = _clean(lock.get("decision_time") or lock.get("created_at")) or _utc_now()
    data_cutoff = _clean(lock.get("data_cutoff") or lock.get("available_data_cutoff")) or None
    subject_obj = lock.get("subject") if isinstance(lock.get("subject"), Mapping) else {}
    target_instrument = (
        _clean(lock.get("target_instrument") or lock.get("symbol") or lock.get("instrument") or subject_obj.get("symbol"))
        or None
    )

    costs = cost_model or CostModelConfig()
    req_sessions = window_sessions if window_sessions is not None else OBSERVATION_WINDOW_SESSIONS.get(observation_window, 5)
    if req_sessions < 1:
        raise ValueError("observation window requires at least 1 trading session")
    stamp = _clean(settled_at) or _utc_now()

    arms_specs, unavail_reasons, arm_order = _extract_lock_arms(
        lock,
        imitation_candidate=imitation_candidate,
    )

    # Prior outcomes lookup for A4 §3 superseding
    prior_arms: Dict[str, Mapping[str, Any]] = {}
    if isinstance(prior_settlement, ShadowDecisionSettlement):
        for pa in prior_settlement.arms:
            prior_arms[pa.arm] = pa.to_dict()
    elif isinstance(prior_settlement, Mapping):
        for pa in prior_settlement.get("arms") or ():
            if isinstance(pa, Mapping) and _clean(pa.get("arm")):
                prior_arms[_clean(pa["arm"])] = pa

    def _unsettled_all(reason: str) -> ShadowDecisionSettlement:
        records = tuple(
            SettledArmRecord(
                decision_id=decision_id,
                arm=arm_name,
                settlement_source=None,
                outcome=None,
                settled_at=None,
                unavailable_reason=unavail_reasons.get(arm_name, reason),
            )
            for arm_name in arm_order
        )
        return ShadowDecisionSettlement(
            decision_id=decision_id,
            tenant_id=tenant_id,
            agora_user_id=agora_user_id,
            account_id=account_id,
            broker_ids=broker_ids,
            target_instrument=target_instrument,
            decision_time=decision_time,
            data_cutoff=data_cutoff,
            cost_model=costs.model_id,
            observation_window=observation_window,
            arms=records,
            settled_at=stamp,
            trader_identity=trader_identity,
        )

    if not data_cutoff:
        return _unsettled_all("missing_data_cutoff")
    if not target_instrument:
        return _unsettled_all("missing_target_instrument")

    norm_target = _normalize_symbol(target_instrument)
    cutoff_date = _session_date(data_cutoff)
    cutoff_dt = _parse_iso_dt(data_cutoff)

    symbol_bars: List[Dict[str, Any]] = []
    pit_violation = False
    for row in market_rows or ():
        if not isinstance(row, Mapping):
            continue
        row_sym = _normalize_symbol(
            row.get("symbol") or row.get("instrument") or row.get("entity") or row.get("target_instrument")
        )
        if row_sym != norm_target:
            continue
        bar_date = _session_date(row.get("date") or row.get("session") or row.get("event_time"))
        if not bar_date:
            continue
        avail_raw = _clean(row.get("available_time") or row.get("available_at"))
        if bar_date <= cutoff_date and avail_raw and cutoff_dt is not None:
            avail_dt = _parse_iso_dt(avail_raw)
            if avail_dt is not None and avail_dt > cutoff_dt:
                pit_violation = True
                continue
        symbol_bars.append(
            {
                "date": bar_date,
                "open": _finite_positive(row.get("open")),
                "high": _finite_positive(row.get("high")),
                "low": _finite_positive(row.get("low")),
                "close": _finite_positive(row.get("close") if "close" in row else row.get("price")),
                "source_ref": _clean(row.get("source_ref") or row.get("dataset_id") or "governed_market_data"),
            }
        )

    if not symbol_bars:
        return _unsettled_all("pit_violation_future_data" if pit_violation else "missing_market_data")

    symbol_bars.sort(key=lambda b: b["date"])
    pre_bars = [b for b in symbol_bars if b["date"] <= cutoff_date]
    post_bars = [b for b in symbol_bars if b["date"] > cutoff_date]

    if not post_bars:
        return _unsettled_all("missing_market_data_after_cutoff")
    if len(post_bars) < req_sessions:
        return _unsettled_all("missing_observation_window_price")

    window_bars = post_bars[:req_sessions]
    if any(b["close"] is None for b in window_bars):
        return _unsettled_all("missing_price_in_observation_window")

    decision_close = pre_bars[-1]["close"] if pre_bars and pre_bars[-1]["close"] is not None else None
    entry_session_open = window_bars[0]["open"]
    benchmark_exit_close = window_bars[-1]["close"]
    assert benchmark_exit_close is not None

    settled_records: List[SettledArmRecord] = []
    for arm_name in arm_order:
        if arm_name in unavail_reasons or arm_name not in arms_specs:
            settled_records.append(
                SettledArmRecord(
                    decision_id=decision_id,
                    arm=arm_name,
                    settlement_source=None,
                    outcome=None,
                    settled_at=None,
                    unavailable_reason=unavail_reasons.get(arm_name, f"{arm_name}_unavailable"),
                )
            )
            continue

        spec = arms_specs[arm_name]
        matched_fills = _match_fills_for_arm(decision_id, arm_name, paper_fills)
        ref_price_for_qty = entry_session_open or decision_close or benchmark_exit_close
        direction, weight, canonical_action = map_action_to_exposure(
            spec.get("action"),
            quantity=spec.get("quantity"),
            weight=spec.get("weight"),
            reference_price=ref_price_for_qty,
            default_notional=costs.default_notional,
        )
        if not canonical_action:
            settled_records.append(
                SettledArmRecord(
                    decision_id=decision_id,
                    arm=arm_name,
                    settlement_source=None,
                    outcome=None,
                    settled_at=None,
                    unavailable_reason=f"unsupported_action:{spec.get('action')}",
                )
            )
            continue

        settlement_source, chosen_fill, verified = _resolve_settlement_source(
            arm_name, spec, canonical_action, matched_fills
        )

        # Resolve entry and exit prices; missing price fails closed per Acceptance 1.
        fill_entry_price = (
            _finite_positive(chosen_fill.get("fill_price") or chosen_fill.get("entry_price") or chosen_fill.get("price"))
            if chosen_fill is not None
            else None
        )
        fill_exit_price = (
            _finite_positive(chosen_fill.get("exit_price"))
            if chosen_fill is not None
            else None
        )
        entry_price = fill_entry_price if fill_entry_price is not None else entry_session_open
        if entry_price is None:
            settled_records.append(
                SettledArmRecord(
                    decision_id=decision_id,
                    arm=arm_name,
                    settlement_source=None,
                    outcome=None,
                    settled_at=None,
                    unavailable_reason="missing_entry_open_price",
                )
            )
            continue

        exit_price = fill_exit_price if fill_exit_price is not None else benchmark_exit_close
        dec_price = decision_close if decision_close is not None else entry_price
        underlying_return_pct = (benchmark_exit_close / entry_price) - 1.0

        if direction == 0 or weight == 0.0 or settlement_source == "declared_no_trade":
            direction = 0
            weight = 0.0
            canonical_action = "no_trade"
            gross_return_pct = 0.0
            cost_pct = 0.0
            net_return_pct = 0.0
            max_drawdown_pct = 0.0
            turnover = 0.0
            slippage_bps = 0.0
        else:
            raw_move = (exit_price / entry_price) - 1.0
            gross_return_pct = direction * weight * raw_move
            cost_pct = costs.roundtrip_cost_fraction(
                target_instrument,
                direction=direction,
                weight=weight,
            )
            net_return_pct = gross_return_pct - cost_pct
            turnover = abs(weight) * (2.0 if costs.liquidate_at_window_end else 1.0)
            slippage_bps = (
                float(_finite_number(chosen_fill.get("slippage_bps")) or costs.slippage_bps)
                if chosen_fill is not None
                else costs.slippage_bps
            )
            # Track path drawdown across window bars
            equity = 1.0
            peak = 1.0
            max_drawdown_pct = 0.0
            prev_p = entry_price
            for b in window_bars:
                close_p = b["close"]
                assert close_p is not None
                step_ret = direction * weight * ((close_p / prev_p) - 1.0)
                equity *= max(1e-6, 1.0 + step_ret)
                peak = max(peak, equity)
                max_drawdown_pct = max(max_drawdown_pct, (peak - equity) / peak)
                prev_p = close_p

        confidence = A4_DEFAULT_CONFIDENCE.get(settlement_source, 0.75)
        if settlement_source == "imported_broker_fill" and verified:
            confidence = 0.95

        # Paper proxy is NEVER labelled as an actual fill (A4 §1).
        actual_fill_claimed = (
            False
            if settlement_source == "pantheon_paper_proxy"
            else (settlement_source in ACTUAL_FILL_SOURCES)
        )
        fill_refs = (
            [_clean(chosen_fill.get("fill_id") or chosen_fill.get("id"))]
            if chosen_fill is not None and _clean(chosen_fill.get("fill_id") or chosen_fill.get("id"))
            else []
        )
        source_refs = sorted({b["source_ref"] for b in window_bars if b.get("source_ref")})

        out_id = _outcome_id(decision_id, arm_name, settlement_source, data_cutoff, observation_window)
        supersedes_id: Optional[str] = None
        prior_arm = prior_arms.get(arm_name)
        if isinstance(prior_arm, Mapping) and prior_arm.get("settlement_source"):
            prior_src = _clean(prior_arm.get("settlement_source"))
            prior_out = prior_arm.get("outcome") if isinstance(prior_arm.get("outcome"), Mapping) else {}
            prior_oid = _clean(prior_arm.get("outcome_id") or prior_out.get("outcome_id"))
            if (
                prior_src in SETTLEMENT_SOURCE_PRIORITY
                and SETTLEMENT_SOURCE_PRIORITY[settlement_source] < SETTLEMENT_SOURCE_PRIORITY[prior_src]
                and prior_oid
            ):
                supersedes_id = prior_oid

        outcome_payload: Dict[str, Any] = {
            "outcome_id": out_id,
            "action": canonical_action,
            "direction": direction,
            "weight": round(weight, 6),
            "exposure": round(direction * weight, 6),
            "decision_price": round(dec_price, 6),
            "entry_price": round(entry_price, 6),
            "exit_price": round(exit_price, 6),
            "benchmark_exit_price": round(benchmark_exit_close, 6),
            "underlying_return_pct": round(underlying_return_pct, 6),
            "gross_return_pct": round(gross_return_pct, 6),
            "cost_pct": round(cost_pct, 6),
            "return_pct": round(net_return_pct, 6),
            "realized_pnl": round(net_return_pct * costs.default_notional, 2),
            "unrealized_pnl": 0.0,
            "max_drawdown_pct": round(max_drawdown_pct, 6),
            "slippage_bps": round(slippage_bps, 2),
            "turnover": round(turnover, 6),
            "confidence": confidence,
            "verified": verified,
            "actual_fill_claimed": actual_fill_claimed,
            "execution_observation_kind": settlement_source,
            "execution_display_label": A4_DISPLAY_LABELS[settlement_source],
            "observation_window": observation_window,
            "window_sessions": req_sessions,
            "window_start_date": window_bars[0]["date"],
            "window_end_date": window_bars[-1]["date"],
            "data_cutoff": data_cutoff,
            "cost_model": costs.model_id,
            "fill_refs": fill_refs,
            "source_refs": source_refs,
        }
        if supersedes_id is not None:
            outcome_payload["supersedes_outcome_id"] = supersedes_id

        settled_records.append(
            SettledArmRecord(
                decision_id=decision_id,
                arm=arm_name,
                settlement_source=settlement_source,
                outcome=outcome_payload,
                settled_at=stamp,
                unavailable_reason=None,
                outcome_id=out_id,
                supersedes_outcome_id=supersedes_id,
            )
        )

    return ShadowDecisionSettlement(
        decision_id=decision_id,
        tenant_id=tenant_id,
        agora_user_id=agora_user_id,
        account_id=account_id,
        broker_ids=broker_ids,
        target_instrument=target_instrument,
        decision_time=decision_time,
        data_cutoff=data_cutoff,
        cost_model=costs.model_id,
        observation_window=observation_window,
        arms=tuple(settled_records),
        settled_at=stamp,
        trader_identity=trader_identity,
    )


__all__ = [
    "A4_DEFAULT_CONFIDENCE",
    "A4_DISPLAY_LABELS",
    "ACTUAL_FILL_SOURCES",
    "CANONICAL_SHADOW_ARMS",
    "CostModelConfig",
    "OBSERVATION_WINDOW_SESSIONS",
    "OPTIONAL_ALT_SHADOW_ARMS",
    "SETTLEMENT_SOURCES",
    "SETTLEMENT_SOURCE_PRIORITY",
    "SettledArmRecord",
    "ShadowDecisionSettlement",
    "TRADER_IMITATION_ARM",
    "map_action_to_exposure",
    "settle_decision_lock",
]
