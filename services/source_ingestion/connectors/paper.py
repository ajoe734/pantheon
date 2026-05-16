"""Governed paper-source adapter skeletons."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence
import urllib.parse

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
    SourceType,
)


OPENALEX_WORKS_URL = "https://api.openalex.org/works"
OPENALEX_ALLOWED_URL_PREFIXES = ("https://api.openalex.org/",)
PAPER_NORMALIZATION_SCHEMA = "paper_source_record.v1"

_DOI_RE = re.compile(r"\b10\.\d{4,9}/[^\s\"<>]+", re.IGNORECASE)
_SAFE_ID_RE = re.compile(r"[^a-z0-9]+")
_OPENALEX_HOSTS = {"openalex.org", "api.openalex.org"}
_ROUTE_KEY_TOKENS = {
    "consumer",
    "consumers",
    "destination",
    "destinations",
    "route",
    "routes",
    "sink",
    "sinks",
    "target",
    "targets",
    "write_to",
}
_FORBIDDEN_EXECUTION_VALUES = {
    "broker",
    "broker_adapter",
    "broker-adapter",
    "canary_execution",
    "direct_execution",
    "execution",
    "lean",
    "lean_direct",
    "lean-direct",
    "live",
    "live_execution",
    "live-execution",
    "order_router",
    "order-router",
    "runtime",
    "runtime_manager",
    "runtime-manager",
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SourceEvidenceError(f"{field_name} is required for paper ingest")
    return text


def _normalized_sequence(values: Sequence[str] | str | None, *, default: Sequence[str]) -> tuple[str, ...]:
    if values is None:
        raw = tuple(default)
    elif isinstance(values, str):
        raw = (values,)
    else:
        raw = tuple(values)
    normalized: list[str] = []
    for value in raw:
        item = str(value).strip()
        if item and item not in normalized:
            normalized.append(item)
    return tuple(normalized or default)


def _safe_slug(value: str) -> str:
    return _SAFE_ID_RE.sub("-", value.strip().lower()).strip("-")


def _normalize_doi(value: Any) -> str | None:
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


def _doi_url(doi: str) -> str:
    return f"https://doi.org/{urllib.parse.quote(doi, safe='/._;()')}"


def _coerce_event_time(value: Any, field_name: str) -> str:
    text = _require_text(value, field_name)
    if re.fullmatch(r"\d{4}", text):
        return f"{text}-01-01T00:00:00Z"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return f"{text}T00:00:00Z"
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceEvidenceError(f"{field_name} must be an ISO date or datetime for paper ingest") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_openalex_uri_and_key(work: Mapping[str, Any]) -> tuple[str, str]:
    raw_id = _require_text(
        work.get("id") or work.get("openalex_id") or work.get("work_id"),
        "openalex work id",
    )
    parsed = urllib.parse.urlsplit(raw_id)
    if parsed.scheme:
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in _OPENALEX_HOSTS:
            raise SourceEvidenceError("OpenAlex work id must be hosted by openalex.org")
        key = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    else:
        key = raw_id
    key = _require_text(key, "openalex work key")
    if not re.fullmatch(r"W\d+", key, flags=re.IGNORECASE):
        raise SourceEvidenceError("OpenAlex work key must look like W<digits>")
    return f"https://openalex.org/{key.upper()}", key.upper()


def _abstract_from_inverted_index(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    by_position: dict[int, str] = {}
    for token, positions in value.items():
        if not isinstance(positions, Sequence) or isinstance(positions, str):
            continue
        for position in positions:
            try:
                by_position[int(position)] = str(token)
            except (TypeError, ValueError):
                continue
    if not by_position:
        return None
    return " ".join(by_position[index] for index in sorted(by_position))


def _paper_abstract(work: Mapping[str, Any]) -> str | None:
    abstract = work.get("abstract")
    if isinstance(abstract, str) and abstract.strip():
        return abstract.strip()
    return _abstract_from_inverted_index(work.get("abstract_inverted_index"))


def _paper_authors(work: Mapping[str, Any]) -> list[dict[str, Any]]:
    authors = work.get("authors")
    if isinstance(authors, Sequence) and not isinstance(authors, str):
        return [dict(author) if isinstance(author, Mapping) else {"name": str(author)} for author in authors]

    normalized: list[dict[str, Any]] = []
    authorships = work.get("authorships")
    if not isinstance(authorships, Sequence) or isinstance(authorships, str):
        return normalized
    for authorship in authorships:
        if not isinstance(authorship, Mapping):
            continue
        author = authorship.get("author")
        if not isinstance(author, Mapping):
            continue
        normalized.append(
            {
                "name": author.get("display_name"),
                "orcid": author.get("orcid"),
                "openalex_author_id": author.get("id"),
            }
        )
    return normalized


def _paper_venue(work: Mapping[str, Any]) -> str | None:
    primary_location = work.get("primary_location")
    if isinstance(primary_location, Mapping):
        source = primary_location.get("source")
        if isinstance(source, Mapping) and source.get("display_name"):
            return str(source["display_name"])
    host_venue = work.get("host_venue")
    if isinstance(host_venue, Mapping) and host_venue.get("display_name"):
        return str(host_venue["display_name"])
    return None


def _paper_open_access(work: Mapping[str, Any]) -> dict[str, Any]:
    open_access = work.get("open_access")
    return dict(open_access) if isinstance(open_access, Mapping) else {}


def _paper_concepts(work: Mapping[str, Any]) -> list[str]:
    concepts = work.get("concepts")
    if not isinstance(concepts, Sequence) or isinstance(concepts, str):
        return []
    labels: list[str] = []
    for concept in concepts:
        if isinstance(concept, Mapping) and concept.get("display_name"):
            labels.append(str(concept["display_name"]))
    return labels


def _flatten_strings(value: Any) -> list[str]:
    if value in (None, "", [], {}):
        return []
    if isinstance(value, Mapping):
        flattened: list[str] = []
        for item in value.values():
            flattened.extend(_flatten_strings(item))
        return flattened
    if isinstance(value, Sequence) and not isinstance(value, str):
        flattened = []
        for item in value:
            flattened.extend(_flatten_strings(item))
        return flattened
    return [str(value).strip().lower().replace("-", "_")]


def _assert_no_execution_routes(metadata: Mapping[str, Any], *, context: str) -> None:
    for key, value in metadata.items():
        normalized_key = str(key).strip().lower()
        if normalized_key in {"direct_execution_allowed", "direct_lean_feed"} and bool(value):
            raise SourceEvidenceError(f"{context} cannot enable direct execution")
        if normalized_key not in _ROUTE_KEY_TOKENS:
            continue
        values = set(_flatten_strings(value))
        forbidden = values.intersection(_FORBIDDEN_EXECUTION_VALUES)
        if forbidden:
            raise SourceEvidenceError(f"{context} cannot target execution routes: {sorted(forbidden)[0]}")


@dataclass(frozen=True)
class OpenAlexPaperIngestAdapter:
    """OpenAlex-backed paper adapter surface for source-ingest."""

    connector_id: str = "conn-openalex-papers"
    provider: str = "OpenAlex"
    license_scope: str = "open"
    api_url: str = OPENALEX_WORKS_URL
    allowed_url_prefixes: Sequence[str] = OPENALEX_ALLOWED_URL_PREFIXES
    max_records: int = 25
    max_bytes: int = 1_000_000
    timeout_seconds: float = 5.0
    default_access_scope: Sequence[str] = ("research", "operator")
    secret_ref_id: str | None = None
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.max_records <= 0 or self.max_records > 1000:
            raise SourceEvidenceError("paper adapter max_records must be > 0 and <= 1000")
        if self.max_bytes <= 0 or self.max_bytes > 10_000_000:
            raise SourceEvidenceError("paper adapter max_bytes must be > 0 and <= 10000000")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 30:
            raise SourceEvidenceError("paper adapter timeout_seconds must be > 0 and <= 30")
        allowed = _normalized_sequence(self.allowed_url_prefixes, default=OPENALEX_ALLOWED_URL_PREFIXES)
        if not any(str(self.api_url).startswith(prefix) for prefix in allowed):
            raise SourceEvidenceError("paper adapter api_url is outside allowed_url_prefixes")
        metadata = dict(self.connector_metadata)
        _assert_no_execution_routes(metadata, context="paper connector metadata")
        object.__setattr__(self, "allowed_url_prefixes", allowed)
        object.__setattr__(
            self,
            "default_access_scope",
            _normalized_sequence(self.default_access_scope, default=("research", "operator")),
        )

    def connector(self) -> SourceConnector:
        auth_type = AuthType.API_KEY if self.secret_ref_id else AuthType.NONE
        metadata = {
            "provider_example": "paper_ingest_adapter",
            "adapter_type": "paper_ingest_skeleton",
            "normalization_schema": PAPER_NORMALIZATION_SCHEMA,
            "governed_sink": "SourceRecord/EvidenceBundle",
            "direct_execution_allowed": False,
            "broker_consumption": "not_direct_action",
            "lean_consumption": "research_only_not_direct_action",
            **dict(self.connector_metadata),
        }
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=SourceType.PAPER,
            provider=self.provider,
            license_scope=self.license_scope,
            auth_type=auth_type,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=auth_type,
                secret_ref={"secret_ref_id": self.secret_ref_id} if self.secret_ref_id else None,
                auth_scope=("source_ingest:read",) if self.secret_ref_id else (),
            ),
            license_policy=LicensePolicy(
                license_scope=self.license_scope,
                allowed_use=("research", "search_index", "strategy_seed"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/openalex",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=10,
                retry_after_seconds=60,
                policy_ref="source-ingest://policy/openalex",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="OpenAlex paper ingest",
                homepage_url="https://openalex.org",
                docs_url="https://docs.openalex.org",
                owner="OpenAlex",
                tags=("paper", "openalex", "external_feed"),
            ),
            metadata=metadata,
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "external_feed",
            "url": self.api_url,
            "allowed_url_prefixes": list(self.allowed_url_prefixes),
            "timeout_seconds": self.timeout_seconds,
            "max_bytes": self.max_bytes,
            "max_records": self.max_records,
            "default_access_scope": list(self.default_access_scope),
            "respect_robots_txt": True,
        }

    def source_records_from_openalex_response(
        self,
        payload: Mapping[str, Any],
        *,
        retrieved_at: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        works = payload.get("results", payload.get("records"))
        if not isinstance(works, Sequence) or isinstance(works, (str, bytes)):
            raise SourceEvidenceError("OpenAlex paper response must include results or records")
        return self.source_records_from_openalex_works(
            works,
            retrieved_at=retrieved_at,
            trace_id=trace_id,
        )

    def source_records_from_openalex_works(
        self,
        works: Sequence[Mapping[str, Any]],
        *,
        retrieved_at: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if len(works) > self.max_records:
            raise SourceEvidenceError(f"OpenAlex paper results exceed adapter max_records={self.max_records}")
        available_time = _coerce_event_time(retrieved_at or _utc_now_iso(), "retrieved_at")
        return tuple(
            self.source_record_from_openalex_work(work, retrieved_at=available_time, trace_id=trace_id)
            for work in works
        )

    def source_record_from_openalex_work(
        self,
        work: Mapping[str, Any],
        *,
        retrieved_at: str | None = None,
        trace_id: str = "",
    ) -> SourceRecord:
        if not isinstance(work, Mapping):
            raise SourceEvidenceError("OpenAlex paper work must be a mapping")
        metadata = dict(work.get("metadata") or {})
        _assert_no_execution_routes(metadata, context="paper work metadata")

        raw_uri, work_key = _canonical_openalex_uri_and_key(work)
        title = _require_text(work.get("title") or work.get("display_name"), "paper title")
        publication_date = work.get("publication_date") or work.get("publication_year")
        event_time = _coerce_event_time(publication_date, "publication_date")
        available_time = _coerce_event_time(retrieved_at or _utc_now_iso(), "retrieved_at")
        doi = _normalize_doi(work.get("doi") or metadata.get("doi"))
        content_ref = _doi_url(doi) if doi else raw_uri
        abstract = _paper_abstract(work)
        body = str(metadata.get("body") or abstract or title)
        source_id = str(work.get("source_id") or f"src-paper-openalex-{_safe_slug(work_key)}")
        authors = _paper_authors(work)
        concepts = _paper_concepts(work)
        open_access = _paper_open_access(work)
        citation_year = event_time[:4]

        payload_metadata = {
            **metadata,
            "provider": self.provider,
            "raw_uri": raw_uri,
            "source_uri": content_ref,
            "source_feed_url": self.api_url,
            "source_fetch_mode": "paper_ingest_adapter",
            "paper_ingest_adapter": "openalex.v1",
            "paper_normalization_schema": PAPER_NORMALIZATION_SCHEMA,
            "openalex_work_id": work_key,
            "doi": doi,
            "canonical_doi": doi,
            "authors": authors,
            "publication_date": str(publication_date),
            "published_at": event_time,
            "event_time": event_time,
            "available_time": available_time,
            "venue": _paper_venue(work),
            "open_access": open_access,
            "citation_count": work.get("cited_by_count"),
            "concepts": concepts,
            "keywords": concepts,
            "abstract": abstract,
            "body": body,
            "body_hash": f"sha256:{sha256(body.encode('utf-8')).hexdigest()}",
            "citation_label": str(metadata.get("citation_label") or f"{title} ({citation_year})"),
            "evidence_item_type": "academic_paper",
            "license_scope": self.license_scope,
            "access_scope": list(self.default_access_scope),
            "allowed_use": ["research", "search_index", "strategy_seed"],
            "strategy_seed_ready": bool(abstract or doi),
            "governance": {
                **(metadata.get("governance") if isinstance(metadata.get("governance"), dict) else {}),
                "canonical_sink": "SourceRecord/EvidenceBundle",
                "direct_execution_allowed": False,
                "broker_consumption": "not_direct_action",
                "lean_consumption": "research_only_not_direct_action",
                "openclaw_consumption": "governed_search_only",
            },
        }

        return SourceRecord(
            source_id=source_id,
            connector_id=self.connector_id,
            source_type=SourceType.PAPER,
            title=title,
            content_ref=content_ref,
            metadata=payload_metadata,
            trace_id=trace_id,
        )
