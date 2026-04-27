"""OpenClaw-facing governed search adapter.

OpenClaw may request research context, but the adapter only returns cited
Pantheon evidence bundle refs. It rejects unscoped requests before retrieval.
"""

from __future__ import annotations

from typing import Any, Mapping

from services.knowledge.evidence.repository import InMemoryEvidenceRepository
from services.search import SearchAccessContext, SearchGateway, SearchRequest
from services.search.filters import SearchPolicyError


class OpenClawSearchGateway:
    def __init__(self, repository: InMemoryEvidenceRepository) -> None:
        self.gateway = SearchGateway(repository)

    def search(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        persona_id = str(payload.get("persona_id") or "").strip()
        workspace_id = str(payload.get("workspace_id") or "").strip()
        context = SearchAccessContext(
            persona_id=persona_id or None,
            workspace_id=workspace_id or None,
            environment=str(payload.get("environment") or "paper"),
            access_scopes=payload.get("access_scopes") or ["public"],
            license_scopes=payload.get("license_scopes") or ["internal", "open"],
        )
        context.require_persona_workspace()

        request = SearchRequest(
            request_id=str(payload.get("request_id") or "openclaw-search"),
            query=str(payload.get("query") or ""),
            persona_id=persona_id,
            workspace_id=workspace_id,
            source_types=payload.get("source_types") or [],
            environment=context.environment,
            top_k=int(payload.get("top_k") or 5),
            require_citations=True,
            trace_id=str(payload.get("trace_id") or "trace-openclaw-search"),
            filters_applied={"requester": "openclaw"},
        )
        response = self.gateway.search(request, context)
        return {
            "request_id": response.request_id,
            "trace_id": response.trace_id,
            "results": [
                {
                    "evidence_bundle_id": result.evidence_bundle_id,
                    "citations": result.citations,
                    "answer_context": result.answer_context,
                    "matched_items": result.matched_items,
                    "relevance_score": result.relevance_score,
                }
                for result in response.results
            ],
            "rejected_items_count": response.rejected_items_count,
            "filters_applied": dict(response.filters_applied),
        }


__all__ = ["OpenClawSearchGateway", "SearchPolicyError"]
