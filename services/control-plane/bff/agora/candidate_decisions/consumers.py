"""Deliver approved measures to review-only owner APIs, never runtime writes."""
from __future__ import annotations

import json
import os
from typing import Any, Mapping
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5

from .models import canonical_sha256
from .store import CandidateDecisionConflict
try:
    from ...research.client import default_http_request
except ImportError:  # standalone BFF module imports
    from research.client import default_http_request


SUPPORTED_MEASURES = frozenset({"research_request", "memory_candidate", "persona_mutation_review"})


def measure_key(candidate: Mapping[str, Any]) -> str:
    return "agora-measure:" + canonical_sha256({
        key: candidate[key] for key in ("tenant_id", "owner_user_id", "persona_id", "interaction_id", "opinion_id", "measure_id")
    })


class RecommendedMeasureConsumers:
    def prepare(self, candidate: Mapping[str, Any], authorization: str | None):
        kind = candidate["proposal_type"]
        if kind not in SUPPORTED_MEASURES:
            raise CandidateDecisionConflict(f"Measure type {kind!r} has no downstream consumer; approval refused")
        if not authorization:
            raise CandidateDecisionConflict("Owner delivery requires the authenticated caller authorization")
        persona = candidate["persona_id"]
        key = measure_key(candidate)
        identity = str(uuid5(NAMESPACE_URL, key))
        value = candidate["proposed_value"]
        if kind == "research_request":
            envs = ("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "RESEARCH_ORCHESTRATOR_URL", "RESEARCH_ORCHESTRATOR_API_URL")
            path = "/api/research/tickets"
            resource_id = "rt-agora-" + identity
            payload = {
                "ticket_id": resource_id, "owner": persona,
                "actor_id": candidate["proposer_id"], "priority": "medium",
                "title": f"Agora research measure {candidate['measure_id']}",
                "description": json.dumps({"rationale": candidate["rationale"], "request": value,
                    "measure_id": candidate["measure_id"], "tenant_id": candidate["tenant_id"],
                    "evidence_refs": candidate["evidence_refs"]}, sort_keys=True),
                "created_at": candidate["created_at"],
            }
            id_field = "ticket_id"
        elif kind == "memory_candidate":
            # The Memory owner accepts lesson candidates. Require real reflection
            # provenance from the reviewed measure rather than inventing a reflection.
            required = {"reflection_id", "trade_episode_ids", "reflection_version", "proposed_change"}
            if not isinstance(value, dict) or set(value) != required:
                raise CandidateDecisionConflict("Memory candidate requires reflection_id, trade_episode_ids, reflection_version and proposed_change")
            envs = ("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_URL", "MEMORY_URL")
            path = "/api/memory/trade-lessons"
            resource_id = identity
            payload = {**value, "lesson_candidate_id": resource_id, "persona_id": persona,
                "tenant_id": candidate["tenant_id"], "scope": "persona",
                "confidence": candidate["confidence"], "review_state": "proposed",
                "created_at": candidate["created_at"], "updated_at": candidate["created_at"],
                "expiry": candidate["expires_at"], "target_env": "paper", "promotion_stage": "proposed"}
            id_field = "lesson_candidate_id"
        else:
            required = {"base_version", "patch_type", "proposed_change", "evidence_refs"}
            if not isinstance(value, dict) or set(value) != required:
                raise CandidateDecisionConflict("Harness proposal requires base_version, patch_type, proposed_change and evidence_refs")
            if candidate["target_id"] != persona or candidate["target_kind"] != "persona":
                raise CandidateDecisionConflict("Harness patch must target the recommending persona")
            if str(value["base_version"]) != candidate["target_version"]:
                raise CandidateDecisionConflict("Harness patch base_version must match the reviewed target_version")
            envs = ("PERSONA_URL", "PANTHEON_PERSONA_URL", "PANTHEON_PERSONA_API_URL")
            path = f"/api/personas/{quote(persona, safe='')}/harness/patches"
            resource_id = identity
            payload = {**value, "patch_id": identity, "persona_id": persona,
                "session_id": candidate["interaction_id"], "rollback_target": value["base_version"],
                "expected_effect": candidate["expected_benefit"]}
            id_field = "patch_id"
        base = next((os.environ[name].rstrip("/") for name in envs if os.getenv(name)), "")
        if not base:
            raise CandidateDecisionConflict(f"{kind} owner is not configured")
        headers = {"Authorization": authorization, "X-Tenant-Id": candidate["tenant_id"], "Idempotency-Key": key}
        if kind == "persona_mutation_review":
            # The existing Persona proposal endpoint requires persona.admin.
            # Use its already configured BFF service principal only after the
            # canonical human approval gate, and only for draft creation.
            service_token = os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN") or os.getenv("PERSONA_SERVICE_TOKEN")
            if service_token:
                headers["Authorization"] = "Bearer " + service_token
        fingerprint = canonical_sha256({"kind": kind, "payload": payload})

        def deliver():
            # Recover a successful owner write whose local transaction/response
            # was lost. IDs and request bytes remain stable across process restart.
            read_path = path + "/" + quote(resource_id, safe="")
            if kind == "persona_mutation_review":
                read_path = path.removesuffix("/patches")
            code, body = default_http_request("GET", base + read_path, None, headers)
            if kind == "persona_mutation_review" and code == 200 and isinstance(body, dict):
                body = body.get("patches", {}).get(resource_id)
                code = 200 if body else 404
            if code == 404:
                code, body = default_http_request("POST", base + path, payload, headers)
                if code == 422 and kind == "memory_candidate":
                    # Concurrent duplicate creation is an error at the Memory
                    # owner. Read and compare the deterministic candidate again.
                    code, body = default_http_request("GET", base + read_path, None, headers)
            record = body.get("proposal") if kind == "persona_mutation_review" and isinstance(body, dict) else body
            if code not in {200, 201} or not isinstance(record, dict):
                raise CandidateDecisionConflict(f"{kind} owner delivery failed (HTTP {code}); retry the approval")
            compared = {field: expected for field, expected in payload.items()
                        if not (kind == "research_request" and field == "actor_id")}
            if any(record.get(field) != expected for field, expected in compared.items()):
                raise CandidateDecisionConflict(f"{kind} owner receipt does not match the reviewed measure")
            if kind == "research_request":
                history = record.get("lifecycle_history") or []
                if not history or history[0].get("transitioned_by") != payload["actor_id"]:
                    raise CandidateDecisionConflict("research_request owner receipt actor does not match the reviewed measure")
            return {"measure_id": candidate["measure_id"], "persona_id": persona,
                "consumer": kind, "resource_id": record[id_field], "resource_path": path + "/" + resource_id,
                "idempotency_key": key, "request_sha256": fingerprint,
                "execution_authority": "none"}

        return key, fingerprint, deliver
