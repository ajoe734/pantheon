"""Reconcile persona data requirements into source-ingest actual state.

The controller boundary here is intentionally store-level: persona records are
desired state, while configured source connectors and connector schedules are
actual state.  Reconciliation writes only when actual state is missing or
drifted, so repeated ticks do not append duplicate JSONL records.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol, Sequence

from jsonschema import Draft7Validator

from .configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from .connectors import SourceConnector, SourceConnectorProvider, SourceEvidenceError
from .connectors.finmind_taiwan import FINMIND_TAIWAN_DATASETS, FinMindTaiwanDatasetAdapter
from .connectors.taiwan_official import (
    TAIWAN_OFFICIAL_ENDPOINTS,
    TW_OFFICIAL_CONNECTOR_ID,
    TaiwanOfficialMarketDatasetAdapter,
)
from .ingest_manager import IngestManager


class _RequirementLike(Protocol):
    dataset: str
    market: str
    cadence: str
    source_class: str
    connector_candidates: Sequence[str]
    policy_gates: Sequence[str]


LIVE_SOURCE_CLASSES = frozenset({"live_push", "live_pull"})
DEFAULT_CONTROLLER_NAME = "persona_source_provisioning_reconciler"
DEFAULT_SCHEMA_VERSION = "persona_source_provisioning_reconcile_result.v1"
RECONCILIATION_METADATA_KEY = "persona_source_reconciliation"
REQUIRED_DATA_SOURCE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "control-plane" / "persona" / "required_data_sources.schema.json"
)
REQUIRED_DATA_SOURCE_VALIDATOR = Draft7Validator(
    json.loads(REQUIRED_DATA_SOURCE_SCHEMA_PATH.read_text(encoding="utf-8"))
)
SUPPORTED_POLICY_GATES = frozenset(
    {
        "no_live_capital",
        "public-source-only",
        "require_connector_approved",
        "require_freshness_within_1d",
        "require_schedule_active",
        "require_source_health_ok",
    }
)

CADENCE_INTERVAL_SECONDS: dict[str, int] = {
    "realtime": 60,
    "minutely": 60,
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "on_demand": 0,
}

_FINMIND_DATASET_BY_NORMALIZED = {
    str(item["normalized_dataset"]): str(item["dataset"])
    for item in FINMIND_TAIWAN_DATASETS
    if item.get("normalized_dataset") and item.get("dataset")
}

_OFFICIAL_TW_DATASETS = {
    str(endpoint["dataset"])
    for endpoint in TAIWAN_OFFICIAL_ENDPOINTS
    if endpoint.get("dataset") and str(endpoint.get("status") or "").startswith("implemented")
}


@dataclass(frozen=True)
class PersonaDataSourceRequirement:
    persona_id: str
    dataset: str
    market: str
    cadence: str
    source_class: str
    connector_candidates: tuple[str, ...] = field(default_factory=tuple)
    policy_gates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def idempotency_key(self) -> str:
        candidates = ",".join(self.connector_candidates)
        return f"{self.persona_id}:{self.market}:{self.dataset}:{self.cadence}:{self.source_class}:{candidates}"

    @property
    def is_live_binding(self) -> bool:
        return self.source_class in LIVE_SOURCE_CLASSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "dataset": self.dataset,
            "market": self.market,
            "cadence": self.cadence,
            "source_class": self.source_class,
            "connector_candidates": list(self.connector_candidates),
            "policy_gates": list(self.policy_gates),
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True)
class ProvisionedConnectorPlan:
    connector: SourceConnector
    fetch: Mapping[str, Any]
    provider_name: str


@dataclass(frozen=True)
class SourceProvisioningAction:
    persona_id: str
    dataset: str
    connector_id: str | None
    status: str
    connector_action: str
    schedule_action: str
    idempotency_key: str
    details: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "dataset": self.dataset,
            "connector_id": self.connector_id,
            "status": self.status,
            "connector_action": self.connector_action,
            "schedule_action": self.schedule_action,
            "idempotency_key": self.idempotency_key,
            "details": dict(self.details),
        }


@dataclass(frozen=True)
class SourceProvisioningResult:
    controller: str
    persona_id: str
    dry_run: bool
    actions: tuple[SourceProvisioningAction, ...]
    schema_version: str = DEFAULT_SCHEMA_VERSION

    @property
    def summary(self) -> dict[str, int]:
        counts = {
            "total": len(self.actions),
            "satisfied": 0,
            "mutated": 0,
            "skipped": 0,
            "conflicts": 0,
            "unsupported": 0,
        }
        for action in self.actions:
            if action.status == "satisfied":
                counts["satisfied"] += 1
            elif action.status == "mutated":
                counts["mutated"] += 1
            elif action.status == "skipped":
                counts["skipped"] += 1
            elif action.status == "conflict":
                counts["conflicts"] += 1
            elif action.status == "unsupported":
                counts["unsupported"] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "controller": self.controller,
            "persona_id": self.persona_id,
            "dry_run": self.dry_run,
            "summary": self.summary,
            "actions": [action.to_dict() for action in self.actions],
        }


ProviderFactory = Callable[[str], SourceConnectorProvider]


class SourceProvisioningReconciler:
    """Idempotent controller for Persona.required_data_sources."""

    def __init__(
        self,
        *,
        manager: IngestManager,
        connector_store: JsonlConfiguredConnectorStore,
        schedule_store: JsonlConnectorScheduleStore,
        controller_name: str = DEFAULT_CONTROLLER_NAME,
        provider_factories: Mapping[str, ProviderFactory] | None = None,
    ) -> None:
        self.manager = manager
        self.connector_store = connector_store
        self.schedule_store = schedule_store
        self.controller_name = controller_name
        self.provider_factories: dict[str, ProviderFactory] = {
            "tw-finmind-datasets": lambda connector_id: FinMindTaiwanDatasetAdapter(connector_id=connector_id),
            TW_OFFICIAL_CONNECTOR_ID: lambda connector_id: TaiwanOfficialMarketDatasetAdapter(connector_id=connector_id),
        }
        if provider_factories:
            self.provider_factories.update(provider_factories)

    def reconcile_persona(self, persona: Mapping[str, Any] | Any, *, dry_run: bool = False) -> SourceProvisioningResult:
        persona_id = _persona_id(persona)
        actions = tuple(self._reconcile_requirement(requirement, dry_run=dry_run) for requirement in _requirements(persona))
        return SourceProvisioningResult(
            controller=self.controller_name,
            persona_id=persona_id,
            dry_run=dry_run,
            actions=actions,
        )

    def reconcile_personas(
        self,
        personas: Iterable[Mapping[str, Any] | Any],
        *,
        dry_run: bool = False,
    ) -> tuple[SourceProvisioningResult, ...]:
        persona_items = tuple(personas)
        connector_bindings: dict[str, str] = {}
        for persona in persona_items:
            for requirement in _requirements(persona):
                if not requirement.is_live_binding:
                    continue
                connector_id = self._select_connector_id(requirement)
                if connector_id is None:
                    continue
                previous = connector_bindings.get(connector_id)
                if previous is not None and previous != requirement.idempotency_key:
                    raise SourceEvidenceError(
                        "singleton connector cannot prove multiple persona data requirements: "
                        f"{connector_id} ({previous}, {requirement.idempotency_key})"
                    )
                connector_bindings[connector_id] = requirement.idempotency_key
        return tuple(self.reconcile_persona(persona, dry_run=dry_run) for persona in persona_items)

    def _reconcile_requirement(
        self,
        requirement: PersonaDataSourceRequirement,
        *,
        dry_run: bool,
    ) -> SourceProvisioningAction:
        if not requirement.is_live_binding:
            return SourceProvisioningAction(
                persona_id=requirement.persona_id,
                dataset=requirement.dataset,
                connector_id=None,
                status="skipped",
                connector_action="skipped_seed_only",
                schedule_action="skipped_seed_only",
                idempotency_key=requirement.idempotency_key,
                details={"reason": "seed_only requirements are labels, not live source bindings"},
            )
        if requirement.source_class == "live_push" or requirement.cadence == "on_demand":
            return SourceProvisioningAction(
                persona_id=requirement.persona_id,
                dataset=requirement.dataset,
                connector_id=None,
                status="unsupported",
                connector_action="unsupported_supervised_mode",
                schedule_action="not_attempted",
                idempotency_key=requirement.idempotency_key,
                details={"reason": "default supervised source controller currently requires scheduled live_pull"},
            )

        connector_id = self._select_connector_id(requirement)
        if connector_id is None:
            return SourceProvisioningAction(
                persona_id=requirement.persona_id,
                dataset=requirement.dataset,
                connector_id=None,
                status="unsupported",
                connector_action="unsupported",
                schedule_action="not_attempted",
                idempotency_key=requirement.idempotency_key,
                details={"reason": "no connector candidate can currently satisfy this requirement"},
            )

        plan = self._build_plan(connector_id, requirement)
        if plan is None:
            existing = self.connector_store.get_config(connector_id)
            if existing is not None:
                gate_results = self._policy_gate_results(existing.connector, requirement)
                failed_gates = sorted(gate for gate, result in gate_results.items() if not result["passed"])
                if failed_gates:
                    return SourceProvisioningAction(
                        persona_id=requirement.persona_id,
                        dataset=requirement.dataset,
                        connector_id=connector_id,
                        status="conflict",
                        connector_action="policy_gate_failed",
                        schedule_action="not_attempted",
                        idempotency_key=requirement.idempotency_key,
                        details={"failed_policy_gates": failed_gates, "policy_gate_results": gate_results},
                    )
                self.manager.upsert_connector(existing.connector)
                schedule_action, schedule_details = self._ensure_schedule(
                    connector_id,
                    requirement=requirement,
                    dry_run=dry_run,
                )
                return self._action(
                    requirement,
                    connector_id=connector_id,
                    connector_action="verified_existing_custom_connector",
                    schedule_action=schedule_action,
                    details={"schedule": schedule_details, "provider": "existing_custom_connector"},
                )
            return SourceProvisioningAction(
                persona_id=requirement.persona_id,
                dataset=requirement.dataset,
                connector_id=connector_id,
                status="unsupported",
                connector_action="unsupported",
                schedule_action="not_attempted",
                idempotency_key=requirement.idempotency_key,
                details={"reason": "connector candidate has no registered config and no built-in provider factory"},
            )

        gate_results = self._policy_gate_results(plan.connector, requirement)
        failed_gates = sorted(gate for gate, result in gate_results.items() if not result["passed"])
        if failed_gates:
            return SourceProvisioningAction(
                persona_id=requirement.persona_id,
                dataset=requirement.dataset,
                connector_id=connector_id,
                status="conflict",
                connector_action="policy_gate_failed",
                schedule_action="not_attempted",
                idempotency_key=requirement.idempotency_key,
                details={"failed_policy_gates": failed_gates, "policy_gate_results": gate_results},
            )
        connector_action, connector_details = self._ensure_connector(plan, dry_run=dry_run)
        if connector_action == "conflict":
            return SourceProvisioningAction(
                persona_id=requirement.persona_id,
                dataset=requirement.dataset,
                connector_id=connector_id,
                status="conflict",
                connector_action=connector_action,
                schedule_action="not_attempted",
                idempotency_key=requirement.idempotency_key,
                details=connector_details,
            )

        schedule_action, schedule_details = self._ensure_schedule(
            connector_id,
            requirement=requirement,
            dry_run=dry_run,
        )
        return self._action(
            requirement,
            connector_id=connector_id,
            connector_action=connector_action,
            schedule_action=schedule_action,
            details={
                "connector": connector_details,
                "schedule": schedule_details,
                "provider": plan.provider_name,
                "policy_gate_results": gate_results,
            },
        )

    def _policy_gate_results(
        self,
        connector: SourceConnector,
        requirement: PersonaDataSourceRequirement,
    ) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for gate in requirement.policy_gates:
            if gate == "require_connector_approved":
                passed = connector.connector_id in self.provider_factories or bool(
                    connector.metadata.get("approved") or connector.metadata.get("approval_status") == "approved"
                )
                results[gate] = {"passed": passed, "authority": "provider_allowlist_or_connector_approval"}
            elif gate in {"require_schedule_active", "require_source_health_ok", "require_freshness_within_1d"}:
                results[gate] = {"passed": True, "authority": "terminal_actual_readback"}
            elif gate == "no_live_capital":
                results[gate] = {
                    "passed": connector.auth_type.value != "broker_ref",
                    "authority": "connector_auth_policy",
                }
            elif gate == "public-source-only":
                results[gate] = {
                    "passed": connector.auth_type.value == "none" and not connector.secret_ref_id,
                    "authority": "connector_auth_policy",
                }
        return results

    def _select_connector_id(self, requirement: PersonaDataSourceRequirement) -> str | None:
        candidates = list(requirement.connector_candidates) or self._default_candidates(requirement)
        for candidate in candidates:
            if self.connector_store.get_config(candidate) is not None:
                return candidate
            if candidate in self.provider_factories and self._provider_supports(candidate, requirement):
                return candidate
        return None

    def _default_candidates(self, requirement: PersonaDataSourceRequirement) -> list[str]:
        if requirement.market.upper() == "TW" and requirement.dataset in _OFFICIAL_TW_DATASETS:
            return [TW_OFFICIAL_CONNECTOR_ID, "tw-finmind-datasets"]
        if requirement.market.upper() == "TW" and requirement.dataset in _FINMIND_DATASET_BY_NORMALIZED:
            return ["tw-finmind-datasets"]
        return []

    def _provider_supports(self, connector_id: str, requirement: PersonaDataSourceRequirement) -> bool:
        if connector_id == "tw-finmind-datasets":
            return requirement.market.upper() == "TW" and requirement.dataset in _FINMIND_DATASET_BY_NORMALIZED
        if connector_id == TW_OFFICIAL_CONNECTOR_ID:
            return requirement.market.upper() == "TW" and requirement.dataset in _OFFICIAL_TW_DATASETS
        return True

    def _build_plan(
        self,
        connector_id: str,
        requirement: PersonaDataSourceRequirement,
    ) -> ProvisionedConnectorPlan | None:
        factory = self.provider_factories.get(connector_id)
        if factory is None:
            return None
        provider = factory(connector_id)
        connector = self._connector_for_requirement(provider.connector(), requirement=requirement)
        fetch = dict(provider.fetch_config())
        fetch = self._fetch_for_requirement(fetch, connector_id=connector_id, requirement=requirement)
        return ProvisionedConnectorPlan(
            connector=connector,
            fetch=fetch,
            provider_name=provider.__class__.__name__,
        )

    def _fetch_for_requirement(
        self,
        fetch: Mapping[str, Any],
        *,
        connector_id: str,
        requirement: PersonaDataSourceRequirement,
    ) -> dict[str, Any]:
        payload = dict(fetch)
        request = dict(payload.get("request") or {})
        if connector_id == TW_OFFICIAL_CONNECTOR_ID:
            request["dataset"] = requirement.dataset
        elif connector_id == "tw-finmind-datasets":
            request["dataset"] = _FINMIND_DATASET_BY_NORMALIZED.get(requirement.dataset, requirement.dataset)
        if request:
            payload["request"] = request
        payload["allow_empty"] = True
        payload["empty_reason"] = (
            f"source provisioning verified {requirement.dataset}; live payload is supplied by scheduler fanout "
            "or provider-specific crawl workers"
        )
        return payload

    def _connector_for_requirement(
        self,
        connector: SourceConnector,
        *,
        requirement: PersonaDataSourceRequirement,
    ) -> SourceConnector:
        """Attach controller ownership and desired-state provenance.

        The marker is deliberately connector-scoped.  It gives later ticks a
        safe ownership test for drift repair without allowing the reconciler
        to overwrite an operator-managed/custom connector merely because the
        connector id happens to match.
        """

        payload = connector.to_dict()
        metadata = dict(payload.get("metadata") or {})
        desired_state = {
            "persona_id": requirement.persona_id,
            "dataset": requirement.dataset,
            "market": requirement.market,
            "cadence": requirement.cadence,
            "source_class": requirement.source_class,
            "connector_candidates": list(requirement.connector_candidates),
            "policy_gates": list(requirement.policy_gates),
            "policy_gate_results": self._policy_gate_results(connector, requirement),
        }
        desired_json = json.dumps(
            desired_state,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        metadata[RECONCILIATION_METADATA_KEY] = {
            "managed_by": self.controller_name,
            "desired_state": desired_state,
            "desired_state_sha256": sha256(desired_json.encode("utf-8")).hexdigest(),
        }
        payload["metadata"] = metadata
        return SourceConnector.from_dict(payload)

    def _ensure_connector(self, plan: ProvisionedConnectorPlan, *, dry_run: bool) -> tuple[str, dict[str, Any]]:
        connector_id = plan.connector.connector_id
        desired_fetch = self.connector_store.normalize_fetch_config(plan.fetch)
        existing = self.connector_store.get_config(connector_id)
        if existing is None:
            if dry_run:
                return "would_create", {"connector_id": connector_id}
            stored = self.connector_store.upsert_config(plan.connector, plan.fetch)
            self.manager.upsert_connector(stored.connector)
            return "created", {"connector_id": connector_id}

        self.manager.upsert_connector(existing.connector)
        connector_matches = existing.connector.to_dict() == plan.connector.to_dict()
        fetch_matches = dict(existing.fetch) == desired_fetch
        if connector_matches and fetch_matches:
            return "verified", {"connector_id": connector_id}
        existing_owner = _reconciliation_owner(existing.connector)
        legacy_adoptable = existing_owner is None and _without_reconciliation(existing.connector) == _without_reconciliation(
            plan.connector
        )
        if existing_owner == self.controller_name or (legacy_adoptable and fetch_matches):
            if dry_run:
                return "would_repair", {
                    "connector_id": connector_id,
                    "reason": "controller_owned_drift" if existing_owner else "adopt_legacy_controller_config",
                }
            stored = self.connector_store.upsert_config(plan.connector, plan.fetch)
            self.manager.upsert_connector(stored.connector)
            return "repaired", {
                "connector_id": connector_id,
                "reason": "controller_owned_drift" if existing_owner else "adopted_legacy_controller_config",
            }
        return "conflict", {
            "connector_id": connector_id,
            "connector_matches": connector_matches,
            "fetch_matches": fetch_matches,
            "existing_owner": existing_owner,
        }

    def _ensure_schedule(
        self,
        connector_id: str,
        *,
        requirement: PersonaDataSourceRequirement,
        dry_run: bool,
    ) -> tuple[str, dict[str, Any]]:
        interval_seconds = CADENCE_INTERVAL_SECONDS.get(requirement.cadence)
        if interval_seconds is None:
            raise SourceEvidenceError(f"Unsupported persona data source cadence: {requirement.cadence}")
        enabled = interval_seconds > 0
        existing = self.schedule_store.get_schedule(connector_id)
        desired = {
            "connector_id": connector_id,
            "interval_seconds": interval_seconds,
            "enabled": enabled,
        }
        if (
            existing is not None
            and existing.interval_seconds == interval_seconds
            and existing.enabled == enabled
        ):
            return "verified", desired
        if dry_run:
            return ("would_repair" if existing is not None else "would_create"), desired
        self.schedule_store.upsert_schedule(
            connector_id,
            interval_seconds=interval_seconds,
            enabled=enabled,
        )
        return ("repaired" if existing is not None else "created"), desired

    def _action(
        self,
        requirement: PersonaDataSourceRequirement,
        *,
        connector_id: str,
        connector_action: str,
        schedule_action: str,
        details: Mapping[str, Any],
    ) -> SourceProvisioningAction:
        mutated_markers = {"created", "repaired", "would_create", "would_repair"}
        status = "mutated" if connector_action in mutated_markers or schedule_action in mutated_markers else "satisfied"
        return SourceProvisioningAction(
            persona_id=requirement.persona_id,
            dataset=requirement.dataset,
            connector_id=connector_id,
            status=status,
            connector_action=connector_action,
            schedule_action=schedule_action,
            idempotency_key=requirement.idempotency_key,
            details=details,
        )


def _reconciliation_owner(connector: SourceConnector) -> str | None:
    marker = connector.metadata.get(RECONCILIATION_METADATA_KEY)
    if not isinstance(marker, Mapping):
        return None
    owner = str(marker.get("managed_by") or "").strip()
    return owner or None


def _without_reconciliation(connector: SourceConnector) -> dict[str, Any]:
    payload = connector.to_dict()
    metadata = dict(payload.get("metadata") or {})
    metadata.pop(RECONCILIATION_METADATA_KEY, None)
    payload["metadata"] = metadata
    return payload


def _persona_id(persona: Mapping[str, Any] | Any) -> str:
    if isinstance(persona, Mapping):
        value = persona.get("persona_id")
    else:
        value = getattr(persona, "persona_id", None)
    persona_id = str(value or "").strip()
    if not persona_id:
        raise SourceEvidenceError("persona_id is required for source provisioning")
    return persona_id


def _requirements(persona: Mapping[str, Any] | Any) -> tuple[PersonaDataSourceRequirement, ...]:
    persona_id = _persona_id(persona)
    raw_requirements = persona.get("required_data_sources", []) if isinstance(persona, Mapping) else getattr(
        persona,
        "required_data_sources",
        [],
    )
    return tuple(_requirement_from_raw(persona_id, item) for item in raw_requirements)


def _requirement_from_raw(persona_id: str, raw: Mapping[str, Any] | _RequirementLike) -> PersonaDataSourceRequirement:
    def value(name: str, default: Any = "") -> Any:
        return raw.get(name, default) if isinstance(raw, Mapping) else getattr(raw, name, default)

    if isinstance(raw, Mapping):
        raw_payload = dict(raw)
    else:
        raw_payload = {
            name: value(name, [] if name in {"connector_candidates", "policy_gates"} else "")
            for name in ("dataset", "market", "cadence", "source_class", "connector_candidates", "policy_gates")
        }
    errors = sorted(REQUIRED_DATA_SOURCE_VALIDATOR.iter_errors(raw_payload), key=lambda item: list(item.path))
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.path) or "required_data_sources"
        raise SourceEvidenceError(f"invalid required data source at {location}: {first.message}")

    dataset = str(value("dataset")).strip()
    market = str(value("market")).strip().upper()
    cadence = str(value("cadence")).strip()
    source_class = str(value("source_class")).strip()
    if not dataset:
        raise SourceEvidenceError("required_data_sources.dataset is required")
    if not market:
        raise SourceEvidenceError("required_data_sources.market is required")
    connector_candidates = _strict_string_list(value("connector_candidates", []), "connector_candidates")
    policy_gates = _strict_string_list(value("policy_gates", []), "policy_gates")
    unknown_policy_gates = sorted(set(policy_gates) - SUPPORTED_POLICY_GATES)
    if unknown_policy_gates:
        raise SourceEvidenceError(
            "required_data_sources.policy_gates contains unsupported gate(s): "
            + ", ".join(unknown_policy_gates)
        )
    return PersonaDataSourceRequirement(
        persona_id=persona_id,
        dataset=dataset,
        market=market,
        cadence=cadence,
        source_class=source_class,
        connector_candidates=tuple(connector_candidates),
        policy_gates=tuple(policy_gates),
    )


def _strict_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise SourceEvidenceError(f"required_data_sources.{field_name} must be an array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SourceEvidenceError(f"required_data_sources.{field_name} must contain non-empty strings")
        normalized.append(item.strip())
    if len(set(normalized)) != len(normalized):
        raise SourceEvidenceError(f"required_data_sources.{field_name} must not contain duplicates")
    return normalized
