"""Tenant-scoped Agora DatasetVersion -> IMT-003 dataset payload translation.

L12-IMIT-001. The imitation lane must train and shadow-evaluate on real,
tenant-scoped Agora ``DatasetVersion`` content produced by the governed Agora
dataset-extraction owner (L12-AGORA-001).  This module is the pure translation
layer between that authority and the existing IMT-003 dataset payload consumed
by :func:`services.research.imitation.bc_trainer.train`.

Two invariants make this module safe to call from a scheduler:

* It never substitutes seed, fixture, or synthesized data.  When the supplied
  records cannot produce a governed dataset the caller gets an exception and is
  expected to degrade truthfully instead of training on something else.
* It never mixes tenants.  A payload is always built inside exactly one
  ``tenant_id`` / ``user_id`` scope, and that scope is carried in the lineage
  descriptor that downstream evaluation and candidate artifacts must retain.

Nothing here writes to a registry, mutates a RuntimeBinding, or promotes an
artifact; it only reads records and shapes them for research training.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence


AGORA_DATASET_AUTHORITY = "agora.dataset.v1"
LEARN_DATASET_KIND = "learn"
OBSERVE_DATASET_KIND = "observe"
DATASET_URI_SCHEME = "agora://dataset-version"

# Default identifiers used when an Agora record carries governed trajectory
# content but no explicit strategy binding.  These are labels for research
# bookkeeping only; they never select a runtime artifact.
DEFAULT_STRATEGY_ID = "agora-human-imitation"
DEFAULT_STRATEGY_SPEC_ID = "strat-agora-human-imitation"


class AgoraDatasetError(ValueError):
    """Base error for Agora dataset translation failures."""

    reason = "agora_dataset_error"


class AgoraDatasetUnavailable(AgoraDatasetError):
    """No governed Agora DatasetVersion content was available.

    Raised when the caller supplied no versions at all.  Callers must degrade;
    substituting seed data in response to this error is a governance defect.
    """

    reason = "agora_dataset_version_unavailable"


class AgoraDatasetRejected(AgoraDatasetError):
    """Versions were present but are not eligible for imitation learning."""

    reason = "agora_dataset_version_rejected"


class AgoraTenantScopeError(AgoraDatasetError):
    """A build was attempted across more than one tenant or user scope."""

    reason = "agora_dataset_tenant_scope_violation"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return ()
        # Postgres JSONB columns round-trip as text in some drivers.
        if stripped.startswith("["):
            try:
                parsed = json.loads(stripped)
            except ValueError:
                return (stripped,)
            return _text_tuple(parsed)
        return (stripped,)
    if isinstance(value, Mapping):
        return ()
    if isinstance(value, Iterable):
        items = [_text(item) for item in value]
        return tuple(item for item in items if item)
    return ()


def _as_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return {}
        try:
            parsed = json.loads(stripped)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _as_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return _text(value).lower() in {"true", "t", "1", "yes", "y"}


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


@dataclass(frozen=True)
class AgoraDatasetVersion:
    """One tenant-scoped Agora ``DatasetVersion`` row.

    Mirrors ``agora.agora_dataset_records`` as written by the Agora dataset
    extraction owner.  ``dataset_version_id`` is the stable identity that every
    downstream candidate and evaluation artifact must cite.
    """

    dataset_version_id: str
    tenant_id: str
    user_id: str
    dataset_kind: str
    interaction_kind: str
    persona_id: str
    evidence_id: str
    content: Mapping[str, Any]
    source_refs: tuple[str, ...] = ()
    session_id: str | None = None
    learning_eligible: bool = True
    captured_at: str = ""
    extracted_at: str = ""
    version: int = 1

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "AgoraDatasetVersion":
        """Build a version from an Agora dataset record mapping.

        Raises:
            AgoraDatasetRejected: when identity columns required for lineage
                (dataset version, tenant, evidence) are missing.
        """

        if not isinstance(record, Mapping):
            raise AgoraDatasetRejected("Agora dataset record must be a mapping")
        dataset_version_id = _text(record.get("dataset_version_id"))
        tenant_id = _text(record.get("tenant_id"))
        evidence_id = _text(record.get("evidence_id"))
        missing = [
            name
            for name, value in (
                ("dataset_version_id", dataset_version_id),
                ("tenant_id", tenant_id),
                ("evidence_id", evidence_id),
            )
            if not value
        ]
        if missing:
            raise AgoraDatasetRejected(
                "Agora dataset record is missing lineage identity: " + ", ".join(missing)
            )
        session_id = _text(record.get("session_id")) or None
        try:
            version = int(record.get("version") or 1)
        except (TypeError, ValueError):
            version = 1
        return cls(
            dataset_version_id=dataset_version_id,
            tenant_id=tenant_id,
            user_id=_text(record.get("user_id")),
            dataset_kind=_text(record.get("dataset_kind")) or LEARN_DATASET_KIND,
            interaction_kind=_text(record.get("interaction_kind")),
            persona_id=_text(record.get("persona_id")),
            evidence_id=evidence_id,
            content=_as_mapping(record.get("content")),
            source_refs=_text_tuple(record.get("source_refs")),
            session_id=session_id,
            learning_eligible=_as_bool(record.get("learning_eligible"), default=True),
            captured_at=_text(record.get("captured_at")),
            extracted_at=_text(record.get("extracted_at")),
            version=version,
        )

    @property
    def dataset_uri(self) -> str:
        """Stable, tenant-qualified lineage URI for this dataset version."""

        return f"{DATASET_URI_SCHEME}/{self.tenant_id}/{self.dataset_version_id}"

    @property
    def evidence_uri(self) -> str:
        return f"evidence://{self.evidence_id}"

    def to_dataset_ref(self) -> dict[str, Any]:
        """Scheduler-facing reference carrying the full discovery scope.

        ``id`` stays populated so existing dedupe and candidate plumbing keeps
        working, but every consumer should prefer ``dataset_version_id``.
        """

        return {
            "id": self.dataset_version_id,
            "dataset_version_id": self.dataset_version_id,
            "type": "agora_dataset_version",
            "source": AGORA_DATASET_AUTHORITY,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "dataset_kind": self.dataset_kind,
            "interaction_kind": self.interaction_kind,
            "persona_id": self.persona_id,
            "session_id": self.session_id,
            "evidence_id": self.evidence_id,
            "extracted_at": self.extracted_at,
            "dataset_uri": self.dataset_uri,
        }

    def content_sha256(self) -> str:
        return sha256_json(
            {
                "dataset_version_id": self.dataset_version_id,
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "evidence_id": self.evidence_id,
                "content": json.loads(_stable_json(self.content)),
            }
        )


def _session_target(content: Mapping[str, Any]) -> dict[str, Any]:
    target = content.get("target")
    if isinstance(target, Mapping) and target:
        return dict(target)
    return {
        "registry_id": _text(content.get("registry_id")) or "agora-observed",
        "strategy_id": _text(content.get("strategy_id")) or DEFAULT_STRATEGY_ID,
        "artifact_version": _text(content.get("artifact_version")) or "0.0.0",
        "artifact_type": "strategy_spec",
        "promotion_state": _text(content.get("promotion_state")) or "candidate",
    }


def _sessions_from_content(version: AgoraDatasetVersion) -> list[dict[str, Any]]:
    """Normalize an Agora record's content into IMT-003 trajectory sessions.

    Three governed content shapes are accepted, in priority order:
      1. ``{"sessions": [...]}``  — already IMT-003 shaped
      2. ``{"steps": [...]}``     — a single trajectory
      3. ``{"observation": ..., "action": ...}`` — a single step
    """

    content = version.content
    sessions_value = content.get("sessions")
    if isinstance(sessions_value, Sequence) and not isinstance(sessions_value, (str, bytes)):
        sessions = [
            dict(session)
            for session in sessions_value
            if isinstance(session, Mapping) and session.get("steps")
        ]
        if sessions:
            return sessions

    base = {
        "actor_id": _text(content.get("actor_id")) or version.persona_id or "agora-actor",
        "actor_role": _text(content.get("actor_role")) or "operator",
        "decision": _text(content.get("decision")) or "approve",
        "target": _session_target(content),
    }

    steps_value = content.get("steps")
    if isinstance(steps_value, Sequence) and not isinstance(steps_value, (str, bytes)) and steps_value:
        return [
            {
                **base,
                "trajectory_id": _text(content.get("trajectory_id")) or f"traj-{version.evidence_id}",
                "steps": [dict(step) for step in steps_value if isinstance(step, Mapping)],
            }
        ]

    if "observation" in content and "action" in content:
        step: dict[str, Any] = {
            "observation": content["observation"],
            "action": content["action"],
            "feedback_event_id": version.evidence_id,
        }
        if content.get("reward") is not None:
            step["reward"] = content["reward"]
        return [
            {
                **base,
                "trajectory_id": _text(content.get("trajectory_id")) or f"traj-{version.evidence_id}",
                "steps": [step],
            }
        ]

    return []


def eligible_versions(versions: Sequence[AgoraDatasetVersion]) -> list[AgoraDatasetVersion]:
    """Filter to versions that may feed an imitation dataset."""

    return [
        version
        for version in versions
        if version.learning_eligible and version.dataset_kind == LEARN_DATASET_KIND
    ]


def _require_single_scope(versions: Sequence[AgoraDatasetVersion]) -> tuple[str, str]:
    tenants = {version.tenant_id for version in versions}
    users = {version.user_id for version in versions}
    if len(tenants) > 1:
        raise AgoraTenantScopeError(
            "Agora dataset payloads may not span tenants: " + ", ".join(sorted(tenants))
        )
    if len(users) > 1:
        raise AgoraTenantScopeError(
            "Agora dataset payloads may not span users: " + ", ".join(sorted(users))
        )
    return next(iter(tenants)), next(iter(users))


def build_dataset_payload(
    versions: Sequence[AgoraDatasetVersion],
    *,
    dataset_id: str | None = None,
) -> dict[str, Any]:
    """Translate Agora DatasetVersions into one IMT-003 dataset payload.

    Args:
        versions: Tenant-scoped versions from the Agora dataset authority.
        dataset_id: Optional override; defaults to the single version's
            ``dataset_version_id`` so the payload identity is the dataset
            version identity.

    Raises:
        AgoraDatasetUnavailable: no versions were supplied.
        AgoraDatasetRejected: versions exist but none carry governed
            trajectory content eligible for learning.
        AgoraTenantScopeError: the versions span more than one tenant or user.
    """

    if not versions:
        raise AgoraDatasetUnavailable(
            "No Agora DatasetVersion content is available for this dataset reference"
        )
    tenant_id, user_id = _require_single_scope(versions)

    usable = eligible_versions(versions)
    if not usable:
        raise AgoraDatasetRejected(
            "Agora DatasetVersions are present but none are learning-eligible "
            f"learn-kind records (tenant={tenant_id})"
        )

    sessions: list[dict[str, Any]] = []
    source_refs: list[str] = []
    strategy_id = ""
    strategy_spec_id = ""
    for version in usable:
        version_sessions = _sessions_from_content(version)
        if not version_sessions:
            continue
        sessions.extend(version_sessions)
        source_refs.append(version.dataset_uri)
        source_refs.append(version.evidence_uri)
        source_refs.extend(version.source_refs)
        strategy_id = strategy_id or _text(version.content.get("strategy_id"))
        strategy_spec_id = strategy_spec_id or _text(version.content.get("source_strategy_spec_id"))

    if not sessions:
        raise AgoraDatasetRejected(
            "Agora DatasetVersions carry no governed trajectory content "
            f"(tenant={tenant_id}, versions={len(usable)})"
        )

    resolved_dataset_id = _text(dataset_id) or usable[0].dataset_version_id
    return {
        "dataset_id": resolved_dataset_id,
        "strategy_id": strategy_id or DEFAULT_STRATEGY_ID,
        "source_dataset_refs": _dedupe(source_refs),
        "source_strategy_spec_id": strategy_spec_id or DEFAULT_STRATEGY_SPEC_ID,
        "sessions": sessions,
        "tenant_id": tenant_id,
        "user_id": user_id,
    }


def build_dataset_lineage(
    versions: Sequence[AgoraDatasetVersion],
    payload: Mapping[str, Any],
    *,
    resolved_at: str | None = None,
) -> dict[str, Any]:
    """Build the lineage descriptor carried by every downstream artifact.

    ``seed_fallback_used`` is a literal ``False``: this module has no seed path,
    so any artifact carrying this lineage is provably built from real
    tenant-scoped Agora content.
    """

    usable = eligible_versions(versions) or list(versions)
    tenant_id, user_id = _require_single_scope(usable) if usable else ("", "")
    return {
        "authority": AGORA_DATASET_AUTHORITY,
        "dataset_id": _text(payload.get("dataset_id")),
        "dataset_version_ids": _dedupe(version.dataset_version_id for version in usable),
        "dataset_uris": _dedupe(version.dataset_uri for version in usable),
        "dataset_kinds": _dedupe(version.dataset_kind for version in usable),
        "tenant_id": tenant_id,
        "user_id": user_id,
        "evidence_ids": _dedupe(version.evidence_id for version in usable),
        "persona_ids": _dedupe(version.persona_id for version in usable),
        "session_ids": _dedupe(_text(version.session_id) for version in usable),
        "source_dataset_refs": list(payload.get("source_dataset_refs") or []),
        "content_sha256": sha256_json(
            [version.content_sha256() for version in usable]
        ),
        "payload_sha256": sha256_json(payload),
        "trajectory_count": len(payload.get("sessions") or []),
        "seed_fallback_used": False,
        "resolved_at": resolved_at or utc_now(),
    }


def attach_step_probabilities(
    artifact: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> dict[str, float]:
    """Pre-compute linear-softmax action probabilities for every dataset step.

    ``eval_metrics.evaluate`` looks predictions up by ``step_id``,
    ``feedback_event_id``, or ``trajectory:index`` key.  Computing them here
    keeps the scheduler free of policy math and keeps the linear-softmax
    contract next to the trainer that produced the weights.

    The returned mapping is also written into ``artifact["policy"]`` in place so
    the caller can hand the artifact straight to the evaluator.
    """

    policy = artifact.get("policy")
    if not isinstance(policy, dict):
        return {}
    weights = policy.get("weights") or []
    bias = policy.get("bias") or []
    action_labels = policy.get("action_labels") or []
    if not weights or not action_labels:
        return {}

    probabilities_by_step: dict[str, Any] = {}
    for session in payload.get("sessions") or []:
        if not isinstance(session, Mapping):
            continue
        trajectory_id = _text(session.get("trajectory_id")) or "default"
        for step_index, step in enumerate(session.get("steps") or []):
            if not isinstance(step, Mapping):
                continue
            observation = step.get("observation") or []
            logits = [
                sum(weight * feature for weight, feature in zip(row, observation)) + offset
                for row, offset in zip(weights, bias)
            ]
            if not logits:
                continue
            max_logit = max(logits)
            exponentials = [math.exp(logit - max_logit) for logit in logits]
            total = sum(exponentials)
            if total <= 0:
                continue
            distribution = {
                label: value / total for label, value in zip(action_labels, exponentials)
            }
            for key in (
                _text(step.get("step_id")),
                _text(step.get("feedback_event_id")),
                f"{trajectory_id}:{step_index}",
                f"{trajectory_id}:step{step_index}",
            ):
                if key:
                    probabilities_by_step[key] = distribution

    policy["probabilities_by_step"] = probabilities_by_step
    return probabilities_by_step


__all__ = [
    "AGORA_DATASET_AUTHORITY",
    "AgoraDatasetError",
    "AgoraDatasetRejected",
    "AgoraDatasetUnavailable",
    "AgoraDatasetVersion",
    "AgoraTenantScopeError",
    "LEARN_DATASET_KIND",
    "OBSERVE_DATASET_KIND",
    "attach_step_probabilities",
    "build_dataset_lineage",
    "build_dataset_payload",
    "eligible_versions",
    "sha256_json",
]
