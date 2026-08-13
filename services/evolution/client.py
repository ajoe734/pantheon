"""
Evolution HTTP Client — unified authenticated client for Postmortems and other services.

Features:
- Bearer token authentication (EVOLUTION_AUTH_TOKEN)
- Tenant identity propagation (X-Tenant-Id)
- Idempotent request headers
- Target decision readback verification
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple, Union

import httpx

log = logging.getLogger(__name__)

DEFAULT_EVOLUTION_URL = "http://localhost:8093"
DEFAULT_TENANT_ID = "pantheon-default"


class EvolutionClientError(Exception):
    """Base exception for Evolution client errors."""

    def __init__(self, message: str, status_code: Optional[int] = None, response_body: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class EvolutionAuthenticationError(EvolutionClientError):
    """Raised on 401/403 authentication/authorization failures."""


class EvolutionReadbackError(EvolutionClientError):
    """Raised when target decision readback verification fails."""


class EvolutionClient:
    """Authenticated client for interacting with the Evolution service."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        tenant_id: Optional[str] = None,
        timeout: float = 10.0,
        async_client: Optional[httpx.AsyncClient] = None,
    ):
        self.base_url = (base_url or os.getenv("EVOLUTION_URL") or os.getenv("PANTHEON_EVOLUTION_API_URL") or DEFAULT_EVOLUTION_URL).rstrip("/")
        self.auth_token = auth_token or os.getenv("EVOLUTION_AUTH_TOKEN") or ""
        self.tenant_id = tenant_id or os.getenv("EVOLUTION_DEFAULT_TENANT_ID") or os.getenv("PANTHEON_TENANT_ID") or DEFAULT_TENANT_ID
        self.timeout = timeout
        self._async_client = async_client

    def _get_headers(self, idempotency_key: Optional[str] = None, extra_headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "X-Tenant-Id": self.tenant_id,
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        if extra_headers:
            headers.update(extra_headers)
        return headers

    async def submit_proposal(
        self,
        proposal_payload: Dict[str, Any],
        idempotency_key: Optional[str] = None,
        verify_readback: bool = True,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """
        Submit an EvolutionDecisionProposal to /api/evolution/proposals.

        If verify_readback is True, fetches the decision from /api/evolution/proposals/{decision_id}
        and verifies backlink matches.

        Returns (response_data, readback_data_or_none).
        """
        url = f"{self.base_url}/api/evolution/proposals"
        headers = self._get_headers(idempotency_key=idempotency_key)

        should_close_client = False
        client = self._async_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True

        try:
            resp = await client.post(url, json=proposal_payload, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution proposal submission failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code not in (200, 201):
                raise EvolutionClientError(
                    f"Evolution proposal submission failed with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )

            data = resp.json()
            decision_id = data.get("decision_id")

            readback_data: Optional[Dict[str, Any]] = None
            if verify_readback and decision_id:
                readback_data = await self.get_decision(decision_id, client=client)
                source_postmortem_id = proposal_payload.get("source_postmortem_id") or proposal_payload.get("metadata", {}).get("source_postmortem_id")
                if source_postmortem_id and readback_data:
                    rb_source = readback_data.get("source_postmortem_id") or readback_data.get("metadata", {}).get("source_postmortem_id")
                    if rb_source and rb_source != source_postmortem_id:
                        raise EvolutionReadbackError(
                            f"Target decision readback source mismatch: expected {source_postmortem_id}, got {rb_source}",
                            status_code=200,
                        )

            return data, readback_data
        finally:
            if should_close_client and client:
                await client.aclose()

    async def get_decision(
        self,
        decision_id: str,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """Fetch a single EvolutionDecision by decision_id."""
        url = f"{self.base_url}/api/evolution/proposals/{decision_id}"
        headers = self._get_headers()

        should_close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True

        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution get_decision failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code != 200:
                raise EvolutionClientError(
                    f"Evolution get_decision failed for {decision_id} with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.json()
        finally:
            if should_close_client and client:
                await client.aclose()
