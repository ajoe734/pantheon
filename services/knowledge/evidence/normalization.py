"""Canonical source/evidence normalization helpers.

The source-ingest service owns SourceRecord persistence, while knowledge owns
EvidenceItem and KnowledgeObject construction. This module is the narrow bridge
that makes source/evidence identity deterministic before either side writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import re
import urllib.parse
from typing import Any, Mapping, Sequence

from services.source_ingestion.connectors.base import SourceRecord

from .models import EvidenceItem, EvidenceValidationError


_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_REPO_HOSTS = {"github.com", "gitlab.com", "bitbucket.org"}


@dataclass(frozen=True)
class NormalizedEvidenceOwnership:
    """Deterministic ownership refs for one source/evidence pair."""

    source_record: SourceRecord
    evidence_item: EvidenceItem
    source_dedupe_key: str
    evidence_dedupe_key: str
    evidence_owner_id: str
    canonical_url: str | None = None
    canonical_doi: str | None = None
    canonical_repo: str | None = None
    content_hash: str | None = None

    def metadata(self) -> dict[str, Any]:
        return {
            "canonical_url": self.canonical_url,
            "canonical_doi": self.canonical_doi,
            "canonical_repo": self.canonical_repo,
            "content_hash": self.content_hash,
            "source_dedupe_key": self.source_dedupe_key,
            "evidence_dedupe_key": self.evidence_dedupe_key,
            "evidence_owner_id": self.evidence_owner_id,
        }


def normalize_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = urllib.parse.urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None

    scheme = parsed.scheme.lower()
    hostname = (parsed.hostname or "").lower()
    port = parsed.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path or "/"), safe="/:@")
    if path != "/":
        path = path.rstrip("/")
    query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query = urllib.parse.urlencode(sorted(query_pairs), doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def normalize_doi(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    text = re.sub(r"^doi:\s*", "", text, flags=re.IGNORECASE)
    parsed = urllib.parse.urlsplit(text)
    if parsed.netloc.lower() in {"doi.org", "dx.doi.org"}:
        text = urllib.parse.unquote(parsed.path.lstrip("/"))
    match = _DOI_RE.search(text)
    if not match:
        return None
    return match.group(0).rstrip(".,;").lower()


def normalize_repo_url(value: Any) -> str | None:
    canonical_url = normalize_url(value)
    if canonical_url is None:
        return None
    parsed = urllib.parse.urlsplit(canonical_url)
    if parsed.netloc.lower() not in _REPO_HOSTS:
        return None
    path = parsed.path.rstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    repo_path = "/".join((owner, repo)).lower()
    return urllib.parse.urlunsplit(("https", parsed.netloc.lower(), f"/{repo_path}", "", ""))


def normalize_scope(values: Sequence[str] | str | None, *, default: Sequence[str]) -> tuple[str, ...]:
    if values is None:
        raw_values = tuple(default)
    elif isinstance(values, str):
        raw_values = (values,)
    else:
        raw_values = tuple(values)
    normalized: list[str] = []
    for value in raw_values:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return tuple(normalized or default)


def content_hash_for_record(record: SourceRecord, *, body: str | None = None) -> str:
    metadata = dict(record.metadata)
    content = body or metadata.get("raw_content") or metadata.get("content") or metadata.get("text") or metadata.get("body")
    if content:
        basis = str(content)
    else:
        basis = "\n".join((record.title, str(metadata.get("canonical_url") or record.content_ref)))
    return f"sha256:{sha256(basis.encode('utf-8')).hexdigest()}"


def _short_hash(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:16]


def _source_dedupe_key(
    *,
    canonical_doi: str | None,
    canonical_repo: str | None,
    canonical_url: str | None,
    content_hash: str,
) -> str:
    if canonical_doi:
        return f"doi:{canonical_doi}"
    if canonical_repo:
        return f"repo:{canonical_repo}"
    if canonical_url:
        return f"url:{canonical_url}"
    return f"content:{content_hash}"


def normalize_source_record(record: SourceRecord, *, connector_license_scope: str | None = None) -> SourceRecord:
    metadata = dict(record.metadata)
    canonical_url = normalize_url(metadata.get("canonical_url") or metadata.get("url") or record.content_ref)
    canonical_doi = normalize_doi(metadata.get("canonical_doi") or metadata.get("doi") or record.content_ref)
    canonical_repo = normalize_repo_url(metadata.get("canonical_repo") or metadata.get("repo_url") or record.content_ref)
    content_hash = str(metadata.get("content_hash") or content_hash_for_record(record))
    source_dedupe_key = _source_dedupe_key(
        canonical_doi=canonical_doi,
        canonical_repo=canonical_repo,
        canonical_url=canonical_url,
        content_hash=content_hash,
    )
    license_scope = str(metadata.get("license_scope") or connector_license_scope or "internal").strip()
    if not license_scope:
        raise EvidenceValidationError("license_scope is required for normalized source evidence")
    access_scope = normalize_scope(metadata.get("access_scope"), default=("public",))

    normalized_metadata = {
        **metadata,
        "canonical_url": canonical_url,
        "canonical_doi": canonical_doi,
        "canonical_repo": canonical_repo,
        "content_hash": content_hash,
        "source_dedupe_key": source_dedupe_key,
        "license_scope": license_scope,
        "access_scope": list(access_scope),
    }
    return SourceRecord(
        source_id=record.source_id,
        connector_id=record.connector_id,
        source_type=record.source_type.value,
        title=record.title,
        content_ref=canonical_url or record.content_ref,
        status=record.status.value,
        metadata=normalized_metadata,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


def normalize_evidence_item(record: SourceRecord, item: EvidenceItem, *, source_owner_id: str | None = None) -> EvidenceItem:
    if item.source_id != record.source_id and source_owner_id is None:
        raise EvidenceValidationError("EvidenceItem must reference the normalized SourceRecord owner")
    metadata = dict(record.metadata)
    item_metadata = dict(item.metadata)
    owner_source_id = source_owner_id or record.source_id
    content_hash = str(metadata.get("content_hash") or content_hash_for_record(record, body=item.body))
    evidence_dedupe_key = str(
        item_metadata.get("evidence_dedupe_key")
        or f"{metadata.get('source_dedupe_key')}#item:{item.item_type}:{content_hash}"
    )
    evidence_owner_id = str(item_metadata.get("evidence_owner_id") or f"evi-{_short_hash(evidence_dedupe_key)}")
    return EvidenceItem(
        evidence_item_id=evidence_owner_id,
        source_id=owner_source_id,
        item_type=item.item_type,
        content_ref=metadata.get("canonical_url") or item.content_ref,
        citation_label=item.citation_label,
        body=item.body,
        event_time=item.event_time,
        available_time=item.available_time,
        confidence=item.confidence,
        access_scope=normalize_scope(item.access_scope, default=("public",)),
        trace_refs=item.trace_refs,
        metadata={
            **item_metadata,
            "canonical_url": metadata.get("canonical_url"),
            "canonical_doi": metadata.get("canonical_doi"),
            "canonical_repo": metadata.get("canonical_repo"),
            "content_hash": content_hash,
            "source_dedupe_key": metadata.get("source_dedupe_key"),
            "evidence_dedupe_key": evidence_dedupe_key,
            "evidence_owner_id": evidence_owner_id,
            "source_owner_id": owner_source_id,
        },
    )


def normalize_source_evidence(
    *,
    source_record: SourceRecord,
    evidence_item: EvidenceItem,
    connector_license_scope: str | None = None,
    source_owner_id: str | None = None,
) -> NormalizedEvidenceOwnership:
    normalized_source = normalize_source_record(source_record, connector_license_scope=connector_license_scope)
    normalized_item = normalize_evidence_item(
        normalized_source,
        evidence_item,
        source_owner_id=source_owner_id,
    )
    metadata = dict(normalized_item.metadata)
    return NormalizedEvidenceOwnership(
        source_record=normalized_source,
        evidence_item=normalized_item,
        source_dedupe_key=str(metadata["source_dedupe_key"]),
        evidence_dedupe_key=str(metadata["evidence_dedupe_key"]),
        evidence_owner_id=str(metadata["evidence_owner_id"]),
        canonical_url=metadata.get("canonical_url"),
        canonical_doi=metadata.get("canonical_doi"),
        canonical_repo=metadata.get("canonical_repo"),
        content_hash=metadata.get("content_hash"),
    )
