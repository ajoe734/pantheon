"""Agora shadow mode: immutable decision lock construction (design A4, §12.3).

Pure functions only. A decision lock records, at the moment a trader decides on a
Trading Room decision event, what the trader chose and what every other arm
(base strategy, private servant, institutional view) said, sharing one decision
time and one data cutoff. Nothing here trades, moves capital, computes an
attribution number, or calls a language model. Settlement belongs to the
attribution task. Missing arms are recorded as unavailable with a reason.

Identity, account, broker ids and raw text are kept as recorded (operator
decision, 2026-11-16); the lock is readable by operators and by that trader.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

_logger = logging.getLogger("agora.shadow_mode")

LOCK_SCHEMA = "AgoraHumanDecisionLock.v1"
LOCK_RESOURCE = "AgoraDecisionLock"

EXECUTION_OBSERVATION_KINDS = (
    "verified_broker_fill",
    "imported_broker_fill",
    "pantheon_paper_proxy",
    "manual_declared_outcome",
    "declared_no_trade",
)
# Kinds the decision route may accept from a trader at lock time. A verified or
# imported broker fill needs a governed integration / checksum review that the
# decision route cannot perform, so it fails closed here.
TRADER_DECLARABLE_KINDS = (
    "pantheon_paper_proxy",
    "manual_declared_outcome",
    "declared_no_trade",
)

# A4 §1 display names. Proxy is never shown as an actual fill.
DISPLAY_LABELS = {
    "verified_broker_fill": "交易員實際成交（已驗證）",
    "imported_broker_fill": "交易員成交（匯入／待完整驗證）",
    "pantheon_paper_proxy": "交易員決策（紙上代理）",
    "manual_declared_outcome": "交易員手動回報結果",
    "declared_no_trade": "交易員選擇不交易",
}
# A4 §6 default confidence at lock time (coordinator proposals).
DEFAULT_CONFIDENCE = {
    "verified_broker_fill": 1.0,
    "imported_broker_fill": 0.8,
    "pantheon_paper_proxy": 0.75,
    "manual_declared_outcome": 0.4,
    "declared_no_trade": 1.0,
}
ACTUAL_FILL_KINDS = ("verified_broker_fill", "imported_broker_fill")

_EVENT_KIND_TO_ACTION = {
    "entry": "enter",
    "add": "add",
    "reduce": "reduce",
    "exit": "exit",
    "review": "hold",
}

DECISION_EVIDENCE_KINDS = (
    "decision",
    "rejection",
    "deferral",
    "intervention",
)

VERDICT_TO_EVIDENCE_KIND = {
    "approve": "decision",
    "reject": "rejection",
    "defer": "deferral",
    "shadow": "deferral",
    "modify": "intervention",
}


class DecisionLockExistsError(ValueError):
    """A lock already exists for this decision; locks are immutable."""

    def __init__(self, decision_event_id: str) -> None:
        super().__init__(f"decision lock already exists for {decision_event_id!r}")
        self.decision_event_id = decision_event_id
        self.status_code = 409
        self.error_code = "DECISION_LOCK_EXISTS"


class DecisionLockInvalidError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = 422
        self.error_code = "DECISION_LOCK_INVALID"


def unavailable(reason: str) -> Dict[str, Any]:
    return {"available": False, "unavailable_reason": reason}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def a4_decision(body_decision: str, event_kind: str) -> str:
    """Map the Trading Room verdict to the A4 decision vocabulary."""
    if body_decision in ("approve", "modify"):
        return _EVENT_KIND_TO_ACTION.get(event_kind, "hold")
    if body_decision == "reject":
        return "reject"
    if body_decision == "defer":
        return "defer"
    if body_decision == "shadow":
        return "no_trade"
    raise DecisionLockInvalidError(f"unsupported_decision:{body_decision}")


def decision_evidence_kind(
    body_decision: str,
    modifications: Optional[Dict[str, Any]] = None,
) -> str:
    """Map a Trading Room verdict and modifications to one of the four learning evidence kinds."""
    if body_decision == "modify" or (body_decision in ("approve", "shadow") and bool(modifications)):
        return "intervention"
    if body_decision == "reject":
        return "rejection"
    if body_decision in ("defer", "shadow"):
        return "deferral"
    if body_decision == "approve":
        return "decision"
    raise DecisionLockInvalidError(f"unsupported_decision:{body_decision}")


def build_decision_time_snapshot(
    event: Dict[str, Any],
    *,
    decision_time: str,
    data_cutoff: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture the deterministic decision-time market and signal snapshot from a decision event."""
    subject = event.get("subject") if isinstance(event.get("subject"), dict) else {}
    raw_snap = (
        event.get("decision_time_snapshot")
        if isinstance(event.get("decision_time_snapshot"), dict)
        else (
            event.get("market_snapshot")
            if isinstance(event.get("market_snapshot"), dict)
            else (event.get("snapshot") if isinstance(event.get("snapshot"), dict) else {})
        )
    )
    cutoff = (
        _clean(data_cutoff)
        or _clean(event.get("data_cutoff"))
        or _clean(raw_snap.get("available_data_cutoff"))
        or None
    )
    symbol = (
        _clean(subject.get("symbol"))
        or _clean(event.get("target_instrument"))
        or _clean(raw_snap.get("symbol"))
        or None
    )
    snapshot: Dict[str, Any] = {
        **json.loads(json.dumps(raw_snap, default=str)),
        "decision_time": decision_time,
        "available_data_cutoff": cutoff,
        "triggered_at": _clean(event.get("triggered_at")) or raw_snap.get("triggered_at"),
        "symbol": symbol,
        "subject": json.loads(json.dumps(subject, default=str)) if subject else raw_snap.get("subject", {}),
        "strategy_id": _clean(event.get("strategy_id")) or raw_snap.get("strategy_id"),
        "strategy_version_id": (
            _clean(event.get("strategy_version_id") or event.get("strategy_version"))
            or raw_snap.get("strategy_version_id")
        ),
        "strategy_spec_registry_id": (
            _clean(event.get("strategy_spec_registry_id"))
            or raw_snap.get("strategy_spec_registry_id")
        ),
        "event_kind": _clean(event.get("event_kind")) or raw_snap.get("event_kind"),
        "origin": _clean(event.get("origin")) or raw_snap.get("origin"),
        "suggested_action": _clean(event.get("suggested_action")) or raw_snap.get("suggested_action"),
        "suggested_size": (
            event.get("suggested_size")
            if event.get("suggested_size") is not None
            else raw_snap.get("suggested_size")
        ),
        "confidence": (
            json.loads(json.dumps(event["confidence"], default=str))
            if isinstance(event.get("confidence"), dict)
            else event.get("confidence", raw_snap.get("confidence"))
        ),
        "probability": (
            json.loads(json.dumps(event["probability"], default=str))
            if isinstance(event.get("probability"), dict)
            else event.get("probability", raw_snap.get("probability"))
        ),
        "expected_value": (
            json.loads(json.dumps(event["expected_value"], default=str))
            if isinstance(event.get("expected_value"), dict)
            else event.get("expected_value", raw_snap.get("expected_value"))
        ),
    }
    return snapshot


