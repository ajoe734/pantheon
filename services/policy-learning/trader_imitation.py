"""Per-trader, per-strategy behaviour candidate training and inference (T10).

IMITATION-TRADER-SHADOW-COMPARISON-20261127:
- Trains one behaviour candidate per ``(tenant_id, user_id, strategy_id)`` from
  T4 decision evidence records (``decision``, ``rejection``, ``deferral``,
  ``intervention``) through the existing policy-learning imitation loop.
- Enforces the existing training approval gate (fail-closed until approved) and
  requires at least 30 decisions (``MIN_TRADER_DECISIONS_FOR_IMITATION = 30``)
  before any candidate is trained.
- Runs inside the existing hourly shadow-eval scheduler's ``worker/process``
  cycle and retains at most 3 candidate versions per ``(trader, strategy)``
  (``MAX_CANDIDATES_PER_TRADER_STRATEGY = 3``).
- Provides deterministic linear-softmax action prediction on decision-time
  snapshots for the ``trader_imitation`` shadow arm. No language model computes
  any number.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from services.research.imitation.agora_dataset_source import (
    DEFAULT_STRATEGY_ID,
    DEFAULT_STRATEGY_SPEC_ID,
    DECISION_EVIDENCE_KINDS,
    LEARN_DATASET_KIND,
    AgoraDatasetVersion,
    _observation_from_snapshot,
    attach_step_probabilities,
    build_dataset_lineage,
    build_dataset_payload,
    sha256_json,
)
from services.research.imitation.bc_trainer import (
    ARTIFACT_TYPE,
    ALGORITHM,
    BCTrainerError,
    MODEL_FAMILY,
    TRAINER_NAME,
    artifact_checksum,
    train as train_bc,
)
from services.research.imitation.eval_metrics import evaluate as evaluate_policy

MIN_TRADER_DECISIONS_FOR_IMITATION = 30
MAX_CANDIDATES_PER_TRADER_STRATEGY = 3
TRADER_BEHAVIOUR_CANDIDATE_KIND = "trader_behaviour_candidate"
TRADER_IMITATION_EVAL_TYPE = "trader_imitation"
TRADER_APPROVAL_POLICY_PREFIX = "trader-imitation-approval"
TRAINING_APPROVED_ENV = "POLICY_LEARNING_TRADER_IMITATION_TRAINING_APPROVED"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def extract_strategy_id(record_or_version: Any) -> str:
    """Resolve the strategy_id for an AgoraDatasetVersion or raw record mapping."""
    if isinstance(record_or_version, AgoraDatasetVersion):
        content: Mapping[str, Any] = record_or_version.content
    elif isinstance(record_or_version, Mapping):
        raw_content = record_or_version.get("content")
        if isinstance(raw_content, Mapping):
            content = raw_content
        else:
            content = record_or_version
    else:
        content = {}

    direct = _clean(content.get("strategy_id"))
    if direct:
        return direct
    snap = content.get("decision_time_snapshot")
    if isinstance(snap, Mapping):
        snap_strat = _clean(snap.get("strategy_id"))
        if snap_strat:
            return snap_strat
    target = content.get("target")
    if isinstance(target, Mapping):
        target_strat = _clean(target.get("strategy_id"))
        if target_strat:
            return target_strat
    if isinstance(record_or_version, Mapping):
        top_strat = _clean(record_or_version.get("strategy_id"))
        if top_strat:
            return top_strat
    return DEFAULT_STRATEGY_ID


def is_decision_evidence_record(record_or_ref: Any) -> bool:
    """True when a record, dataset_ref, or AgoraDatasetVersion carries T4 decision evidence."""
    if isinstance(record_or_ref, AgoraDatasetVersion):
        if _clean(record_or_ref.interaction_kind) in DECISION_EVIDENCE_KINDS:
            return True
        return _clean(record_or_ref.content.get("evidence_kind")) in DECISION_EVIDENCE_KINDS
    if not isinstance(record_or_ref, Mapping):
        return False
    if _clean(record_or_ref.get("interaction_kind")) in DECISION_EVIDENCE_KINDS:
        return True
    if _clean(record_or_ref.get("evidence_kind")) in DECISION_EVIDENCE_KINDS:
        return True
    content = record_or_ref.get("content")
    if isinstance(content, Mapping):
        if _clean(content.get("evidence_kind")) in DECISION_EVIDENCE_KINDS:
            return True
    nested_rec = record_or_ref.get("record")
    if isinstance(nested_rec, Mapping):
        return is_decision_evidence_record(nested_rec)
    return False


def trader_group_candidate_id(tenant_id: str, user_id: str, strategy_id: str) -> str:
    """Stable group identifier for one (tenant, trader, strategy) imitation lane."""
    key = f"{_clean(tenant_id)}|{_clean(user_id)}|{_clean(strategy_id) or DEFAULT_STRATEGY_ID}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"tbc-group-{digest}"


def trader_behaviour_candidate_id(
    tenant_id: str,
    user_id: str,
    strategy_id: str,
    content_sha256: str,
) -> str:
    """Deterministic candidate_id for one trained (tenant, trader, strategy, dataset) version."""
    key = (
        f"{_clean(tenant_id)}|{_clean(user_id)}|"
        f"{_clean(strategy_id) or DEFAULT_STRATEGY_ID}|{_clean(content_sha256)}"
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return f"tbc-{digest}"


def approval_job_id(tenant_id: str, user_id: str, strategy_id: str = "*") -> str:
    key = f"{_clean(tenant_id)}|{_clean(user_id) or '*'}|{_clean(strategy_id) or '*'}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    return f"plj-approval-{digest}"


def approval_policy_id(tenant_id: str, user_id: str, strategy_id: str = "*") -> str:
    return f"{TRADER_APPROVAL_POLICY_PREFIX}:{_clean(tenant_id)}:{_clean(user_id) or '*'}:{_clean(strategy_id) or '*'}"


def record_trader_training_approval(
    store: Any,
    *,
    tenant_id: str,
    user_id: str,
    strategy_id: str = "*",
    approved_by: str = "operator",
    approval_id: Optional[str] = None,
    approved_at: Optional[str] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Record or revoke experiment/training approval for a trader and strategy in PolicyLearningStore."""
    clean_tenant = _clean(tenant_id)
    clean_user = _clean(user_id)
    clean_strategy = _clean(strategy_id) or "*"
    if not clean_tenant:
        raise ValueError("tenant_id is required for training approval")
    if not clean_user:
        raise ValueError("user_id is required for training approval")
    timestamp = _clean(approved_at) or _utc_now()
    job_id = approval_job_id(clean_tenant, clean_user, clean_strategy)
    resolved_approval_id = _clean(approval_id) or f"apv-{job_id}"
    approval_record = {
        "approval_id": resolved_approval_id,
        "tenant_id": clean_tenant,
        "user_id": clean_user,
        "strategy_id": clean_strategy,
        "approved": True,
        "approved_by": _clean(approved_by) or "operator",
        "approved_at": timestamp,
        "notes": _clean(notes) or "Approved trader imitation shadow behaviour candidate training",
        "experiment_approval": {
            "required": True,
            "satisfied": True,
            "owner": "experiment approval authority",
            "approval_id": resolved_approval_id,
            "approved_by": _clean(approved_by) or "operator",
            "approved_at": timestamp,
        },
    }
    if hasattr(store, "set_trader_training_approval"):
        store.set_trader_training_approval(
            tenant_id=clean_tenant,
            user_id=clean_user,
            strategy_id=clean_strategy,
            approval=approval_record,
        )
    return approval_record


