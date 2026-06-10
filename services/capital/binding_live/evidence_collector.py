"""Capital binding live readiness evidence collector.

Resolves the local evidence refs carried by
CapitalBindingLiveReadiness.required_evidence. The collector is read-only and
fail-closed: missing, unreadable, malformed, or out-of-root refs are reported as
blocking issues instead of being silently skipped.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from json import JSONDecodeError
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit

from services.capital.binding_live.readiness_model import (
    REQUIRED_EVIDENCE_FIELDS,
    CapitalBindingLiveReadiness,
    validate_readiness,
)


class CapitalBindingEvidenceCollectionError(ValueError):
    """Raised when required capital binding evidence is incomplete."""


@dataclass(frozen=True)
class EvidenceResolutionIssue:
    field: str
    ref: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "field": self.field,
            "ref": self.ref,
            "code": self.code,
            "message": self.message,
        }


@dataclass(frozen=True)
class CollectedEvidence:
    field: str
    ref: str
    resolved_path: str
    sha256: str
    content_type: str
    payload: Any
    fragment: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "field": self.field,
            "ref": self.ref,
            "resolved_path": self.resolved_path,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "payload": self.payload,
        }
        if self.fragment:
            payload["fragment"] = self.fragment
        return payload


@dataclass(frozen=True)
class CapitalBindingEvidenceCollection:
    readiness_id: str
    binding_id: str
    evidence_root: str
    collected_evidence: tuple[CollectedEvidence, ...]
    issues: tuple[EvidenceResolutionIssue, ...]

    @property
    def complete(self) -> bool:
        return not self.issues and len(self.collected_evidence) == len(REQUIRED_EVIDENCE_FIELDS)

    @property
    def missing_evidence(self) -> tuple[EvidenceResolutionIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == "missing_evidence_ref")

    @property
    def invalid_evidence(self) -> tuple[EvidenceResolutionIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code != "missing_evidence_ref")

    @property
    def blocking_reasons(self) -> tuple[str, ...]:
        if self.complete:
            return ()
        return tuple(f"{issue.field}:{issue.code}" for issue in self.issues)

    def raise_for_incomplete(self) -> None:
        if self.complete:
            return
        details = "; ".join(
            f"{issue.field} {issue.code}: {issue.message}" for issue in self.issues
        )
        raise CapitalBindingEvidenceCollectionError(
            "capital binding required evidence failed closed: " + details
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "readiness_id": self.readiness_id,
            "binding_id": self.binding_id,
            "evidence_root": self.evidence_root,
            "complete": self.complete,
            "collected_evidence": [item.to_dict() for item in self.collected_evidence],
            "missing_evidence": [issue.to_dict() for issue in self.missing_evidence],
            "invalid_evidence": [issue.to_dict() for issue in self.invalid_evidence],
            "blocking_reasons": list(self.blocking_reasons),
        }


class CapitalBindingEvidenceCollector:
    """Resolve required evidence refs for a readiness packet from local files."""

    def __init__(self, evidence_root: str | Path = ".") -> None:
        self.evidence_root = Path(evidence_root).resolve()

    def collect(
        self,
        packet: Mapping[str, Any] | CapitalBindingLiveReadiness,
    ) -> CapitalBindingEvidenceCollection:
        readiness = validate_readiness(packet)
        refs = readiness.required_evidence.to_dict()
        collected: list[CollectedEvidence] = []
        issues: list[EvidenceResolutionIssue] = []

        for field in REQUIRED_EVIDENCE_FIELDS:
            ref = refs[field]
            evidence, issue = self._resolve_ref(field, ref)
            if issue is not None:
                issues.append(issue)
            elif evidence is not None:
                collected.append(evidence)

        return CapitalBindingEvidenceCollection(
            readiness_id=readiness.readiness_id,
            binding_id=readiness.binding_id,
            evidence_root=self.evidence_root.as_posix(),
            collected_evidence=tuple(collected),
            issues=tuple(issues),
        )

    def _resolve_ref(
        self,
        field: str,
        ref: str,
    ) -> tuple[CollectedEvidence | None, EvidenceResolutionIssue | None]:
        parsed = urlsplit(ref)
        if parsed.scheme and parsed.scheme != "file":
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="unsupported_evidence_ref_scheme",
                message=f"{field} uses unsupported scheme {parsed.scheme}",
            )
        if parsed.scheme == "file" and parsed.netloc:
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="unsupported_evidence_ref_scheme",
                message=f"{field} file URI must not include a network location",
            )

        path_text = unquote(parsed.path if parsed.scheme else urlsplit(ref).path).strip()
        fragment = parsed.fragment or None
        if not path_text:
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="empty_evidence_ref",
                message=f"{field} has an empty evidence path",
            )

        raw_path = Path(path_text)
        candidate = raw_path if raw_path.is_absolute() else self.evidence_root / raw_path
        resolved = candidate.resolve(strict=False)
        if not _is_relative_to(resolved, self.evidence_root):
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="evidence_ref_outside_root",
                message=f"{field} resolves outside evidence root",
            )

        if not resolved.exists():
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="missing_evidence_ref",
                message=f"{field} evidence file does not exist",
            )
        if not resolved.is_file():
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="invalid_evidence_ref",
                message=f"{field} evidence ref is not a file",
            )

        try:
            raw = resolved.read_bytes()
        except OSError as exc:
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="unreadable_evidence_ref",
                message=f"{field} evidence file could not be read: {exc}",
            )

        if not raw:
            return None, EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="empty_evidence_payload",
                message=f"{field} evidence file is empty",
            )

        payload, content_type, issue = _decode_payload(field, ref, resolved, raw)
        if issue is not None:
            return None, issue

        return (
            CollectedEvidence(
                field=field,
                ref=ref,
                resolved_path=resolved.relative_to(self.evidence_root).as_posix(),
                sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
                content_type=content_type,
                payload=payload,
                fragment=fragment,
            ),
            None,
        )


def collect_required_evidence(
    packet: Mapping[str, Any] | CapitalBindingLiveReadiness,
    *,
    evidence_root: str | Path = ".",
) -> CapitalBindingEvidenceCollection:
    """Collect all CapitalBindingLiveReadiness.required_evidence refs."""

    return CapitalBindingEvidenceCollector(evidence_root=evidence_root).collect(packet)


def _decode_payload(
    field: str,
    ref: str,
    path: Path,
    raw: bytes,
) -> tuple[Any, str, EvidenceResolutionIssue | None]:
    suffix = path.suffix.lower()
    if suffix == ".json":
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError as exc:
            return None, "application/json", EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="invalid_json_evidence",
                message=f"{field} JSON evidence is not UTF-8: {exc}",
            )
        except JSONDecodeError as exc:
            return None, "application/json", EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="invalid_json_evidence",
                message=f"{field} JSON evidence is malformed: {exc.msg}",
            )
        if payload is None:
            return None, "application/json", EvidenceResolutionIssue(
                field=field,
                ref=ref,
                code="empty_evidence_payload",
                message=f"{field} JSON evidence must not be null",
            )
        return payload, "application/json", None

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return None, "text/plain", EvidenceResolutionIssue(
            field=field,
            ref=ref,
            code="invalid_text_evidence",
            message=f"{field} text evidence is not UTF-8: {exc}",
        )
    if not text.strip():
        return None, _content_type_for(path), EvidenceResolutionIssue(
            field=field,
            ref=ref,
            code="empty_evidence_payload",
            message=f"{field} text evidence is empty",
        )
    return text, _content_type_for(path), None


def _content_type_for(path: Path) -> str:
    if path.suffix.lower() == ".md":
        return "text/markdown"
    return "text/plain"


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True