def resolve_observation_kind(body_decision: str, declared: Optional[str]) -> str:
    if declared:
        if declared not in TRADER_DECLARABLE_KINDS:
            raise DecisionLockInvalidError(
                f"execution_observation_kind_not_declarable:{declared}"
            )
        if body_decision in ("reject", "defer", "shadow") and declared != "declared_no_trade":
            raise DecisionLockInvalidError("no_trade_decision_requires_declared_no_trade")
        return declared
    # Default: approve/modify map to the paper proxy path (A4 §3 item 3); the
    # lock itself carries no fill. reject/defer/shadow produce no order.
    return "pantheon_paper_proxy" if body_decision in ("approve", "modify") else "declared_no_trade"


def _arm_base_strategy(event: Dict[str, Any]) -> Dict[str, Any]:
    strategy_id = _clean(event.get("strategy_id"))
    if not strategy_id:
        return unavailable("event_has_no_strategy_id")
    version = _clean(
        event.get("strategy_version_id") or event.get("strategy_version")
    )
    arm: Dict[str, Any] = {
        "available": True,
        "strategy_id": strategy_id,
        "strategy_spec_registry_id": _clean(event.get("strategy_spec_registry_id")) or None,
        "suggested_action": event.get("suggested_action"),
        "suggested_size": event.get("suggested_size"),
        "origin": event.get("origin"),
    }
    if version:
        arm["strategy_version_id"] = version
    else:
        arm["strategy_version_id"] = None
        arm["strategy_version_unavailable_reason"] = "event_has_no_strategy_version"
    return arm