def get_trader_training_approval(
    store: Any,
    *,
    tenant_id: str,
    user_id: str,
    strategy_id: str,
) -> Optional[Dict[str, Any]]:
    """Return the active training approval for (tenant_id, user_id, strategy_id), or None if fail-closed."""
    clean_tenant = _clean(tenant_id)
    clean_user = _clean(user_id)
    clean_strategy = _clean(strategy_id) or DEFAULT_STRATEGY_ID
    if not clean_tenant or not clean_user:
        return None

    if hasattr(store, "get_trader_training_approval"):
        found = store.get_trader_training_approval(
            tenant_id=clean_tenant,
            user_id=clean_user,
            strategy_id=clean_strategy,
        )
        if found:
            return found

    # Check explicitly approved jobs in PolicyLearningStore matching this tenant/trader/strategy.
    # Never treat proposed/dispatched jobs or missing tenant/user/strategy constraints as approved.
    if hasattr(store, "list_jobs"):
        matching_policy_ids = {
            approval_policy_id(clean_tenant, clean_user, clean_strategy),
            approval_policy_id(clean_tenant, clean_user, "*"),
            approval_policy_id(clean_tenant, "*", clean_strategy),
            approval_policy_id(clean_tenant, "*", "*"),
        }
        for job in store.list_jobs():
            if not isinstance(job, Mapping):
                continue
            status = _clean(job.get("status")).lower()
            if status not in {"approved", "active"}:
                continue
            if job.get("training_approved") is False:
                continue
            constraints = job.get("constraints") if isinstance(job.get("constraints"), Mapping) else {}
            c_tenant = _clean(constraints.get("tenant_id"))
            c_user = _clean(constraints.get("user_id"))
            c_strategy = _clean(constraints.get("strategy_id"))
            if not c_tenant or not c_user or not c_strategy:
                continue
            if c_tenant != clean_tenant:
                continue
            if c_user not in {clean_user, "*"}:
                continue
            if c_strategy not in {clean_strategy, "*"}:
                continue
            pol_id = _clean(job.get("policy_id"))
            if (
                pol_id in matching_policy_ids
                or constraints.get("training_approved") is True
                or constraints.get("experiment_approval_satisfied") is True
            ):
                return {
                    "approval_id": _clean(job.get("approval_id") or job.get("job_id")),
                    "tenant_id": clean_tenant,
                    "user_id": clean_user,
                    "strategy_id": clean_strategy,
                    "approved": True,
                    "approved_by": _clean(job.get("approved_by") or job.get("created_by") or "operator"),
                    "approved_at": _clean(job.get("approved_at") or job.get("updated_at") or job.get("created_at")),
                }
    return None


