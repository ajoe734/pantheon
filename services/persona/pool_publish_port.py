"""Automatic paper publication of an applied pool version (design R6), as a cycle port.

``PoolPublishPort`` is mixed into ``HttpPoolCyclePorts``. It owns no publication logic of its
own: what changed is decided by the pool policy's ``publication_change`` against the durable
publish record, and the publication itself goes through the BFF publish route, i.e. the
existing publisher + coordinator + Deployment chain. The only new rule here is the
``envelope_publication`` consultation: a publication that changes live membership proceeds
only with a verified published memo of the risk persona panel; a pure re-weight does not.
Missing configuration or data fails closed with a readable reason.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping
from urllib.parse import quote

CONSULT_FAILED_STATES = ("canceled", "cancelled", "rejected", "failed", "expired")
# The executor names a consultation failed once its dead-letter recovery budget is spent.
# That is a dead end for this request, not for the membership: a replacement may be opened.
CONSULT_DEAD_LETTERED = "failed"
MAX_CONSULT_REPLACEMENTS = 2


def membership_digest(pool: Mapping[str, Any]) -> str:
    from services.persona.consult_triggers import pool_membership_signature

    return hashlib.sha256(json.dumps(pool_membership_signature(pool["members"])).encode()).hexdigest()


def envelope_publication_requirement(tenant_id: str, persona_id: str, pool: Mapping[str, Any], pool_version: int):
    """The consultation envelope_publication requires (consult_triggers rules, unmodified)."""
    from services.persona.consult_triggers import DECISION_ENVELOPE_PUBLICATION, required_consultation

    digest = membership_digest(pool)
    decision_id = "envelope-publication-" + hashlib.sha256(
        json.dumps([tenant_id, persona_id, digest, str(pool_version)]).encode()).hexdigest()[:32]
    required = required_consultation(DECISION_ENVELOPE_PUBLICATION, decision_id, initiator_persona_id=persona_id,
                                     target_version=str(pool_version), candidate_digest=digest)
    assert required is not None
    return required


class PoolPublishPort:
    """Needs ``self._call``, ``self.pool_store`` and ``self.consultation_factory`` from the host ports."""

    def publication_state(self, persona):
        out = self._call("GET", "bff", f"/bff/personas/{quote(persona['persona_id'], safe='')}/strategy-pool/publish-history", persona)
        return (out or {}).get("data", out)

    def publish_pool(self, persona, pool_version):
        out = self._call("POST", "bff", f"/bff/personas/{quote(persona['persona_id'], safe='')}/strategy-pool/publish", persona,
                         {"pool_version": pool_version, "stage": "paper"})
        return (out or {}).get("data", out)

    def envelope_publication_consultation(self, persona, pool, packet_id):
        """Open (once per live membership) or read the risk panel for this membership; verify its memo."""
        from services.persona.consult_triggers import verify_published_memo

        pid, tenant = persona["persona_id"], persona["tenant_id"]
        digest = membership_digest(pool)
        reader = self.consultation_factory(tenant)
        found = []
        for request in reader.list_requests(target_type="envelope_publication", tenant_id=tenant) or []:
            gated = (request.get("metadata") or {}).get("panel", {}).get("gated_decision", {})
            if request.get("from_persona_id") == pid and gated.get("candidate_digest") == digest:
                found.append((int(gated.get("target_version") or 0), request))
        dead = [item for item in found if str(item[1].get("status") or "") == CONSULT_DEAD_LETTERED]
        found = [item for item in found if str(item[1].get("status") or "") != CONSULT_DEAD_LETTERED]

        def open_panel(request_id):
            requirement = envelope_publication_requirement(tenant, pid, pool, pool["pool_version"])
            created = self.consultation_factory(tenant, f"persona:{pid}").open_persona_panel(
                requirement, persona_id=pid, trace_id=f"ooda://{packet_id}", tenant_id=tenant,
                task=f"Risk review of paper envelope publication of pool v{pool['pool_version']} (live membership changed)",
                evidence_refs=[f"ooda://{packet_id}"], request_id=request_id)
            return requirement, created

        if not found and dead:
            # Every consultation for this membership dead-lettered past its recovery budget: report that
            # by name (never as pending) and open one replacement so the next cycle waits on a live panel.
            latest = max(dead, key=lambda item: item[0])[1]
            failure = {"state": "failed", "reason": "consultation_dead_lettered", "failed_request_id": latest["request_id"]}
            if len(dead) > MAX_CONSULT_REPLACEMENTS:
                return {"state": "operator", "reason": "consultation_dead_lettered_retries_exhausted",
                        "request_id": latest["request_id"], "decision_id": envelope_publication_requirement(
                            tenant, pid, pool, pool["pool_version"]).decision_id}
            requirement, created = open_panel(f"consult-{envelope_publication_requirement(tenant, pid, pool, pool['pool_version']).decision_id}-r{len(dead)}")
            return {**failure, "request_id": created["request_id"], "decision_id": requirement.decision_id,
                    "replacement_opened": True}
        if not found:
            requirement, created = open_panel("consult-" + envelope_publication_requirement(
                tenant, pid, pool, pool["pool_version"]).decision_id)
            return {"state": "opened", "request_id": created["request_id"], "decision_id": requirement.decision_id}
        bound, request = max(found, key=lambda item: item[0])
        requirement = envelope_publication_requirement(tenant, pid, pool, bound)
        ids = {"decision_id": requirement.decision_id, "request_id": request["request_id"]}
        status = str(request.get("status") or "")
        if status in CONSULT_FAILED_STATES:
            return {"state": "operator", "reason": "consultation_" + status, **ids}
        if status != "published":
            return {"state": "pending", **ids}
        memos = reader.list_memos(request_id=request["request_id"], status="published")
        if not memos:
            return {"state": "pending", **ids}
        check = None
        for memo in memos:
            check = verify_published_memo(reader, memo["memo_id"], tenant_id=tenant, persona_id=pid, requirement=requirement)
            if check.ok:
                return {"state": "verified", "memo_id": memo["memo_id"], **ids}
        return {"state": "operator", "reason": check.reason, **ids}
