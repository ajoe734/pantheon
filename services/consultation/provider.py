"""Real HTTP committee/red-team contribution provider contract."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Mapping


_PARTICIPANT_TO_AUTHOR = {
    "committee": "committee",
    "human_reviewer": "human",
    "persona": "persona",
}
_RECOMMENDATIONS = frozenset(
    {
        "approve",
        "approve_with_conditions",
        "reject",
        "request_more_research",
        "freeze",
        "rollback",
        "escalate",
    }
)


class ContributionProviderError(RuntimeError):
    """Provider transport or contract error."""


class ContributionBlocked(ContributionProviderError):
    """Provider explicitly could not produce a qualified contribution."""

    def __init__(self, reason: str, *, retryable: bool = True) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


def _blocked_http_response(exc: urllib.error.HTTPError) -> ContributionBlocked:
    """Translate the provider endpoint's typed blocked envelope when present."""

    reason = f"provider HTTP {exc.code}: {exc.reason}"
    retryable = exc.code == 429 or exc.code >= 500
    try:
        raw = exc.read().decode("utf-8")
        payload = json.loads(raw) if raw else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        payload = {}
    if isinstance(payload, Mapping) and str(payload.get("status") or "").lower() == "blocked":
        supplied_reason = str(payload.get("reason") or "").strip()
        if supplied_reason:
            reason = supplied_reason
        supplied_retryable = payload.get("retryable")
        if isinstance(supplied_retryable, bool):
            retryable = supplied_retryable
    return ContributionBlocked(reason, retryable=retryable)


def _required_text(payload: Mapping[str, Any], field: str) -> str:
    value = str(payload.get(field) or "").strip()
    if not value:
        raise ContributionProviderError(f"provider response is missing {field}")
    return value


@dataclass(frozen=True)
class QualifiedContribution:
    contribution_id: str
    tenant_id: str
    request_id: str
    participant_type: str
    participant_ref: str
    author_type: str
    summary: str
    recommendation: str
    confidence: float
    event_type: str
    event_content: dict[str, Any]
    evidence: tuple[dict[str, Any], ...]
    findings: tuple[dict[str, Any], ...]

    @classmethod
    def from_payload(
        cls,
        payload: Mapping[str, Any],
        *,
        expected_tenant_id: str,
        expected_request_id: str,
    ) -> "QualifiedContribution":
        tenant_id = _required_text(payload, "tenant_id")
        request_id = _required_text(payload, "request_id")
        if tenant_id != expected_tenant_id:
            raise ContributionProviderError("provider contribution tenant mismatch")
        if request_id != expected_request_id:
            raise ContributionProviderError("provider contribution request mismatch")
        participant_type = _required_text(payload, "participant_type")
        author_type = _PARTICIPANT_TO_AUTHOR.get(participant_type)
        if author_type is None:
            raise ContributionProviderError(
                "provider participant_type must be committee, human_reviewer, or persona"
            )
        recommendation = _required_text(payload, "recommendation")
        if recommendation not in _RECOMMENDATIONS:
            raise ContributionProviderError(
                f"provider recommendation is unsupported: {recommendation}"
            )
        raw_confidence = payload.get("confidence", 1.0)
        if isinstance(raw_confidence, bool):
            raise ContributionProviderError("provider confidence must be numeric")
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError) as exc:
            raise ContributionProviderError(
                "provider confidence must be numeric"
            ) from exc
        if not 0.0 <= confidence <= 1.0:
            raise ContributionProviderError("provider confidence must be between 0 and 1")
        event_content = payload.get("event_content")
        if not isinstance(event_content, Mapping) or not event_content:
            raise ContributionProviderError(
                "provider contribution requires non-empty event_content"
            )
        raw_evidence = payload.get("evidence")
        if not isinstance(raw_evidence, list) or not raw_evidence:
            raise ContributionProviderError(
                "provider contribution requires at least one evidence item"
            )
        evidence: list[dict[str, Any]] = []
        for index, item in enumerate(raw_evidence):
            if not isinstance(item, Mapping):
                raise ContributionProviderError(
                    f"provider evidence[{index}] must be an object"
                )
            normalized = dict(item)
            for field in ("id", "evidence_type", "link"):
                if not str(normalized.get(field) or "").strip():
                    raise ContributionProviderError(
                        f"provider evidence[{index}] is missing {field}"
                    )
            evidence.append(normalized)
        raw_findings = payload.get("findings") or []
        if not isinstance(raw_findings, list) or any(
            not isinstance(item, Mapping) for item in raw_findings
        ):
            raise ContributionProviderError("provider findings must be an array of objects")
        return cls(
            contribution_id=_required_text(payload, "contribution_id"),
            tenant_id=tenant_id,
            request_id=request_id,
            participant_type=participant_type,
            participant_ref=_required_text(payload, "participant_ref"),
            author_type=author_type,
            summary=_required_text(payload, "summary"),
            recommendation=recommendation,
            confidence=confidence,
            event_type=str(payload.get("event_type") or "participant_contribution"),
            event_content=dict(event_content),
            evidence=tuple(evidence),
            findings=tuple(dict(item) for item in raw_findings),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contribution_id": self.contribution_id,
            "tenant_id": self.tenant_id,
            "request_id": self.request_id,
            "participant_type": self.participant_type,
            "participant_ref": self.participant_ref,
            "author_type": self.author_type,
            "summary": self.summary,
            "recommendation": self.recommendation,
            "confidence": self.confidence,
            "event_type": self.event_type,
            "event_content": dict(self.event_content),
            "evidence": [dict(item) for item in self.evidence],
            "findings": [dict(item) for item in self.findings],
        }