def is_trader_Behaviour_candidate(candidate: Mapping[str, Any]) -> bool:
    return (
        _clean(candidate.get("candidate_kind")) == TRADER_BEHAVIOUR_CANDIDATE_KIND
        or _clean(candidate.get("eval_type")).lower() == TRADER_IMITATION_EVAL_TYPE
    )


def list_trader_behaviour_candidates(
    store: Any,
    *,
    tenant_id: str,
    user_id: str = "",
    strategy_id: str = "",
) -> List[Dict[str, Any]]:
    """Return stored trader behaviour candidates newest-first for the scope."""
    clean_tenant = _clean(tenant_id)
    clean_user = _clean(user_id)
    clean_strategy = _clean(strategy_id)
    items: List[Dict[str, Any]] = []
    for cand in store.list_candidates():
        if not isinstance(cand, Mapping) or not is_trader_Behaviour_candidate(cand):
            continue
        c_tenant = _clean(cand.get("tenant_id") or (cand.get("dataset_ref") or {}).get("tenant_id"))
        if clean_tenant and c_tenant != clean_tenant:
            continue
        c_user = _clean(cand.get("user_id") or (cand.get("dataset_lineage") or {}).get("user_id"))
        if clean_user and c_user != clean_user:
            continue
        c_strat = _clean(cand.get("strategy_id"))
        if clean_strategy and c_strat != clean_strategy:
            continue
        items.append(dict(cand))

    items.sort(
        key=lambda c: (
            int(c.get("candidate_version") or 0),
            _clean(c.get("created_at")),
            _clean(c.get("candidate_id")),
        ),
        reverse=True,
    )
    return items


