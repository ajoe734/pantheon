"""
services/optimizer-svc — portfolio synthesis HTTP service.

Routes
------
  GET  /__health__                           liveness probe
  POST /api/optimizer/synthesize             run multi-persona portfolio synthesis
  GET  /api/optimizer/policies/{policy_id}   read a produced AllocationPolicyArtifact
  GET  /api/optimizer/logs/{log_id}          read a ConflictResolutionLog
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from services.foundation.health import register_fastapi_health_routes

# Ensure the service directory is on the path when run via uvicorn from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from portfolio_synthesis import (  # noqa: E402
    AllocationPolicyArtifact,
    CommitteeReferral,
    ConflictResolutionLog,
    PersonaAllocationProposal,
    PoolRiskPolicy,
    PortfolioSynthesizer,
    SynthesisError,
)

app = FastAPI(title="Pantheon Optimizer Service", version="0.2.0")

# In-memory stores (v1: no persistence layer).
_policies: Dict[str, Union[AllocationPolicyArtifact, CommitteeReferral]] = {}
_logs: Dict[str, ConflictResolutionLog] = {}
register_fastapi_health_routes(
    app,
    "optimizer-svc",
    metrics=lambda: {"policy_count": len(_policies), "log_count": len(_logs)},
)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ProposalIn(BaseModel):
    proposal_id: str
    persona_id: str
    capital_pool_id: str
    scope_ref: str
    target_type: str
    directions: List[str]
    target_weights: Dict[str, float]
    conviction: float
    uncertainty: float
    rationale_ref: Optional[str] = None
    regime_ref: Optional[str] = None
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    evidence_refs: List[str]
    created_at: str
    reliability_score: float = 1.0
    regime_fit_score: float = 1.0
    governance_multiplier: float = 1.0
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SynthesizeRequest(BaseModel):
    proposals: List[ProposalIn]
    capital_pool_id: str
    scope_ref: str
    constraints_bundle: Optional[Dict[str, Any]] = None
    risk_budget: Optional[float] = None
    pool_risk_policy: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/__health__")
async def health():
    return {"status": "ok", "service": "optimizer-svc"}


@app.post("/api/optimizer/synthesize", status_code=201)
async def synthesize(req: SynthesizeRequest):
    """
    Aggregate multi-persona proposals into a single AllocationPolicyArtifact.

    Returns the artifact on success, or a CommitteeReferral when escalation
    is required.  A ConflictResolutionLog is always recorded.
    """
    try:
        proposals = [
            PersonaAllocationProposal(**p.model_dump(exclude_none=True))
            for p in req.proposals
        ]
    except (SynthesisError, Exception) as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    policy = None
    if req.pool_risk_policy:
        policy = PoolRiskPolicy(
            forbidden_asset_classes=set(req.pool_risk_policy.get("forbidden_asset_classes", [])),
            forbidden_strategy_families=set(req.pool_risk_policy.get("forbidden_strategy_families", [])),
            max_single_weight=req.pool_risk_policy.get("max_single_weight", 1.0),
        )

    synthesizer = PortfolioSynthesizer(pool_risk_policy=policy)

    try:
        result, log = synthesizer.synthesize_with_log(
            proposals=proposals,
            capital_pool_id=req.capital_pool_id,
            scope_ref=req.scope_ref,
            constraints_bundle=req.constraints_bundle,
            risk_budget=req.risk_budget,
        )
    except SynthesisError as exc:
        log = synthesizer.last_conflict_resolution_log
        if log is not None:
            _logs[log.log_id] = log
        raise HTTPException(status_code=422, detail=str(exc))

    _logs[log.log_id] = log

    if isinstance(result, CommitteeReferral):
        _policies[result.referral_id] = result
        referral_payload = result.to_dict()
        log_payload = log.to_dict()
        return {
            "outcome": "committee_referral",
            "referral_id": result.referral_id,
            "trigger_reason": result.trigger_reason,
            "proposal_ids": result.proposal_ids,
            "conflict_resolution_log_id": log.log_id,
            "committee_referral": referral_payload,
            "conflict_resolution_log": log_payload,
        }

    _policies[result.artifact_id] = result
    artifact_payload = result.to_dict()
    log_payload = log.to_dict()
    return {
        "outcome": "artifact",
        "artifact_id": result.artifact_id,
        "sponsor_persona_id": result.sponsor_persona_id,
        "synthesis_method": result.synthesis_method,
        "target_weights": result.target_weights,
        "conflict_resolution_log_id": log.log_id,
        "allocation_policy_artifact": artifact_payload,
        "conflict_resolution_log": log_payload,
    }


@app.get("/api/optimizer/policies/{policy_id}")
async def get_policy(policy_id: str):
    entry = _policies.get(policy_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="policy not found")
    if isinstance(entry, AllocationPolicyArtifact):
        return entry.to_dict()
    return entry.to_dict()


@app.get("/api/optimizer/logs/{log_id}")
async def get_log(log_id: str):
    entry = _logs.get(log_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="conflict resolution log not found")
    return entry.to_dict()


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8088"))
    uvicorn.run(app, host="0.0.0.0", port=port)
