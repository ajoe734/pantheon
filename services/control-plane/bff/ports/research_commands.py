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


HttpPostFn = Callable[[str, Dict[str, Any], Optional[Dict[str, str]]], Tuple[int, Optional[Dict[str, Any]]]]


def _default_http_post(
    url: str,
    payload: Dict[str, Any],
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, Optional[Dict[str, Any]]]:
    """POST JSON ``payload`` to ``url`` and return ``(status_code, parsed_json_or_none)``."""
    req_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=req_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=_timeout_seconds()) as resp:
            status_code = resp.status
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            return status_code, body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = None
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            body = {"detail": raw.decode("utf-8", errors="replace")}
        return exc.code, body
    except Exception as exc:
        log.warning("Research command POST %s failed transport: %s", url, exc)
        return 503, {"detail": str(exc)}


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


HttpRequestFn = Callable[[str, str, Optional[Dict[str, Any]], Optional[Dict[str, str]]], Tuple[int, Optional[Any]]]


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
            status_code = resp.status
            raw = resp.read()
            body = json.loads(raw.decode("utf-8")) if raw else None
            return status_code, body
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        body = None
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
    """HTTP client communicating with the authoritative Research service for tickets, experiments, and notes."""

    base_url: Optional[str] = None
    http_request: HttpRequestFn = field(default=_default_http_request)

    def _get_base_url(self) -> str:
        url = self.base_url or _resolve_orchestrator_base_url()
        if not url:
            raise ResearchCommandUnavailableError(
                "Research orchestrator service is not configured (missing PANTHEON_RESEARCH_ORCHESTRATOR_API_URL)."
            )
        return url

    def _url(self, path: str) -> str:
        return f"{self._get_base_url().rstrip('/')}{path}"

    # Tickets (RW-01)
    def create_research_ticket(
        self,
        *,
        title: str,
        description: str = "",
        priority: str = "medium",
        owner: str = "",
        actor_id: str = "operator",
        created_at: Optional[str] = None,
        ticket_id: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        url = self._url("/api/research/tickets")
        payload = {
            "title": title,
            "description": description,
            "priority": priority,
            "owner": owner,
            "actor_id": actor_id,
            "created_at": created_at,
            "ticket_id": ticket_id,
            **extra,
        }
        code, body = self.http_request("POST", url, payload, None)
        if code in (200, 201):
            return body or {}
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to create research ticket (HTTP {code})", status_code=code)

    def patch_research_ticket(
        self,
        ticket_id: str,
        *,
        patch: Dict[str, Any],
        actor_id: str = "operator",
        updated_at: Optional[str] = None,
        **extra: Any,
    ) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/research/tickets/{ticket_id}")
        payload = {"patch": patch, "actor_id": actor_id, "updated_at": updated_at, **extra}
        code, body = self.http_request("PATCH", url, payload, None)
        if code in (200, 201):
            return body
        if code == 404:
            return None
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to patch research ticket (HTTP {code})", status_code=code)

    def get_research_ticket(self, ticket_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not ticket_id:
            return None
        url = self._url(f"/api/research/tickets/{ticket_id}")
        code, body = self.http_request("GET", url, None, None)
        if code == 200:
            return body
        return None

    def list_research_tickets(
        self,
        *,
        statuses: Optional[Any] = None,
        owner: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        params = []
        if statuses:
            if isinstance(statuses, (list, tuple, set)):
                params.append(f"status={','.join(str(s) for s in statuses)}")
            else:
                params.append(f"status={statuses}")
        if owner:
            params.append(f"owner={owner}")
        qs = f"?{'&'.join(params)}" if params else ""
        url = self._url(f"/api/research/tickets{qs}")
        code, body = self.http_request("GET", url, None, None)
        if code == 200 and isinstance(body, list):
            return body
        return []

    # Experiments (RW-04)
    def create_research_experiment(
        self,
        *,
        ticket_id: str,
        experiment_name: str,
        strategy_selector: Dict[str, Any],
        parameter_set: Dict[str, Any],
        run_config: Dict[str, Any],
        launch_context: Dict[str, Any],
        queued_at: Optional[str] = None,
        experiment_id: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        url = self._url("/api/research/experiments")
        payload = {
            "ticket_id": ticket_id,
            "experiment_name": experiment_name,
            "strategy_selector": strategy_selector,
            "parameter_set": parameter_set,
            "run_config": run_config,
            "launch_context": launch_context,
            "queued_at": queued_at,
            "experiment_id": experiment_id,
            **extra,
        }
        code, body = self.http_request("POST", url, payload, None)
        if code in (200, 201):
            return body or {}
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to create research experiment (HTTP {code})", status_code=code)

    def get_research_experiment(self, experiment_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not experiment_id:
            return None
        url = self._url(f"/api/research/experiments/{experiment_id}")
        code, body = self.http_request("GET", url, None, None)
        if code == 200:
            return body
        return None

    def list_research_experiments(
        self,
        *,
        ticket_id: Optional[str] = None,
        status: Optional[str] = None,
        include_archived: bool = False,
    ) -> List[Dict[str, Any]]:
        params = []
        if ticket_id:
            params.append(f"ticket_id={ticket_id}")
        if status:
            params.append(f"status={status}")
        if include_archived:
            params.append("include_archived=true")
        qs = f"?{'&'.join(params)}" if params else ""
        url = self._url(f"/api/research/experiments{qs}")
        code, body = self.http_request("GET", url, None, None)
        if code == 200 and isinstance(body, list):
            return body
        return []

    def cancel_research_experiment(
        self,
        experiment_id: str,
        *,
        completed_at: Optional[str] = None,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/research/experiments/{experiment_id}/cancel")
        payload = {"completed_at": completed_at, "reason": reason, "actor_id": actor_id}
        code, body = self.http_request("POST", url, payload, None)
        if code in (200, 201):
            return body
        if code in (404, 409):
            return None
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to cancel experiment (HTTP {code})", status_code=code)

    def retry_research_experiment(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str] = None,
        requested_at: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/research/experiments/{experiment_id}/retry")
        payload = {"actor_id": actor_id, "requested_at": requested_at, "idempotency_key": idempotency_key}
        code, body = self.http_request("POST", url, payload, None)
        if code in (200, 201):
            return body
        if code in (404, 409):
            return None
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to retry experiment (HTTP {code})", status_code=code)

    def archive_research_experiment(
        self,
        experiment_id: str,
        *,
        actor_id: Optional[str] = None,
        archived_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/research/experiments/{experiment_id}/archive")
        payload = {"actor_id": actor_id, "archived_at": archived_at}
        code, body = self.http_request("POST", url, payload, None)
        if code in (200, 201):
            return body
        if code in (404, 409):
            return None
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to archive experiment (HTTP {code})", status_code=code)

    def invalidate_research_experiment(
        self,
        experiment_id: str,
        *,
        reason: Optional[str] = None,
        actor_id: Optional[str] = None,
        invalidated_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        url = self._url(f"/api/research/experiments/{experiment_id}/invalidate")
        payload = {"reason": reason, "actor_id": actor_id, "invalidated_at": invalidated_at}
        code, body = self.http_request("POST", url, payload, None)
        if code in (200, 201):
            return body
        if code in (404, 409):
            return None
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        raise ResearchCommandError((body or {}).get("detail") or f"Failed to invalidate experiment (HTTP {code})", status_code=code)

    # Notes (KW-02)
    def create_research_note(self, note: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        url = self._url("/api/research/notes")
        code, body = self.http_request("POST", url, note, None)
        if code in (200, 201):
            return body
        if code == 503:
            raise ResearchCommandUnavailableError((body or {}).get("detail") or "Research write owner unavailable")
        return None

    def get_research_note(self, note_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not note_id:
            return None
        url = self._url(f"/api/research/notes/{note_id}")
        code, body = self.http_request("GET", url, None, None)
        if code == 200:
            return body
        return None

    def list_research_notes(self) -> List[Dict[str, Any]]:
        url = self._url("/api/research/notes")
        code, body = self.http_request("GET", url, None, None)
        if code == 200 and isinstance(body, list):
            return body
        return []