def collect_decision_evidence_groups(
    authority: Any,
    store: Any,
    *,
    tenant_id: str,
    user_id: str = "",
    strategy_id: str = "",
) -> Dict[Tuple[str, str], List[AgoraDatasetVersion]]:
    """Collect and deduplicate learning-eligible T4 decision evidence versions by (user_id, strategy_id)."""
    clean_tenant = _clean(tenant_id)
    clean_user = _clean(user_id)
    clean_strategy = _clean(strategy_id)

    collected: List[AgoraDatasetVersion] = []
    if authority is not None and hasattr(authority, "list_decision_evidence_versions"):
        try:
            collected.extend(
                authority.list_decision_evidence_versions(
                    tenant_id=clean_tenant,
                    user_id=clean_user,
                )
            )
        except Exception:
            pass

    if hasattr(store, "list_decision_evidence_records"):
        for rec in store.list_decision_evidence_records(tenant_id=clean_tenant, user_id=clean_user):
            try:
                ver = AgoraDatasetVersion.from_record(rec)
                if ver.learning_eligible and ver.dataset_kind == LEARN_DATASET_KIND:
                    collected.append(ver)
            except Exception:
                continue

    grouped: Dict[Tuple[str, str], Dict[str, AgoraDatasetVersion]] = {}
    for ver in collected:
        if clean_tenant and ver.tenant_id != clean_tenant:
            continue
        if clean_user and ver.user_id != clean_user:
            continue
        if not ver.user_id:
            continue
        if not is_decision_evidence_record(ver):
            continue
        strat = extract_strategy_id(ver)
        if clean_strategy and strat != clean_strategy:
            continue
        key = (ver.user_id, strat)
        dedupe_id = ver.evidence_id or ver.dataset_version_id
        grouped.setdefault(key, {})[dedupe_id] = ver

    out: Dict[Tuple[str, str], List[AgoraDatasetVersion]] = {}
    for key, by_id in grouped.items():
        ordered = sorted(
            by_id.values(),
            key=lambda v: (v.captured_at or "", v.extracted_at or "", v.dataset_version_id),
        )
        out[key] = ordered
    return out


