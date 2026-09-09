"""Canonical read-only ApprovalDecision evidence; no service-main imports.

All domains use the same exact-ID HTTP reader and validity check. Domain
adapters supply their immutable target/version/digest and additional predicates.
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler

from pydantic import BaseModel, ConfigDict, Field


class ApprovalInvalid(ValueError):
    pass


class ApprovalUnavailable(ApprovalInvalid):
    pass


class ApprovalEvidence(BaseModel):
    model_config = ConfigDict(strict=True, extra='allow', frozen=True)
    decision_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    target_id: str = Field(min_length=1)
    target_version: str = Field(min_length=1)
    decision_state: str
    decision: str | None
    actor_id: str | None
    actor_role: str | None
    decided_at: str | None
    expires_at: str | None
    revoked_at: str | None = None
    superseded_by: str | None = None
    conditions: list[str]
    candidate_digest: str | None = None
    proof_digest: str | None = None
    controller_record_ref: str | None
    authority_status: str | None
    recorded_at: str | None
    authorization_scope: dict | None = None
    version: int = Field(ge=1)
    event_id: str = Field(min_length=1)

    def require_valid(self, *, expected: Mapping[str, Any] | None = None,
                      now: datetime | None = None,
                      usage_context: Any = None) -> 'ApprovalEvidence':
        """Common validity predicate for every domain consumer.

        ``usage_context`` is an ``ApprovalUsageContext`` (environment,
        target_stage, capital_scale_pct) describing what the caller is about
        to do.  Unscoped approvals ignore it.  A scoped approval fails closed
        without it and admits only a context inside its stored scope.
        """
        if self.decision_state != 'decided':
            raise ApprovalInvalid('approval_decision must be in decided state')
        if (self.decision_state != 'decided' or self.decision != 'approved'
                or self.revoked_at or self.superseded_by or self.conditions
                or not self.actor_id or not self.actor_role or not self.decided_at
                or not self.recorded_at or self.authority_status != 'authoritative'
                or not self.controller_record_ref):
            raise ApprovalInvalid('Approval is not unconditional current owner authority (state/decision, revoked, superseded, conditions or provenance)')
        from services.governance.write_authority import is_authorized_to_decide
        if not is_authorized_to_decide(self.actor_role, getattr(self, 'risk_level', '')):
            raise ApprovalInvalid('Approval role is not authorized for its risk')
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace('Z', '+00:00'))
            if expiry.tzinfo is None or expiry <= (now or datetime.now(timezone.utc)):
                raise ValueError('expired')
        except (AttributeError, TypeError, ValueError) as exc:
            raise ApprovalInvalid('Approval expiry is missing, malformed or expired') from exc
        from services.governance.paper_approval_scope import (
            AuthorizationScopeError, UsageContextViolation, enforce_authorization_scope,
            require_dedicated_subject_scope,
        )
        try:
            # The dedicated paper subject never has unscoped legacy authority:
            # absent/null/malformed/broadened scope on its decisions is invalid.
            require_dedicated_subject_scope(
                actor_id=self.actor_id,
                owner_user_id=getattr(self, 'owner_user_id', None),
                authorization_scope=self.authorization_scope,
            )
            enforce_authorization_scope(self.authorization_scope, usage_context)
        except (AuthorizationScopeError, UsageContextViolation) as exc:
            raise ApprovalInvalid(f'Approval authorization_scope rejects this use: {exc}') from exc
        values = self.model_dump()
        for name, value in (expected or {}).items():
            if value is None or value == '' or values.get(name) != value:
                raise ApprovalInvalid(f'Approval does not match expected {name}')
        return self


class NoOwnerRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Exact owner reads must not forward the scoped bearer to another URL.
        return None


class ApprovalReader:
    def __init__(self, *, base_url: str, service_token: str, timeout_seconds: float = 5.0,
                 token_provider: Callable[[], str] | None = None):
        try:
            parsed = urlsplit(base_url)
        except ValueError as exc:
            raise ApprovalUnavailable('Governance reader URL is malformed') from exc
        if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or (not service_token and token_provider is None)
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
            raise ApprovalUnavailable('Governance reader URL, scoped principal and timeout required')
        self._opener = build_opener(NoOwnerRedirect())
        self.base_url = base_url.rstrip('/')
        self.service_token = service_token
        self.token_provider = token_provider
        self.timeout_seconds = timeout_seconds

    def get(self, decision_id: str) -> ApprovalEvidence:
        if not decision_id or not decision_id.strip():
            raise ApprovalInvalid('Exact decision ID required')
        try:
            token = self.token_provider() if self.token_provider else self.service_token
            if not token:
                raise RuntimeError('Missing service credential')
        except RuntimeError as exc:
            raise ApprovalUnavailable('Governance read principal unavailable') from exc
        request = Request(
            f'{self.base_url}/api/governance/approvals/{quote(decision_id, safe="")}',
            headers={'Accept': 'application/json', 'Authorization': 'Bearer ' + token},
            method='GET',
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200 or response.headers.get_content_type() != 'application/json':
                    raise ApprovalUnavailable('Governance did not return a JSON decision')
                body = json.loads(response.read().decode('utf-8'))
        except HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise ApprovalInvalid('Governance denied exact decision read') from exc
            raise ApprovalUnavailable('Governance read unavailable') from exc
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise ApprovalUnavailable('Governance read unavailable or malformed') from exc
        try:
            evidence = ApprovalEvidence.model_validate(body)
        except ValueError as exc:
            raise ApprovalInvalid('Malformed Governance decision') from exc
        if evidence.decision_id != decision_id:
            raise ApprovalInvalid('Governance exact decision ID mismatch')
        return evidence

    def verify(self, decision_id: str, *, expected: Mapping[str, Any], now=None,
               usage_context: Any = None) -> ApprovalEvidence:
        return self.get(decision_id).require_valid(expected=expected, now=now, usage_context=usage_context)


def configured_approval_reader(domain: str, *, base_url: str | None = None) -> ApprovalReader:
    from services.service_token_file import configured_service_token
    prefix = domain.upper().replace('-', '_')
    variable = f'{prefix}_GOVERNANCE_SERVICE_TOKEN'
    try:
        timeout = float(os.getenv(f'{prefix}_GOVERNANCE_TIMEOUT_SECONDS', '5'))
    except ValueError as exc:
        raise ApprovalUnavailable('Governance reader timeout is malformed') from exc
    return ApprovalReader(
        base_url=base_url or os.getenv(f'{prefix}_GOVERNANCE_BASE_URL', ''),
        service_token=os.getenv(f'{prefix}_GOVERNANCE_SERVICE_TOKEN', ''),
        token_provider=lambda: configured_service_token(variable),
        timeout_seconds=timeout,
    )