class HttpContributionProvider:
    """Fetch a qualified contribution from a configured provider boundary."""

    def __init__(
        self,
        *,
        endpoint: str,
        bearer_token: str,
        service_actor: str,
        timeout_seconds: float,
    ) -> None:
        self.endpoint = endpoint.strip()
        self.bearer_token = bearer_token.strip()
        self.service_actor = service_actor.strip()
        self.timeout_seconds = timeout_seconds

    def obtain(
        self,
        *,
        tenant_id: str,
        request: Mapping[str, Any],
    ) -> QualifiedContribution:
        if not self.endpoint:
            raise ContributionBlocked(
                "CONSULTATION_PROVIDER_URL is not configured",
                retryable=False,
            )
        if not self.bearer_token:
            raise ContributionBlocked(
                "CONSULTATION_PROVIDER_TOKEN is not configured",
                retryable=False,
            )
        request_id = str(request.get("request_id") or "")
        idempotency_key = "consultation-provider:" + sha256(
            f"{tenant_id}\0{request_id}".encode("utf-8")
        ).hexdigest()
        payload = json.dumps(
            {
                "tenant_id": tenant_id,
                "request_id": request_id,
                "request": dict(request),
                "required_output": "qualified_committee_or_redteam_contribution",
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=payload,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.bearer_token}",
                "Content-Type": "application/json",
                "Idempotency-Key": idempotency_key,
                "X-Pantheon-Service-Actor": self.service_actor,
                "X-Pantheon-Tenant-Id": tenant_id,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(  # noqa: S310 - configured service boundary
                http_request,
                timeout=self.timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise _blocked_http_response(exc) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ContributionBlocked(
                f"provider transport failed: {exc}",
                retryable=True,
            ) from exc
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ContributionProviderError(
                "provider response must be valid JSON"
            ) from exc
        if not isinstance(parsed, Mapping):
            raise ContributionProviderError("provider response must be a JSON object")
        if str(parsed.get("status") or "").lower() == "blocked":
            raise ContributionBlocked(
                str(parsed.get("reason") or "provider reported blocked"),
                retryable=bool(parsed.get("retryable", True)),
            )
        contribution_payload = (
            parsed.get("contribution")
            if isinstance(parsed.get("contribution"), Mapping)
            else parsed
        )
        return QualifiedContribution.from_payload(
            contribution_payload,
            expected_tenant_id=tenant_id,
            expected_request_id=request_id,
        )
