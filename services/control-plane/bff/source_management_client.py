"""Service client for BFF-owned source management and lifecycle command surfaces (SD-SRCM-03).

The BFF interacts with the source-ingestion service via its published HTTP API.
Write commands require service authorization (Authorization: Bearer <service_token>),
forwarding a configured service token rather than operator user credentials.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from services.source_ingestion.strategy_seed_builder import StrategySpecSeed
from services.source_ingestion.strategy_seed_store import SeedReviewDecision, StrategySpecSeedReviewError
try:
    from services.control_plane.bff.command_adapters.base import ActionUnavailableError, bound_tenant
    from services.control_plane.bff.core.owner_reads import authorization, selected_tenant
except (ImportError, ValueError):
    from .command_adapters.base import ActionUnavailableError, bound_tenant  # type: ignore
    from .core.owner_reads import authorization, selected_tenant  # type: ignore

_SEED_REVIEW_ERROR_CODES = frozenset({
    "seed_not_found", "merge_target_not_found", "idempotency_conflict",
    "terminal_seed_status", "invalid_status_transition", "invalid_merge_target",
    "invalid_review_action", "missing_review_reason", "missing_reviewer_id",
})


_SOURCE_INGEST_URL_ENVS = (
    "PANTHEON_SOURCE_INGEST_API_URL",
    "PANTHEON_SOURCE_INGEST_URL",
    "SOURCE_INGEST_SERVICE_URL", "SOURCE_INGEST_URL",
)

_SOURCE_INGEST_SERVICE_TOKEN_ENVS = (
    "PANTHEON_SOURCE_INGEST_SERVICE_TOKEN",
    "SOURCE_INGEST_SERVICE_TOKEN",
    "PANTHEON_SERVICE_TOKEN",
)


class SourceManagementClientError(RuntimeError):
    """Raised when communication with the source-ingest management API fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        error_code: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.payload = payload or {}

    def to_surface(self) -> Dict[str, Any]:
        return {
            "status": "unavailable" if self.status_code in {0, 503, 504} else "degraded",
            "source": "service_client",
            "reason": self.error_code,
            "message": self.message,
            "http_status": self.status_code if self.status_code else None,
        }


def _base_url_from_env(env_names: tuple[str, ...]) -> Optional[str]:
    for name in env_names:
        raw = os.getenv(name, "").strip()
        if raw:
            return raw.rstrip("/")
    return None


def _service_token_from_env() -> Optional[str]:
    for name in _SOURCE_INGEST_SERVICE_TOKEN_ENVS:
        raw = os.getenv(name, "").strip()
        if raw:
            return raw
    return None


def _timeout_seconds() -> float:
    raw = os.getenv("PANTHEON_BFF_SERVICE_TIMEOUT_SECONDS", "2.0").strip()
    try:
        return max(float(raw), 0.1)
    except ValueError:
        return 2.0


