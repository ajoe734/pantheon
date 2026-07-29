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
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Mapping, Optional


@dataclass(frozen=True)
class CanonicalOperationError(RuntimeError):
    """A sanitized failure returned by a canonical downstream authority.

    ``partial_effects`` carries the downstream identifiers that were already
    durably created before the failing step (for example a research task that
    exists even though its run dispatch failed).  The router persists these in
    the failed command receipt so a new-key retry can resume the recorded
    downstream resources instead of creating duplicates.
    """

    authority: str
    reason: str
    status_code: Optional[int] = None
    retryable: bool = False
    partial_effects: Optional[Mapping[str, Any]] = field(default=None, compare=False)

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

    @staticmethod
    def _with_partial_effects(
        error: CanonicalOperationError,
        effects: Mapping[str, Any],
    ) -> CanonicalOperationError:
        """Attach the already-created downstream identifiers to a failure."""

        merged = {key: value for key, value in dict(effects).items() if value}
        if error.partial_effects:
            merged.update(dict(error.partial_effects))
        if not merged:
            return error
        return replace(error, partial_effects=merged)

    def dispatch_research_run(
        self,
        *,
        task_payload: Dict[str, Any],
        run_payload: Dict[str, Any],
        resume: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create-or-adopt the canonical research task and run.

        ``resume`` carries downstream identifiers recorded by a prior failed
        attempt.  Recorded resources are adopted with authoritative readback
        instead of re-created; a recorded id that the downstream authority no
        longer knows (404) falls back to creation so the retry stays truthful.
        Any failure after a downstream resource exists raises with
        ``partial_effects`` naming that resource.
        """

        resume_state = dict(resume or {})
        task_id = str(resume_state.get("research_task_id") or "")
        run_id = str(resume_state.get("research_run_id") or "")
        task_readback: Optional[Dict[str, Any]] = None
        if task_id:
            try:
                task_readback = self._request_json(
                    "research_orchestrator",
                    "GET",
                    self.research_base_url,
                    f"/api/research-orchestrator/tasks/{urllib.parse.quote(task_id, safe='')}",
                )
            except CanonicalOperationError as exc:
                if exc.status_code == 404:
                    # Downstream never durably admitted the recorded task, so
                    # the retry must create it (same downstream idempotency
                    # key, derived by the router from the recorded digest).
                    task_id = ""
                    run_id = ""
                else:
                    raise self._with_partial_effects(
                        exc,
                        {"research_task_id": task_id, "research_run_id": run_id},
                    ) from exc
        if not task_id:
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
            try:
                task_readback = self._request_json(
                    "research_orchestrator",
                    "GET",
                    self.research_base_url,
                    f"/api/research-orchestrator/tasks/{urllib.parse.quote(task_id, safe='')}",
                )
            except CanonicalOperationError as exc:
                raise self._with_partial_effects(
                    exc, {"research_task_id": task_id}
                ) from exc
        run_readback: Optional[Dict[str, Any]] = None
        if run_id:
            try:
                run_readback = self.get_research_run(run_id)
            except CanonicalOperationError as exc:
                if exc.status_code == 404:
                    run_id = ""
                else:
                    raise self._with_partial_effects(
                        exc,
                        {"research_task_id": task_id, "research_run_id": run_id},
                    ) from exc
        if not run_id:
            try:
                run = self._request_json(
                    "research_orchestrator",
                    "POST",
                    self.research_base_url,
                    f"/api/research-orchestrator/tasks/{urllib.parse.quote(task_id, safe='')}/runs",
                    run_payload,
                )
            except CanonicalOperationError as exc:
                raise self._with_partial_effects(
                    exc, {"research_task_id": task_id}
                ) from exc
            run_id = str((run or {}).get("run_id") or (run or {}).get("id") or "")
            if not run_id:
                raise self._with_partial_effects(
                    CanonicalOperationError(
                        "research_orchestrator",
                        "canonical research dispatch response is missing run_id",
                    ),
                    {"research_task_id": task_id},
                )
            try:
                run_readback = self.get_research_run(run_id)
            except CanonicalOperationError as exc:
                raise self._with_partial_effects(
                    exc,
                    {"research_task_id": task_id, "research_run_id": run_id},
                ) from exc
        if str((run_readback or {}).get("task_id") or "") != task_id:
            raise self._with_partial_effects(
                CanonicalOperationError(
                    "research_orchestrator",
                    "authoritative research run readback task mismatch",
                ),
                {"research_task_id": task_id, "research_run_id": run_id},
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
        resume: bool = False,
    ) -> Dict[str, Any]:
        """Create-or-adopt the canonical consultation request and submit it.

        With ``resume`` the adapter first reads ``request_id`` back: a prior
        failed attempt may already have created it downstream.  An existing
        request is adopted (and submitted if still draft) instead of being
        re-created.  Failures once creation succeeded — or when the create
        outcome is unknown (retryable transport failure) — raise with
        ``partial_effects`` carrying ``consultation_request_id`` so the router
        can persist resumable lineage.
        """

        created: Optional[Dict[str, Any]] = None
        if resume:
            try:
                created = self.get_consultation(request_id)
            except CanonicalOperationError as exc:
                if exc.status_code != 404:
                    raise self._with_partial_effects(
                        exc, {"consultation_request_id": request_id}
                    ) from exc
                created = None
        if created is None:
            create_payload = dict(payload)
            create_payload["request_id"] = request_id
            try:
                created = self._request_json(
                    "consultation_service",
                    "POST",
                    self.consultation_base_url,
                    "/api/consult/requests",
                    create_payload,
                )
            except CanonicalOperationError as exc:
                if exc.retryable:
                    # Unknown outcome: the request may exist downstream even
                    # though the response was lost.  Record the deterministic
                    # id so a retry adopts rather than duplicates it.
                    raise self._with_partial_effects(
                        exc, {"consultation_request_id": request_id}
                    ) from exc
                raise
            created_id = str((created or {}).get("request_id") or "")
            if created_id != request_id:
                raise self._with_partial_effects(
                    CanonicalOperationError(
                        "consultation_service",
                        "canonical consultation create response id mismatch",
                    ),
                    {"consultation_request_id": request_id},
                )
        status = str((created or {}).get("status") or "").lower()
        if status == "draft":
            try:
                self._request_json(
                    "consultation_service",
                    "POST",
                    self.consultation_base_url,
                    f"/api/consult/requests/{urllib.parse.quote(request_id, safe='')}/submit",
                    {},
                )
            except CanonicalOperationError as exc:
                raise self._with_partial_effects(
                    exc, {"consultation_request_id": request_id}
                ) from exc
        try:
            readback = self.get_consultation(request_id)
        except CanonicalOperationError as exc:
            raise self._with_partial_effects(
                exc, {"consultation_request_id": request_id}
            ) from exc
        if str(readback.get("status") or "").lower() == "draft":
            raise self._with_partial_effects(
                CanonicalOperationError(
                    "consultation_service",
                    "authoritative consultation readback remained draft",
                ),
                {"consultation_request_id": request_id},
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