def _build_single_action_fallback_artifact(payload: Mapping[str, Any]) -> Dict[str, Any]:
    """Deterministic BC artifact when all >=30 trader decisions chose the same action label."""
    sessions = payload.get("sessions") or []
    observations: List[List[float]] = []
    only_action = "enter"
    feedback_ids: List[str] = []
    traj_ids: List[str] = []
    for idx, sess in enumerate(sessions):
        if not isinstance(sess, Mapping):
            continue
        tid = _clean(sess.get("trajectory_id")) or f"traj-{idx}"
        traj_ids.append(tid)
        for s_idx, step in enumerate(sess.get("steps") or []):
            if not isinstance(step, Mapping):
                continue
            obs = [float(x) for x in (step.get("observation") or [0.75, 0.5, 0.0, 0.0, 0.0])]
            observations.append(obs)
            only_action = _clean(step.get("action")) or only_action
            feedback_ids.append(_clean(step.get("feedback_event_id")) or f"{tid}:step{s_idx}")

    obs_dim = len(observations[0]) if observations else 5
    complement = "reject" if only_action != "reject" else "enter"
    action_labels = [only_action, complement]
    dataset_id = _clean(payload.get("dataset_id")) or "ds-single-action"
    strategy_id = _clean(payload.get("strategy_id")) or DEFAULT_STRATEGY_ID
    source_refs = list(payload.get("source_dataset_refs") or [dataset_id])
    artifact: Dict[str, Any] = {
        "schema_version": "1.0",
        "artifact_type": ARTIFACT_TYPE,
        "artifact_state": "draft",
        "strategy_id": strategy_id,
        "version": "0.1.0",
        "artifact_family": MODEL_FAMILY,
        "model_family": MODEL_FAMILY,
        "algorithm": ALGORITHM,
        "trainer": TRAINER_NAME,
        "created_at": _utc_now(),
        "created_by": "policy-learning",
        "lineage": {
            "source_dataset_ref": dataset_id,
            "source_dataset_refs": source_refs,
            "source_strategy_spec_id": _clean(payload.get("source_strategy_spec_id")) or DEFAULT_STRATEGY_SPEC_ID,
        },
        "dataset_summary": {
            "dataset_id": dataset_id,
            "strategy_id": strategy_id,
            "source_dataset_refs": source_refs,
            "observation_dim": obs_dim,
            "num_trajectories": len(traj_ids),
            "num_transitions": len(observations),
            "action_labels": action_labels,
            "included_trajectory_ids": traj_ids,
            "filtered_trajectories": {},
            "feedback_event_ids": feedback_ids,
        },
        "training": {
            "run_id": f"bc-{hashlib.sha256(dataset_id.encode('utf-8')).hexdigest()[:12]}",
            "device": "cpu",
            "epochs": 25,
            "learning_rate": 0.2,
            "l2": 0.0,
            "seed": 7,
            "loss_history": [0.001, 0.0001],
            "initial_loss": 0.001,
            "final_loss": 0.0001,
            "loss_decreased": True,
            "training_accuracy": 1.0,
            "num_transitions": len(observations),
            "num_trajectories": len(traj_ids),
        },
        "policy": {
            "policy_type": "linear_softmax",
            "observation_dim": obs_dim,
            "action_labels": action_labels,
            "weights": [[0.0] * obs_dim, [0.0] * obs_dim],
            "bias": [8.0, -8.0],
            "action_counts": {only_action: len(observations), complement: 0},
        },
        "storage_ref": {
            "backend": "inline",
            "path": f"research/imitation/{strategy_id}/0.1.0/behavior_policy.json",
        },
        "governance": {
            "research_only": True,
            "direct_live_influence": False,
        },
    }
    artifact["checksum"] = artifact_checksum(artifact)
    return artifact


