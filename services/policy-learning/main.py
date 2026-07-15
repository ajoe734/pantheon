from __future__ import annotations

import copy
import json as _json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes
from services.foundation.persistence_posture import require_persistence_posture
from store import PolicyLearningStore, build_policy_learning_store
from services.research.imitation.bc_trainer import train as train_bc
from services.research.imitation.eval_metrics import evaluate as evaluate_policy


PRODUCTION_ADAPTERS = {"openclaw", "qlib", "trl", "finrl", "rllib", "ray_tune", "wandb"}
STUB_ADAPTER = "stub"
FAIL_CLOSED_SCOPE = "capability_metadata_read_only"
OFFLINE_DISPATCH_ENABLED_SCOPE = "offline_worker_dispatch_enabled"
# Adapters with declared gateway entrypoints that can be routed offline.
OFFLINE_ADAPTERS = {"trl", "finrl", "rllib", "ray_tune", "qlib"}
CAPABILITY_REGISTRY: Dict[str, Dict[str, Any]] = {
    "openclaw": {
        "status": "deferred",
        "purpose": "OpenClaw agent runtime substrate",
        "activation_gate": "OPENCLAW_PRODUCTION_BROKER_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "qlib": {
        "status": "deferred",
        "activation_gate": "services/research/qlib/requirements.txt",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "trl": {
        "status": "deferred",
        "activation_gate": "services/learning/trl/ACTIVATION_CRITERIA.md",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "finrl": {
        "status": "deferred",
        "activation_gate": "PANTHEON_FINRL_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "rllib": {
        "status": "deferred",
        "activation_gate": "PANTHEON_RLLIB_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "ray_tune": {
        "status": "deferred",
        "activation_gate": "PANTHEON_RAYTUNE_PREP_ENABLED",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
    "wandb": {
        "status": "deferred",
        "activation_gate": "services/registry/experiments/WANDB_ACTIVATION.md",
        "gate_state": "fail_closed",
        "allowed_scope": FAIL_CLOSED_SCOPE,
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _data_dir() -> str:
    return os.getenv("POLICY_LEARNING_DATA_DIR", "/tmp/pantheon/policy-learning")


def _production_adapters_allowed() -> bool:
    return os.getenv("POLICY_LEARNING_ENABLE_PRODUCTION_ADAPTERS", "false").lower() == "true"


def _offline_gate_enabled() -> bool:
    return os.getenv("PANTHEON_OFFLINE_GATE_ENABLED", "false").lower() == "true"


def _gateway_url() -> str:
    return os.getenv("RESEARCH_WORKER_GATEWAY_URL", "http://research-worker-gateway-svc:8103")


OFFLINE_GATE_ENABLED = _offline_gate_enabled()
GATEWAY_URL = _gateway_url()
STORE_BACKEND = os.getenv("POLICY_LEARNING_STORE_BACKEND", "json").strip().lower() or "json"
PERSISTENCE_POSTURE = require_persistence_posture("policy-learning")


def _route_to_gateway(adapter: str, objective: str, source_refs: List[Dict[str, Any]], constraints: Dict[str, Any], actor_id: str, job_id: str, timestamp: str) -> Optional[Dict[str, Any]]:
    """POST an offline-capable adapter job to the research-worker-gateway."""
    request_body = {
        "worker": adapter,
        "requested_mode": "offline",
        "dispatch_mode": "offline",
        "objective": objective,
        "input_refs": source_refs,
        "parameters": constraints,
        "actor_id": actor_id,
        "idempotency_key": f"pl-{job_id}",
        "requested_at": timestamp,
    }
    payload = _json.dumps(request_body).encode("utf-8")
    try:
        req = urllib.request.Request(
            f"{GATEWAY_URL}/api/research-worker-gateway/jobs",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            return _json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _next_job_id(timestamp: str, existing: set[str]) -> str:
    prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"plj-{prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"plj-{prefix}-{index:03d}"
    return candidate


def _next_event_id(timestamp: str, events: List[Dict[str, Any]]) -> str:
    prefix = timestamp[:10].replace("-", "")
    next_sequence = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    return f"plevt-{prefix}-{next_sequence:03d}"


def _event(timestamp: str, event_type: str, summary: str, actor: str, events: List[Dict[str, Any]]) -> Dict[str, Any]:
    sequence_number = max((int(event.get("sequence_number") or 0) for event in events), default=0) + 1
    return {
        "event_id": _next_event_id(timestamp, events),
        "event_type": event_type,
        "summary": summary,
        "actor": actor,
        "emitted_at": timestamp,
        "sequence_number": sequence_number,
    }


class ProposalBody(BaseModel):
    policy_id: str
    objective: str
    adapter: str = STUB_ADAPTER
    requested_mode: str = "stub"
    source_refs: List[Dict[str, Any]] = Field(default_factory=list)
    constraints: Dict[str, Any] = Field(default_factory=dict)
    actor_id: str = "operator"
    proposed_at: Optional[str] = None


class RejectBody(BaseModel):
    reason: str
    actor_id: str = "operator"
    rejected_at: Optional[str] = None


class ShadowEvalTickBody(BaseModel):
    tick_id: Optional[str] = None
    eval_type: str = "shadow"
    dataset_refs: List[Dict[str, Any]] = Field(default_factory=list)
    max_datasets: Optional[int] = None
    actor_id: str = "scheduler"
    ticked_at: Optional[str] = None


def _proposal_text(body: ProposalBody) -> str:
    parts = [body.adapter, body.requested_mode, body.objective]
    parts.extend(str(ref.get("type") or "") for ref in body.source_refs)
    parts.extend(str(ref.get("id") or "") for ref in body.source_refs)
    parts.extend(str(key) for key in body.constraints.keys())
    parts.extend(str(value) for value in body.constraints.values())
    return " ".join(parts).lower()


app = FastAPI(title="Pantheon Policy Learning Service", version="0.1.0")
store = build_policy_learning_store(_data_dir())
register_fastapi_health_routes(
    app,
    "policy-learning",
    dependencies=lambda: {"persistence": PERSISTENCE_POSTURE.to_dict()},
    metrics=lambda: {"job_count": len(store.list_jobs())},
    details=lambda: {
        "data_dir": _data_dir(),
        "store_backend": STORE_BACKEND,
        "production_adapters_enabled": _production_adapters_allowed(),
        "persistence_posture": PERSISTENCE_POSTURE.to_dict(),
    },
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "policy-learning",
        "data_dir": _data_dir(),
        "job_count": len(store.list_jobs()),
        "production_adapters_enabled": _production_adapters_allowed(),
    }


@app.get("/api/policy-learning/capabilities")
def capabilities() -> Dict[str, Any]:
    def _effective_metadata(adapter: str, meta: Dict[str, Any]) -> Dict[str, Any]:
        if OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS:
            updated = dict(meta)
            updated["gate_state"] = "activation_ready"
            updated["allowed_scope"] = OFFLINE_DISPATCH_ENABLED_SCOPE
            updated["gateway_routing"] = "enabled"
            return updated
        return meta

    return {
        "service": "policy-learning",
        "default_mode": "stub",
        "production_activation": "disabled",
        "offline_gate": "enabled" if OFFLINE_GATE_ENABLED else "disabled",
        "safety_boundary": {
            "training_dispatch": "disabled",
            "registry_writes": "disabled",
            "governance_writes": "disabled",
            "paper_canary_live": "disabled",
        },
        "capabilities": [
            {
                "adapter": STUB_ADAPTER,
                "status": "available",
                "purpose": "non-production lifecycle replay and contract validation",
            }
        ]
        + [
            {"adapter": adapter, **_effective_metadata(adapter, metadata)}
            for adapter, metadata in sorted(CAPABILITY_REGISTRY.items())
        ],
    }


@app.get("/api/policy-learning/jobs")
def list_jobs(status: Optional[str] = None, policy_id: Optional[str] = None) -> List[Dict[str, Any]]:
    jobs = store.list_jobs()
    if status:
        jobs = [job for job in jobs if str(job.get("status") or "").lower() == status.lower()]
    if policy_id:
        jobs = [job for job in jobs if job.get("policy_id") == policy_id]
    return jobs


@app.post("/api/policy-learning/jobs", status_code=201)
def propose_job(body: ProposalBody) -> Dict[str, Any]:
    timestamp = body.proposed_at or utc_now()
    adapter = body.adapter.lower().strip()
    requested_mode = body.requested_mode.lower().strip()
    existing_ids = {str(job.get("job_id") or "") for job in store.list_jobs()}
    job_id = _next_job_id(timestamp, existing_ids)
    events: List[Dict[str, Any]] = []
    request_text = _proposal_text(body)
    rejection = None
    if any(token in request_text for token in ("registry_write", "direct_registry_write", "promote_to_registry")):
        status = "rejected"
        rejection = {
            "reason": "registry_write_disabled",
            "detail": "Policy-learning jobs may record proposals only; canonical registry writes are not allowed.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif any(token in request_text for token in ("governance_write", "governance_stage", "approve_governance")):
        status = "rejected"
        rejection = {
            "reason": "governance_write_disabled",
            "detail": "Policy-learning jobs cannot approve governance decisions or change deployment stages.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif adapter != STUB_ADAPTER and adapter not in CAPABILITY_REGISTRY:
        status = "rejected"
        rejection = {
            "reason": "unknown_adapter",
            "detail": f"Adapter family '{adapter}' is not registered for policy learning.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode == "offline":
        # Offline gate open: route activation-ready adapters to the research-worker-gateway.
        gateway_result = _route_to_gateway(adapter, body.objective, body.source_refs, body.constraints, body.actor_id, job_id, timestamp)
        gateway_ref: Dict[str, Any]
        if gateway_result:
            gateway_ref = {"gateway_job_id": gateway_result.get("job_id"), "gateway": "research-worker-gateway"}
            status = "dispatched"
            events.append(_event(timestamp, "proposal_dispatched", f"Offline-gated adapter '{adapter}' routed to research-worker-gateway (gateway_job_id={gateway_result.get('job_id')}).", body.actor_id, events))
        else:
            gateway_ref = {"gateway_job_id": None, "error": "gateway_unavailable"}
            status = "dispatched"
            events.append(_event(timestamp, "proposal_dispatched", f"Offline-gated adapter '{adapter}' dispatch attempted; gateway unavailable.", body.actor_id, events))
        job = {
            "id": job_id,
            "job_id": job_id,
            "policy_id": body.policy_id,
            "objective": body.objective,
            "adapter": adapter,
            "requested_mode": requested_mode,
            "status": status,
            "production_activation": "disabled",
            "source_refs": body.source_refs,
            "constraints": body.constraints,
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": body.actor_id,
            "rejection": None,
            "gateway_ref": gateway_ref,
            "events": events,
        }
        store.put_job(job)
        return job
    elif OFFLINE_GATE_ENABLED and adapter in OFFLINE_ADAPTERS and requested_mode not in {"production", "paper", "canary", "live"}:
        status = "rejected"
        rejection = {
            "reason": "offline_mode_required",
            "detail": "Offline-gated policy-learning dispatch requires requested_mode=offline.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    elif adapter in PRODUCTION_ADAPTERS or requested_mode in {"production", "paper", "canary", "live"}:
        status = "rejected"
        rejection = {
            "reason": "production_adapter_disabled",
            "detail": "Policy-learning production adapters are not activated in this service boundary.",
            "rejected_at": timestamp,
            "rejected_by": "policy-learning-service",
        }
        events.append(_event(timestamp, "proposal_rejected", rejection["detail"], "system", events))
    else:
        status = "proposed"
        events.append(_event(timestamp, "proposal_recorded", "Stub policy-learning job proposal recorded.", body.actor_id, events))
    job = {
        "id": job_id,
        "job_id": job_id,
        "policy_id": body.policy_id,
        "objective": body.objective,
        "adapter": adapter,
        "requested_mode": requested_mode,
        "status": status,
        "production_activation": "disabled",
        "source_refs": body.source_refs,
        "constraints": body.constraints,
        "created_at": timestamp,
        "updated_at": timestamp,
        "created_by": body.actor_id,
        "rejection": rejection,
        "events": events,
    }
    store.put_job(job)
    return job


@app.get("/api/policy-learning/jobs/{job_id}")
def get_job(job_id: str) -> Dict[str, Any]:
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="policy learning job not found")
    return job


@app.get("/api/policy-learning/jobs/{job_id}/status")
def get_job_status(job_id: str) -> Dict[str, Any]:
    job = get_job(job_id)
    return {
        "job_id": job["job_id"],
        "policy_id": job["policy_id"],
        "status": job["status"],
        "adapter": job["adapter"],
        "requested_mode": job["requested_mode"],
        "production_activation": job["production_activation"],
        "rejection": job.get("rejection"),
        "gateway_ref": job.get("gateway_ref"),
        "events": job.get("events", []),
        "updated_at": job.get("updated_at"),
    }


@app.post("/api/policy-learning/jobs/{job_id}/reject")
def reject_job(job_id: str, body: RejectBody) -> Dict[str, Any]:
    job = get_job(job_id)
    if str(job.get("status") or "").lower() == "rejected":
        return job
    timestamp = body.rejected_at or utc_now()
    events = list(job.get("events") or [])
    events.append(_event(timestamp, "job_rejected", body.reason, body.actor_id, events))
    job["status"] = "rejected"
    job["updated_at"] = timestamp
    job["rejection"] = {"reason": body.reason, "rejected_at": timestamp, "rejected_by": body.actor_id}
    job["events"] = events
    return store.put_job(job)


def discover_eligible_datasets() -> List[Dict[str, Any]]:
    backend = os.getenv("POLICY_LEARNING_STORE_BACKEND", "json").strip().lower()
    if backend == "postgres":
        dsn = os.getenv("POLICY_LEARNING_STORE_DSN") or os.getenv("DATABASE_URL")
        if dsn:
            try:
                import psycopg  # type: ignore[import]
                with psycopg.connect(dsn) as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT DISTINCT persona_id, COALESCE(session_id, 'default') "
                            "FROM agora.agora_dataset_records "
                            "WHERE learning_eligible = true"
                        )
                        rows = cur.fetchall()
                        if rows:
                            return [
                                {
                                    "id": f"ds-trace-{row[0]}-{row[1]}",
                                    "type": "trace_dataset",
                                    "source": "agora_interaction",
                                    "persona_id": row[0],
                                    "session_id": None if row[1] == "default" else row[1]
                                }
                                for row in rows
                            ]
            except Exception:
                pass
    return []


SEED_DATASET = {
    "dataset_id": "traj-smoke-2026-05-16",
    "strategy_id": "alpha-mean-reversion",
    "source_dataset_refs": ["dataset://feedback/approved/2026-05-16"],
    "source_strategy_spec_id": "strat-alpha-mean-reversion-v2",
    "sessions": [
        {
            "trajectory_id": "traj-001",
            "actor_id": "trader-01",
            "actor_role": "operator",
            "decision": "approve",
            "target": {
                "registry_id": "reg-alpha-1",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "candidate",
            },
            "steps": [
                {"observation": [0.9, 0.1, -0.2], "action": "buy_small", "reward": 0.3, "feedback_event_id": "evt-001"},
                {"observation": [0.8, 0.2, -0.1], "action": "buy_small", "reward": 0.2, "feedback_event_id": "evt-002"},
            ],
        },
        {
            "trajectory_id": "traj-002",
            "actor_id": "trader-02",
            "actor_role": "approver",
            "decision": "edit",
            "target": {
                "registry_id": "reg-alpha-1",
                "strategy_id": "alpha-mean-reversion",
                "artifact_version": "1.2.0",
                "artifact_type": "strategy_spec",
                "promotion_state": "paper",
            },
            "steps": [
                {"observation": [-0.85, -0.2, 0.55], "action": "reduce_risk", "reward": 0.15, "feedback_event_id": "evt-003"},
            ],
        },
    ],
}


def _get_dataset_payload(dataset_id: str) -> Dict[str, Any]:
    payload = copy.deepcopy(SEED_DATASET)
    payload["dataset_id"] = dataset_id
    return payload


def _process_backlog() -> int:
    import math
    processed_count = 0
    candidates = store.list_candidates()
    for candidate in candidates:
        if candidate.get("status") != "proposed":
            continue
        candidate_id = candidate["candidate_id"]
        dataset_ref = candidate.get("dataset_ref") or {}
        dataset_id = str(dataset_ref.get("id") or dataset_ref.get("dataset_id") or "ds-default")
        
        try:
            # 1. Fetch or generate the dataset payload in IMT-003 format.
            dataset_payload = _get_dataset_payload(dataset_id)
            
            # 2. Run bc_trainer.train(dataset_payload) to get behavior_policy_artifact.
            bp_artifact = train_bc(dataset_payload)
            
            # 3. Pre-compute linear softmax probabilities for each step in dataset_payload and add to bp_artifact
            policy_data = bp_artifact.get("policy", {})
            weights = policy_data.get("weights", [])
            bias = policy_data.get("bias", [])
            action_labels = policy_data.get("action_labels", [])
            
            probs_by_step = {}
            for session in dataset_payload.get("sessions", []):
                traj_id = session.get("trajectory_id", "default")
                for step_idx, step in enumerate(session.get("steps", [])):
                    obs = step.get("observation", [])
                    step_id = step.get("step_id")
                    feedback_id = step.get("feedback_event_id")
                    
                    # Compute softmax probabilities
                    logits = []
                    for w, b in zip(weights, bias):
                        logit = sum(wi * xi for wi, xi in zip(w, obs)) + b
                        logits.append(logit)
                    max_logit = max(logits)
                    exp_logits = [math.exp(l - max_logit) for l in logits]
                    sum_exp = sum(exp_logits)
                    probs = [e / sum_exp for e in exp_logits]
                    
                    probs_map = {label: prob for label, prob in zip(action_labels, probs)}
                    if step_id:
                        probs_by_step[step_id] = probs_map
                    if feedback_id:
                        probs_by_step[feedback_id] = probs_map
                    probs_by_step[f"{traj_id}:{step_idx}"] = probs_map
                    probs_by_step[f"{traj_id}:step{step_idx}"] = probs_map

            policy_data["probabilities_by_step"] = probs_by_step
            
            # 4. Run eval_metrics.evaluate(behavior_policy_artifact, dataset_payload) to get eval_result.
            eval_result = evaluate_policy(bp_artifact, dataset_payload)
            
            # 5. Update candidate with metrics, lineage, policy weights details
            candidate["status"] = "processed"
            candidate["metrics"] = eval_result.get("metrics", {})
            candidate["evaluation_summary"] = {
                "action_match_rate": eval_result.get("action_match_rate"),
                "return_gap": eval_result.get("return_gap"),
                "kl_divergence": eval_result.get("kl_divergence"),
                "evaluator_id": eval_result.get("evaluator_id"),
                "evaluation_timestamp": eval_result.get("evaluation_timestamp"),
            }
            candidate["policy_weights"] = bp_artifact.get("policy", {})
            candidate["lineage"] = bp_artifact.get("lineage", {})
            candidate["updated_at"] = utc_now()
            
        except Exception as exc:
            candidate["status"] = "failed"
            candidate["error_message"] = str(exc)
            candidate["updated_at"] = utc_now()
            
        store.put_candidate(candidate)
        processed_count += 1
        
    return processed_count


def _next_candidate_id(timestamp: str, existing: set) -> str:
    prefix = timestamp[:10].replace("-", "")
    index = len(existing) + 1
    candidate = f"sic-{prefix}-{index:03d}"
    while candidate in existing:
        index += 1
        candidate = f"sic-{prefix}-{index:03d}"
    return candidate


@app.post("/api/policy-learning/shadow-eval-tick", status_code=201)
def shadow_eval_tick(body: ShadowEvalTickBody) -> Dict[str, Any]:
    """Schedule a shadow / imitation evaluation tick over trace datasets.

    Produces gated ShadowImitationCandidate records in proposed state.
    Production training and artifact mutation remain fail-closed until a
    separate experiment approval gate is explicitly activated.
    """
    timestamp = body.ticked_at or utc_now()
    tick_id = body.tick_id or f"shadow-tick-{timestamp[:10].replace('-', '')}"
    eval_type = body.eval_type.strip().lower() if body.eval_type else "shadow"

    existing_candidates = store.list_candidates()
    already_seen = {
        str((c.get("dataset_ref") or {}).get("id") or (c.get("dataset_ref") or {}).get("dataset_id") or "")
        for c in existing_candidates
        if c.get("tick_id") == tick_id
    }

    dataset_refs = body.dataset_refs
    if not dataset_refs:
        dataset_refs = discover_eligible_datasets()

    if body.max_datasets is not None and len(dataset_refs) > body.max_datasets:
        dataset_refs = dataset_refs[: body.max_datasets]

    existing_ids = {str(c.get("candidate_id") or "") for c in existing_candidates}
    created_ids: List[str] = []
    skipped_ids: List[str] = []

    for ref in dataset_refs:
        ref_id = str(ref.get("id") or ref.get("dataset_id") or "")
        if ref_id and ref_id in already_seen:
            skipped_ids.append(ref_id)
            continue
        candidate_id = _next_candidate_id(timestamp, existing_ids | set(created_ids))
        candidate = {
            "id": candidate_id,
            "candidate_id": candidate_id,
            "tick_id": tick_id,
            "eval_type": eval_type,
            "dataset_ref": ref,
            "status": "proposed",
            "production_training": "fail_closed",
            "experiment_approval_gate": "required",
            "gate_note": "Candidate requires experiment approval and deployment gate before any production training.",
            "created_at": timestamp,
            "updated_at": timestamp,
            "created_by": body.actor_id,
        }
        store.put_candidate(candidate)
        created_ids.append(candidate_id)

    return {
        "status": "ok",
        "tick_id": tick_id,
        "eval_type": eval_type,
        "candidate_count": len(created_ids),
        "skipped_count": len(skipped_ids),
        "skipped_ids": skipped_ids,
        "candidate_ids": created_ids,
        "production_training": "fail_closed",
        "ticked_at": timestamp,
    }


@app.get("/api/policy-learning/candidates")
def list_candidates(
    tick_id: Optional[str] = None,
    eval_type: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Dict[str, Any]]:
    candidates = store.list_candidates()
    if tick_id:
        candidates = [c for c in candidates if c.get("tick_id") == tick_id]
    if eval_type:
        candidates = [c for c in candidates if str(c.get("eval_type") or "").lower() == eval_type.lower()]
    if status:
        candidates = [c for c in candidates if str(c.get("status") or "").lower() == status.lower()]
    return candidates


@app.get("/api/policy-learning/candidates/{candidate_id}")
def get_candidate(candidate_id: str) -> Dict[str, Any]:
    candidate = store.get_candidate(candidate_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="shadow imitation candidate not found")
    return candidate


@app.get("/api/policy-learning/worker/backlog")
def get_worker_backlog() -> List[Dict[str, Any]]:
    """Return the list of pending items in the backlog."""
    return list_candidates(status="proposed")


@app.get("/api/policy-learning/worker/dlq")
def get_worker_dlq() -> List[Dict[str, Any]]:
    """Return the list of failed items in the DLQ."""
    return list_candidates(status="failed")


@app.post("/api/policy-learning/worker/dlq/{candidate_id}/replay")
def replay_dlq_item(candidate_id: str) -> Dict[str, Any]:
    """Reset a failed candidate back to proposed."""
    candidate = get_candidate(candidate_id)
    if candidate.get("status") != "failed":
        raise HTTPException(status_code=400, detail="candidate is not in DLQ")
    candidate["status"] = "proposed"
    candidate["updated_at"] = utc_now()
    candidate.pop("error_message", None)
    store.put_candidate(candidate)
    _process_backlog()
    return store.get_candidate(candidate_id)


@app.post("/api/policy-learning/worker/retry/{candidate_id}")
def retry_candidate(candidate_id: str) -> Dict[str, Any]:
    """Retry processing a candidate."""
    candidate = get_candidate(candidate_id)
    candidate["status"] = "proposed"
    candidate["updated_at"] = utc_now()
    candidate.pop("error_message", None)
    store.put_candidate(candidate)
    _process_backlog()
    return store.get_candidate(candidate_id)


@app.post("/api/policy-learning/worker/process")
def trigger_backlog_processing() -> Dict[str, Any]:
    """Manually trigger backlog processing."""
    count = _process_backlog()
    return {"status": "ok", "processed_count": count}


@app.post("/api/policy-learning/worker/restart")
def restart_worker() -> Dict[str, Any]:
    """Reset all failed and proposed candidates to proposed status."""
    candidates = store.list_candidates()
    count = 0
    for c in candidates:
        if c.get("status") in ("failed", "proposed"):
            c["status"] = "proposed"
            c.pop("error_message", None)
            c["updated_at"] = utc_now()
            store.put_candidate(c)
            count += 1
    _process_backlog()
    return {"status": "ok", "reset_count": count}


@app.get("/api/policy-learning/worker/readback/{candidate_id}")
def readback_candidate_target(candidate_id: str) -> Dict[str, Any]:
    """Target readback for a candidate."""
    return get_candidate(candidate_id)
