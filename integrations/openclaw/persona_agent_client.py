"""BFF-side client for the trading-persona agent routes of the OpenClaw adapter.

Extends the existing ops client (same base URL, timeout and service-token
handling); adds no credential and no new transport.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any, Dict, Optional

from services.control_plane.bff.openclaw_ops_client import (
    OPENCLAW_ADAPTER_AGENT_PREFIX,
    OpenClawOpsClient,
    OpenClawOpsClientError,
)
from services.persona.runtime_profile import build_persona_runtime_profile


class TradingPersonaAgentClient(OpenClawOpsClient):
    def ensure_trading_persona_agent(self, persona: Dict[str, Any]) -> Dict[str, Any]:
        """Reconcile a trading persona into its own read-only+proposal agent.

        ``persona`` is the Persona owner record (including its harness metadata);
        the adapter renders SOUL with the shared renderer and materializes memory.
        """
        profile = build_persona_runtime_profile(persona)
        persona_id = profile.persona_id
        digest = hashlib.sha256(
            json.dumps(persona, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        response = self._request(
            "POST",
            f"{OPENCLAW_ADAPTER_AGENT_PREFIX}/persona/ensure",
            body={"persona": persona, "workspace_ref": profile.workspace_ref},
            headers={
                "Idempotency-Key": str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"pantheon:trading-persona-agent:{persona_id}:{digest}")
                ),
                "X-Request-Id": str(uuid.uuid4()),
            },
            expected_status={200, 201},
            timeout_seconds=max(self._timeout, 135.0),
        )
        agent = response.get("agent")
        if not isinstance(agent, dict) or str(agent.get("agent_id") or "") != persona_id:
            raise OpenClawOpsClientError(
                "OpenClaw adapter returned a mismatched trading persona agent.",
                status_code=503,
                error_code="OPENCLAW_PERSONA_AGENT_INVALID_RESPONSE",
                payload=response,
            )
        return agent

    def materialize_trading_persona_memory(
        self, persona_id: str, workspace_ref: str, query: str, tags: Optional[list] = None
    ) -> Dict[str, Any]:
        """Refresh one persona's workspace memory cache before a cycle turn."""
        response = self._request(
            "POST",
            f"{OPENCLAW_ADAPTER_AGENT_PREFIX}/persona/memory/materialize",
            body={"persona_id": persona_id, "workspace_ref": workspace_ref, "query": query, "tags": list(tags or [])},
            expected_status={200},
            timeout_seconds=max(self._timeout, 30.0),
        )
        memory = response.get("memory")
        return memory if isinstance(memory, dict) else {}
