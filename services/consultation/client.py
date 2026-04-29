"""HTTP client for the deployable consultation service.

Control-plane and runtime code should use this client when
``PANTHEON_CONSULTATION_API_URL`` is configured instead of reading the
consultation lifecycle store through a shared data directory.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


class ConsultationClientError(RuntimeError):
    """Raised when consultation-svc rejects or cannot serve a request."""

    def __init__(
        self,
        message: str,
        *,
        status_code: Optional[int] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload or {}


class ConsultationServiceClient:
    """Thin adapter over the consultation-svc HTTP API."""

    ENV_NAMES = ("PANTHEON_CONSULTATION_API_URL", "PANTHEON_CONSULTATION_SERVICE_URL")

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        self._base_url = (base_url or self.resolve_base_url() or "").strip().rstrip("/")
        self._timeout_seconds = float(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PANTHEON_CONSULTATION_API_TIMEOUT_SECONDS", "10")
        )

    @classmethod
    def resolve_base_url(cls) -> Optional[str]:
        for env_name in cls.ENV_NAMES:
            raw = os.getenv(env_name, "").strip()
            if raw:
                return raw.rstrip("/")
        return None

    @classmethod
    def configured(cls) -> bool:
        return cls.resolve_base_url() is not None

    def is_configured(self) -> bool:
        return bool(self._base_url)

    def list_requests(
        self,
        *,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, str] = {}
        if target_type:
            query["target_type"] = target_type
        if target_id:
            query["target_id"] = target_id
        if status:
            query["status"] = status
        payload = self._request_json("GET", self._with_query("/api/consult/requests", query))
        return list(payload or [])

    def get_request(self, request_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._request_json("GET", f"/api/consult/requests/{request_id}")
        except ConsultationClientError as exc:
            if exc.status_code == 404:
                return None
            raise

    def create_request(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._request_json("POST", "/api/consult/requests", payload)

    def cancel_request(
        self,
        request_id: str,
        *,
        actor_id: str,
        canceled_at: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        payload: Dict[str, Any] = {
            "actor_ref": {"actor_type": "operator", "actor_id": actor_id},
        }
        if canceled_at:
            payload["canceled_at"] = canceled_at
        try:
            return self._request_json("POST", f"/api/consult/requests/{request_id}/cancel", payload)
        except ConsultationClientError as exc:
            if exc.status_code == 404:
                return None
            raise

    def list_memos(
        self,
        *,
        request_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        query: Dict[str, str] = {}
        if request_id:
            query["request_id"] = request_id
        if status:
            query["status"] = status
        payload = self._request_json("GET", self._with_query("/api/consult/memos", query))
        return list(payload or [])

    def get_memo(self, memo_id: str) -> Optional[Dict[str, Any]]:
        try:
            return self._request_json("GET", f"/api/consult/memos/{memo_id}")
        except ConsultationClientError as exc:
            if exc.status_code == 404:
                return None
            raise

    def list_transcripts(self) -> List[Dict[str, Any]]:
        payload = self._request_json("GET", "/api/consult/transcripts")
        return list(payload or [])

    def list_handoffs(self, *, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query: Dict[str, str] = {}
        if request_id:
            query["request_id"] = request_id
        payload = self._request_json("GET", self._with_query("/api/consult/handoffs", query))
        return list(payload or [])

    def list_handoffs_for_request(self, request_id: str) -> List[Dict[str, Any]]:
        payload = self._request_json("GET", f"/api/consult/requests/{request_id}/handoffs")
        return list(payload or [])

    def record_sponsor_decision(
        self,
        committee_id: str,
        *,
        sponsor_decision: str,
        rationale_ref: str,
        actor_id: str,
        recorded_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "sponsor_decision": sponsor_decision,
            "rationale_ref": rationale_ref,
            "actor_id": actor_id,
        }
        if recorded_at:
            payload["recorded_at"] = recorded_at
        return self._request_json(
            "POST",
            f"/api/consult/committees/{committee_id}/sponsor-decision",
            payload,
        )

    @staticmethod
    def _with_query(path: str, query: Dict[str, str]) -> str:
        if not query:
            return path
        return f"{path}?{urllib.parse.urlencode(query)}"

    def _request_json(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Any:
        if not self._base_url:
            raise ConsultationClientError("consultation service URL is not configured")
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"{self._base_url}/{path.lstrip('/')}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
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
