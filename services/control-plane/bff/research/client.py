"""HTTP client for communicating with the authoritative Research orchestrator service."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple
from services.control_plane.bff.core.owner_reads import authorization, verified_caller_tenant, remaining_management_read_budget
from services.control_plane.bff.command_adapters.base import ActionUnavailableError
import urllib.error
import urllib.request
import urllib.parse

log = logging.getLogger(__name__)


def resolve_orchestrator_base_url() -> Optional[str]:
    return (
        os.getenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL")
        or os.getenv("RESEARCH_ORCHESTRATOR_URL")
        or os.getenv("RESEARCH_ORCHESTRATOR_API_URL")
    )


class ResearchCommandError(Exception):
    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class ResearchCommandUnavailableError(ResearchCommandError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503)


HttpRequestFn = Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, str]]], Tuple[int, Optional[Any]]]


def default_http_request(
    method: str,
    url: str,
    payload: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Any]]:
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
    try:
        timeout = float(os.getenv("RESEARCH_ORCHESTRATOR_TIMEOUT_SECONDS", "3.0"))
        budget = remaining_management_read_budget(timeout)
        if budget is not None:
            if budget <= 0:
                return 503, {"detail": "Research owner read_timeout"}
            timeout = min(timeout, budget)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            # Ticket pages contain bounded summaries; reject oversized replies
            # rather than materializing an unpaged/archive response in the BFF.
            raw = resp.read(4 * 1024 * 1024 + 1) if "/api/research/tickets" in url else resp.read()
            if "/api/research/tickets" in url and len(raw) > 4 * 1024 * 1024:
                return 502, {"detail": "Research ticket response exceeds 4 MiB"}
            return resp.status, json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            body = {"detail": raw.decode("utf-8", errors="replace")}
        return exc.code, body
    except Exception as exc:
        log.warning("Research command %s %s failed transport: %s", method, url, exc)
        return 503, {"detail": str(exc)}


@dataclass
class ResearchServiceClient:
    """HTTP client communicating with authoritative Research service for tickets, experiments, and notes."""

    base_url: Optional[str] = None
    http_request: HttpRequestFn = field(default=default_http_request)

    def _url(self, path: str) -> str:
        url = self.base_url or resolve_orchestrator_base_url()
        if not url:
            raise ResearchCommandUnavailableError(
                "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL)."
            )
        return f"{url.rstrip('/')}{path}"

    def _call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, *, allow_none: Tuple[int, ...] = (), headers: Optional[Dict[str, str]] = None) -> Any:
        url = self._url(path)
        code, body = self.http_request(method, url, payload, headers)
        if code in (200, 201, 202):
            return body
        if code in allow_none:
            return None
        detail = (body or {}).get("detail") if isinstance(body, dict) else None
        if code == 503:
            raise ResearchCommandUnavailableError(detail or "Research write owner unavailable")
        raise ResearchCommandError(detail or f"Research command failed (HTTP {code})", status_code=code)

    # Tickets (RW-01)
    def _ticket_headers(self) -> Dict[str, str]:
        # Forward the authenticated caller; a body actor or service identity is
        # never a substitute for the owner's tenant/role/transition checks.
        auth = authorization.get()
        if not auth:
            raise ResearchCommandError("Research tickets require caller authorization", 401)
        try:
            tenant = verified_caller_tenant()
        except ActionUnavailableError as exc:
            raise ResearchCommandError("Research ticket tenant mismatch", 403) from exc
        return {"Authorization": auth, "X-Tenant-Id": tenant}

    def list_research_ticket_page(self, **filters: Any) -> Dict[str, Any]:
        params = {k: v for k, v in filters.items() if v is not None}
        params["meeting_linked"] = "true"
        statuses = params.pop("statuses", None)
        if statuses:
            params["status"] = ",".join(statuses)
        raw = self._call("GET", "/api/research/tickets?" + urllib.parse.urlencode(params), headers=self._ticket_headers())
        if not isinstance(raw, dict) or not isinstance(raw.get("data"), list) or not isinstance(raw.get("page_info"), dict):
            raise ResearchCommandUnavailableError("Invalid paged Research ticket response")
        if len(raw["data"]) > int(params.get("page_size", 100)):
            raise ResearchCommandUnavailableError("Research ticket owner exceeded requested page size")
        return raw

    def create_research_ticket(self, **kwargs: Any) -> Dict[str, Any]:
        return self._call("POST", "/api/research/tickets", kwargs, headers=self._ticket_headers()) or {}
    def patch_research_ticket(self, ticket_id: str, *, patch: Dict[str, Any], **kw: Any) -> Optional[Dict[str, Any]]:
        return self._call("PATCH", f"/api/research/tickets/{urllib.parse.quote(ticket_id, safe='')}", {"patch": patch, **kw}, allow_none=(404,), headers=self._ticket_headers())
    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._call("GET", f"/api/research/tickets/{urllib.parse.quote(ticket_id, safe='')}", allow_none=(404,), headers=self._ticket_headers()) if ticket_id else None
    def list_research_tickets(self, *, statuses: Optional[Any] = None, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        params = {}
        if statuses:
            params["status"] = ",".join(str(s) for s in statuses) if isinstance(statuses, (list, tuple, set)) else statuses
        if owner:
            params["owner"] = owner
        query = "?" + urllib.parse.urlencode(params) if params else ""
        res = self._call("GET", f"/api/research/tickets{query}", allow_none=(404,), headers=self._ticket_headers())
        return res if isinstance(res, list) else []

    # Experiments (RW-04)
    def create_research_experiment(self, **kwargs: Any) -> Dict[str, Any]:
        return self._call("POST", "/api/research/experiments", kwargs) or {}
    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._call("GET", f"/api/research/experiments/{experiment_id}", allow_none=(404,)) if experiment_id else None
    def list_research_experiments(self, *, ticket_id: Optional[str] = None, status: Optional[str] = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        params = [f"{k}={v}" for k, v in (("ticket_id", ticket_id), ("status", status), ("include_archived", "true" if include_archived else None)) if v is not None]
        query = f"?{'&'.join(params)}" if params else ""
        res = self._call("GET", f"/api/research/experiments{query}", allow_none=(404,))
        return res if isinstance(res, list) else []
    def cancel_research_experiment(self, eid: str, **kw: Any) -> Optional[Dict[str, Any]]:
        return self._call("POST", f"/api/research/experiments/{eid}/cancel", kw or None, allow_none=(404, 409))
    def retry_research_experiment(self, eid: str, **kw: Any) -> Optional[Dict[str, Any]]:
        return self._call("POST", f"/api/research/experiments/{eid}/retry", kw or None, allow_none=(404, 409))
    def archive_research_experiment(self, eid: str, **kw: Any) -> Optional[Dict[str, Any]]:
        return self._call("POST", f"/api/research/experiments/{eid}/archive", kw or None, allow_none=(404, 409))
    def invalidate_research_experiment(self, eid: str, **kw: Any) -> Optional[Dict[str, Any]]:
        return self._call("POST", f"/api/research/experiments/{eid}/invalidate", kw or None, allow_none=(404, 409))

    # Notes (KW-02)
    def list_research_notes(self) -> List[Dict[str, Any]]:
        # No allow_none: a 404 or a non-list body is an owner failure, never a genuine
        # empty collection, so callers can tell a failed read from zero rows.
        res = self._call("GET", "/api/research/notes")
        if not isinstance(res, list):
            raise ResearchCommandError("Research note collection response was not a list", status_code=502)
        return res
    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._call("GET", f"/api/research/notes/{note_id}", allow_none=(404,)) if note_id else None
    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._call("POST", "/api/research/notes", note, allow_none=(400, 404))

    # Saved run-output artifacts (Management Artifact catalog): caller-scoped, request-time reads.
    @staticmethod
    def _caller_headers() -> Dict[str, str]:
        try:
            from ..core.owner_reads import authorization, verified_caller_tenant
        except (ImportError, ValueError):
            from services.control_plane.bff.core.owner_reads import authorization, verified_caller_tenant
        auth = authorization.get()
        if not auth:
            raise ResearchCommandError("Research artifact reads require the caller's authorization", status_code=401)
        return {"Authorization": auth, "X-Tenant-Id": verified_caller_tenant()}

    def list_research_artifacts(self) -> List[Dict[str, Any]]:
        # No allow_none: a 404 here is an owner failure, never a genuine empty collection.
        res = self._call("GET", "/api/research/artifacts", headers=self._caller_headers())
        if not isinstance(res, list):
            raise ResearchCommandError("Research artifact collection response was not a list", status_code=502)
        return res
    def list_research_artifacts_page(self, *, page_size: int, page_token: Optional[str] = None) -> Dict[str, Any]:
        """One bounded ordered page of the caller's saved outputs (``items``/``total``/``next_page_token``)."""
        path = f"/api/research/artifacts?page_size={int(page_size)}"
        if page_token:
            path += f"&page_token={urllib.parse.quote(str(page_token), safe='')}"
        res = self._call("GET", path, headers=self._caller_headers())
        # A previous-release owner answers a bare list; that is not a page and must not be mistaken for one.
        if not isinstance(res, dict) or not isinstance(res.get("items"), list):
            raise ResearchCommandError("Research artifact page response was not a page", status_code=502)
        return res
    def get_research_artifact(self, artifact_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not artifact_id:
            return None
        res = self._call("GET", f"/api/research/artifacts/{urllib.parse.quote(str(artifact_id), safe='')}", headers=self._caller_headers(), allow_none=(404,))
        return res if isinstance(res, dict) else None
