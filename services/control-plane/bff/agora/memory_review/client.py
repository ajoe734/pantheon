"""Memory HTTP adapter; never reads BFF files or OpenClaw memory caches."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from ..committee.owner import unavailable


class MemoryReviewClient:
    def __init__(self, authorization: str, tenant_id: str):
        self.base = (os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("PANTHEON_MEMORY_SERVICE_URL")
                     or os.getenv("MEMORY_URL") or "").strip().rstrip("/")
        self.authorization = authorization
        self.tenant_id = tenant_id

    def call(self, method, path, payload=None, idempotency_key=None):
        if not self.base:
            raise unavailable("memory", "memory_owner_unconfigured")
        if not self.authorization:
            raise unavailable("memory", "memory_caller_credential_missing", 401)
        headers = {"Authorization": self.authorization, "X-Tenant-Id": self.tenant_id,
                   "Accept": "application/json", "Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        req = urllib.request.Request(self.base + path, method=method, headers=headers,
                                     data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=float(os.getenv("PANTHEON_MEMORY_API_TIMEOUT_SECONDS", "3"))) as response:
                result = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            status = exc.code if exc.code in {400, 401, 403, 404, 409, 422} else 503
            raise unavailable("memory", f"memory_owner_http_{exc.code}", status) from exc
        except (OSError, ValueError) as exc:
            raise unavailable("memory", "memory_owner_unavailable") from exc
        if not isinstance(result, dict):
            raise unavailable("memory", "memory_owner_invalid_response")
        return result

    def review(self, *, actor_id: str, persona_id: str | None, scope: str, limit: int):
        query = {"actor_id": actor_id, "actor_roles": "", "scope": scope, "limit": limit,
                 "session_id": "agora-memory-review:" + actor_id}
        if persona_id:
            query["persona_id"] = persona_id
        # Owner derives roles from the verified token and calls Governance.
        # Do not assert a session_persona_id from a caller-supplied persona.
        result = self.call("GET", "/api/memory/retrieve?" + urlencode(query))
        hits = result.get("hits")
        if not isinstance(result.get("authz"), dict) or result["authz"].get("allowed") is not True:
            raise unavailable("memory", "memory_retrieval_not_authorized", 403)
        if not isinstance(hits, list) or any(not isinstance(h, dict) or h.get("type") not in {"persona", "institutional"}
                                             or not isinstance(h.get("entry"), dict) for h in hits):
            raise unavailable("memory", "memory_owner_invalid_hits")
        for hit in hits:
            entry = hit["entry"]
            id_field = "memory_id" if hit["type"] == "persona" else "entry_id"
            if not entry.get(id_field) or entry.get("tenant_id") != self.tenant_id:
                raise unavailable("memory", "memory_entry_tenant_or_identity_mismatch")
            if (scope != "both" and hit["type"] != scope) or (hit["type"] == "persona" and hit["entry"].get("persona_id") != persona_id):
                raise unavailable("memory", "memory_owner_scope_mismatch")
        lessons = []
        if persona_id:
            candidate_result = self.call("GET", "/api/memory/trade-lessons?" + urlencode({"persona_id": persona_id}))
            lessons = candidate_result.get("candidates")
            if not isinstance(lessons, list) or any(not isinstance(c, dict) or not c.get("lesson_candidate_id")
                                                   or c.get("tenant_id") != self.tenant_id
                                                   or c.get("persona_id") != persona_id for c in lessons):
                raise unavailable("memory", "memory_owner_invalid_candidates")
        return {"entries": hits, "lesson_candidates": lessons, "authz": result["authz"]}

    def quarantine(self, memory_id, *, actor_id, reason, audit_receipt_id, idempotency_key):
        path = "/api/memory/trade-lessons/" + quote(memory_id, safe="")
        candidate = self.call("GET", path)
        if candidate.get("lesson_candidate_id") != memory_id or candidate.get("tenant_id") != self.tenant_id:
            raise unavailable("memory", "memory_candidate_scope_mismatch", 404)
        if candidate.get("target_env", "paper") != "paper":
            raise unavailable("memory", "quarantine_requires_paper_candidate", 409)
        result = self.call("POST", path + "/decide", {
            "action": "quarantine", "operator_id": actor_id, "reason": reason,
            "audit_receipt_id": audit_receipt_id, "target_env": "paper",
        }, idempotency_key=idempotency_key)
        if (result.get("lesson_candidate_id") != memory_id or result.get("tenant_id") != self.tenant_id
                or result.get("review_state") != "quarantined"):
            raise unavailable("memory", "memory_quarantine_not_confirmed")
        return result