def _safe_json(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return loaded if isinstance(loaded, dict) else {"data": loaded}


class SourceManagementClient:
    """Client for source-ingest management and command routes (SD-SRCM-03)."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        service_token: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
    ) -> None:
        raw_url = base_url if base_url is not None else _base_url_from_env(_SOURCE_INGEST_URL_ENVS)
        self._base_url = raw_url.rstrip("/") if raw_url else ""
        self._service_token = service_token if service_token is not None else _service_token_from_env()
        self._timeout = timeout_seconds if timeout_seconds is not None else _timeout_seconds()

    @property
    def configured(self) -> bool:
        return bool(self._base_url)

    @property
    def path(self) -> str:
        return f"{self._base_url}/api/source-ingest/management/strategy-seeds" if self._base_url else "source-ingest://management/strategy-seeds"

    def _get_headers(
        self,
        *,
        idempotency_key: Optional[str] = None,
        require_auth: bool = False,
    ) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        token = self._service_token or _service_token_from_env()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif require_auth:
            raise SourceManagementClientError(
                "Source ingest service token is not configured",
                status_code=503,
                error_code="SERVICE_TOKEN_NOT_CONFIGURED",
            )
        return headers

    def _http_request(
        self,
        method: str,
        path: str,
        *,
        body: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        require_auth: bool = False,
        headers_override: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        if not self._base_url:
            raise SourceManagementClientError(
                "Source ingest service URL is not configured",
                status_code=503,
                error_code="SERVICE_NOT_CONFIGURED",
            )
        url = f"{self._base_url}{path}"
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
            if query:
                url = f"{url}?{query}"

        headers = headers_override or self._get_headers(idempotency_key=idempotency_key, require_auth=require_auth)
        data = json.dumps(body).encode("utf-8") if body is not None else None

        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw_text = resp.read().decode("utf-8").strip()
                return _safe_json(raw_text)
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            payload = _safe_json(body_text)
            detail = payload.get("detail") or payload.get("error") or body_text or str(exc)
            if isinstance(detail, dict):
                inner = detail.get("error") if isinstance(detail.get("error"), dict) else detail
                error_message = inner.get("message") or str(detail)
                error_code = inner.get("code") or f"HTTP_{exc.code}"
            else:
                error_message = str(detail)
                error_code = f"HTTP_{exc.code}"
            raise SourceManagementClientError(
                error_message,
                status_code=exc.code,
                error_code=error_code,
                payload=payload,
            ) from exc
        except OSError as exc:
            raise SourceManagementClientError(
                str(exc),
                status_code=503,
                error_code="CONNECTION_ERROR",
            ) from exc

    def list_connector_definitions(self) -> Dict[str, Any]:
        """GET /api/source-ingest/management/connector-definitions"""
        return self._http_request("GET", "/api/source-ingest/management/connector-definitions")

    def get_connector_definition(self, definition_id: str) -> Dict[str, Any]:
        """GET /api/source-ingest/management/connector-definitions/{definition_id}"""
        encoded_id = urllib.parse.quote(definition_id, safe="")
        return self._http_request("GET", f"/api/source-ingest/management/connector-definitions/{encoded_id}")

    def list_sources(
        self,
        *,
        source_kind: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
    ) -> Dict[str, Any]:
        """GET /api/source-ingest/management/sources"""
        params: Dict[str, Any] = {}
        if source_kind:
            params["source_kind"] = source_kind
        if lifecycle_state:
            params["lifecycle_state"] = lifecycle_state
        return self._http_request("GET", "/api/source-ingest/management/sources", params=params)

    def get_source(self, source_instance_id: str) -> Dict[str, Any]:
        """GET /api/source-ingest/management/sources/{source_instance_id}"""
        encoded_id = urllib.parse.quote(source_instance_id, safe="")
        return self._http_request("GET", f"/api/source-ingest/management/sources/{encoded_id}")

    def list_source_observations(
        self,
        source_instance_id: str,
        *,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """GET /api/source-ingest/management/sources/{source_instance_id}/observations"""
        encoded_id = urllib.parse.quote(source_instance_id, safe="")
        return self._http_request(
            "GET",
            f"/api/source-ingest/management/sources/{encoded_id}/observations",
            params={"limit": limit},
        )

    def list_source_canaries(
        self,
        source_instance_id: str,
        *,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """GET /api/source-ingest/management/sources/{source_instance_id}/canaries"""
        encoded_id = urllib.parse.quote(source_instance_id, safe="")
        return self._http_request(
            "GET",
            f"/api/source-ingest/management/sources/{encoded_id}/canaries",
            params={"limit": limit},
        )

    def get_source_canary(
        self,
        source_instance_id: str,
        canary_id: str,
    ) -> Dict[str, Any]:
        """GET /api/source-ingest/management/sources/{source_instance_id}/canaries/{canary_id}"""
        encoded_source_id = urllib.parse.quote(source_instance_id, safe="")
        encoded_canary_id = urllib.parse.quote(canary_id, safe="")
        return self._http_request(
            "GET",
            f"/api/source-ingest/management/sources/{encoded_source_id}/canaries/{encoded_canary_id}",
        )

    def list_source_receipts(
        self,
        source_instance_id: str,
        *,
        limit: int = 100,
    ) -> Dict[str, Any]:
        """GET /api/source-ingest/management/sources/{source_instance_id}/receipts"""
        encoded_id = urllib.parse.quote(source_instance_id, safe="")
        return self._http_request(
            "GET",
            f"/api/source-ingest/management/sources/{encoded_id}/receipts",
            params={"limit": limit},
        )

    def get_command_receipt(self, receipt_id: str) -> Dict[str, Any]:
        """GET /api/source-ingest/management/commands/{receipt_id}"""
        encoded_id = urllib.parse.quote(receipt_id, safe="")
        return self._http_request("GET", f"/api/source-ingest/management/commands/{encoded_id}")

    def execute_command(
        self,
        command_payload: Dict[str, Any],
        *,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        """POST /api/source-ingest/management/commands (requires service token authorization)"""
        return self._http_request(
            "POST",
            "/api/source-ingest/management/commands",
            body=command_payload,
            idempotency_key=idempotency_key,
            require_auth=True,
        )

    def _seed_headers(self, *, idempotency_key: Optional[str] = None) -> Dict[str, str]:
        raw_auth = str(authorization.get() or "").strip()
        token = raw_auth.removeprefix("Bearer ").strip() if raw_auth.startswith("Bearer ") else raw_auth
        if not token:
            raise SourceManagementClientError("Caller authorization token is required for strategy seed access", status_code=401, error_code="AUTH_REQUIRED")
        try:
            tenant_id = bound_tenant({}, tenant_id=selected_tenant.get(), auth_token=token)
        except ActionUnavailableError as exc:
            raise SourceManagementClientError(exc.message, status_code=exc.downstream_status, error_code=exc.error_code) from exc
        headers = {"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {token}", "X-Tenant-Id": tenant_id}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        return headers

    def _seed_request(self, method: str, path: str, *, body: Optional[Dict[str, Any]] = None, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        headers = self._seed_headers(idempotency_key=idempotency_key)
        try:
            return self._http_request(method, path, body=body, headers_override=headers)
        except SourceManagementClientError as exc:
            if exc.error_code in _SEED_REVIEW_ERROR_CODES:
                raise StrategySpecSeedReviewError(exc.error_code, exc.message) from exc
            if exc.status_code >= 500:
                raise SourceManagementClientError(exc.message, status_code=503, error_code=exc.error_code, payload=exc.payload) from exc
            raise

    def list_all(self) -> List[StrategySpecSeed]:
        resp = self._seed_request("GET", "/api/source-ingest/management/strategy-seeds")
        return [StrategySpecSeed.from_dict(item) for item in (resp.get("items") or []) if isinstance(item, Mapping)]

    def get(self, seed_id: str) -> Optional[StrategySpecSeed]:
        encoded = urllib.parse.quote(seed_id, safe="")
        try:
            resp = self._seed_request("GET", f"/api/source-ingest/management/strategy-seeds/{encoded}")
        except StrategySpecSeedReviewError as exc:
            if exc.code == "seed_not_found":
                return None
            raise
        raw = resp.get("seed")
        return StrategySpecSeed.from_dict(raw) if isinstance(raw, Mapping) else None

    def record_review_decision(self, seed_id: str, *, decision: Any, reviewer_id: str, reason: str = "", target_refs: Sequence[Mapping[str, Any]] = (), created_at: Any = None, idempotency_key: Optional[str] = None, request_hash: Optional[str] = None) -> Tuple[StrategySpecSeed, SeedReviewDecision]:
        encoded = urllib.parse.quote(seed_id, safe="")
        act = decision.value if hasattr(decision, "value") else str(decision)
        ts = created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else None)
        resp = self._seed_request("POST", f"/api/source-ingest/management/strategy-seeds/{encoded}/review", body={"decision": act, "reviewer_id": reviewer_id, "reason": reason, "target_refs": [dict(r) for r in target_refs], "created_at": ts, "idempotency_key": idempotency_key, "request_hash": request_hash}, idempotency_key=idempotency_key)
        dec = resp["decision"]
        return StrategySpecSeed.from_dict(resp["seed"]), SeedReviewDecision.from_dict(dec, idempotent_replay=bool(dec.get("idempotent_replay", False)))

    def merge_seed(self, seed_id: str, *, target_seed_id: str, reviewer_id: str, reason: str = "", created_at: Any = None, idempotency_key: Optional[str] = None, request_hash: Optional[str] = None, target_refs: Sequence[Mapping[str, Any]] = (),) -> Tuple[StrategySpecSeed, SeedReviewDecision]:
        encoded = urllib.parse.quote(seed_id, safe="")
        ts = created_at.isoformat() if hasattr(created_at, "isoformat") else (str(created_at) if created_at else None)
        resp = self._seed_request("POST", f"/api/source-ingest/management/strategy-seeds/{encoded}/merge", body={"target_seed_id": target_seed_id, "reviewer_id": reviewer_id, "reason": reason, "created_at": ts, "idempotency_key": idempotency_key, "request_hash": request_hash, "target_refs": [dict(r) for r in target_refs]}, idempotency_key=idempotency_key)
        dec = resp["decision"]
        return StrategySpecSeed.from_dict(resp["seed"]), SeedReviewDecision.from_dict(dec, idempotent_replay=bool(dec.get("idempotent_replay", False)))

    def record_replication_submission(self, seed_id: str, *, replication_ref: str, experiment_task_id: str, strategy_id: str, strategy_spec_version: str, submitted_by: str, submitted_at: str, idempotency_key: str, research_task_ref: Optional[str] = None) -> StrategySpecSeed:
        encoded = urllib.parse.quote(seed_id, safe="")
        resp = self._seed_request("POST", f"/api/source-ingest/management/strategy-seeds/{encoded}/replication-submission", body={"replication_ref": replication_ref, "experiment_task_id": experiment_task_id, "strategy_id": strategy_id, "strategy_spec_version": strategy_spec_version, "submitted_by": submitted_by, "submitted_at": submitted_at, "idempotency_key": idempotency_key, "research_task_ref": research_task_ref}, idempotency_key=idempotency_key)
        return StrategySpecSeed.from_dict(resp["seed"])

