"""No committee store: all records and publishing belong to Consultation."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import HTTPException

from services.consultation.client import ConsultationClientError, ConsultationServiceClient

from .owner import unavailable


class CommitteeOwnerClient:
    def __init__(self, tenant_id: str, actor_id: str):
        # Consultation accepts its existing configured service credential, not
        # BFF JWTs. BFF admission precedes this adapter; never mint credentials.
        self.client = ConsultationServiceClient(tenant_id=tenant_id, service_actor=f"bff:{actor_id}")
        self.tenant_id = tenant_id

    def call(self, method: str, path: str):
        if not self.client._token:
            raise unavailable("consultation", "consultation_service_credential_unconfigured")
        try:
            return self.client._request_json(method, path, tenant_id=self.tenant_id)
        except ConsultationClientError as exc:
            status = exc.status_code if exc.status_code in {400, 401, 403, 404, 409, 422} else 503
            raise unavailable("consultation", f"consultation_owner_http_{exc.status_code}" if exc.status_code
                              else "consultation_owner_unavailable", status) from exc

    def panel(self, session_id: str) -> dict:
        item = self.call("GET", f"/api/consult/requests/{quote(session_id, safe='')}")
        if not isinstance(item, dict) or item.get("request_id") != session_id:
            raise unavailable("consultation", "invalid_panel_response")
        if item.get("tenant_id") != self.tenant_id or item.get("consultation_type") != "persona_panel":
            raise unavailable("consultation", "panel_not_found", 404)
        return item

    def panels(self) -> list:
        items = self.call("GET", "/api/consult/requests")
        if not isinstance(items, list) or any(not isinstance(r, dict) or not r.get("request_id") for r in items):
            raise unavailable("consultation", "invalid_panel_list_response")
        if any(r.get("tenant_id") != self.tenant_id for r in items):
            raise unavailable("consultation", "panel_tenant_mismatch")
        return [r for r in items if r.get("consultation_type") == "persona_panel"]

    def case_file_view(self, session_id: str) -> dict:
        """Owner-persisted case file of a panel; failure is a recorded reason, never a guess."""
        from .owner import project_panel, unavailable_case as no_case
        try:
            transcript = self.call("GET", f"/api/consult/requests/{quote(session_id, safe='')}/transcript")
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return no_case(str(detail.get("reason") or "consultation_owner_unavailable"))
        return project_panel(transcript, request_id=session_id, tenant_id=self.tenant_id)

    def memos(self, session_id: str) -> list:
        self.panel(session_id)
        items = self.call("GET", "/api/consult/memos?request_id=" + quote(session_id, safe=""))
        if not isinstance(items, list) or any(not isinstance(r, dict) or not r.get("memo_id")
                                             or r.get("request_id") != session_id for r in items):
            raise unavailable("consultation", "invalid_memo_list_response")
        return items

    def publish(self, session_id: str, memo_id: str) -> dict:
        self.panel(session_id)
        path = "/api/consult/memos/" + quote(memo_id, safe="")
        # Read before write: the owner may return an already-published memo
        # early, so bind the memo to the selected tenant/panel before publishing.
        item = self.call("GET", path)
        self.check_memo(item, session_id, memo_id)
        published = self.call("POST", path + "/publish")
        self.check_memo(published, session_id, memo_id)
        if published.get("status") != "published":
            raise unavailable("consultation", "memo_publish_not_confirmed")
        return published

    @staticmethod
    def check_memo(item, session_id: str, memo_id: str):
        if not isinstance(item, dict) or item.get("memo_id") != memo_id or item.get("request_id") != session_id:
            raise unavailable("consultation", "memo_panel_mismatch", 404)
