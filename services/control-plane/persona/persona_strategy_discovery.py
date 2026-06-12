"""Deterministic Persona strategy discovery matching.

This module is intentionally a pure scoring library.  It reads Persona
settings and candidate strategy objects, then returns explainable research-only
matches.  It does not create StrategySpec records, launch experiments, deploy
strategies, or open execution routes.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

try:  # Imported directly by persona-plane tests and by the BFF via sys.path.
    from persona_registry import Persona
except Exception:  # pragma: no cover - defensive for tooling imports.
    Persona = Any  # type: ignore

try:
    from services.source_ingestion.strategy_seed_builder import (
        StrategySpecSeed,
        StrategySpecSeedStatus,
    )
except Exception:  # pragma: no cover - defensive for docs/tooling imports.
    StrategySpecSeed = Any  # type: ignore
    StrategySpecSeedStatus = Any  # type: ignore


_SCORER_VERSION = "deterministic-v1"
_SCHEMA_VERSION = "persona_strategy_match.v1"

_RESEARCH_CAPABLE_LIFECYCLES = frozenset(
    {
        "draft",
        "research_only",
        "consultable",
        "paper_owner",
        "live_owner",
        "active",
        "ready",
        "running",
        "deployed",
    }
)
_NON_RESEARCH_STATUSES = frozenset({"suspended", "archived", "retired"})
_BLOCKING_SOURCE_STATUSES = frozenset({"disabled", "retired", "rejected", "blocked"})
_BLOCKING_SEED_STATUSES = frozenset({"rejected"})
_RESEARCH_ALLOWED_USE_TOKENS = frozenset(
    {
        "research",
        "research_discovery",
        "strategy_seed",
        "strategy_seed_generation",
        "research_ticket_creation",
        "rapid_eval_request",
        "search_index",
    }
)
_CONTRACT_ALLOWED_USE = frozenset(
    {
        "research_discovery",
        "strategy_seed_generation",
        "research_ticket_creation",
        "rapid_eval_request",
    }
)
_FORBIDDEN_DISCOVERY_TOKENS = frozenset(
    {
        "broker",
        "live",
        "order_router",
        "order_routing",
        "runtime_direct",
        "lean_direct",
        "live_trading",
    }
)
_DATA_GENERIC_TOKENS = frozenset(
    {
        "data",
        "dataset",
        "datasets",
        "field",
        "fields",
        "point",
        "time",
        "source",
        "governed",
        "reference",
        "references",
    }
)

_ALIASES = {
    "equities": "equity",
    "stocks": "equity",
    "stock": "equity",
    "shares": "equity",
    "taiwan": "twse",
    "tw": "twse",
    "tpex": "twse",
    "nyse": "us",
    "nasdaq": "us",
    "usa": "us",
    "united_states": "us",
    "mean_reversion": "mean_reversion",
    "meanreversion": "mean_reversion",
    "reversion": "mean_reversion",
    "mom": "momentum",
    "intraday": "intraday",
    "day": "intraday",
    "daily": "swing",
    "overnight": "swing",
    "medium": "medium",
    "moderate": "medium",
}


@dataclass(frozen=True)
class PersonaStrategyProfile:
    persona_id: str
    mandate_terms: tuple[str, ...]
    strategy_families: tuple[str, ...]
    preferred_markets: tuple[str, ...]
    asset_classes: tuple[str, ...]
    holding_periods: tuple[str, ...]
    risk_constraints: Mapping[str, Any]
    backend_preferences: tuple[str, ...]
    allowed_source_scopes: tuple[str, ...]
    blocked_source_scopes: tuple[str, ...]
    blocked_backends: tuple[str, ...]
    data_availability_scope: tuple[str, ...]
    existing_strategy_refs: tuple[str, ...]
    lifecycle_state: str
    status: str = "active"
    route_policy_ref: str | None = None
    allows_data_backfill: bool = False
    blockers: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "mandate_terms": list(self.mandate_terms),
            "strategy_families": list(self.strategy_families),
            "preferred_markets": list(self.preferred_markets),
            "asset_classes": list(self.asset_classes),
            "holding_periods": list(self.holding_periods),
            "risk_constraints": dict(self.risk_constraints),
            "backend_preferences": list(self.backend_preferences),
            "allowed_source_scopes": list(self.allowed_source_scopes),
            "blocked_source_scopes": list(self.blocked_source_scopes),
            "blocked_backends": list(self.blocked_backends),
            "data_availability_scope": list(self.data_availability_scope),
            "existing_strategy_refs": list(self.existing_strategy_refs),
            "lifecycle_state": self.lifecycle_state,
            "status": self.status,
            "route_policy_ref": self.route_policy_ref,
            "allows_data_backfill": self.allows_data_backfill,
            "blockers": list(self.blockers),
        }


@dataclass(frozen=True)
class StrategyMatchScore:
    strategy_family_match: float = 0.0
    market_asset_match: float = 0.0
    holding_period_match: float = 0.0
    required_data_available: float = 0.0
    evidence_quality: float = 0.0
    backend_compatibility: float = 0.0
    risk_profile_match: float = 0.0
    novelty_or_diversification: float = 0.0

    @property
    def total(self) -> float:
        return _round_score(
            self.strategy_family_match
            + self.market_asset_match
            + self.holding_period_match
            + self.required_data_available
            + self.evidence_quality
            + self.backend_compatibility
            + self.risk_profile_match
            + self.novelty_or_diversification
        )

    def to_dict(self) -> dict[str, float]:
        payload = asdict(self)
        payload["total"] = self.total
        return payload


@dataclass(frozen=True)
class MatchedField:
    field: str
    persona_value: str
    strategy_value: str
    contribution: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissingData:
    dataset: str
    severity: str
    remediation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RecommendedAction:
    type: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class StrategyCandidate:
    matched_object_type: str
    matched_object_id: str
    title: str
    strategy_families: tuple[str, ...]
    markets: tuple[str, ...]
    asset_classes: tuple[str, ...]
    holding_periods: tuple[str, ...]
    required_data: tuple[str, ...]
    backend_preferences: tuple[str, ...]
    risk_terms: tuple[str, ...]
    evidence_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    allowed_use: tuple[str, ...]
    entitlement_tags: tuple[str, ...]
    license_scope: str | None = None
    source_status: str | None = None
    status: str | None = None
    confidence: float = 0.0
    execution_route: str | None = None
    execution_mode_hint: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PersonaStrategyMatch:
    match_id: str
    persona_id: str
    matched_object_type: str
    matched_object_id: str
    score_breakdown: StrategyMatchScore
    rank: int
    matched_fields: tuple[MatchedField, ...]
    missing_data: tuple[MissingData, ...]
    allowed_use: tuple[str, ...]
    entitlement_tags: tuple[str, ...]
    recommended_action: RecommendedAction
    evidence_refs: tuple[str, ...]
    source_refs: tuple[str, ...]
    lineage: Mapping[str, Any]
    status: str
    created_at: str
    blockers: tuple[str, ...] = field(default_factory=tuple)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def score(self) -> float:
        if self.blockers:
            return _round_score(min(self.score_breakdown.total, 49.0))
        return self.score_breakdown.total

    def with_rank(self, rank: int) -> "PersonaStrategyMatch":
        return PersonaStrategyMatch(
            match_id=self.match_id,
            persona_id=self.persona_id,
            matched_object_type=self.matched_object_type,
            matched_object_id=self.matched_object_id,
            score_breakdown=self.score_breakdown,
            rank=rank,
            matched_fields=self.matched_fields,
            missing_data=self.missing_data,
            allowed_use=self.allowed_use,
            entitlement_tags=self.entitlement_tags,
            recommended_action=self.recommended_action,
            evidence_refs=self.evidence_refs,
            source_refs=self.source_refs,
            lineage=self.lineage,
            status=self.status,
            created_at=self.created_at,
            blockers=self.blockers,
            metadata=self.metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        metadata = {
            "research_only": True,
            "execution_route": "none",
            "blockers": list(self.blockers),
            "scorer_version": _SCORER_VERSION,
            **dict(self.metadata),
        }
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "match_id": self.match_id,
            "persona_id": self.persona_id,
            "matched_object_type": self.matched_object_type,
            "matched_object_id": self.matched_object_id,
            "score": self.score,
            "rank": self.rank,
            "score_breakdown": self.score_breakdown.to_dict(),
            "matched_fields": [item.to_dict() for item in self.matched_fields],
            "missing_data": [item.to_dict() for item in self.missing_data],
            "allowed_use": list(self.allowed_use),
            "entitlement_tags": list(self.entitlement_tags),
            "recommended_action": self.recommended_action.to_dict(),
            "evidence_refs": list(self.evidence_refs),
            "lineage": dict(self.lineage),
            "status": self.status,
            "created_at": self.created_at,
            "metadata": metadata,
        }
        if self.source_refs:
            payload["source_refs"] = list(self.source_refs)
        return payload


def extract_persona_strategy_profile(
    persona: Persona | Mapping[str, Any],
    *,
    route_policy: Mapping[str, Any] | None = None,
    capability_snapshot: Mapping[str, Any] | None = None,
) -> PersonaStrategyProfile:
    """Extract a deterministic Strategy Discovery profile from a persona."""

    raw = _to_mapping(persona)
    metadata = _mapping(raw.get("metadata"))
    route = _mapping(route_policy)
    capability = _mapping(capability_snapshot)
    persona_id = _first_text(raw, "persona_id", "id")
    lifecycle_state = _first_text(raw, "lifecycle_state", "state", default="draft").lower()
    status = _first_text(raw, "status", default="active").lower()

    mandate_terms = _stable_tokens(
        [
            raw.get("mandate"),
            metadata.get("mandate"),
            metadata.get("description"),
            metadata.get("memo"),
            metadata.get("current_work"),
        ]
    )
    strategy_families = _stable_tokens(
        [
            raw.get("strategy_family"),
            metadata.get("strategy_family"),
            metadata.get("strategy_families"),
            metadata.get("archetype"),
        ]
    )
    preferred_markets = _stable_tokens(
        [
            raw.get("market_scope"),
            metadata.get("market_scope"),
            metadata.get("preferred_markets"),
            metadata.get("markets"),
            metadata.get("allowed_markets"),
            _route_values(route, "preferred_markets", "allowed_markets", "market_scope", "markets"),
        ]
    )
    asset_classes = _stable_tokens(
        [
            raw.get("asset_classes"),
            raw.get("asset_class"),
            metadata.get("asset_classes"),
            metadata.get("asset_class"),
            metadata.get("assets"),
            _route_values(route, "asset_classes", "asset_class", "assets"),
        ]
    )
    holding_periods = _stable_tokens(
        [
            raw.get("holding_period"),
            raw.get("holding_periods"),
            metadata.get("holding_period"),
            metadata.get("holding_periods"),
            metadata.get("horizon"),
            metadata.get("rebalance_cadence"),
        ]
    )
    backend_preferences = _stable_tokens(
        [
            raw.get("backend_preferences"),
            metadata.get("backend_preferences"),
            metadata.get("allowed_research_backends"),
            metadata.get("research_backends"),
            _route_values(route, "backend_preferences", "allowed_research_backends", "allowed_backends"),
            capability.get("effective_tools"),
            capability.get("effective_workflows"),
        ]
    )
    allowed_source_scopes = _stable_tokens(
        [
            raw.get("allowed_source_scopes"),
            raw.get("source_scopes"),
            metadata.get("allowed_source_scopes"),
            metadata.get("source_scopes"),
            metadata.get("license_scopes"),
            _route_values(route, "allowed_source_scopes", "source_scopes", "license_scopes"),
        ]
    )
    blocked_source_scopes = _stable_tokens(
        [
            raw.get("blocked_source_scopes"),
            metadata.get("blocked_source_scopes"),
            _route_values(route, "blocked_source_scopes", "blocked_license_scopes"),
        ]
    )
    blocked_backends = _stable_tokens(
        [
            raw.get("blocked_backends"),
            metadata.get("blocked_backends"),
            _route_values(route, "blocked_backends", "blocked_research_backends"),
        ]
    )
    data_availability_scope = _stable_tokens(
        [
            raw.get("data_availability_scope"),
            raw.get("available_data"),
            raw.get("data_sources"),
            metadata.get("data_availability_scope"),
            metadata.get("available_data"),
            metadata.get("data_sources"),
            metadata.get("data_source_refs"),
            _route_values(route, "data_availability_scope", "available_data", "data_sources"),
        ]
    )
    existing_strategy_refs = _unique_strings(
        _flatten_strings(
            [
                raw.get("existing_strategy_refs"),
                raw.get("strategy_refs"),
                metadata.get("existing_strategy_refs"),
                metadata.get("strategy_refs"),
                metadata.get("routed_strategy_refs"),
                metadata.get("active_strategy_refs"),
            ]
        )
    )
    risk_constraints = {
        "risk_level": _first_text(
            metadata,
            "risk_level",
            "risk_profile",
            default=_first_text(raw, "risk_level", "risk_profile", default=""),
        ),
        "risk_constraints": list(
            _unique_strings(
                _flatten_strings(
                    [
                        raw.get("risk_constraints"),
                        metadata.get("risk_constraints"),
                        metadata.get("risk_flags"),
                        metadata.get("risk_notes"),
                    ]
                )
            )
        ),
    }

    blockers: list[str] = []
    if lifecycle_state not in _RESEARCH_CAPABLE_LIFECYCLES:
        blockers.append("persona_lifecycle_not_research_capable")
    if status in _NON_RESEARCH_STATUSES:
        blockers.append(f"persona_status_{status}")

    return PersonaStrategyProfile(
        persona_id=persona_id,
        mandate_terms=mandate_terms,
        strategy_families=strategy_families,
        preferred_markets=preferred_markets,
        asset_classes=asset_classes,
        holding_periods=holding_periods,
        risk_constraints=risk_constraints,
        backend_preferences=backend_preferences,
        allowed_source_scopes=allowed_source_scopes,
        blocked_source_scopes=blocked_source_scopes,
        blocked_backends=blocked_backends,
        data_availability_scope=data_availability_scope,
        existing_strategy_refs=existing_strategy_refs,
        lifecycle_state=lifecycle_state,
        status=status,
        route_policy_ref=_first_text(raw, "route_policy_id", default=_first_text(route, "policy_id", "id", "version")),
        allows_data_backfill=_truthy(
            metadata.get("allow_data_backfill")
            or metadata.get("backfill_allowed")
            or route.get("allow_data_backfill")
            or route.get("backfill_allowed")
        ),
        blockers=tuple(blockers),
    )


class PersonaStrategyDiscoveryService:
    """Deterministic scorer for Persona to strategy seed/spec matching."""

    def match_candidates(
        self,
        profile: PersonaStrategyProfile,
        *,
        strategy_seeds: Sequence[StrategySpecSeed | Mapping[str, Any]] = (),
        strategy_specs: Sequence[Mapping[str, Any]] = (),
        created_at: str | None = None,
        discovery_session_id: str | None = None,
        include_blocked: bool = True,
    ) -> list[PersonaStrategyMatch]:
        timestamp = created_at or _utc_now()
        candidates = [
            _candidate_from_seed(seed)
            for seed in strategy_seeds
        ] + [
            _candidate_from_strategy_spec(spec)
            for spec in strategy_specs
        ]
        matches = [
            self._score_candidate(
                profile,
                candidate,
                created_at=timestamp,
                discovery_session_id=discovery_session_id,
            )
            for candidate in candidates
        ]
        if not include_blocked:
            matches = [match for match in matches if not match.blockers]
        matches.sort(
            key=lambda match: (
                bool(match.blockers),
                -match.score,
                match.matched_object_type,
                match.matched_object_id,
            )
        )
        return [match.with_rank(index + 1) for index, match in enumerate(matches)]

    def _score_candidate(
        self,
        profile: PersonaStrategyProfile,
        candidate: StrategyCandidate,
        *,
        created_at: str,
        discovery_session_id: str | None,
    ) -> PersonaStrategyMatch:
        matched_fields: list[MatchedField] = []
        missing_data: list[MissingData] = []
        blockers = list(profile.blockers)

        family_score = _score_set_overlap(
            profile.strategy_families,
            candidate.strategy_families,
            max_score=20,
            neutral_score=6,
        )
        if family_score == 0 and profile.strategy_families:
            family_score = _score_term_overlap(
                profile.strategy_families,
                [candidate.title, *candidate.evidence_refs],
                max_score=12,
            )
        _append_match(
            matched_fields,
            "strategy_family",
            profile.strategy_families,
            candidate.strategy_families,
            family_score,
        )

        market_score = _score_set_overlap(
            profile.preferred_markets,
            candidate.markets,
            max_score=8,
            neutral_score=4,
        )
        asset_score = _score_set_overlap(
            profile.asset_classes,
            candidate.asset_classes,
            max_score=7,
            neutral_score=3.5,
        )
        market_asset_score = _round_score(market_score + asset_score)
        _append_match(
            matched_fields,
            "market_asset",
            [*profile.preferred_markets, *profile.asset_classes],
            [*candidate.markets, *candidate.asset_classes],
            market_asset_score,
        )

        holding_score = _score_set_overlap(
            profile.holding_periods,
            candidate.holding_periods,
            max_score=10,
            neutral_score=4,
        )
        _append_match(
            matched_fields,
            "holding_period",
            profile.holding_periods,
            candidate.holding_periods,
            holding_score,
        )

        data_score, data_missing, data_blockers = _score_data_availability(profile, candidate)
        missing_data.extend(data_missing)
        blockers.extend(data_blockers)
        _append_match(
            matched_fields,
            "required_data_available",
            profile.data_availability_scope,
            candidate.required_data,
            data_score,
        )

        evidence_score = _score_evidence_quality(candidate)
        _append_match(
            matched_fields,
            "evidence_quality",
            "profile accepts governed evidence",
            candidate.evidence_refs,
            evidence_score,
        )

        backend_score, backend_blockers = _score_backend_compatibility(profile, candidate)
        blockers.extend(backend_blockers)
        _append_match(
            matched_fields,
            "backend_compatibility",
            profile.backend_preferences,
            candidate.backend_preferences,
            backend_score,
        )

        risk_score = _score_risk(profile, candidate)
        _append_match(
            matched_fields,
            "risk_profile",
            _risk_values(profile),
            candidate.risk_terms,
            risk_score,
        )

        novelty_score = 1.0 if candidate.matched_object_id in profile.existing_strategy_refs else 5.0
        _append_match(
            matched_fields,
            "novelty_or_diversification",
            profile.existing_strategy_refs,
            candidate.matched_object_id,
            novelty_score,
        )

        blockers.extend(_candidate_hard_blockers(profile, candidate))
        blockers = list(_unique_strings(blockers))

        score_breakdown = StrategyMatchScore(
            strategy_family_match=family_score,
            market_asset_match=market_asset_score,
            holding_period_match=holding_score,
            required_data_available=data_score,
            evidence_quality=evidence_score,
            backend_compatibility=backend_score,
            risk_profile_match=risk_score,
            novelty_or_diversification=novelty_score,
        )
        recommended_action = _recommended_action(candidate, score_breakdown, blockers)
        allowed_use = _contract_allowed_use(candidate, recommended_action, blockers)
        status = _match_status(recommended_action, blockers)
        evidence_refs = _unique_strings(candidate.evidence_refs)
        source_refs = _unique_strings(candidate.source_refs)
        match_id = _stable_match_id(
            profile.persona_id,
            candidate.matched_object_type,
            candidate.matched_object_id,
        )
        lineage = {
            "discovery_session_id": discovery_session_id,
            "persona_profile_ref": f"persona:{profile.persona_id}:strategy-profile:{_SCORER_VERSION}",
            "matched_source_refs": list(source_refs),
            "evidence_refs": list(evidence_refs),
        }
        if candidate.matched_object_type == "strategy_spec_seed":
            lineage["strategy_seed_refs"] = [candidate.matched_object_id]
        if candidate.matched_object_type == "strategy_spec":
            lineage["strategy_spec_refs"] = [candidate.matched_object_id]

        return PersonaStrategyMatch(
            match_id=match_id,
            persona_id=profile.persona_id,
            matched_object_type=candidate.matched_object_type,
            matched_object_id=candidate.matched_object_id,
            score_breakdown=score_breakdown,
            rank=0,
            matched_fields=tuple(matched_fields),
            missing_data=tuple(missing_data),
            allowed_use=tuple(allowed_use),
            entitlement_tags=_unique_strings(candidate.entitlement_tags),
            recommended_action=recommended_action,
            evidence_refs=tuple(evidence_refs),
            source_refs=tuple(source_refs),
            lineage=lineage,
            status=status,
            created_at=created_at,
            blockers=tuple(blockers),
            metadata={
                "candidate_status": candidate.status,
                "candidate_source_status": candidate.source_status,
                "candidate_license_scope": candidate.license_scope,
                "candidate_execution_mode_hint": candidate.execution_mode_hint,
                "negative_memory_match": candidate.metadata.get("negative_memory_match"),
            },
        )


def match_persona_strategies(
    persona: Persona | Mapping[str, Any],
    *,
    route_policy: Mapping[str, Any] | None = None,
    capability_snapshot: Mapping[str, Any] | None = None,
    strategy_seeds: Sequence[StrategySpecSeed | Mapping[str, Any]] = (),
    strategy_specs: Sequence[Mapping[str, Any]] = (),
    created_at: str | None = None,
    discovery_session_id: str | None = None,
) -> list[PersonaStrategyMatch]:
    profile = extract_persona_strategy_profile(
        persona,
        route_policy=route_policy,
        capability_snapshot=capability_snapshot,
    )
    return PersonaStrategyDiscoveryService().match_candidates(
        profile,
        strategy_seeds=strategy_seeds,
        strategy_specs=strategy_specs,
        created_at=created_at,
        discovery_session_id=discovery_session_id,
    )


def _candidate_from_seed(seed: StrategySpecSeed | Mapping[str, Any]) -> StrategyCandidate:
    seed_obj = seed if hasattr(seed, "to_dict") else None
    raw = seed_obj.to_dict() if seed_obj is not None else dict(seed)  # type: ignore[arg-type]
    metadata = dict(_mapping(raw.get("metadata")))
    negative_memory_match = _mapping(raw.get("negative_memory_match") or metadata.get("negative_memory_match"))
    if negative_memory_match:
        metadata["negative_memory_match"] = dict(negative_memory_match)
    lineage = _mapping(raw.get("lineage"))
    source_ids = _unique_strings(_flatten_strings([raw.get("source_ids"), raw.get("source_id")]))
    allowed_use = _unique_strings(
        _flatten_strings(
            [
                raw.get("allowed_use"),
                metadata.get("allowed_use"),
                metadata.get("access_scope"),
            ]
        )
    )
    family = _stable_tokens(
        [
            metadata.get("strategy_family"),
            metadata.get("strategy_families"),
            metadata.get("family"),
            raw.get("feature_hints"),
            raw.get("hypothesis"),
        ]
    )
    return StrategyCandidate(
        matched_object_type="strategy_spec_seed",
        matched_object_id=str(raw.get("seed_id") or ""),
        title=str(raw.get("hypothesis") or raw.get("seed_id") or ""),
        strategy_families=family,
        markets=_stable_tokens([raw.get("market_scope"), metadata.get("markets"), metadata.get("market_scope")]),
        asset_classes=_stable_tokens([raw.get("asset_class"), metadata.get("asset_classes"), metadata.get("asset_class")]),
        holding_periods=_stable_tokens([raw.get("holding_period"), metadata.get("holding_period")]),
        required_data=_unique_strings(_flatten_strings(raw.get("required_data"))),
        backend_preferences=_stable_tokens([raw.get("backend_hint"), metadata.get("backend_hint"), metadata.get("backend")]),
        risk_terms=_stable_tokens([raw.get("risk_notes"), metadata.get("risk_profile"), metadata.get("risk_level")]),
        evidence_refs=_unique_strings(
            _flatten_strings(
                [
                    raw.get("evidence_bundle_id"),
                    raw.get("evidence_item_ids"),
                    raw.get("citation_refs"),
                    raw.get("trace_refs"),
                ]
            )
        ),
        source_refs=source_ids,
        allowed_use=allowed_use,
        entitlement_tags=_unique_strings(_flatten_strings([metadata.get("entitlement_tags"), raw.get("entitlement_tags")])),
        license_scope=_optional_text(raw.get("license_scope") or metadata.get("source_license_scope") or metadata.get("license_scope")),
        source_status=_optional_text(metadata.get("source_status") or metadata.get("source_lifecycle_state")),
        status=_optional_text(raw.get("status")),
        confidence=float(raw.get("confidence") or 0.0),
        execution_route=_optional_text(metadata.get("execution_route") or lineage.get("execution_route")),
        execution_mode_hint=_optional_text(metadata.get("execution_mode_hint")),
        metadata=metadata,
    )


def _candidate_from_strategy_spec(spec: Mapping[str, Any]) -> StrategyCandidate:
    raw = dict(spec)
    metadata = _mapping(raw.get("metadata"))
    market_scope = _mapping(raw.get("market_scope"))
    execution_profile = _mapping(raw.get("execution_profile"))
    governance = _mapping(raw.get("governance"))
    provenance = _mapping(raw.get("provenance"))
    citation_bundle = _mapping(raw.get("citation_bundle"))
    evidence_refs = _unique_strings(
        _flatten_strings(
            [
                _evidence_ref_ids(raw.get("evidence_refs")),
                _evidence_ref_ids(citation_bundle.get("evidence_refs")),
                raw.get("derived_from_source_refs"),
            ]
        )
    )
    source_refs = _unique_strings(
        _flatten_strings(
            [
                provenance.get("source_refs"),
                raw.get("derived_from_source_refs"),
                raw.get("source_refs"),
            ]
        )
    )
    lifecycle = str(raw.get("lifecycle_state") or raw.get("status") or "").lower()
    confidence = {
        "approved": 0.9,
        "candidate": 0.7,
        "review": 0.65,
        "draft": 0.45,
        "rejected": 0.0,
    }.get(lifecycle, 0.55)
    allowed_use = _unique_strings(
        _flatten_strings(
            [
                metadata.get("allowed_use"),
                raw.get("allowed_use"),
                ["research_discovery", "rapid_eval_request"],
            ]
        )
    )
    return StrategyCandidate(
        matched_object_type="strategy_spec",
        matched_object_id=str(raw.get("strategy_id") or raw.get("id") or raw.get("spec_version_id") or ""),
        title=str(raw.get("title") or raw.get("name") or raw.get("strategy_id") or ""),
        strategy_families=_stable_tokens(
            [
                metadata.get("strategy_family"),
                metadata.get("strategy_families"),
                raw.get("strategy_family"),
                raw.get("source_kind"),
                raw.get("title"),
                raw.get("hypothesis"),
            ]
        ),
        markets=_stable_tokens([market_scope.get("venues"), market_scope.get("symbols"), metadata.get("markets")]),
        asset_classes=_stable_tokens([market_scope.get("asset_classes"), metadata.get("asset_classes")]),
        holding_periods=_stable_tokens(
            [
                execution_profile.get("rebalance_cadence"),
                metadata.get("holding_period"),
                metadata.get("holding_periods"),
            ]
        ),
        required_data=_unique_strings(
            _flatten_strings(
                [
                    [item.get("ref") for item in raw.get("data_dependencies") or [] if isinstance(item, Mapping)],
                    metadata.get("required_data"),
                ]
            )
        ),
        backend_preferences=_stable_tokens(
            [
                metadata.get("backend_hint"),
                metadata.get("research_backend"),
                execution_profile.get("backend_hint"),
            ]
        ),
        risk_terms=_stable_tokens([governance.get("risk_profile"), governance.get("risk_level"), metadata.get("risk_profile")]),
        evidence_refs=evidence_refs,
        source_refs=source_refs,
        allowed_use=allowed_use,
        entitlement_tags=_unique_strings(_flatten_strings(metadata.get("entitlement_tags"))),
        license_scope=_optional_text(metadata.get("license_scope")),
        source_status=_optional_text(metadata.get("source_status")),
        status=lifecycle,
        confidence=confidence,
        execution_route=_optional_text(metadata.get("execution_route")),
        execution_mode_hint=_optional_text(execution_profile.get("execution_mode_hint")),
        metadata=metadata,
    )


def _candidate_hard_blockers(
    profile: PersonaStrategyProfile,
    candidate: StrategyCandidate,
) -> list[str]:
    blockers: list[str] = []
    if candidate.matched_object_type == "strategy_spec_seed" and str(candidate.status or "").lower() in _BLOCKING_SEED_STATUSES:
        blockers.append("seed_status_rejected")
    if str(candidate.status or "").lower() in {"rejected", "unavailable"}:
        blockers.append(f"{candidate.matched_object_type}_status_{candidate.status}")
    source_status = str(candidate.source_status or "").lower()
    if source_status in _BLOCKING_SOURCE_STATUSES or _truthy(candidate.metadata.get("source_disabled")):
        blockers.append(f"source_status_{source_status or 'disabled'}")
    allowed = {_normalize_token(value) for value in candidate.allowed_use if value}
    if allowed and not allowed.intersection(_RESEARCH_ALLOWED_USE_TOKENS):
        blockers.append("license_does_not_allow_research_use")
    license_scope = _normalize_token(candidate.license_scope)
    if license_scope in {"disabled", "rejected", "denied", "forbidden"}:
        blockers.append(f"license_scope_{license_scope}")
    if profile.allowed_source_scopes and license_scope and license_scope not in profile.allowed_source_scopes:
        blockers.append("route_policy_blocks_source_scope")
    if profile.blocked_source_scopes and license_scope and license_scope in profile.blocked_source_scopes:
        blockers.append("route_policy_blocks_source_scope")
    execution_route = _normalize_token(candidate.execution_route)
    if execution_route and execution_route not in {"none", "research", "research_only"}:
        blockers.append("strategy_requires_execution_route_during_discovery")
    mode_hint = _normalize_token(candidate.execution_mode_hint)
    if mode_hint in {"live", "canary"} and _truthy(candidate.metadata.get("discovery_requires_execution_access")):
        blockers.append("strategy_requires_execution_route_during_discovery")
    backend_text = " ".join(candidate.backend_preferences).lower()
    if any(token in backend_text for token in _FORBIDDEN_DISCOVERY_TOKENS):
        blockers.append("strategy_requires_execution_route_during_discovery")
    negative_memory_match = _mapping(candidate.metadata.get("negative_memory_match"))
    if str(negative_memory_match.get("warning_level") or "").lower() == "blocking":
        blockers.append("negative_memory_blocking_match")
    return blockers


def _score_data_availability(
    profile: PersonaStrategyProfile,
    candidate: StrategyCandidate,
) -> tuple[float, list[MissingData], list[str]]:
    required = _unique_strings(candidate.required_data)
    if not required:
        return 15.0, [], []
    if not profile.data_availability_scope:
        return (
            5.0,
            [
                MissingData(
                    dataset=dataset,
                    severity="warning",
                    remediation="Declare persona data availability scope before research ticket execution.",
                )
                for dataset in required
            ],
            [],
        )

    covered = [dataset for dataset in required if _data_requirement_is_available(dataset, profile.data_availability_scope)]
    missing = [dataset for dataset in required if dataset not in covered]
    if not missing:
        return 15.0, [], []

    score = _round_score(15.0 * (len(covered) / len(required)))
    has_backfill_path = profile.allows_data_backfill or _truthy(candidate.metadata.get("data_backfill_available"))
    severity = "warning" if has_backfill_path else "blocker"
    remediation = (
        "Request governed data backfill before rapid evaluation."
        if has_backfill_path
        else "Add an approved data source or backfill path before using this match."
    )
    blockers = [] if has_backfill_path else ["required_data_unavailable"]
    return (
        score,
        [MissingData(dataset=dataset, severity=severity, remediation=remediation) for dataset in missing],
        blockers,
    )


def _score_backend_compatibility(
    profile: PersonaStrategyProfile,
    candidate: StrategyCandidate,
) -> tuple[float, list[str]]:
    blockers: list[str] = []
    candidate_backends = candidate.backend_preferences
    if not candidate_backends:
        return 5.0, blockers
    blocked = set(profile.blocked_backends).intersection(candidate_backends)
    if blocked:
        return 0.0, ["route_policy_blocks_required_backend"]
    if not profile.backend_preferences:
        return 6.0, blockers
    overlap = set(profile.backend_preferences).intersection(candidate_backends)
    if overlap:
        return 10.0, blockers
    return 0.0, ["route_policy_blocks_required_backend"]


def _score_evidence_quality(candidate: StrategyCandidate) -> float:
    base = max(0.0, min(1.0, candidate.confidence)) * 10.0
    evidence_bonus = min(5.0, len(candidate.evidence_refs) * 1.5)
    return _round_score(base + evidence_bonus)


def _score_risk(profile: PersonaStrategyProfile, candidate: StrategyCandidate) -> float:
    persona_terms = _risk_values(profile)
    if not persona_terms or not candidate.risk_terms:
        return 5.0
    if set(persona_terms).intersection(candidate.risk_terms):
        return 10.0
    persona_level = _normalize_token(profile.risk_constraints.get("risk_level"))
    candidate_level = next(iter(candidate.risk_terms), "")
    if persona_level == "low" and candidate_level in {"high", "critical"}:
        return 2.0
    if persona_level == "high" and candidate_level == "low":
        return 6.0
    return 5.0


def _recommended_action(
    candidate: StrategyCandidate,
    score: StrategyMatchScore,
    blockers: Sequence[str],
) -> RecommendedAction:
    blocker_set = set(blockers)
    if blocker_set == {"required_data_unavailable"} or (
        "required_data_unavailable" in blocker_set and len(blocker_set) == 1
    ):
        return RecommendedAction(
            type="request_data_backfill",
            reason="Candidate matches the persona but required data is unavailable without a governed backfill path.",
        )
    if blockers:
        return RecommendedAction(
            type="ignore",
            reason=f"Hard blockers prevent research use: {', '.join(blockers)}.",
        )
    total = score.total
    if candidate.matched_object_type == "strategy_spec_seed" and total >= 70:
        return RecommendedAction(
            type="promote_seed_candidate",
            reason="Seed is a strong deterministic fit and can be promoted to StrategySpec candidate review.",
        )
    if candidate.matched_object_type == "strategy_spec" and total >= 65:
        return RecommendedAction(
            type="run_rapid_eval",
            reason="StrategySpec is a strong deterministic fit for research evaluation.",
        )
    if total >= 40:
        return RecommendedAction(
            type="create_research_ticket",
            reason="Candidate has enough overlap to justify a governed research ticket.",
        )
    return RecommendedAction(
        type="ignore",
        reason="Deterministic fit score is too low for this persona.",
    )


def _match_status(action: RecommendedAction, blockers: Sequence[str]) -> str:
    if blockers and action.type != "request_data_backfill":
        return "ignored"
    if action.type == "ignore":
        return "ignored"
    return "candidate"


def _contract_allowed_use(
    candidate: StrategyCandidate,
    action: RecommendedAction,
    blockers: Sequence[str],
) -> tuple[str, ...]:
    allowed: list[str] = ["research_discovery"]
    raw_allowed = {_normalize_token(value) for value in candidate.allowed_use}
    if candidate.matched_object_type == "strategy_spec_seed" and (
        not raw_allowed or raw_allowed.intersection({"research", "strategy_seed", "strategy_seed_generation"})
    ):
        allowed.append("strategy_seed_generation")
    if action.type == "create_research_ticket" or not blockers:
        allowed.append("research_ticket_creation")
    if action.type == "run_rapid_eval":
        allowed.append("rapid_eval_request")
    if action.type == "request_data_backfill":
        allowed = ["research_discovery"]
    return tuple(value for value in _unique_strings(allowed) if value in _CONTRACT_ALLOWED_USE)


def _score_set_overlap(
    persona_values: Sequence[str],
    candidate_values: Sequence[str],
    *,
    max_score: float,
    neutral_score: float,
) -> float:
    if not persona_values or not candidate_values:
        return _round_score(neutral_score)
    persona = set(persona_values)
    candidate = set(candidate_values)
    if not persona or not candidate:
        return _round_score(neutral_score)
    overlap = persona.intersection(candidate)
    if not overlap:
        return 0.0
    denominator = min(len(persona), len(candidate))
    return _round_score(max_score * (len(overlap) / denominator))


def _score_term_overlap(
    persona_values: Sequence[str],
    candidate_values: Sequence[Any],
    *,
    max_score: float,
) -> float:
    persona = set(persona_values)
    candidate = set(_stable_tokens(candidate_values))
    overlap = persona.intersection(candidate)
    if not overlap:
        return 0.0
    return _round_score(max_score * (len(overlap) / min(len(persona), len(candidate))))


def _append_match(
    matched_fields: list[MatchedField],
    field: str,
    persona_value: Any,
    strategy_value: Any,
    contribution: float,
) -> None:
    if contribution <= 0:
        return
    matched_fields.append(
        MatchedField(
            field=field,
            persona_value=_display_value(persona_value),
            strategy_value=_display_value(strategy_value),
            contribution=_round_score(contribution),
        )
    )


def _data_requirement_is_available(dataset: str, available_values: Sequence[str]) -> bool:
    available = set(available_values)
    dataset_tokens = [token for token in _stable_tokens(dataset) if token not in _DATA_GENERIC_TOKENS]
    if not dataset_tokens:
        return False
    if set(dataset_tokens).intersection(available):
        return True
    normalized_dataset = _normalize_token(dataset)
    return normalized_dataset in available


def _risk_values(profile: PersonaStrategyProfile) -> tuple[str, ...]:
    return _stable_tokens(
        [
            profile.risk_constraints.get("risk_level"),
            profile.risk_constraints.get("risk_constraints"),
        ]
    )


def _route_values(route_policy: Mapping[str, Any], *keys: str) -> list[Any]:
    values: list[Any] = []
    for key in keys:
        if key in route_policy:
            values.append(route_policy.get(key))
    for rule in route_policy.get("rules") or []:
        if not isinstance(rule, Mapping):
            continue
        for key in keys:
            if key in rule:
                values.append(rule.get(key))
    return values


def _to_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(mapping: Mapping[str, Any], *keys: str, default: str = "") -> str:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, "", [], ()):
            return str(value).strip()
    return default


def _optional_text(value: Any) -> str | None:
    if value in (None, "", [], ()):
        return None
    return str(value).strip()


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "allow", "allowed"}


def _stable_tokens(values: Any) -> tuple[str, ...]:
    tokens: list[str] = []
    for value in _flatten_strings(values):
        normalized = _normalize_token(value)
        if normalized:
            tokens.append(normalized)
        words = [
            _normalize_token(part)
            for part in re.split(r"[^A-Za-z0-9_]+", str(value))
            if _normalize_token(part)
        ]
        tokens.extend(words)
    return _unique_strings(tokens)


def _normalize_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return _ALIASES.get(text, text)


def _flatten_strings(value: Any) -> list[str]:
    if value in (None, "", [], ()):
        return []
    if isinstance(value, Mapping):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_flatten_strings(item))
        return strings
    if isinstance(value, (str, int, float)):
        text = str(value).strip()
        if not text:
            return []
        if "," in text:
            return [part.strip() for part in text.split(",") if part.strip()]
        return [text]
    if isinstance(value, Sequence):
        strings = []
        for item in value:
            strings.extend(_flatten_strings(item))
        return strings
    return [str(value).strip()] if str(value).strip() else []


def _unique_strings(values: Sequence[Any]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _display_value(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(item) for item in value if str(item).strip())
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True)
    return str(value or "")


def _evidence_ref_ids(value: Any) -> list[str]:
    refs: list[str] = []
    for item in value or []:
        if isinstance(item, Mapping):
            ref = item.get("ref_id") or item.get("ref") or item.get("id")
            if ref:
                refs.append(str(ref))
        elif item:
            refs.append(str(item))
    return refs


def _stable_match_id(persona_id: str, object_type: str, object_id: str) -> str:
    digest = hashlib.sha256(
        json.dumps(
            {
                "schema_version": _SCHEMA_VERSION,
                "scorer_version": _SCORER_VERSION,
                "persona_id": persona_id,
                "object_type": object_type,
                "object_id": object_id,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()[:16]
    return f"psm-{digest}"


def _round_score(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 6)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
