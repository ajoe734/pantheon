"""Agora persona lab: BFF forwarding and composition only.

Every route forwards to the persona owner (harness patches, versions, shadow
comparison records) or the memory owner (lesson candidates). The BFF owns no
store. Missing owner configuration or owner data fails closed with a reason.
Paper only: no route here activates anything without the owner's own gates.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import APIRouter, Body, Header, HTTPException

from ..identity.scope import AgoraScopeResolutionError, resolve_agora_user_scope
from ..models import AgoraEnvelope, AgoraMeta

HttpRequest = Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, str]]], Tuple[int, Any]]
HANDOFF_KIND = "trainer_feedback_to_persona_update"
SHADOW_ARMS = ("base", "candidate")


def _default_http() -> HttpRequest:
    from ...research.client import default_http_request
    return default_http_request


def create_persona_lab_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    require_write_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
    http_request: Optional[HttpRequest] = None,
) -> APIRouter:
    router = APIRouter(tags=["agora-persona-lab"])
    send = http_request or _default_http()

    def _base(kind: str) -> str:
        envs = (("PANTHEON_PERSONA_API_URL", "PANTHEON_PERSONA_SERVICE_URL", "PERSONA_URL") if kind == "persona"
                else ("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_URL", "MEMORY_URL"))
        base = next((os.environ[n].strip().rstrip("/") for n in envs if os.getenv(n, "").strip()), "")
        if not base:
            raise bff_error(503, "OWNER_UNAVAILABLE", f"{kind} owner is not configured", f"{kind}_owner_unconfigured")
        return base

    def _scope(authorization, tenant, write: bool):
        identity = extract_identity(authorization)
        (require_write_role if write else require_read_role)(identity)
        try:
            return resolve_agora_user_scope(identity, utc_now=utc_now, requested_tenant_id=tenant)
        except AgoraScopeResolutionError as exc:
            raise bff_error(exc.status_code, "FORBIDDEN", exc.message, exc.reason)

    def _owner(kind: str, method: str, path: str, scope, authorization, payload=None) -> Any:
        headers = {"Authorization": authorization or "", "X-Tenant-Id": scope.tenant_id}
        code, body = send(method, _base(kind) + path, payload, headers)
        if code not in (200, 201):
            reason = body.get("detail") if isinstance(body, dict) else None
            raise bff_error(code if 400 <= code < 600 else 502, "OWNER_REFUSED",
                            f"{kind} owner refused (HTTP {code})", str(reason) if reason else f"{kind}_owner_http_{code}")
        if not isinstance(body, dict):
            raise bff_error(502, "OWNER_INVALID", f"{kind} owner returned no record", f"{kind}_owner_empty")
        return body

    def _harness(persona_id: str, scope, authorization) -> Dict[str, Any]:
        return _owner("persona", "GET", f"/api/personas/{quote(persona_id, safe='')}/harness", scope, authorization)

    def _wrap(scope, data: Any) -> Dict[str, Any]:
        return AgoraEnvelope(data=data, meta=AgoraMeta(
            snapshot_at=utc_now(), capability="agora.persona_lab.v1",
            audience=f"tenant:{scope.tenant_id}:user:{scope.user_id}")).model_dump()

    def _patch_path(persona_id: str, patch_id: str = "", tail: str = "") -> str:
        path = f"/api/personas/{quote(persona_id, safe='')}/harness/patches"
        return path + (f"/{quote(patch_id, safe='')}" if patch_id else "") + tail

    def _draft(persona_id, body, scope, authorization, patch_type: Optional[str]):
        body = dict(body)
        if patch_type is not None and body.get("patch_type") not in (None, patch_type):
            raise bff_error(422, "VALIDATION", f"patch_type must be {patch_type}", "patch_type_mismatch")
        if patch_type is not None:
            body["patch_type"] = patch_type
        body["persona_id"] = persona_id
        return _owner("persona", "POST", _patch_path(persona_id), scope, authorization, body)

    base = "/bff/agora/persona-lab/{persona_id}"
    Auth = Header(default=None)
    Ten = Header(default=None, alias="X-Tenant-Id")

    @router.get(base + "/harness")
    def read_harness(persona_id: str, authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, False)
        return _wrap(scope, _harness(persona_id, scope, authorization))

    @router.post(base + "/patches", status_code=201)
    def draft_patch(persona_id: str, body: Dict[str, Any] = Body(...), authorization: Optional[str] = Auth,
                    x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        return _wrap(scope, _draft(persona_id, body, scope, authorization, None))

    @router.post(base + "/skill-coaching/patches", status_code=201)
    def draft_skill_patch(persona_id: str, body: Dict[str, Any] = Body(...), authorization: Optional[str] = Auth,
                          x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        return _wrap(scope, _draft(persona_id, body, scope, authorization, "skill"))

    @router.post(base + "/patches/{patch_id}/shadow-runs", status_code=201)
    def run_shadow(persona_id: str, patch_id: str, authorization: Optional[str] = Auth,
                   x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        _owner("persona", "POST", _patch_path(persona_id, patch_id, "/precheck"), scope, authorization, {})
        return _wrap(scope, _owner("persona", "POST", _patch_path(persona_id, patch_id, "/shadow"), scope,
                                   authorization, {}))

    @router.post(base + "/patches/{patch_id}/submit", status_code=201)
    def submit_patch(persona_id: str, patch_id: str, body: Dict[str, Any] = Body(default_factory=dict),
                     authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        """Same pre-check and adoption pipeline; hand off to persona management review.

        The owner's existing governance reference (outside paper or operator-required) is
        resolved first and its delivery to the management review is confirmed from the owner
        outbox; otherwise an adopted patch is committed through the owner's activation gate.
        """
        scope = _scope(authorization, x_tenant_id, True)
        adoption_ref = body.get("adoption_ref")

        def row_of() -> Dict[str, Any]:
            return (_harness(persona_id, scope, authorization).get("patches") or {}).get(patch_id) or {}

        row = row_of()
        result: Dict[str, Any] = {"handoff_kind": HANDOFF_KIND, "patch_id": patch_id, "execution_authority": "none"}
        committed = None
        if not row.get("governance_proposal_ref"):
            result["precheck"] = _owner("persona", "POST", _patch_path(persona_id, patch_id, "/precheck"),
                                        scope, authorization, {})
            if adoption_ref:
                try:
                    committed = _owner("persona", "POST", _patch_path(persona_id, patch_id, "/activate"), scope,
                                       authorization, {"adoption_ref": adoption_ref})
                except HTTPException:
                    row = row_of()  # the owner may have queued an operator-required review
                    if not row.get("governance_proposal_ref"):
                        raise
        ref = row_of().get("governance_proposal_ref") if committed is None else None
        if ref:
            delivery = _owner("persona", "POST", f"/api/personas/{quote(persona_id, safe='')}/harness/deliver",
                              scope, authorization, {})
            outbox = (delivery.get("outbox") or {}).get(ref) or {}
            if outbox.get("status") != "delivered":
                raise bff_error(502, "REVIEW_NOT_DELIVERED", "management review has not consumed the handoff",
                                "management_review_not_delivered")
            result.update(review_ref=ref, review_status="delivered", review_receipt=outbox.get("receipt"))
        elif committed is not None:
            result.update(review_ref=adoption_ref, review_status="committed", committed=committed)
        else:
            raise bff_error(409, "REVIEW_UNAVAILABLE", "no management review reference exists for this patch",
                            "adoption_ref_or_governance_proposal_required")
        return _wrap(scope, result)

    def _runs(state: Dict[str, Any]) -> List[Dict[str, Any]]:
        runs = []
        for patch_id, row in sorted((state.get("patches") or {}).items()):
            for index, rec in enumerate(row.get("comparisons") or []):
                runs.append({"run_id": f"{patch_id}:{index}", "suite_id": patch_id, "patch_id": patch_id,
                             "status": rec.get("status"), "decision": rec.get("decision"),
                             "reasons": rec.get("reasons", []), "criteria": rec.get("criteria"),
                             "bundle_count": len(rec.get("bundles") or [])})
        return runs

    @router.get(base + "/evaluation/suites")
    def list_suites(persona_id: str, authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, False)
        state = _harness(persona_id, scope, authorization)
        suites: Dict[str, Dict[str, Any]] = {}
        for run in _runs(state):
            suite = suites.setdefault(run["suite_id"], {"suite_id": run["suite_id"], "runs": 0})
            suite["runs"] += 1
        return _wrap(scope, list(suites.values()))

    @router.get(base + "/evaluation/runs")
    def list_runs(persona_id: str, authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, False)
        return _wrap(scope, _runs(_harness(persona_id, scope, authorization)))

    @router.get(base + "/patches/{patch_id}/shadow-comparison")
    def shadow_view(persona_id: str, patch_id: str, authorization: Optional[str] = Auth,
                    x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, False)
        row = (_harness(persona_id, scope, authorization).get("patches") or {}).get(patch_id)
        comparisons = (row or {}).get("comparisons") or []
        if not comparisons:
            raise bff_error(404, "NOT_FOUND", "no shadow comparison recorded", "shadow_comparison_not_recorded")
        rec = comparisons[-1]
        rows = [{"decision_time": b.get("packet_id"), **{arm: b.get(arm) for arm in SHADOW_ARMS},
                 "decisions_differ": b.get("decisions_differ"), "virtual_book_delta": b.get("virtual_book_delta")}
                for b in rec.get("bundles") or []]
        return _wrap(scope, {"patch_id": patch_id, "status": rec.get("status"), "decision": rec.get("decision"),
                             "reasons": rec.get("reasons", []), "arms": list(SHADOW_ARMS), "rows": rows})

    @router.get(base + "/change-log")
    def change_log(persona_id: str, authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, False)
        state = _harness(persona_id, scope, authorization)
        patches = state.get("patches") or {}
        entries = []
        for version in state.get("versions") or []:
            patch = patches.get(version.get("patch_id") or "") or {}
            proposal = patch.get("proposal") or {}
            entries.append({"version": version.get("version"), "patch_id": version.get("patch_id"),
                            "status": patch.get("status"), "evidence_refs": proposal.get("evidence_refs", []),
                            "active": version.get("version") == state.get("active_version"),
                            "experience": patch.get("experience"),
                            "actions": ["accept", "undo", "never_again", "tell_why"]})
        return _wrap(scope, {"active_version": state.get("active_version"), "entries": entries})

    @router.post(base + "/change-log/{patch_id}/accept")
    def accept(persona_id: str, patch_id: str, body: Dict[str, Any] = Body(...), authorization: Optional[str] = Auth,
               x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        if not body.get("adoption_ref"):
            raise bff_error(422, "VALIDATION", "adoption_ref is required", "adoption_ref_required")
        return _wrap(scope, _owner("persona", "POST", _patch_path(persona_id, patch_id, "/activate"), scope,
                                   authorization, {"adoption_ref": body["adoption_ref"]}))

    @router.post(base + "/change-log/{patch_id}/undo")
    def undo(persona_id: str, patch_id: str, body: Dict[str, Any] = Body(...), authorization: Optional[str] = Auth,
             x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        if not str(body.get("reason") or "").strip():
            raise bff_error(422, "VALIDATION", "reason is required", "reason_required")
        return _wrap(scope, _owner("persona", "POST", f"/api/personas/{quote(persona_id, safe='')}/harness/rollback",
                                   scope, authorization, {"patch_id": patch_id, "reason": body["reason"]}))

    def _lesson(kind: str, persona_id, patch_id, body, scope, authorization):
        required = {"reflection_id", "trade_episode_ids", "reflection_version", "text"}
        if not required <= set(body) or not body["trade_episode_ids"] or not str(body["text"]).strip():
            raise bff_error(422, "VALIDATION", "reflection_id, trade_episode_ids, reflection_version and text required",
                            "memory_candidate_provenance_required")
        now = utc_now()
        payload = {"lesson_candidate_id": f"lab-{kind}-{persona_id}-{patch_id}", "persona_id": persona_id,
                   "tenant_id": scope.tenant_id, "scope": "persona", "reflection_id": body["reflection_id"],
                   "trade_episode_ids": body["trade_episode_ids"], "reflection_version": body["reflection_version"],
                   "proposed_change": f"[{kind}] patch {patch_id}: {body['text']}",
                   "confidence": float(body.get("confidence", 0.5)), "review_state": "proposed",
                   "created_at": now, "updated_at": now,
                   "expiry": body.get("expiry") or "", "target_env": "paper", "promotion_stage": "proposed"}
        if not payload["expiry"]:
            raise bff_error(422, "VALIDATION", "expiry is required", "expiry_required")
        return _owner("memory", "POST", "/api/memory/trade-lessons", scope, authorization, payload)

    @router.post(base + "/change-log/{patch_id}/never-again", status_code=201)
    def never_again(persona_id: str, patch_id: str, body: Dict[str, Any] = Body(...),
                    authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        reason = str(body.get("reason") or "").strip()
        if not reason:
            raise bff_error(422, "VALIDATION", "reason is required", "reason_required")
        return _wrap(scope, _owner("persona", "POST", _patch_path(persona_id, patch_id, "/negative-experience"),
                                   scope, authorization, {"reason": reason}))

    @router.post(base + "/change-log/{patch_id}/tell-why", status_code=201)
    def tell_why(persona_id: str, patch_id: str, body: Dict[str, Any] = Body(...),
                 authorization: Optional[str] = Auth, x_tenant_id: Optional[str] = Ten):
        scope = _scope(authorization, x_tenant_id, True)
        return _wrap(scope, _lesson("operator_reason", persona_id, patch_id, body, scope, authorization))

    return router
