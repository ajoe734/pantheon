"""HTTP client for communicating with the authoritative Research orchestrator service."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple
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
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
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

    def _call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, *, allow_none: Tuple[int, ...] = ()) -> Any:
        url = self._url(path)
        code, body = self.http_request(method, url, payload, None)
        if code in (200, 201, 202):
            return body
        if code in allow_none:
            return None
        detail = (body or {}).get("detail") if isinstance(body, dict) else None
        if code == 503:
            raise ResearchCommandUnavailableError(detail or "Research write owner unavailable")
        raise ResearchCommandError(detail or f"Research command failed (HTTP {code})", status_code=code)

    # Tickets (RW-01)
    def create_research_ticket(self, **kwargs: Any) -> Dict[str, Any]:
        return self._call("POST", "/api/research/tickets", kwargs) or {}
    def patch_research_ticket(self, ticket_id: str, *, patch: Dict[str, Any], **kw: Any) -> Optional[Dict[str, Any]]:
        return self._call("PATCH", f"/api/research/tickets/{ticket_id}", {"patch": patch, **kw}, allow_none=(404,))
    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._call("GET", f"/api/research/tickets/{ticket_id}", allow_none=(404,)) if ticket_id else None
    def list_research_tickets(self, *, statuses: Optional[Any] = None, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        params = [f"status={','.join(str(s) for s in statuses) if isinstance(statuses, (list, tuple, set)) else statuses}"] if statuses else []
        if owner:
            params.append(f"owner={owner}")
        query = f"?{'&'.join(params)}" if params else ""
        res = self._call("GET", f"/api/research/tickets{query}", allow_none=(404,))
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
        res = self._call("GET", "/api/research/notes", allow_none=(404,))
        return res if isinstance(res, list) else []
    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._call("GET", f"/api/research/notes/{note_id}", allow_none=(404,)) if note_id else None
    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        return self._call("POST", "/api/research/notes", note, allow_none=(400, 404))
