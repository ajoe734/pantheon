"""Typed Research Commands port (ResearchCommandsPort).

Implements the command dispatch seam for the research orchestrator service,
enabling real action execution closure (cancel, retry, and cancellation fencing)
for research orchestrator runs in U10B
(see ``docs/operations/bff-upstream-v2-20260911/decisions/research-jobs.md`` §2–§4).

Mirrors the pattern established in ``ports/job_read.py``:
- standard urllib POST requests with JSON payloads
- base URL resolved from ``PANTHEON_RESEARCH_ORCHESTRATOR_API_URL``
- dependency injection via ``http_post`` callable for test isolation
- distinguished domain error types (Unavailable 503, Conflict 409, NotFound 404)
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

log = logging.getLogger(__name__)

_BASE_URL_ENVS: Tuple[str, ...] = (
    "PANTHEON_RESEARCH_ORCHESTRATOR_API_URL",
    "RESEARCH_ORCHESTRATOR_URL",
    "RESEARCH_ORCHESTRATOR_API_URL",
)


class ResearchCommandError(RuntimeError):
    """Base exception for research command failures."""

    def __init__(self, message: str, *, status_code: int = 500, detail: Optional[str] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.detail = detail or message


class ResearchCommandUnavailableError(ResearchCommandError):
    """Raised when research orchestrator service is unconfigured or unreachable (503)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=503)


class ResearchCommandNotFoundError(ResearchCommandError):
    """Raised when the target run or task does not exist (404)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=404)


class ResearchCommandConflictError(ResearchCommandError):
    """Raised when the action conflicts with run lifecycle state (409)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, status_code=409)


def _resolve_orchestrator_base_url() -> Optional[str]:
    for env_name in _BASE_URL_ENVS:
        raw = os.getenv(env_name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "3.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 3.0


HttpRequestFn = Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, str]]], Tuple[int, Optional[Any]]]
HttpPostFn = Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Tuple[int, Optional[Dict[str, Any]]]]


def _default_http_request(
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
        with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
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


def _default_http_post(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """POST JSON ``payload`` to ``url`` and return ``(status_code, parsed_json_or_none)``."""
    return _default_http_request("POST", url, payload, headers)


@dataclass
class ResearchCommandsPort:
    """Port for dispatching operational commands to the research orchestrator service."""

    http_post: HttpPostFn = field(default=_default_http_post)
    base_url: Optional[str] = None

    def _get_base_url(self) -> str:
        url = self.base_url or _resolve_orchestrator_base_url()
        if not url:
            raise ResearchCommandUnavailableError(
                "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL)."
            )
        return url

    def cancel_run(
        self,
        run_id: str,
        *,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
        canceled_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel an in-flight research orchestrator run and enforce cancellation fence."""
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required for cancel_run")

        base = self._get_base_url()
        url = f"{base}/api/research-orchestrator/runs/{clean_run_id}/cancel"
        payload = {
            "reason": reason or "Canceled via Management Job Action",
            "actor_id": actor_id or "operator",
            "canceled_at": canceled_at,
        }

        status_code, body = self.http_post(url, payload, None)
        if status_code == 404:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' not found."
            raise ResearchCommandNotFoundError(detail)
        if status_code == 409:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' cannot be canceled (state conflict)."
            raise ResearchCommandConflictError(detail)
        if status_code in (502, 503, 504):
            detail = (body or {}).get("detail") or "Research orchestrator service unreachable."
            raise ResearchCommandUnavailableError(detail)
        if status_code not in (200, 201, 202):
            detail = (body or {}).get("detail") or f"Research run cancel failed with HTTP {status_code}."
            raise ResearchCommandError(detail, status_code=status_code)

        return body or {}

    def retry_run(
        self,
        run_id: str,
        *,
        actor_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        requested_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retry a failed or canceled research orchestrator run under the same parent task."""
        clean_run_id = str(run_id or "").strip()
        if not clean_run_id:
            raise ValueError("run_id is required for retry_run")

        base = self._get_base_url()
        url = f"{base}/api/research-orchestrator/runs/{clean_run_id}/retry"
        payload = {
            "actor_id": actor_id or "operator",
            "idempotency_key": idempotency_key,
            "requested_at": requested_at,
        }

        status_code, body = self.http_post(url, payload, None)
        if status_code == 404:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' not found."
            raise ResearchCommandNotFoundError(detail)
        if status_code == 409:
            detail = (body or {}).get("detail") or f"Research run '{clean_run_id}' is not eligible for retry."
            raise ResearchCommandConflictError(detail)
        if status_code in (502, 503, 504):
            detail = (body or {}).get("detail") or "Research orchestrator service unreachable."
            raise ResearchCommandUnavailableError(detail)
        if status_code not in (200, 201, 202):
            detail = (body or {}).get("detail") or f"Research run retry failed with HTTP {status_code}."
            raise ResearchCommandError(detail, status_code=status_code)

        return body or {}


def create_research_commands_port(
    *,
    base_url: Optional[str] = None,
    http_post: Optional[HttpPostFn] = None,
) -> ResearchCommandsPort:
    """Factory creating a configured ResearchCommandsPort."""
    return ResearchCommandsPort(
        http_post=http_post or _default_http_post,
        base_url=base_url,
    )

@dataclass
class ResearchServiceClient:
    """HTTP client communicating with the authoritative Research service for tickets, experiments, and notes."""

    base_url: Optional[str] = None
    http_request: HttpRequestFn = field(default=_default_http_request)

    def _url(self, path: str) -> str:
        url = self.base_url or _resolve_orchestrator_base_url()
        if not url:
            raise ResearchCommandUnavailableError(
                "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL)."
            )
        return f"{url.rstrip('/')}{path}"

    def _call(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None, *, allow_none: Tuple[int, ...] = ()) -> Any:
        url = self._url(path)
        code, body = self.http_request(method, url, payload, None)
        if code in (200, 201):
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
        qs = f"?{'&'.join(params)}" if params else ""
        res = self._call("GET", f"/api/research/tickets{qs}", allow_none=(404,))
        return res if isinstance(res, list) else []

    # Experiments (RW-04)
    def create_research_experiment(self, **kwargs: Any) -> Dict[str, Any]:
        return self._call("POST", "/api/research/experiments", kwargs) or {}

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self._call("GET", f"/api/research/experiments/{experiment_id}", allow_none=(404,)) if experiment_id else None

    def list_research_experiments(self, *, ticket_id: Optional[str] = None, status: Optional[str] = None, include_archived: bool = False) -> List[Dict[str, Any]]:
        params = [f"{k}={v}" for k, v in [("ticket_id", ticket_id), ("status", status), ("include_archived", "true" if include_archived else None)] if v is not None]
        qs = f"?{'&'.join(params)}" if params else ""
        res = self._call("GET", f"/api/research/experiments{qs}", allow_none=(404,))
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
