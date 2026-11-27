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
from urllib.parse import quote, urlencode

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
    """Needs ``self._call``, ``self._is_not_found``, ``self.pool_store`` and ``self.consultation_factory`` from the host ports."""

    def publication_state(self, persona):
        out = self._call("GET", "bff", f"/bff/personas/{quote(persona['persona_id'], safe='')}/strategy-pool/publish-history", persona)
        return (out or {}).get("data", out)

    def publish_pool(self, persona, pool_version):
        out = self._call("POST", "bff", f"/bff/personas/{quote(persona['persona_id'], safe='')}/strategy-pool/publish", persona,
                         {"pool_version": pool_version, "stage": "paper", "defer_confirmation": True})
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
                bound = max(dead, key=lambda item: item[0])[0] or pool["pool_version"]
                return {"state": "operator", "reason": "consultation_dead_lettered_retries_exhausted",
                        "request_id": latest["request_id"], "pool_version": bound, "decision_id": envelope_publication_requirement(
                            tenant, pid, pool, bound).decision_id}
            requirement, created = open_panel(f"consult-{envelope_publication_requirement(tenant, pid, pool, pool['pool_version']).decision_id}-r{len(dead)}")
            return {**failure, "request_id": created["request_id"], "decision_id": requirement.decision_id,
                    "replacement_opened": True}
        if not found:
            requirement, created = open_panel("consult-" + envelope_publication_requirement(
                tenant, pid, pool, pool["pool_version"]).decision_id)
            return {"state": "opened", "request_id": created["request_id"], "decision_id": requirement.decision_id}
        bound, request = max(found, key=lambda item: item[0])
        requirement = envelope_publication_requirement(tenant, pid, pool, bound)
        ids = {"decision_id": requirement.decision_id, "request_id": request["request_id"], "pool_version": bound}
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
        return {"state": "operator", "reason": check.reason, "memo_id": memo["memo_id"], **ids}

    # --- operator decision for an escalated envelope publication -------------------------------------
    # The same governance queue trial admissions use (evolution proposals, flag_for_review). One entry per
    # escalated consultation: its decision id binds tenant, persona, live membership and pool version, so a
    # repeat cycle reads the same entry and never queues a second one.

    @staticmethod
    def publication_operator_decision_id(consult: Mapping[str, Any]) -> str:
        return "pool-publication-op-" + hashlib.sha256(str(consult["decision_id"]).encode()).hexdigest()[:24]

    def publication_operator_decision(self, persona, consult):
        """The operator's decision on this escalated publication as the evolution owner holds it, or None.

        Reads ``GET /api/evolution/proposals/{id}``, the record Management's ReviewMutation /
        ApproveEvolutionDecision / RejectMutation commands write. A record that does not bind this tenant,
        persona and consultation fails closed (raises) instead of being taken as an approval.
        """
        decision_id = self.publication_operator_decision_id(consult)
        try:
            record = self._call("GET", "evolution", "/api/evolution/proposals/" + quote(decision_id, safe=""), persona)
        except Exception as exc:  # noqa: BLE001 - only a confirmed absence means "not queued yet"
            if self._is_not_found(exc):
                return None
            raise
        record = (record or {}).get("data", record) or {}
        metadata = record.get("metadata") or {}
        if (record.get("decision_id") != decision_id or record.get("tenant_id") != persona["tenant_id"]
                or record.get("persona_id") != persona["persona_id"]
                or metadata.get("consultation_decision_id") != consult["decision_id"]):
            raise ValueError("operator_decision_binding_mismatch:" + decision_id)
        chain = [step for step in record.get("review_chain") or [] if isinstance(step, Mapping)]
        last = chain[-1] if chain else {}
        return {"decision_id": decision_id, "decision_state": str(record.get("decision_state") or ""),
                "actor_id": last.get("actor_id"), "note": last.get("note"),
                "approval_decision_id": record.get("approval_decision_id")}

    def queue_publication_operator_decision(self, persona, pool, consult):
        """Queue the escalated publication once as an operator decision (paper only, never auto-approved)."""
        decision_id = self.publication_operator_decision_id(consult)
        pool_version = int(consult.get("pool_version") or pool["pool_version"])
        reason = consult.get("reason") or "consultation_operator"
        body = {"decision_id": decision_id, "tenant_id": persona["tenant_id"], "target_type": "persona",
                "target_id": persona["persona_id"], "target_version": str(pool_version),
                "action_type": "flag_for_review", "created_by_id": "persona-svc", "created_by_role": "evolution_controller",
                "persona_id": persona["persona_id"],
                "rationale": (f"Paper envelope publication of strategy pool v{pool_version} (live membership changed) "
                              f"needs an operator decision: {reason}"),
                "evidence_refs": [{"ref_type": "service_handoff", "ref_id": consult.get("request_id") or consult["decision_id"],
                                   "storage_ref": {"owner": "consultation-svc", "persona_id": persona["persona_id"],
                                                   "kind": "envelope_publication_consultation"}}],
                "metadata": {"operator_required": True, "decision_kind": "pool_envelope_publication", "reason": reason,
                             "pool_version": pool_version, "membership_digest": membership_digest(pool),
                             "consultation_decision_id": consult["decision_id"],
                             "consultation_request_id": consult.get("request_id"),
                             "consultation_memo_id": consult.get("memo_id"), "paper_only": True}}
        try:
            self._call("POST", "evolution", "/api/evolution/proposals", persona, body)
        except Exception as exc:  # noqa: BLE001 - a repeated id is the existing queue entry, not a new one
            if getattr(exc, "code", None) != 409:
                raise
            return {"decision_id": decision_id, "status": "already_queued"}
        return {"decision_id": decision_id, "status": "queued"}

    def supersede_publication_operator_decisions(self, persona, pool, confirmed):
        """Withdraw our pending escalations for the now-confirmed membership only."""
        from services.control_plane.persona.pool_policy import load_policy, publication_change

        if confirmed.get("status") != "published" or publication_change(confirmed, pool, load_policy())["membership_changed"]:
            return []
        digest = membership_digest(pool)
        query = urlencode({"target_type": "persona", "target_id": persona["persona_id"],
                           "tenant_id": persona["tenant_id"], "decision_state": "proposed,reviewed"})
        records = self._call("GET", "evolution", "/api/evolution/proposals?" + query, persona)
        retired = []
        for record in records or []:
            meta = record.get("metadata") or {}
            if (record.get("tenant_id") != persona["tenant_id"] or record.get("persona_id") != persona["persona_id"]
                    or record.get("created_by_id") != "persona-svc"
                    or record.get("decision_state") not in ("proposed", "reviewed")
                    or meta.get("decision_kind") != "pool_envelope_publication"
                    or meta.get("paper_only") is not True or meta.get("membership_digest") != digest):
                continue
            decision_id = record["decision_id"]
            self._call("POST", "evolution", "/api/evolution/proposals/" + quote(decision_id, safe="") + "/supersede", persona,
                       {"tenant_id": persona["tenant_id"], "actor_id": "persona-svc", "actor_role": "evolution_controller",
                        "membership_digest": digest, "superseded_by": confirmed["envelope_artifact_id"],
                        "note": "publication_membership_already_confirmed"})
            retired.append(decision_id)
        return retired
