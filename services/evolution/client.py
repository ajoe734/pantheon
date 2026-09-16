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

    # ------------------------------------------------------------------
    # Evolution Program owner API (U8A) — /api/evolution/programs
    #
    # These mirror ``submit_proposal``/``get_decision``'s auth/tenant/
    # idempotency header conventions above. U8A only implements durable
    # create/list/get/PATCH(name); there is deliberately no program action
    # method here — real lifecycle effects are U8B's obligation and must
    # not be fabricated by this client.
    # ------------------------------------------------------------------

    async def create_program(
        self,
        *,
        name: str,
        actor_id: str,
        idempotency_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """Create a new Evolution Program (POST /api/evolution/programs)."""
        url = f"{self.base_url}/api/evolution/programs"
        headers = self._get_headers(idempotency_key=idempotency_key)
        payload = {"name": name, "actor_id": actor_id}

        should_close_client = False
        client = client or self._async_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution create_program failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code not in (200, 201):
                raise EvolutionClientError(
                    f"Evolution create_program failed with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.json()
        finally:
            if should_close_client and client:
                await client.aclose()

    async def list_programs(
        self,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """List Evolution Programs for the caller's tenant (GET /api/evolution/programs)."""
        url = f"{self.base_url}/api/evolution/programs"
        headers = self._get_headers()

        should_close_client = False
        client = client or self._async_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution list_programs failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code != 200:
                raise EvolutionClientError(
                    f"Evolution list_programs failed with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.json()
        finally:
            if should_close_client and client:
                await client.aclose()

    async def get_program(
        self,
        program_id: str,
        *,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """Fetch a single Evolution Program (GET /api/evolution/programs/{id})."""
        url = f"{self.base_url}/api/evolution/programs/{program_id}"
        headers = self._get_headers()

        should_close_client = False
        client = client or self._async_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True
        try:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution get_program failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code == 404:
                raise EvolutionClientError(
                    f"Evolution program not found: {program_id}",
                    status_code=404,
                    response_body=resp.text,
                )
            if resp.status_code != 200:
                raise EvolutionClientError(
                    f"Evolution get_program failed for {program_id} with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.json()
        finally:
            if should_close_client and client:
                await client.aclose()

    async def patch_program(
        self,
        program_id: str,
        *,
        name: str,
        actor_id: str,
        expected_revision: int,
        idempotency_key: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """Patch a program's ``name`` (PATCH /api/evolution/programs/{id}).

        The owner API allowlists ``name`` only; ``expected_revision`` is the
        CAS precondition, not a patchable field. Any other field is rejected
        with 422 by the owner service — this client does not smuggle
        unsupported fields through.
        """
        url = f"{self.base_url}/api/evolution/programs/{program_id}"
        headers = self._get_headers(idempotency_key=idempotency_key)
        payload = {"name": name, "actor_id": actor_id, "expected_revision": expected_revision}

        should_close_client = False
        client = client or self._async_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True
        try:
            resp = await client.patch(url, json=payload, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution patch_program failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code == 404:
                raise EvolutionClientError(
                    f"Evolution program not found: {program_id}",
                    status_code=404,
                    response_body=resp.text,
                )
            if resp.status_code not in (200, 201):
                raise EvolutionClientError(
                    f"Evolution patch_program failed for {program_id} with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.json()
        finally:
            if should_close_client and client:
                await client.aclose()

    async def execute_program_action(
        self,
        program_id: str,
        action_id: str,
        *,
        actor_id: str,
        actor_role: str = "operator",
        expected_revision: Optional[int] = None,
        idempotency_key: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> Dict[str, Any]:
        """Execute a program lifecycle action (POST /api/evolution/programs/{id}/actions/{action_id})."""
        url = f"{self.base_url}/api/evolution/programs/{program_id}/actions/{action_id}"
        headers = self._get_headers(idempotency_key=idempotency_key)
        body = dict(payload or {})
        body["actor_id"] = actor_id
        body["actor_role"] = actor_role
        if expected_revision is not None:
            body["expected_revision"] = expected_revision

        should_close_client = False
        client = client or self._async_client
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout)
            should_close_client = True
        try:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code in (401, 403):
                raise EvolutionAuthenticationError(
                    f"Evolution execute_program_action failed with auth error status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            if resp.status_code == 404:
                raise EvolutionClientError(
                    f"Evolution program not found: {program_id}",
                    status_code=404,
                    response_body=resp.text,
                )
            if resp.status_code not in (200, 201, 202):
                raise EvolutionClientError(
                    f"Evolution action {action_id} failed for {program_id} with status={resp.status_code}: {resp.text}",
                    status_code=resp.status_code,
                    response_body=resp.text,
                )
            return resp.json()
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
        client = client or self._async_client
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