def _arm_from_event_key(event: Dict[str, Any], keys: Iterable[str], reason: str) -> Dict[str, Any]:
    for key in keys:
        value = event.get(key)
        if isinstance(value, dict) and value:
            return {"available": True, "source_field": key, **json.loads(json.dumps(value))}
    return unavailable(reason)


_IMITATION_ACTION_CODE_MAP: Dict[str, float] = {
    "enter": 1.0,
    "entry": 1.0,
    "buy": 1.0,
    "add": 0.5,
    "increase": 0.5,
    "hold": 0.0,
    "review": 0.0,
    "defer": -0.25,
    "reduce": -0.5,
    "trim": -0.5,
    "exit": -1.0,
    "sell": -1.0,
    "reject": -1.0,
}


def _observation_for_imitation(event: Mapping[str, Any], snapshot: Mapping[str, Any]) -> List[float]:
    for candidate in (event.get("observation"), snapshot.get("observation")):
        if isinstance(candidate, Sequence) and not isinstance(candidate, (str, bytes)) and candidate:
            if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in candidate):
                return [float(x) for x in candidate]

    conf = snapshot.get("confidence") if snapshot.get("confidence") is not None else event.get("confidence")
    if isinstance(conf, Mapping) and isinstance(conf.get("value"), (int, float)) and not isinstance(conf.get("value"), bool):
        conf_val = float(conf["value"])
    elif isinstance(conf, (int, float)) and not isinstance(conf, bool):
        conf_val = float(conf)
    else:
        conf_val = 0.75

    prob = snapshot.get("probability") if snapshot.get("probability") is not None else event.get("probability")
    if isinstance(prob, Mapping) and isinstance(prob.get("value"), (int, float)) and not isinstance(prob.get("value"), bool):
        prob_val = float(prob["value"])
    elif isinstance(prob, (int, float)) and not isinstance(prob, bool):
        prob_val = float(prob)
    else:
        prob_val = 0.5

    ev = snapshot.get("expected_value") if snapshot.get("expected_value") is not None else event.get("expected_value")
    if isinstance(ev, Mapping):
        net_raw = ev.get("net")
        down_raw = ev.get("downside")
        net_val = float(net_raw) if isinstance(net_raw, (int, float)) and not isinstance(net_raw, bool) else 0.0
        down_val = float(down_raw) if isinstance(down_raw, (int, float)) and not isinstance(down_raw, bool) else 0.0
    else:
        net_val = 0.0
        down_val = 0.0

    action_token = _clean(
        snapshot.get("suggested_action") or snapshot.get("event_kind") or event.get("suggested_action")
    ).lower()
    action_code = _IMITATION_ACTION_CODE_MAP.get(action_token, 0.0)
    return [conf_val, prob_val, net_val, down_val, action_code]


