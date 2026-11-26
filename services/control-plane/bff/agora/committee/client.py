"""No committee store: all records and publishing belong to Consultation."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Mapping, Optional
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from services.consultation.client import ConsultationClientError, ConsultationServiceClient

from .owner import (
    annotate_trader_meeting_names,
    newest_first,
    project_meeting,
    project_meeting_case_file,
    project_meeting_proposal,
    project_panel,
    project_panel_rounds,
    trader_meeting_not_found,
    trader_owns_meeting,
    unavailable,
    unavailable_case as no_case,
)

# Owner list pages read when scoping the committee to the caller's meetings.
_OWNER_PAGE_LIMIT = 500
_OWNER_MAX_PAGES = 20


def _owner_error_reason(exc: ConsultationClientError) -> str:
    payload = exc.payload if isinstance(exc.payload, Mapping) else {}
    detail = payload.get("detail")
    if isinstance(detail, Mapping):
        for key in ("reason", "code", "error"):
            value = detail.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    for key in ("reason", "code"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(detail, str) and detail.strip() and detail.strip() in {
        "insufficient_participants",
        "servant_absent",
        "data_unavailable",
        "meeting_budget_exceeded",
        "participant_budget_exceeded",
    }:
        return detail.strip()
    return f"consultation_owner_http_{exc.status_code}" if exc.status_code else "consultation_owner_unavailable"


class CommitteeOwnerClient:
    def __init__(self, tenant_id: str, actor_id: str, *, user_id: Optional[str] = None):
        # Consultation accepts its existing configured service credential, not
        # BFF JWTs. BFF admission precedes this adapter; never mint credentials.
        self.client = ConsultationServiceClient(tenant_id=tenant_id, service_actor=f"bff:{actor_id}")
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.user_id = user_id or actor_id

    def call(
        self,
        method: str,
        path: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        if not self.client._token:
            raise unavailable("consultation", "consultation_service_credential_unconfigured")
        try:
            if payload is None and idempotency_key is None:
                return self.client._request_json(method, path, tenant_id=self.tenant_id)
            return self._request_with_headers(
                method,
                path,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        except ConsultationClientError as exc:
            status = exc.status_code if exc.status_code in {400, 401, 403, 404, 409, 422} else 503
            raise unavailable("consultation", _owner_error_reason(exc), status) from exc

    def _request_with_headers(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        if not self.client._base_url:
            raise ConsultationClientError("consultation service URL is not configured")
        body = None
        headers: dict[str, str] = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            f"{self.client._base_url}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        if self.tenant_id:
            request.add_unredirected_header("X-Pantheon-Tenant-Id", self.tenant_id)
        if self.client._token:
            request.add_unredirected_header("Authorization", f"Bearer {self.client._token}")
        if self.client._service_actor:
            request.add_unredirected_header("X-Pantheon-Service-Actor", self.client._service_actor)
        try:
            with urllib.request.urlopen(request, timeout=self.client._timeout_seconds) as response:
                text = response.read().decode("utf-8").strip()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
            except Exception:
                error_payload = {}
            detail = error_payload.get("detail") if isinstance(error_payload, dict) else None
            message = detail if isinstance(detail, str) else f"consultation API returned HTTP {exc.code}"
            raise ConsultationClientError(
                message,
                status_code=exc.code,
                payload=error_payload if isinstance(error_payload, dict) else {},
            ) from exc
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ConsultationClientError(f"consultation API unavailable: {exc}") from exc

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

    def _panel_case_file_view(self, session_id: str, *, fallback_to_meeting_on_404: bool = False) -> dict:
        try:
            transcript = self.call("GET", f"/api/consult/requests/{quote(session_id, safe='')}/transcript")
        except HTTPException as exc:
            if fallback_to_meeting_on_404 and exc.status_code == 404:
                try:
                    return self.meeting(session_id)["case_file_view"]
                except HTTPException as meeting_exc:
                    m_detail = meeting_exc.detail if isinstance(meeting_exc.detail, Mapping) else {}
                    if meeting_exc.status_code == 404 and m_detail.get("reason") in {
                        "meeting_tenant_mismatch",
                        "meeting_not_found",
                    }:
                        raise
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            return no_case(str(detail.get("reason") or "consultation_owner_unavailable"))
        return project_panel(transcript, request_id=session_id, tenant_id=self.tenant_id)

    def case_file_view(
        self,
        session_id: str,
        *,
        meeting_detail: Optional[Mapping[str, Any]] = None,
    ) -> dict:
        """Owner-persisted case file of a panel or deliberative meeting; failure is a recorded reason, never a guess."""
        if isinstance(meeting_detail, Mapping):
            existing = meeting_detail.get("case_file_view")
            if isinstance(existing, dict):
                return existing
            return project_meeting_case_file(meeting_detail, tenant_id=self.tenant_id, meeting_id=session_id)
        if str(session_id).startswith("mtg-"):
            try:
                return self.meeting(session_id)["case_file_view"]
            except HTTPException as exc:
                if exc.status_code == 404:
                    raise
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                return no_case(str(detail.get("reason") or "consultation_owner_unavailable"))
        return self._panel_case_file_view(session_id, fallback_to_meeting_on_404=True)

    def _panel_with_rounds(self, item: Mapping[str, Any], *, include_case_file: bool = False) -> dict:
        sid = str(item["request_id"])
        transcript = None
        view = None
        if include_case_file:
            try:
                transcript = self.call("GET", f"/api/consult/requests/{quote(sid, safe='')}/transcript")
                view = project_panel(transcript, request_id=sid, tenant_id=self.tenant_id)
            except HTTPException as exc:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                view = no_case(str(detail.get("reason") or "consultation_owner_unavailable"))
        rounds = project_panel_rounds(transcript, case_file_view=view)
        result = {
            **dict(item),
            "session_id": sid,
            "session_class": "gate_panel",
            "round": 1,
            "rounds": rounds,
        }
        if include_case_file:
            result["case_file_view"] = view
        return result

    def meeting(self, meeting_id: str) -> dict:
        item = self.call("GET", f"/api/consult/meetings/{quote(meeting_id, safe='')}")
        if not isinstance(item, Mapping) or str(item.get("meeting_id") or "") != meeting_id:
            raise unavailable("consultation", "invalid_meeting_response")
        return project_meeting(item, tenant_id=self.tenant_id, user_id=self.user_id)

    def meetings(
        self,
        *,
        status: Optional[str] = None,
        meeting_type: Optional[str] = None,
        persona_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        include_rounds: bool = True,
        tolerate_unmounted_list: bool = False,
    ) -> list[dict]:
        query: dict[str, Any] = {}
        if status:
            query["status"] = status
        if meeting_type:
            query["type"] = meeting_type
        if persona_id:
            query["persona_id"] = persona_id
        if cursor:
            query["cursor"] = cursor
        if limit is not None:
            query["limit"] = limit
        path = "/api/consult/meetings" + ("?" + urlencode(query) if query else "")
        try:
            payload = self.call("GET", path)
        except HTTPException as exc:
            if tolerate_unmounted_list and exc.status_code == 404:
                return []
            raise
        if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
            raw_items = payload["items"]
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raise unavailable("consultation", "invalid_meeting_list_response")

        out: list[dict] = []
        for raw in raw_items:
            if not isinstance(raw, Mapping) or not raw.get("meeting_id"):
                raise unavailable("consultation", "invalid_meeting_list_response")
            if raw.get("tenant_id") is not None and str(raw.get("tenant_id")).strip() != self.tenant_id:
                raise unavailable("consultation", "meeting_tenant_mismatch")
            mid = str(raw["meeting_id"])
            detail: Optional[Mapping[str, Any]] = None
            if include_rounds and not isinstance(raw.get("rounds"), list):
                detail_resp = self.call("GET", f"/api/consult/meetings/{quote(mid, safe='')}")
                if not isinstance(detail_resp, Mapping) or str(detail_resp.get("meeting_id") or "") != mid:
                    raise unavailable("consultation", "invalid_meeting_response")
                detail = detail_resp
            try:
                projected = project_meeting(
                    raw,
                    tenant_id=self.tenant_id,
                    user_id=self.user_id,
                    detail=detail,
                )
            except HTTPException as exc:
                detail_obj = exc.detail if isinstance(exc.detail, Mapping) else {}
                if exc.status_code == 404 and detail_obj.get("reason") == "meeting_not_found":
                    continue
                raise
            if persona_id and not (
                projected.get("chair_persona_id") == persona_id
                or any(p.get("persona_id") == persona_id for p in (projected.get("participants") or []))
            ):
                continue
            if status and str(projected.get("status") or "") != status:
                continue
            if meeting_type and str(projected.get("meeting_type") or "") != meeting_type:
                continue
            out.append(projected)
        return out

    def sessions(
        self,
        *,
        status: Optional[str] = None,
        meeting_type: Optional[str] = None,
        persona_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
        session_class: Optional[str] = None,
    ) -> list[dict]:
        results: list[dict] = []
        if session_class in {None, "", "all", "gate_panel", "persona_panel"}:
            for panel_item in self.panels():
                if status and str(panel_item.get("status") or "") != status:
                    continue
                if persona_id and str(panel_item.get("from_persona_id") or "") != persona_id:
                    continue
                results.append(self._panel_with_rounds(panel_item, include_case_file=False))
        if session_class in {None, "", "all", "deliberative_meeting", "persona_meeting"}:
            meetings = self.meetings(
                status=status,
                meeting_type=meeting_type,
                persona_id=persona_id,
                cursor=cursor,
                limit=limit,
                include_rounds=True,
                tolerate_unmounted_list=True,
            )
            results.extend(meetings)
        return results

    def session_detail(self, session_id: str) -> dict:
        if str(session_id).startswith("mtg-"):
            return self.meeting(session_id)
        try:
            panel_item = self.panel(session_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            return self.meeting(session_id)
        return self._panel_with_rounds(panel_item, include_case_file=True)

    def proposals(
        self,
        *,
        status: Optional[str] = None,
        persona_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
    ) -> list[dict]:
        query: dict[str, str] = {}
        if status:
            query["status"] = status
        if persona_id:
            query["persona_id"] = persona_id
        if meeting_id:
            query["meeting_id"] = meeting_id
        path = "/api/consult/meetings/proposals" + ("?" + urlencode(query) if query else "")
        payload = self.call("GET", path)
        if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
            raw_items = payload["items"]
        elif isinstance(payload, list):
            raw_items = payload
        else:
            raise unavailable("consultation", "invalid_meeting_proposal_list_response")
        if any(
            isinstance(r, Mapping)
            and r.get("tenant_id") is not None
            and str(r.get("tenant_id")).strip() != self.tenant_id
            for r in raw_items
        ):
            raise unavailable("consultation", "meeting_proposal_tenant_mismatch")
        return [project_meeting_proposal(item, meeting_id=meeting_id) for item in raw_items]

    def catalogue(self) -> dict:
        payload = self.call("GET", "/api/consult/meetings/catalogue")
        if (
            not isinstance(payload, Mapping)
            or not isinstance(payload.get("types"), list)
            or not isinstance(payload.get("schedule"), list)
            or not isinstance(payload.get("refusals"), list)
        ):
            raise unavailable("consultation", "invalid_meeting_catalogue_response")
        return {
            "types": list(payload["types"]),
            "schedule": list(payload["schedule"]),
            "refusals": list(payload["refusals"]),
        }

    def convene_meeting(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        if not idempotency_key or not str(idempotency_key).strip():
            raise unavailable("consultation", "missing_idempotency_key", 400)
        response = self.call(
            "POST",
            "/api/consult/meetings",
            payload=payload,
            idempotency_key=str(idempotency_key).strip(),
        )
        if (
            not isinstance(response, Mapping)
            or not str(response.get("meeting_id") or "").strip()
            or not str(response.get("status") or "").strip()
        ):
            raise unavailable("consultation", "invalid_convene_meeting_response")
        if response.get("tenant_id") is not None and str(response.get("tenant_id")).strip() != self.tenant_id:
            raise unavailable("consultation", "meeting_tenant_mismatch")
        return dict(response)

    def memos(self, session_id: str, *, meeting_detail: Optional[dict] = None) -> list:
        is_panel = False
        if meeting_detail is not None:
            pass
        elif str(session_id).startswith("mtg-"):
            meeting_detail = self.meeting(session_id)
            is_panel = False
        else:
            try:
                self.panel(session_id)
                is_panel = True
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                meeting_detail = self.meeting(session_id)
                is_panel = False
        items = self.call("GET", "/api/consult/memos?request_id=" + quote(session_id, safe=""))
        if not isinstance(items, list) or any(not isinstance(r, dict) or not r.get("memo_id")
                                             or r.get("request_id") != session_id for r in items):
            if not is_panel and meeting_detail is not None:
                synthesis = meeting_detail.get("synthesis") if isinstance(meeting_detail.get("synthesis"), Mapping) else {}
                dissent_id = synthesis.get("dissent_memo_id")
                if dissent_id:
                    memo = self.call("GET", "/api/consult/memos/" + quote(str(dissent_id), safe=""))
                    if isinstance(memo, dict) and memo.get("memo_id") == dissent_id:
                        items = [memo]
                    else:
                        items = []
                else:
                    items = []
            else:
                raise unavailable("consultation", "invalid_memo_list_response")
        if not is_panel and meeting_detail is not None:
            view = self.case_file_view(session_id, meeting_detail=meeting_detail)
            return [{**m, "case_file_view": view} for m in items]
        return items

    # ---- Trader-scoped committee reads (operator decision 2026-11-26) -------------

    def _display_names(self) -> Any:
        try:
            from ...core.display_names import current_resolver

            return current_resolver()
        except Exception:  # noqa: BLE001 - names fall back to ids
            return None

    def _owner_meeting_rows(
        self,
        *,
        status: Optional[str] = None,
        meeting_type: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> list[Mapping[str, Any]]:
        """Every owner list row for the tenant, following the owner cursor (bounded)."""
        rows: list[Mapping[str, Any]] = []
        cursor: Optional[str] = None
        for _ in range(_OWNER_MAX_PAGES):
            query: dict[str, Any] = {"limit": _OWNER_PAGE_LIMIT}
            if status:
                query["status"] = status
            if meeting_type:
                query["type"] = meeting_type
            if persona_id:
                query["persona_id"] = persona_id
            if cursor:
                query["cursor"] = cursor
            try:
                payload = self.call("GET", "/api/consult/meetings?" + urlencode(query))
            except HTTPException as exc:
                if exc.status_code == 404:
                    return rows
                raise
            if isinstance(payload, Mapping) and isinstance(payload.get("items"), list):
                page, cursor = payload["items"], payload.get("next_cursor")
            elif isinstance(payload, list):
                page, cursor = payload, None
            else:
                raise unavailable("consultation", "invalid_meeting_list_response")
            for raw in page:
                if not isinstance(raw, Mapping) or not raw.get("meeting_id"):
                    raise unavailable("consultation", "invalid_meeting_list_response")
                if raw.get("tenant_id") is not None and str(raw.get("tenant_id")).strip() != self.tenant_id:
                    raise unavailable("consultation", "meeting_tenant_mismatch")
                rows.append(raw)
            if not cursor:
                return rows
        raise unavailable("consultation", "meeting_list_page_budget_exceeded")

    def _raw_meeting(self, meeting_id: str) -> Mapping[str, Any]:
        item = self.call("GET", f"/api/consult/meetings/{quote(meeting_id, safe='')}")
        if not isinstance(item, Mapping) or str(item.get("meeting_id") or "") != meeting_id:
            raise unavailable("consultation", "invalid_meeting_response")
        return item

    def _trader_raw_rows(self, **filters: Any) -> list[tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]]:
        """Owner rows the caller owns, with the detail when it was needed to decide."""
        out: list[tuple[Mapping[str, Any], Optional[Mapping[str, Any]]]] = []
        for raw in self._owner_meeting_rows(**filters):
            if trader_owns_meeting(raw, self.user_id):
                out.append((raw, None))
                continue
            # An MT-4 subject may be named only in the attribution record of the detail.
            kinds = {str(raw.get(k) or "").strip().lower() for k in ("meeting_code", "meeting_type", "meeting_slug")}
            if kinds & {"mt-4", "mt4", "performance_attribution_review"} and not isinstance(
                raw.get("attribution_record"), Mapping
            ):
                detail = self._raw_meeting(str(raw["meeting_id"]))
                if trader_owns_meeting({**dict(raw), **dict(detail)}, self.user_id):
                    out.append((raw, detail))
        return out

    def _project_trader_meeting(self, raw: Mapping[str, Any], detail: Optional[Mapping[str, Any]], names: Any) -> Optional[dict]:
        if detail is None and not isinstance(raw.get("rounds"), list):
            detail = self._raw_meeting(str(raw["meeting_id"]))
        try:
            projected = project_meeting(raw, tenant_id=self.tenant_id, user_id=self.user_id, detail=detail)
        except HTTPException as exc:
            reason = exc.detail.get("reason") if isinstance(exc.detail, Mapping) else None
            if exc.status_code == 404 and reason == "meeting_not_found":
                return None
            raise
        if not trader_owns_meeting(projected, self.user_id):
            return None
        projected.setdefault("created_at", raw.get("created_at"))
        projected.setdefault("closed_at", raw.get("closed_at"))
        return annotate_trader_meeting_names(projected, user_id=self.user_id, names=names)

    def trader_meetings(
        self,
        *,
        status: Optional[str] = None,
        meeting_type: Optional[str] = None,
        persona_id: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """Meetings the caller convened plus MT-4 reviews of the caller, newest first."""
        owned = self._trader_raw_rows(status=status, meeting_type=meeting_type, persona_id=persona_id)
        owned.sort(
            key=lambda pair: (str(pair[0].get("created_at") or ""), str(pair[0].get("meeting_id") or "")),
            reverse=True,
        )
        start = 0
        if cursor:
            try:
                start = max(0, int(cursor))
            except ValueError:
                ids = [str(raw.get("meeting_id")) for raw, _ in owned]
                start = ids.index(cursor) + 1 if cursor in ids else len(ids)
        window = owned[start:start + limit] if limit is not None else owned[start:]
        names = self._display_names()
        out: list[dict] = []
        for raw, detail in window:
            projected = self._project_trader_meeting(raw, detail, names)
            if projected is None:
                continue
            if persona_id and not (
                projected.get("chair_persona_id") == persona_id
                or any(p.get("persona_id") == persona_id for p in (projected.get("participants") or []))
            ):
                continue
            out.append(projected)
        return newest_first(out)

    def trader_meeting(self, meeting_id: str) -> dict:
        """One meeting in the caller's set; anything else is the same non-enumerating 404."""
        mid = str(meeting_id or "").strip()
        if not mid:
            raise trader_meeting_not_found()
        try:
            raw = self._raw_meeting(mid)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise trader_meeting_not_found() from exc
            raise
        if not trader_owns_meeting(raw, self.user_id):
            raise trader_meeting_not_found()
        try:
            projected = self._project_trader_meeting(raw, raw, self._display_names())
        except HTTPException as exc:
            if exc.status_code == 404:  # e.g. tenant mismatch: same answer as a missing meeting
                raise trader_meeting_not_found() from exc
            raise
        if projected is None:
            raise trader_meeting_not_found()
        return projected

    def trader_proposals(
        self,
        *,
        status: Optional[str] = None,
        persona_id: Optional[str] = None,
        meeting_id: Optional[str] = None,
    ) -> list[dict]:
        """Proposals of the caller's meetings only."""
        if meeting_id:
            owned_ids = {str(self.trader_meeting(meeting_id)["meeting_id"])}
        else:
            owned_ids = {str(raw.get("meeting_id")) for raw, _ in self._trader_raw_rows()}
            if not owned_ids:
                return []
        items = self.proposals(status=status, persona_id=persona_id, meeting_id=meeting_id)
        return [p for p in items if str(p.get("meeting_id") or "") in owned_ids]

    def trader_memos(self, session_id: str) -> list:
        return self.memos(session_id, meeting_detail=self.trader_meeting(session_id))

    def trader_publish(self, session_id: str, memo_id: str) -> dict:
        self.trader_meeting(session_id)
        return self.publish(session_id, memo_id)

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
