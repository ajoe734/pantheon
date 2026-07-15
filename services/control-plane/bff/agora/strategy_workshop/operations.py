"""Canonical downstream adapters for Strategy Workshop operations.

The workshop aggregate owns conversation state, version links, and command
receipts.  It deliberately does not own StrategySpec documents, research run
state, consultation lifecycle state, or approval decisions.  This module keeps
those boundaries explicit and always performs authoritative readback after a
write before returning to the workshop router.
"""
from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class CanonicalOperationError(RuntimeError):
    """A sanitized failure returned by a canonical downstream authority."""

    authority: str
    reason: str
    status_code: Optional[int] = None
    retryable: bool = False

    def __str__(self) -> str:
        suffix = f" (HTTP {self.status_code})" if self.status_code else ""
        return f"{self.authority}: {self.reason}{suffix}"


class WorkshopCanonicalOperations:
    """Narrow client used by the Strategy Workshop router.

    ``approval_resolver`` is injected by the BFF assembly so this module does
    not import the process-global read store.  The remaining calls use the
    deployable service APIs configured in the normal operator-BFF environment.
    """

    def __init__(
        self,
        *,
        registry_base_url: Optional[str] = None,
        research_base_url: Optional[str] = None,
        consultation_base_url: Optional[str] = None,
        approval_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self.registry_base_url = (
            registry_base_url
            or os.getenv("PANTHEON_REGISTRY_API_URL")
            or os.getenv("PANTHEON_REGISTRY_URL")
            or ""
        ).strip().rstrip("/")
        self.research_base_url = (
            research_base_url
            or os.getenv("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL")
            or ""
        ).strip().rstrip("/")
        self.consultation_base_url = (
            consultation_base_url
            or os.getenv("PANTHEON_CONSULTATION_API_URL")
            or os.getenv("PANTHEON_CONSULTATION_SERVICE_URL")
            or ""
        ).strip().rstrip("/")
        self.approval_resolver = approval_resolver
        self.timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PANTHEON_WORKSHOP_OPERATION_TIMEOUT_SECONDS", "10")
        )

    @staticmethod
    def _url(base_url: str, path: str, authority: str) -> str:
        if not base_url:
            raise CanonicalOperationError(
                authority,
                "canonical service URL is not configured",
                retryable=True,
            )
        return f"{base_url}/{path.lstrip('/')}"

    def _request_json(
        self,
        authority: str,
        method: str,
        base_url: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = self._url(base_url, path, authority)
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8").strip()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            detail = "canonical request was rejected"
            try:
                error_payload = json.loads(exc.read().decode("utf-8"))
                candidate = error_payload.get("detail") if isinstance(error_payload, dict) else None
                if isinstance(candidate, str) and candidate:
                    detail = candidate
                elif isinstance(candidate, dict):
                    detail = str(candidate.get("reason") or detail)
            except Exception:
                pass
            raise CanonicalOperationError(
                authority,
                detail,
                status_code=exc.code,
                retryable=exc.code >= 500 or exc.code == 429,
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise CanonicalOperationError(
                authority,
                "canonical service is unavailable",
                retryable=True,
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise CanonicalOperationError(
                authority,
                "canonical service returned an invalid JSON response",
                retryable=False,
            ) from exc

    # -- Strategy Registry -------------------------------------------------

    def get_strategy_spec(self, registry_id: str) -> Dict[str, Any]:
        value = self._request_json(
            "strategy_registry",
            "GET",
            self.registry_base_url,
            f"/api/registry/strategy-specs/{urllib.parse.quote(registry_id, safe='')}",
        )
        if not isinstance(value, dict) or not isinstance(value.get("entry"), dict):
            raise CanonicalOperationError(
                "strategy_registry",
                "authoritative StrategySpec readback is missing entry",
            )
        if str(value["entry"].get("registry_id") or "") != registry_id:
            raise CanonicalOperationError(
                "strategy_registry",
                "authoritative StrategySpec readback id mismatch",
            )
        return value

    def create_strategy_spec(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        created = self._request_json(
            "strategy_registry",
            "POST",
            self.registry_base_url,
            "/api/registry/strategy-specs",
            payload,
        )
        entry = created.get("entry") if isinstance(created, dict) else None
        registry_id = str((entry or {}).get("registry_id") or payload.get("registry_id") or "")
        if not registry_id:
            raise CanonicalOperationError(
                "strategy_registry",
                "canonical create response is missing registry_id",
            )
        readback = self.get_strategy_spec(registry_id)
        expected_strategy_id = str(payload.get("strategy_id") or "")
        if str(readback["entry"].get("strategy_id") or "") != expected_strategy_id:
            raise CanonicalOperationError(
                "strategy_registry",
                "authoritative StrategySpec readback strategy mismatch",
            )
        return readback

    # -- Research Orchestrator --------------------------------------------

    def dispatch_research_run(
        self,
        *,
        task_payload: Dict[str, Any],
        run_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        task = self._request_json(
            "research_orchestrator",
            "POST",
            self.research_base_url,
            "/api/research-orchestrator/tasks",
            task_payload,
        )
        task_id = str((task or {}).get("task_id") or (task or {}).get("id") or "")
        if not task_id:
            raise CanonicalOperationError(
                "research_orchestrator",
                "canonical research task response is missing task_id",
            )
        task_readback = self._request_json(
            "research_orchestrator",
            "GET",
            self.research_base_url,
            f"/api/research-orchestrator/tasks/{urllib.parse.quote(task_id, safe='')}",
        )
        run = self._request_json(
            "research_orchestrator",
            "POST",
            self.research_base_url,
            f"/api/research-orchestrator/tasks/{urllib.parse.quote(task_id, safe='')}/runs",
            run_payload,
        )
        run_id = str((run or {}).get("run_id") or (run or {}).get("id") or "")
        if not run_id:
            raise CanonicalOperationError(
                "research_orchestrator",
                "canonical research dispatch response is missing run_id",
            )
        run_readback = self.get_research_run(run_id)
        if str(run_readback.get("task_id") or "") != task_id:
            raise CanonicalOperationError(
                "research_orchestrator",
                "authoritative research run readback task mismatch",
            )
        return {"task": task_readback, "run": run_readback}

    def get_research_run(self, run_id: str) -> Dict[str, Any]:
        value = self._request_json(
            "research_orchestrator",
            "GET",
            self.research_base_url,
            f"/api/research-orchestrator/runs/{urllib.parse.quote(run_id, safe='')}",
        )
        if not isinstance(value, dict) or str(value.get("run_id") or value.get("id") or "") != run_id:
            raise CanonicalOperationError(
                "research_orchestrator",
                "authoritative research run readback id mismatch",
            )
        return value

    # -- Consultation Service --------------------------------------------

    def open_consultation(
        self,
        *,
        payload: Dict[str, Any],
        request_id: str,
    ) -> Dict[str, Any]:
        create_payload = dict(payload)
        create_payload["request_id"] = request_id
        created = self._request_json(
            "consultation_service",
            "POST",
            self.consultation_base_url,
            "/api/consult/requests",
            create_payload,
        )
        created_id = str((created or {}).get("request_id") or "")
        if created_id != request_id:
            raise CanonicalOperationError(
                "consultation_service",
                "canonical consultation create response id mismatch",
            )
        status = str((created or {}).get("status") or "").lower()
        if status == "draft":
            self._request_json(
                "consultation_service",
                "POST",
                self.consultation_base_url,
                f"/api/consult/requests/{urllib.parse.quote(request_id, safe='')}/submit",
                {},
            )
        readback = self.get_consultation(request_id)
        if str(readback.get("status") or "").lower() == "draft":
            raise CanonicalOperationError(
                "consultation_service",
                "authoritative consultation readback remained draft",
            )
        return readback

    def get_consultation(self, request_id: str) -> Dict[str, Any]:
        value = self._request_json(
            "consultation_service",
            "GET",
            self.consultation_base_url,
            f"/api/consult/requests/{urllib.parse.quote(request_id, safe='')}",
        )
        if not isinstance(value, dict) or str(value.get("request_id") or "") != request_id:
            raise CanonicalOperationError(
                "consultation_service",
                "authoritative consultation readback id mismatch",
            )
        return value

    def cancel_consultation(self, request_id: str, *, actor_id: str, trace_id: str) -> None:
        self._request_json(
            "consultation_service",
            "POST",
            self.consultation_base_url,
            f"/api/consult/requests/{urllib.parse.quote(request_id, safe='')}/cancel",
            {
                "actor_ref": {"actor_type": "operator", "actor_id": actor_id},
                "trace_id": trace_id,
            },
        )

    # -- Governance -------------------------------------------------------

    def get_approval_decision(self, decision_id: str) -> Dict[str, Any]:
        if self.approval_resolver is None:
            raise CanonicalOperationError(
                "approval_decision_store",
                "canonical approval resolver is not configured",
                retryable=True,
            )
        try:
            decision = self.approval_resolver(decision_id)
        except Exception as exc:
            raise CanonicalOperationError(
                "approval_decision_store",
                "canonical approval store is unavailable",
                retryable=True,
            ) from exc
        if not isinstance(decision, dict):
            raise CanonicalOperationError(
                "approval_decision_store",
                "approval decision was not found",
                status_code=404,
            )
        observed_id = str(decision.get("decision_id") or decision.get("id") or "")
        if observed_id != decision_id:
            raise CanonicalOperationError(
                "approval_decision_store",
                "authoritative approval decision id mismatch",
            )
        return decision


__all__ = ["CanonicalOperationError", "WorkshopCanonicalOperations"]