def predict_trader_imitation_action(
    candidate_or_policy: Mapping[str, Any],
    *,
    snapshot: Optional[Mapping[str, Any]] = None,
    event: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically evaluate a trained trader behaviour candidate on decision-time inputs."""
    if not isinstance(candidate_or_policy, Mapping):
        return unavailable("invalid_trader_imitation_candidate")
    for wrap_key in ("candidate", "active_candidate"):
        if wrap_key in candidate_or_policy and not (
            candidate_or_policy.get("policy_weights")
            or candidate_or_policy.get("policy")
            or candidate_or_policy.get("artifact")
            or candidate_or_policy.get("weights")
        ):
            inner = candidate_or_policy.get(wrap_key)
            if not isinstance(inner, Mapping):
                return unavailable("no_trader_imitation_candidate_on_decision_event")
            candidate_or_policy = inner
            break

    policy: Mapping[str, Any] = candidate_or_policy
    for key in ("policy_weights", "policy"):
        sub = candidate_or_policy.get(key)
        if isinstance(sub, Mapping) and sub.get("weights") and sub.get("action_labels"):
            policy = sub
            break
    if not (policy.get("weights") and policy.get("action_labels")):
        art = candidate_or_policy.get("artifact")
        if isinstance(art, Mapping) and isinstance(art.get("policy"), Mapping):
            policy = art["policy"]
        elif isinstance(art, Mapping) and isinstance(art.get("model"), Mapping):
            model = art["model"]
            m_weights = model.get("weights")
            m_biases = model.get("biases")
            m_actions = [str(a).strip() for a in (model.get("actions") or []) if str(a).strip()]
            if isinstance(m_weights, Mapping) and m_actions:
                feat_names = ["confidence", "probability", "expected_net", "expected_downside", "suggested_action_code"]
                converted_w: List[List[float]] = []
                converted_b: List[float] = []
                for act in m_actions:
                    row_map = m_weights.get(act) if isinstance(m_weights.get(act), Mapping) else {}
                    row_vec = [
                        float(row_map.get("confidence", 0.0) or 0.0),
                        float(row_map.get("probability", row_map.get("risk_score", 0.0)) or 0.0),
                        float(row_map.get("expected_net", 0.0) or 0.0),
                        float(row_map.get("expected_downside", 0.0) or 0.0),
                        float(row_map.get("suggested_action_code", 0.0) or 0.0),
                    ]
                    converted_w.append(row_vec)
                    b_val = float((m_biases or {}).get(act, 0.0) or 0.0) if isinstance(m_biases, Mapping) else 0.0
                    converted_b.append(b_val)
                policy = {
                    "action_labels": m_actions,
                    "weights": converted_w,
                    "bias": converted_b,
                    "feature_names": feat_names,
                }

    weights = policy.get("weights")
    bias = policy.get("bias")
    action_labels = [str(a).strip() for a in (policy.get("action_labels") or []) if str(a).strip()]
    if (
        not isinstance(weights, Sequence)
        or not isinstance(bias, Sequence)
        or not action_labels
        or len(weights) != len(action_labels)
        or len(bias) != len(action_labels)
    ):
        return unavailable("candidate_missing_policy_weights")

    event_map = event if isinstance(event, Mapping) else {}
    snap_map = (
        snapshot
        if isinstance(snapshot, Mapping)
        else (
            event_map.get("decision_time_snapshot")
            if isinstance(event_map.get("decision_time_snapshot"), Mapping)
            else {}
        )
    )
    observation = _observation_for_imitation(event_map, snap_map)

    logits: List[float] = []
    for row, offset in zip(weights, bias):
        if not isinstance(row, Sequence):
            return unavailable("candidate_malformed_weight_row")
        dot = sum(float(w) * float(x) for w, x in zip(row, observation))
        logits.append(dot + float(offset))

    max_logit = max(logits)
    exps = [math.exp(l - max_logit) for l in logits]
    denom = sum(exps)
    if denom <= 0.0:
        return unavailable("candidate_zero_probability_norm")

    probabilities = {label: val / denom for label, val in zip(action_labels, exps)}
    predicted_action = max(probabilities.items(), key=lambda kv: (round(kv[1], 12), kv[0]))[0]
    predicted_conf = probabilities[predicted_action]

    return {
        "available": True,
        "arm": "trader_imitation",
        "arm_kind": "optional_alt_version",
        "source_field": "trader_imitation_candidate",
        "action": predicted_action,
        "suggested_action": predicted_action,
        "suggestion": predicted_action,
        "confidence": round(predicted_conf, 6),
        "action_probabilities": {k: round(v, 6) for k, v in probabilities.items()},
        "observation": [round(float(x), 6) for x in observation],
        "candidate_id": _clean(candidate_or_policy.get("candidate_id") or candidate_or_policy.get("id")) or None,
        "candidate_version": candidate_or_policy.get("candidate_version") or candidate_or_policy.get("version"),
        "strategy_id": _clean(candidate_or_policy.get("strategy_id")) or None,
        "decision_count": candidate_or_policy.get("decision_count"),
        "artifact_checksum": _clean(
            candidate_or_policy.get("artifact_checksum") or candidate_or_policy.get("checksum")
        )
        or None,
    }


def _arm_trader_imitation(
    event: Dict[str, Any],
    *,
    snapshot: Dict[str, Any],
    trader_imitation_candidate: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    candidate = (
        trader_imitation_candidate
        or event.get("trader_imitation_candidate")
        or event.get("imitation_candidate")
    )
    if isinstance(candidate, Mapping):
        if candidate.get("available") is False:
            return unavailable(
                _clean(
                    candidate.get("unavailable_reason")
                    or candidate.get("reason")
                    or "no_trader_imitation_candidate_on_decision_event"
                )
            )
        pred = predict_trader_imitation_action(candidate, snapshot=snapshot, event=event)
        if pred.get("available") is True:
            return pred

    for key in ("trader_imitation", "trader_imitation_suggestion"):
        value = event.get(key)
        if isinstance(value, Mapping) and value:
            if value.get("available") is False:
                return unavailable(
                    _clean(
                        value.get("unavailable_reason")
                        or value.get("reason")
                        or "no_trader_imitation_candidate_on_decision_event"
                    )
                )
            if value.get("policy_weights") or value.get("policy") or value.get("weights"):
                pred = predict_trader_imitation_action(value, snapshot=snapshot, event=event)
                if pred.get("available") is True:
                    return pred
            action = _clean(
                value.get("action")
                or value.get("suggestion")
                or value.get("suggested_action")
                or value.get("decision")
                or value.get("verdict")
                or value.get("stance")
            )
            if action:
                return {
                    "available": True,
                    "arm": "trader_imitation",
                    "arm_kind": "optional_alt_version",
                    "source_field": key,
                    **json.loads(json.dumps(dict(value))),
                    "action": action,
                    "suggested_action": _clean(value.get("suggested_action")) or action,
                    "suggestion": _clean(value.get("suggestion")) or action,
                }
            return unavailable(
                _clean(value.get("unavailable_reason") or value.get("reason") or "trader_imitation_missing_suggestion")
            )

    reason = (
        _clean(event.get("trader_imitation_unavailable_reason"))
        or "no_trader_imitation_candidate_on_decision_event"
    )
    return unavailable(reason)


def canonical_checksum(lock: Dict[str, Any]) -> str:
    body = {k: v for k, v in lock.items() if k != "checksum"}
    encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_decision_lock(
    *,
    human_decision_id: str,
    decision_record_id: str,
    event: Dict[str, Any],
    decision_event_id: str,
    tenant_id: str,
    user_id: str,
    body_decision: str,
    rationale: Optional[str],
    modifications: Optional[Dict[str, Any]],
    decision_time: str,
    execution_observation_kind: Optional[str] = None,
    account_id: Optional[str] = None,
    broker_ids: Optional[List[str]] = None,
    actor_roles: Optional[List[str]] = None,
    trader_imitation_candidate: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    if not _clean(tenant_id) or not _clean(user_id):
        raise DecisionLockInvalidError("trader_identity_missing")
    event_kind = _clean(event.get("event_kind"))
    kind = resolve_observation_kind(body_decision, execution_observation_kind)
    cutoff = _clean(event.get("data_cutoff"))
    subject = event.get("subject") if isinstance(event.get("subject"), dict) else {}
    acct = _clean(account_id) or _clean(event.get("account_id")) or None
    brokers = [str(b) for b in (broker_ids or event.get("broker_ids") or []) if _clean(b)]
    snapshot = build_decision_time_snapshot(
        event,
        decision_time=decision_time,
        data_cutoff=cutoff or None,
    )
    imitation_arm = _arm_trader_imitation(
        event,
        snapshot=snapshot,
        trader_imitation_candidate=trader_imitation_candidate,
    )
    lock: Dict[str, Any] = {
        "schema": LOCK_SCHEMA,
        "human_decision_id": human_decision_id,
        "decision_record_id": decision_record_id,
        "decision_event_id": decision_event_id,
        "tenant_id": _clean(tenant_id),
        "user_id": _clean(user_id),
        "trader_identity": {
            "tenant_id": _clean(tenant_id),
            "user_id": _clean(user_id),
            "roles": list(actor_roles or []),
        },
        "account_id": acct,
        "account_unavailable_reason": None if acct else "account_not_supplied",
        "broker_ids": brokers,
        "broker_ids_unavailable_reason": None if brokers else "broker_ids_not_supplied",
        "strategy_id": _clean(event.get("strategy_id")) or None,
        "source_trading_event_id": decision_event_id,
        "decision_time": decision_time,
        "available_data_cutoff": cutoff or None,
        "data_cutoff_unavailable_reason": None if cutoff else "event_has_no_data_cutoff",
        "decision": a4_decision(body_decision, event_kind),
        "trading_room_verdict": body_decision,
        "evidence_kind": decision_evidence_kind(body_decision, modifications),
        "decision_time_snapshot": snapshot,
        "target_instrument": _clean(subject.get("symbol")) or None,
        "event_kind": event_kind or None,
        "raw_rationale": rationale,
        "raw_modifications": modifications,
        "execution_observation_kind": kind,
        "execution_display_label": DISPLAY_LABELS[kind],
        "execution_confidence": DEFAULT_CONFIDENCE[kind],
        "execution_verified": False,
        "actual_fill_claimed": False,
        "fill_refs": [],
        "outcome": unavailable("settlement_owned_by_attribution_task"),
        "arms": {
            "human_actual": {
                "available": True,
                "action": a4_decision(body_decision, event_kind),
                "decision": a4_decision(body_decision, event_kind),
                "trading_room_verdict": body_decision,
                "execution_observation_kind": kind,
                "execution_display_label": DISPLAY_LABELS[kind],
                "execution_confidence": DEFAULT_CONFIDENCE[kind],
                "execution_verified": False,
                "actual_fill_claimed": False,
            },
            "base_strategy": _arm_base_strategy(event),
            "private_servant": _arm_from_event_key(
                event,
                ("servant_suggestion", "private_servant_suggestion"),
                "no_servant_suggestion_on_decision_event",
            ),
            "institutional_view": _arm_from_event_key(
                event,
                ("institutional_view", "committee_variant"),
                "no_institutional_view_on_decision_event",
            ),
            "trader_imitation": dict(imitation_arm),
        },
        "optional_alt_versions": {
            "trader_imitation": dict(imitation_arm),
        },
        "no_order_route_proof": "agora_decision_lock_record_only",
        "immutable": True,
    }
    lock["checksum"] = canonical_checksum(lock)
    return lock


def verify_checksum(lock: Dict[str, Any]) -> bool:
    return lock.get("checksum") == canonical_checksum(lock)


_POLICY_LEARNING_URL_ENVS = (
    "PANTHEON_POLICY_LEARNING_API_URL",
    "POLICY_LEARNING_API_URL",
    "PANTHEON_POLICY_LEARNING_SERVICE_URL",
    "POLICY_LEARNING_URL",
)
_POLICY_LEARNING_TOKEN_ENVS = (
    "POLICY_LEARNING_SERVICE_TOKEN",
    "PANTHEON_POLICY_LEARNING_SERVICE_TOKEN",
    "POLICY_LEARNING_API_TOKEN",
    "AGORA_HANDOFF_SERVICE_TOKEN",
)
POLICY_LEARNING_URL_ENVS = _POLICY_LEARNING_URL_ENVS
POLICY_LEARNING_TOKEN_ENVS = _POLICY_LEARNING_TOKEN_ENVS

_PUBLISHED_DEV_POLICY_LEARNING_TOKENS = frozenset(
    {
        "pantheon-local-policy-learning-service",
        "replace-me-policy-learning-service-token",
        "pantheon-local-agora-handoff-service-token",
        "replace-me-agora-handoff-service-token",
    }
)
_PLACEHOLDER_TOKEN_PREFIXES = (
    "replace-me",
    "replaceme",
    "change-me",
    "changeme",
    "placeholder",
    "example",
    "sample",
    "dummy",
    "todo",
    "your-",
)


def _is_enforced_deployment_posture() -> bool:
    for env_name in ("PANTHEON_PERSISTENCE_POSTURE", "PERSISTENCE_POSTURE", "PANTHEON_ENV"):
        raw = _clean(os.getenv(env_name)).lower()
        if raw in {"stage", "staging", "staging-live", "prod", "production"} or raw.startswith(
            ("staging", "prod")
        ):
            return True
    return False


def _is_published_or_placeholder_token(token: str) -> bool:
    clean = _clean(token)
    if not clean:
        return False
    if clean in _PUBLISHED_DEV_POLICY_LEARNING_TOKENS:
        return True
    norm = clean.lower().replace("_", "-")
    return norm.startswith(_PLACEHOLDER_TOKEN_PREFIXES)


class PolicyLearningImitationCandidateClient:
    """Tenant-bound client for policy-learning's /api/policy-learning/trader-imitation/candidates route."""

    URL_ENVS = _POLICY_LEARNING_URL_ENVS
    TOKEN_ENVS = _POLICY_LEARNING_TOKEN_ENVS

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        urlopen: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._base_url = base_url
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._urlopen = urlopen

    def _resolve_base_url(self) -> str:
        if self._base_url is not None and _clean(self._base_url):
            return _clean(self._base_url).rstrip("/")
        for env_name in _POLICY_LEARNING_URL_ENVS:
            val = _clean(os.getenv(env_name))
            if val:
                return val.rstrip("/")
        return ""

    def _resolve_token(self) -> str:
        enforced = _is_enforced_deployment_posture()
        if self._token is not None and _clean(self._token):
            tok = _clean(self._token)
            if enforced and _is_published_or_placeholder_token(tok):
                return ""
            return tok
        for env_name in _POLICY_LEARNING_TOKEN_ENVS:
            val = _clean(os.getenv(env_name))
            if not val:
                continue
            if enforced and _is_published_or_placeholder_token(val):
                continue
            return val
        return ""

    def _resolve_timeout(self) -> float:
        if self._timeout_seconds is not None:
            return max(0.05, float(self._timeout_seconds))
        raw = _clean(os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0"))
        try:
            return max(0.05, float(raw))
        except ValueError:
            return 2.0

    def get_trader_imitation_state(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Query GET /api/policy-learning/trader-imitation/candidates bound to caller's tenant and user."""
        clean_tenant = _clean(tenant_id)
        clean_user = _clean(user_id)
        clean_strategy = _clean(strategy_id)
        if not clean_tenant or not clean_user:
            return None
        base_url = self._resolve_base_url()
        token = self._resolve_token()
        if not base_url or not token:
            reason = (
                "policy_learning_url_unconfigured"
                if not base_url
                else "policy_learning_token_unconfigured"
            )
            _logger.warning(
                "PolicyLearningImitationCandidateClient candidate source unconfigured (%s) for tenant=%s user=%s",
                reason,
                clean_tenant,
                clean_user,
            )
            return {
                "candidate": None,
                "candidates": [],
                "decision_count": None,
                "training_approved": False,
                "state": "candidate_source_unconfigured",
                "source": "unconfigured",
                "unavailable_reason": reason,
            }

        params: Dict[str, str] = {"user_id": clean_user}
        if clean_strategy:
            params["strategy_id"] = clean_strategy
        qs = urllib.parse.urlencode(params)
        url = f"{base_url}/api/policy-learning/trader-imitation/candidates?{qs}"
        req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        req.add_unredirected_header("Authorization", f"Bearer {token}")
        req.add_unredirected_header("X-Tenant-Id", clean_tenant)
        req.add_unredirected_header("X-Pantheon-Tenant", clean_tenant)
        req.add_unredirected_header("X-Pantheon-Service-Actor", f"bff:{clean_user}")
        opener = self._urlopen or urllib.request.urlopen
        try:
            with opener(req, timeout=self._resolve_timeout()) as resp:
                raw_bytes = resp.read()
                text = (
                    raw_bytes.decode("utf-8").strip()
                    if isinstance(raw_bytes, (bytes, bytearray))
                    else str(raw_bytes or "").strip()
                )
                payload = json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            reason = f"policy_learning_http_{exc.code}"
            _logger.warning(
                "PolicyLearningImitationCandidateClient HTTP %s from %s for tenant=%s user=%s",
                exc.code,
                url,
                clean_tenant,
                clean_user,
            )
            return {
                "candidate": None,
                "candidates": [],
                "decision_count": None,
                "training_approved": False,
                "state": "candidate_source_unavailable",
                "source": "unavailable",
                "unavailable_reason": reason,
            }
        except Exception as exc:
            _logger.warning(
                "PolicyLearningImitationCandidateClient request to %s failed for tenant=%s user=%s: %s",
                url,
                clean_tenant,
                clean_user,
                exc,
            )
            return {
                "candidate": None,
                "candidates": [],
                "decision_count": None,
                "training_approved": False,
                "state": "candidate_source_unavailable",
                "source": "unavailable",
                "unavailable_reason": "policy_learning_request_failed",
            }

        if not isinstance(payload, Mapping):
            _logger.warning(
                "PolicyLearningImitationCandidateClient received invalid payload from %s for tenant=%s user=%s",
                url,
                clean_tenant,
                clean_user,
            )
            return {
                "candidate": None,
                "candidates": [],
                "decision_count": None,
                "training_approved": False,
                "state": "candidate_source_unavailable",
                "source": "unavailable",
                "unavailable_reason": "policy_learning_invalid_payload",
            }
        resp_tenant = _clean(payload.get("tenant_id"))
        resp_user = _clean(payload.get("user_id"))
        if (resp_tenant and resp_tenant != clean_tenant) or (resp_user and resp_user != clean_user):
            _logger.warning(
                "PolicyLearningImitationCandidateClient scope mismatch from %s (expected=%s/%s got=%s/%s)",
                url,
                clean_tenant,
                clean_user,
                resp_tenant,
                resp_user,
            )
            return None

        raw_cands = payload.get("candidates")
        candidates = (
            [
                dict(c)
                for c in raw_cands
                if isinstance(c, Mapping)
                and (not _clean(c.get("tenant_id")) or _clean(c.get("tenant_id")) == clean_tenant)
                and (not _clean(c.get("user_id")) or _clean(c.get("user_id")) == clean_user)
            ]
            if isinstance(raw_cands, (list, tuple))
            else []
        )
        active = payload.get("active_candidate") or payload.get("candidate")
        candidate_obj: Optional[Dict[str, Any]] = None
        if isinstance(active, Mapping):
            if (not _clean(active.get("tenant_id")) or _clean(active.get("tenant_id")) == clean_tenant) and (
                not _clean(active.get("user_id")) or _clean(active.get("user_id")) == clean_user
            ):
                candidate_obj = dict(active)
        elif candidates:
            candidate_obj = candidates[0]
        if candidate_obj is not None and not candidates:
            candidates = [candidate_obj]

        return {
            "candidate": candidate_obj,
            "candidates": candidates[:3],
            "decision_count": payload.get("decision_count"),
            "training_approved": bool(payload.get("training_approved", False)),
            "state": _clean(payload.get("state")) or None,
            "source": "policy_learning",
        }

    def list_trader_behaviour_candidates(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        state = self.get_trader_imitation_state(
            tenant_id=tenant_id,
            user_id=user_id,
            strategy_id=strategy_id,
        )
        if not isinstance(state, Mapping):
            return []
        return list(state.get("candidates") or [])

    def resolve_candidate(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        state = self.get_trader_imitation_state(
            tenant_id=tenant_id,
            user_id=user_id,
            strategy_id=strategy_id,
        )
        if not isinstance(state, Mapping):
            return None
        cand = state.get("candidate")
        return dict(cand) if isinstance(cand, Mapping) else None

    def __call__(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        return self.get_trader_imitation_state(
            tenant_id=tenant_id,
            user_id=user_id,
            strategy_id=strategy_id,
        )


def build_default_imitation_candidate_resolver(
    *,
    base_url: Optional[str] = None,
    token: Optional[str] = None,
    timeout_seconds: Optional[float] = None,
    urlopen: Optional[Callable[..., Any]] = None,
) -> PolicyLearningImitationCandidateClient:
    return PolicyLearningImitationCandidateClient(
        base_url=base_url,
        token=token,
        timeout_seconds=timeout_seconds,
        urlopen=urlopen,
    )


__all__ = [
    "ACTUAL_FILL_KINDS",
    "DECISION_EVIDENCE_KINDS",
    "DISPLAY_LABELS",
    "DecisionLockExistsError",
    "DecisionLockInvalidError",
    "EXECUTION_OBSERVATION_KINDS",
    "LOCK_RESOURCE",
    "LOCK_SCHEMA",
    "PolicyLearningImitationCandidateClient",
    "TRADER_DECLARABLE_KINDS",
    "VERDICT_TO_EVIDENCE_KIND",
    "a4_decision",
    "build_decision_lock",
    "build_decision_time_snapshot",
    "build_default_imitation_candidate_resolver",
    "canonical_checksum",
    "decision_evidence_kind",
    "predict_trader_imitation_action",
    "unavailable",
    "verify_checksum",
]
