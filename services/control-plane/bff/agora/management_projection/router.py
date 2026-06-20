"""Agora management-projection router — ContextBundle redaction and central persona boundary.

AG-BE-ID-004: Exposes the management-plane ContextBundle builder for central
persona consult calls.  Per C1 design-closure (agora-expert-consult SPEC.md):

  - Central persona only receives allowed fields:
      strategy_spec_draft_ref / question / symbols /
      evidence_refs / data_cutoff / required_output_schema
  - raw_prompt_included and user_identity_included are always False.
  - Any attempt to pass forbidden private fields returns RAW_PRIVATE_CONTENT_FORBIDDEN.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from integrations.openclaw.adapter.agora_context_bundle import (
    AgoraContextBundle,
    ContextBundlePrivacyError,
    build_context_bundle,
)


class ConsultBundleRequest(BaseModel):
    """Parameters for building a privacy-safe ContextBundle for central persona consult."""

    strategy_spec_draft_ref: str = Field(min_length=1)
    question: str = Field(min_length=1)
    symbols: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    data_cutoff: str = Field(min_length=1)
    required_output_schema: str = Field(default="")
    explicit_user_authorization: bool = False


class ConsultBundleResponse(BaseModel):
    """Response carrying the validated, redacted ContextBundle."""

    context_bundle: AgoraContextBundle
    privacy_code: str = "OK"


def create_management_projection_router(
    *,
    extract_identity: Callable[..., Any],
    require_read_role: Callable[..., None],
    bff_error: Callable[..., HTTPException],
    utc_now: Callable[[], str],
) -> APIRouter:
    """Management-projection sub-router: ContextBundle redaction boundary."""

    router = APIRouter(tags=["agora-management"])

    @router.post(
        "/bff/agora/management/consult-bundle",
        response_model=ConsultBundleResponse,
        summary="Build a privacy-safe ContextBundle for central persona consult",
    )
    def build_consult_bundle(
        body: ConsultBundleRequest,
    ) -> ConsultBundleResponse:
        """Construct a ContextBundle with mandatory redaction of private content.

        Central persona (agora-expert-consult / committee / red-team) may only
        receive the allowed fields. raw_prompt and user_identity are always
        excluded. Returns RAW_PRIVATE_CONTENT_FORBIDDEN on any violation.
        """
        try:
            bundle = build_context_bundle(
                strategy_spec_draft_ref=body.strategy_spec_draft_ref,
                question=body.question,
                symbols=body.symbols,
                evidence_refs=body.evidence_refs,
                data_cutoff=body.data_cutoff,
                required_output_schema=body.required_output_schema,
                explicit_user_authorization=body.explicit_user_authorization,
            )
        except ContextBundlePrivacyError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "violated_fields": exc.violated_fields,
                },
            )
        return ConsultBundleResponse(context_bundle=bundle)

    return router
