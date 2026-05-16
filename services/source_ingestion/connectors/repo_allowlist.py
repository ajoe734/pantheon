"""Repo allowlist provider skeleton for governed source ingestion.

The provider deliberately emits bounded ``static_records`` fetch config.  It
does not fetch arbitrary repository URLs; callers must name an allowlisted
``owner/repo`` plus explicit relative paths that downstream workers may use as
research-only references.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re
from typing import Any, Mapping, Sequence

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceEvidenceError,
    SourceMetadata,
)


_REPO_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_SAFE_REF_RE = re.compile(r"^[A-Za-z0-9._/@+-]+$")


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise SourceEvidenceError(f"{field_name} is required")
    return text


def _normalize_repo_full_name(value: str) -> str:
    full_name = _require_text(value, "repo_full_name")
    if not _REPO_FULL_NAME_RE.match(full_name):
        raise SourceEvidenceError("repo_full_name must be owner/repo, not a URL or arbitrary locator")
    return full_name


def _normalize_ref(value: str | None, *, default_ref: str) -> str:
    ref = _require_text(value or default_ref, "ref")
    if ".." in ref or ref.startswith(("/", "\\")) or not _SAFE_REF_RE.match(ref):
        raise SourceEvidenceError("ref must be a safe branch, tag, or commit ref")
    return ref


def _normalize_allowed_path(value: Any) -> str:
    path = _require_text(value, "allowed_paths")
    if path.startswith(("/", "\\")):
        raise SourceEvidenceError("allowed_paths must be repository-relative paths")
    path = path.replace("\\", "/").strip("/")
    if not path or path == ".":
        return "."
    parts = [part for part in path.split("/") if part]
    if any(part == ".." for part in parts):
        raise SourceEvidenceError("allowed_paths must not contain traversal")
    if any(part in {"*", "**"} for part in parts):
        raise SourceEvidenceError("allowed_paths must be explicit prefixes, not wildcards")
    if ":" in path:
        raise SourceEvidenceError("allowed_paths must be repository-relative paths")
    return "/".join(parts)


def _normalize_allowed_paths(values: Sequence[str] | str) -> tuple[str, ...]:
    raw_values = (values,) if isinstance(values, str) else tuple(values)
    paths: list[str] = []
    for value in raw_values:
        path = _normalize_allowed_path(value)
        if path not in paths:
            paths.append(path)
    if not paths:
        raise SourceEvidenceError("allowed_paths must include at least one repository-relative path")
    return tuple(paths)


def _stable_source_id(repo_full_name: str) -> str:
    digest = sha256(repo_full_name.lower().encode("utf-8")).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", repo_full_name).strip("-").lower()
    return f"src-repo-{slug}-{digest}"


@dataclass(frozen=True)
class RepoAllowlistEntry:
    """One governed GitHub repository entry.

    ``repo_full_name`` must be the GitHub ``owner/repo`` identifier.  External
    URLs and free-form locators are intentionally rejected at construction time.
    """

    repo_full_name: str
    allowed_paths: Sequence[str] | str
    ref: str | None = None
    purpose: str = "research"
    license_scope: str | None = None
    access_scope: Sequence[str] = field(default_factory=lambda: ("research",))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        full_name = _normalize_repo_full_name(self.repo_full_name)
        object.__setattr__(self, "repo_full_name", full_name)
        object.__setattr__(self, "allowed_paths", _normalize_allowed_paths(self.allowed_paths))
        object.__setattr__(self, "ref", _normalize_ref(self.ref, default_ref="main"))
        object.__setattr__(self, "purpose", _require_text(self.purpose, "purpose"))
        object.__setattr__(self, "license_scope", str(self.license_scope).strip() if self.license_scope else None)
        object.__setattr__(
            self,
            "access_scope",
            tuple(str(item).strip() for item in self.access_scope if str(item).strip()) or ("research",),
        )
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def owner(self) -> str:
        return self.repo_full_name.split("/", 1)[0]

    @property
    def repo(self) -> str:
        return self.repo_full_name.split("/", 1)[1]

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.repo_full_name}"

    def source_record_payload(self, *, connector_id: str, default_license_scope: str) -> dict[str, Any]:
        license_scope = self.license_scope or default_license_scope
        metadata = {
            **dict(self.metadata),
            "provider": "github",
            "repo_allowlist_policy": "repo_allowlist_v1",
            "allowlist_enforced": True,
            "repo_full_name": self.repo_full_name,
            "repo_owner": self.owner,
            "repo_name": self.repo,
            "repo_url": self.repo_url,
            "raw_uri": self.repo_url,
            "canonical_repo": self.repo_url,
            "ref": self.ref,
            "allowed_paths": list(self.allowed_paths),
            "purpose": self.purpose,
            "license_scope": license_scope,
            "access_scope": list(self.access_scope),
            "source_quality_score": 0.72,
            "evidence_item_type": "code_repository",
            "citation_label": f"{self.repo_full_name}@{self.ref}",
            "keywords": ["repo", "github", self.owner, self.repo],
            "body": (
                f"Governed GitHub repository allowlist entry for {self.repo_full_name} "
                f"at ref {self.ref}; allowed paths: {', '.join(self.allowed_paths)}."
            ),
            "governance": {
                "canonical_sink": "SourceRecord/EvidenceBundle",
                "direct_execution_allowed": False,
                "lean_consumption": "research_only_not_direct_action",
                "broker_consumption": "not_direct_action",
                "allowed_ingest_mode": "repo_allowlist_static_record",
            },
        }
        return {
            "source_id": _stable_source_id(self.repo_full_name),
            "connector_id": connector_id,
            "source_type": "repo",
            "title": f"GitHub allowlisted repository: {self.repo_full_name}",
            "content_ref": self.repo_url,
            "status": "normalized",
            "metadata": metadata,
        }

    def to_policy_dict(self) -> dict[str, Any]:
        return {
            "repo_full_name": self.repo_full_name,
            "repo_url": self.repo_url,
            "allowed_paths": list(self.allowed_paths),
            "ref": self.ref,
            "purpose": self.purpose,
            "access_scope": list(self.access_scope),
            **({"license_scope": self.license_scope} if self.license_scope else {}),
        }

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, default_ref: str) -> "RepoAllowlistEntry":
        return cls(
            repo_full_name=str(data["repo_full_name"]),
            allowed_paths=data.get("allowed_paths") or (),
            ref=str(data.get("ref") or default_ref),
            purpose=str(data.get("purpose") or "research"),
            license_scope=data.get("license_scope"),
            access_scope=tuple(data.get("access_scope") or ("research",)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RepoAllowlistProvider:
    """SourceConnectorProvider for a bounded GitHub repository allowlist."""

    connector_id: str
    allowlist: Sequence[RepoAllowlistEntry | Mapping[str, Any]]
    provider: str = "GitHub repo allowlist"
    license_scope: str = "oss"
    default_ref: str = "main"
    secret_ref_id: str | None = None
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "connector_id", _require_text(self.connector_id, "connector_id"))
        object.__setattr__(self, "provider", _require_text(self.provider, "provider"))
        object.__setattr__(self, "license_scope", _require_text(self.license_scope, "license_scope"))
        object.__setattr__(self, "default_ref", _normalize_ref(self.default_ref, default_ref="main"))
        entries: list[RepoAllowlistEntry] = []
        for item in self.allowlist:
            entry = (
                item
                if isinstance(item, RepoAllowlistEntry)
                else RepoAllowlistEntry.from_mapping(item, default_ref=self.default_ref)
            )
            if entry.repo_full_name in {existing.repo_full_name for existing in entries}:
                raise SourceEvidenceError(f"duplicate repo allowlist entry: {entry.repo_full_name}")
            entries.append(entry)
        if not entries:
            raise SourceEvidenceError("repo allowlist must include at least one entry")
        object.__setattr__(self, "allowlist", tuple(entries))
        object.__setattr__(self, "connector_metadata", dict(self.connector_metadata))

    def connector(self) -> SourceConnector:
        metadata = {
            "provider_example": "repo_allowlist",
            "repo_allowlist_policy": "repo_allowlist_v1",
            "allowlist_enforced": True,
            "direct_execution_allowed": False,
            "canonical_sink": "SourceRecord/EvidenceBundle",
            "allowed_repositories": [entry.to_policy_dict() for entry in self.allowlist],
            **dict(self.connector_metadata),
        }
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="repo",
            provider=self.provider,
            license_scope=self.license_scope,
            auth_type=AuthType.SECRET_REF if self.secret_ref_id else AuthType.NONE,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.SECRET_REF if self.secret_ref_id else AuthType.NONE,
                secret_ref={"secret_ref_id": self.secret_ref_id} if self.secret_ref_id else None,
                auth_scope=("github:read",) if self.secret_ref_id else (),
            ),
            license_policy=LicensePolicy(
                license_scope=self.license_scope,
                allowed_use=("research", "search_index", "strategy_seed"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/repo-allowlist",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=10,
                retry_after_seconds=60,
                policy_ref="source-ingest://policy/repo-allowlist",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="GitHub repo allowlist",
                homepage_url="https://github.com",
                docs_url="https://docs.github.com/rest",
                owner="pantheon-source-ingest",
                tags=("repo", "github", "allowlist"),
            ),
            metadata=metadata,
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "static_records",
            "records": [
                entry.source_record_payload(
                    connector_id=self.connector_id,
                    default_license_scope=self.license_scope,
                )
                for entry in self.allowlist
            ],
            "next_watermark": None,
        }