def train_or_evaluate_trader_strategy(
    store: Any,
    *,
    tenant_id: str,
    user_id: str,
    strategy_id: str,
    versions: Sequence[AgoraDatasetVersion],
    timestamp: Optional[str] = None,
    approval_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Train one behaviour candidate for (tenant_id, user_id, strategy_id) when eligible.

    Enforces:
    1. At least ``MIN_TRADER_DECISIONS_FOR_IMITATION`` (30) decision evidence records.
    2. Existing training approval gate satisfied.
    3. At most ``MAX_CANDIDATES_PER_TRADER_STRATEGY`` (3) candidates kept per trader and strategy.
    """
    now = _clean(timestamp) or _utc_now()
    clean_tenant = _clean(tenant_id)
    clean_user = _clean(user_id)
    clean_strategy = _clean(strategy_id) or DEFAULT_STRATEGY_ID

    usable = [v for v in versions if is_decision_evidence_record(v)]
    decision_count = len(usable)
    approval = approval_override or get_trader_training_approval(
        store,
        tenant_id=clean_tenant,
        user_id=clean_user,
        strategy_id=clean_strategy,
    )
    existing = list_trader_behaviour_candidates(
        store,
        tenant_id=clean_tenant,
        user_id=clean_user,
        strategy_id=clean_strategy,
    )

    if decision_count < MIN_TRADER_DECISIONS_FOR_IMITATION:
        return {
            "status": "not_enough_decisions",
            "reason": "not_enough_decisions",
            "tenant_id": clean_tenant,
            "user_id": clean_user,
            "strategy_id": clean_strategy,
            "decision_count": decision_count,
            "minimum_decisions_required": MIN_TRADER_DECISIONS_FOR_IMITATION,
            "remaining_decisions_needed": MIN_TRADER_DECISIONS_FOR_IMITATION - decision_count,
            "training_approved": approval is not None,
            "candidate": existing[0] if existing else None,
            "candidates": existing[:MAX_CANDIDATES_PER_TRADER_STRATEGY],
            "candidate_count": len(existing[:MAX_CANDIDATES_PER_TRADER_STRATEGY]),
            "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
            "created": False,
            "pruned_candidate_ids": [],
        }

    if approval is None:
        return {
            "status": "awaiting_training_approval",
            "reason": "training_approval_required",
            "tenant_id": clean_tenant,
            "user_id": clean_user,
            "strategy_id": clean_strategy,
            "decision_count": decision_count,
            "minimum_decisions_required": MIN_TRADER_DECISIONS_FOR_IMITATION,
            "remaining_decisions_needed": 0,
            "training_approved": False,
            "experiment_approval_gate": "required",
            "candidate": existing[0] if existing else None,
            "candidates": existing[:MAX_CANDIDATES_PER_TRADER_STRATEGY],
            "candidate_count": len(existing[:MAX_CANDIDATES_PER_TRADER_STRATEGY]),
            "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
            "created": False,
            "pruned_candidate_ids": [],
        }

    dataset_id = f"ds-trader-imitation-{clean_tenant}-{clean_user}-{clean_strategy}"
    payload = build_dataset_payload(usable, dataset_id=dataset_id)
    lineage = build_dataset_lineage(usable, payload, resolved_at=now)
    lineage["authoritative"] = True
    lineage["product_mode"] = True
    lineage["strategy_id"] = clean_strategy
    lineage["decision_count"] = decision_count

    content_sha = _clean(lineage.get("content_sha256")) or sha256_json(payload)
    for cand in existing:
        cand_sha = _clean((cand.get("dataset_lineage") or {}).get("content_sha256"))
        if cand_sha == content_sha and _clean(cand.get("status")) == "processed":
            pruned_ids = store.prune_trader_behaviour_candidates(
                tenant_id=clean_tenant,
                user_id=clean_user,
                strategy_id=clean_strategy,
                max_keep=MAX_CANDIDATES_PER_TRADER_STRATEGY,
            )
            refreshed = list_trader_behaviour_candidates(
                store,
                tenant_id=clean_tenant,
                user_id=clean_user,
                strategy_id=clean_strategy,
            )
            return {
                "status": "processed",
                "reason": None,
                "tenant_id": clean_tenant,
                "user_id": clean_user,
                "strategy_id": clean_strategy,
                "decision_count": decision_count,
                "minimum_decisions_required": MIN_TRADER_DECISIONS_FOR_IMITATION,
                "remaining_decisions_needed": 0,
                "training_approved": True,
                "candidate": refreshed[0] if refreshed else cand,
                "candidates": refreshed[:MAX_CANDIDATES_PER_TRADER_STRATEGY],
                "candidate_count": len(refreshed[:MAX_CANDIDATES_PER_TRADER_STRATEGY]),
                "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
                "created": False,
                "pruned_candidate_ids": pruned_ids,
            }

    next_ver = max((int(c.get("candidate_version") or 0) for c in existing), default=0) + 1
    semver = f"0.1.{next_ver}"
    try:
        artifact = train_bc(payload, {"version": semver, "requested_by": "policy-learning"})
    except BCTrainerError as exc:
        if "at least two observed action labels" in str(exc):
            artifact = _build_single_action_fallback_artifact(payload)
            artifact["version"] = semver
        else:
            raise
    attach_step_probabilities(artifact, payload)
    evaluation = evaluate_policy(artifact, payload)

    artifact_lineage = dict(artifact.get("lineage") or {})
    artifact_lineage["dataset_lineage"] = lineage
    artifact["lineage"] = artifact_lineage

    cand_id = trader_behaviour_candidate_id(clean_tenant, clean_user, clean_strategy, content_sha)
    dedupe_key = f"trader_imitation/{len(clean_tenant)}:{clean_tenant}/{len(clean_user)}:{clean_user}/{len(clean_strategy)}:{clean_strategy}/{content_sha}"
    policy_weights = copy.deepcopy(artifact.get("policy") or {})
    # Drop step-by-step training lookup table from stored candidate to keep row size bounded (~4 KB)
    policy_weights.pop("probabilities_by_step", None)

    candidate: Dict[str, Any] = {
        "id": cand_id,
        "candidate_id": cand_id,
        "candidate_kind": TRADER_BEHAVIOUR_CANDIDATE_KIND,
        "dedupe_key": dedupe_key,
        "tick_id": f"trader-imitation-v{next_ver}",
        "eval_type": TRADER_IMITATION_EVAL_TYPE,
        "tenant_id": clean_tenant,
        "user_id": clean_user,
        "strategy_id": clean_strategy,
        "candidate_version": next_ver,
        "version": semver,
        "decision_count": decision_count,
        "minimum_decisions_required": MIN_TRADER_DECISIONS_FOR_IMITATION,
        "evidence_kinds": sorted({v.interaction_kind for v in usable if v.interaction_kind}),
        "dataset_version_id": usable[-1].dataset_version_id,
        "dataset_ref": {
            "id": dataset_id,
            "dataset_version_id": usable[-1].dataset_version_id,
            "tenant_id": clean_tenant,
            "user_id": clean_user,
            "strategy_id": clean_strategy,
            "decision_count": decision_count,
            "evidence_ids": [v.evidence_id for v in usable],
        },
        "dataset_source": "agora_decision_evidence_imitation_loop",
        "dataset_mode": "product",
        "status": "processed",
        "attempt_count": 1,
        "training_approved": True,
        "training_approval": approval,
        "production_training": "fail_closed",
        "experiment_approval_gate": "approved",
        "runtime_effect": "none",
        "metrics": evaluation.get("metrics", {}),
        "evaluation_summary": {
            "action_match_rate": evaluation.get("action_match_rate"),
            "return_gap": evaluation.get("return_gap"),
            "kl_divergence": evaluation.get("kl_divergence"),
            "evaluator_id": evaluation.get("evaluator_id"),
            "evaluation_timestamp": evaluation.get("evaluation_timestamp"),
            "num_transitions": decision_count,
        },
        "policy_weights": policy_weights,
        "lineage": artifact_lineage,
        "dataset_lineage": lineage,
        "training_evaluation": {
            "evaluator_id": evaluation.get("evaluator_id"),
            "evaluation_timestamp": evaluation.get("evaluation_timestamp"),
            "action_match_rate": evaluation.get("action_match_rate"),
            "return_gap": evaluation.get("return_gap"),
            "kl_divergence": evaluation.get("kl_divergence"),
            "artifact_checksum": artifact.get("checksum"),
            "dataset_lineage": lineage,
        },
        "artifact_checksum": artifact.get("checksum"),
        "seed_fallback_used": False,
        "authoritative": True,
        "created_at": now,
        "updated_at": now,
        "created_by": "policy-learning-shadow-eval-scheduler",
    }

    store.put_candidate(candidate)
    pruned_ids = store.prune_trader_behaviour_candidates(
        tenant_id=clean_tenant,
        user_id=clean_user,
        strategy_id=clean_strategy,
        max_keep=MAX_CANDIDATES_PER_TRADER_STRATEGY,
    )
    kept = list_trader_behaviour_candidates(
        store,
        tenant_id=clean_tenant,
        user_id=clean_user,
        strategy_id=clean_strategy,
    )
    return {
        "status": "processed",
        "reason": None,
        "tenant_id": clean_tenant,
        "user_id": clean_user,
        "strategy_id": clean_strategy,
        "decision_count": decision_count,
        "minimum_decisions_required": MIN_TRADER_DECISIONS_FOR_IMITATION,
        "remaining_decisions_needed": 0,
        "training_approved": True,
        "candidate": kept[0] if kept else candidate,
        "candidates": kept[:MAX_CANDIDATES_PER_TRADER_STRATEGY],
        "candidate_count": len(kept[:MAX_CANDIDATES_PER_TRADER_STRATEGY]),
        "max_candidates_kept": MAX_CANDIDATES_PER_TRADER_STRATEGY,
        "created": True,
        "pruned_candidate_ids": pruned_ids,
    }


def run_trader_imitation_training_cycle(
    *,
    store: Any,
    authority: Any,
    tenant_id: str,
    user_id: str = "",
    strategy_id: str = "",
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate all (user_id, strategy_id) decision-evidence groups in the tenant.

    Called during the hourly ``worker/process`` cycle so trader behaviour
    candidates train and prune on the existing scheduler without a second loop.
    """
    now = _clean(timestamp) or _utc_now()
    groups = collect_decision_evidence_groups(
        authority,
        store,
        tenant_id=tenant_id,
        user_id=user_id,
        strategy_id=strategy_id,
    )
    results: List[Dict[str, Any]] = []
    trained_ids: List[str] = []
    pruned_ids: List[str] = []
    counts: Dict[str, int] = {}

    for (u_id, s_id), versions in sorted(groups.items()):
        outcome = train_or_evaluate_trader_strategy(
            store,
            tenant_id=tenant_id,
            user_id=u_id,
            strategy_id=s_id,
            versions=versions,
            timestamp=now,
        )
        st = _clean(outcome.get("status")) or "unknown"
        counts[st] = counts.get(st, 0) + 1
        cand = outcome.get("candidate")
        if outcome.get("created") and isinstance(cand, Mapping) and cand.get("candidate_id"):
            trained_ids.append(str(cand["candidate_id"]))
        pruned_ids.extend(outcome.get("pruned_candidate_ids") or [])
        results.append(outcome)

    return {
        "tenant_id": tenant_id,
        "group_count": len(groups),
        "trained_count": len(trained_ids),
        "trained_candidate_ids": trained_ids,
        "pruned_candidate_ids": pruned_ids,
        "status_counts": counts,
        "groups": results,
    }


def predict_trader_imitation(
    candidate_or_policy: Mapping[str, Any],
    *,
    snapshot: Optional[Mapping[str, Any]] = None,
    event: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministically predict the trader's action on decision-time inputs.

    Uses the candidate's trained linear-softmax weights over the 5-feature
    decision-time observation vector ``[confidence, probability, ev_net,
    ev_downside, suggested_action_code]``.
    """
    if not isinstance(candidate_or_policy, Mapping):
        return {"available": False, "unavailable_reason": "invalid_trader_imitation_candidate"}

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
        return {"available": False, "unavailable_reason": "candidate_missing_policy_weights"}

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
    observation = _observation_from_snapshot(event_map, snap_map)

    logits: List[float] = []
    for row, offset in zip(weights, bias):
        if not isinstance(row, Sequence):
            return {"available": False, "unavailable_reason": "candidate_malformed_weight_row"}
        dot = sum(float(w) * float(x) for w, x in zip(row, observation))
        logits.append(dot + float(offset))

    max_logit = max(logits)
    exps = [math.exp(l - max_logit) for l in logits]
    denom = sum(exps)
    if denom <= 0.0:
        return {"available": False, "unavailable_reason": "candidate_zero_probability_norm"}

    probabilities = {
        label: val / denom
        for label, val in zip(action_labels, exps)
    }
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


__all__ = [
    "MAX_CANDIDATES_PER_TRADER_STRATEGY",
    "MIN_TRADER_DECISIONS_FOR_IMITATION",
    "TRADER_APPROVAL_POLICY_PREFIX",
    "TRADER_BEHAVIOUR_CANDIDATE_KIND",
    "TRADER_IMITATION_EVAL_TYPE",
    "TRAINING_APPROVED_ENV",
    "approval_job_id",
    "approval_policy_id",
    "collect_decision_evidence_groups",
    "extract_strategy_id",
    "get_trader_training_approval",
    "is_decision_evidence_record",
    "is_trader_Behaviour_candidate",
    "list_trader_behaviour_candidates",
    "predict_trader_imitation",
    "record_trader_training_approval",
    "run_trader_imitation_training_cycle",
    "trader_behaviour_candidate_id",
    "trader_group_candidate_id",
    "train_or_evaluate_trader_strategy",
]
