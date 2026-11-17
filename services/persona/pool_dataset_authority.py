"""Pool admission inputs from the training service's source DatasetVersion authority.

Direct paths are explicit test/dev overrides. The normal service path pins the
source owner's bytes and validates the same evaluation policy as training; it
never substitutes generated bars or a previous snapshot after rejection.
"""
from __future__ import annotations

import importlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping


def referenced_feature_inputs(candidate: Mapping) -> dict[str, list[str]]:
    """Governed non-price ``dataset -> fields`` the candidate rule's inputs reference."""
    from services.registry.factor_rule import PRICE_VOLUME_DATASETS

    artifact = candidate.get("strategy_artifact")
    logic = artifact.get("strategy_logic") if isinstance(artifact, Mapping) else None
    out: dict[str, list[str]] = {}
    if isinstance(logic, Mapping) and logic.get("kind") == "factor_rule_v1":
        for item in logic.get("inputs") or ():
            if not isinstance(item, Mapping):
                continue
            dataset, field = str(item.get("dataset") or ""), str(item.get("field") or "")
            if dataset and dataset not in PRICE_VOLUME_DATASETS:
                fields = out.setdefault(dataset, [])
                if field and field not in fields:
                    fields.append(field)
    return out


def screened_logic(candidate: Mapping) -> Mapping | None:
    """The candidate's ``factor_rule_v1`` logic when it carries a market ``screen``."""
    artifact = candidate.get("strategy_artifact")
    logic = artifact.get("strategy_logic") if isinstance(artifact, Mapping) else None
    if isinstance(logic, Mapping) and logic.get("kind") == "factor_rule_v1" and "screen" in logic:
        return logic
    return None


def require_market_feature_coverage(market: Mapping, referenced: Mapping[str, list[str]]) -> None:
    """Every referenced non-price ``dataset.field`` must be carried by at least one market symbol."""
    authority = importlib.import_module("services.training-session.evaluation_authority")
    symbols = market.get("symbols") if isinstance(market, Mapping) else None
    for ds, fields in referenced.items():
        for field in fields:
            if not any(
                isinstance(p, Mapping) and ((p.get("features") or {}).get(ds) or {}).get(field)
                for p in (symbols or {}).values()
            ):
                raise authority.AuthorityValidationError(
                    f"feature dataset coverage missing: {ds}.{field} has no point-in-time observations "
                    "in the candidate pool market"
                )


def _series_for(observations: list[Mapping], instrument: str, dataset: str, field: str) -> list[dict]:
    keys = {instrument, instrument.split(".")[0]}
    rows = [
        {k: o[k] for k in ("event_time", "available_time", "ingest_time", "availability_lag_days", "value") if k in o}
        for o in observations
        if o.get("dataset") == dataset and o.get("field") == field and str(o.get("entity")) in keys
    ]
    rows.sort(key=lambda r: (r["event_time"], r["available_time"], str(r.get("ingest_time") or "")))
    return rows


def attach_feature_observations(dataset: dict, referenced: Mapping[str, list[str]],
                                observations_by_dataset: Mapping[str, list[Mapping]]) -> dict:
    """Attach point-in-time observations per instrument; fail closed on missing coverage.

    Every referenced ``dataset.field`` must carry observations for at least one price
    instrument, otherwise the candidate cannot be evaluated and the reason is raised.
    """
    authority = importlib.import_module("services.training-session.evaluation_authority")
    instruments = sorted({str(r["instrument"]) for r in dataset.get("records") or () if r.get("instrument")})
    features: dict[str, dict] = {inst: {} for inst in instruments}
    for ds, fields in referenced.items():
        observations = list(observations_by_dataset.get(ds) or ())
        for field in fields:
            covered = 0
            for inst in instruments:
                series = _series_for(observations, inst, ds, field)
                if series:
                    covered += 1
                    features[inst].setdefault(ds, {})[field] = series
            if not covered:
                raise authority.AuthorityValidationError(
                    f"feature dataset coverage missing: {ds}.{field} has no point-in-time observations "
                    f"for any of {len(instruments)} admission instruments"
                )
    return {**dataset, "features_by_instrument": features}


def governed_dataset_loader(
    env: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
    market_fetcher: Callable[..., Mapping] | None = None,
) -> Callable[[Mapping, Mapping], dict]:
    source = os.environ if env is None else env
    now = clock or (lambda: datetime.now(timezone.utc))

    def load(persona: Mapping, candidate: Mapping) -> dict:
        referenced = referenced_feature_inputs(candidate)
        dataset = _load_price(persona, candidate)
        logic = screened_logic(candidate)
        if logic is not None:
            # A screened rule reads its non-price inputs from the governed whole-market input,
            # which the source owner builds point-in-time; it fails closed without that market.
            market = _load_candidate_pool_market(logic)
            require_market_feature_coverage(market, referenced)
            return {**dataset, "candidate_pool_market": dict(market)}
        if not referenced:
            return dataset
        return attach_feature_observations(dataset, referenced, _load_feature_observations(candidate, referenced))

    def _load_candidate_pool_market(logic: Mapping) -> Mapping:
        authority = importlib.import_module("services.training-session.evaluation_authority")
        if source.get("PERSONA_POOL_CANONICAL_DATASET_PATH") or source.get("TRAINING_SESSION_CANONICAL_DATASET_PATH"):
            raise authority.AuthorityValidationError(
                "feature dataset coverage missing: a canonical dataset path override carries no candidate pool market"
            )
        from services.execution.lean_runtime.pool_market_inputs import (
            PoolMarketInputsUnavailable, fetch_candidate_pool_market,
        )

        fetch = market_fetcher or fetch_candidate_pool_market
        try:
            return fetch(str(source.get("SOURCE_INGEST_API_URL") or "").strip(), list(logic.get("inputs") or ()))
        except PoolMarketInputsUnavailable as exc:
            raise authority.AuthorityValidationError(
                f"feature dataset coverage missing: candidate pool market unavailable: {exc}") from exc

    def _load_feature_observations(candidate: Mapping, referenced: Mapping[str, list[str]]) -> dict[str, list]:
        authority = importlib.import_module("services.training-session.evaluation_authority")
        if source.get("PERSONA_POOL_CANONICAL_DATASET_PATH") or source.get("TRAINING_SESSION_CANONICAL_DATASET_PATH"):
            raise authority.AuthorityValidationError(
                "feature dataset coverage missing: a canonical dataset path override carries no feature datasets"
            )
        source_authority = importlib.import_module("services.training-session.source_dataset_authority")
        from services.source_ingestion.feature_catalogue import DATASET_SPECS

        try:
            connectors = json.loads(source.get("PERSONA_POOL_FEATURE_CONNECTOR_IDS") or "{}")
        except ValueError:
            connectors = {}
        normalized = {spec.name: spec.normalized_dataset for spec in DATASET_SPECS}
        source_url = str(source.get("SOURCE_INGEST_API_URL") or "").strip()
        source_root = str(source.get("PERSONA_POOL_SOURCE_VOLUME_ROOT") or "").strip()
        output_root = str(source.get("PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT") or "").strip()
        out: dict[str, list] = {}
        for ds in referenced:
            connector = str(connectors.get(ds) or "").strip() if isinstance(connectors, Mapping) else ""
            if ds not in normalized or not connector:
                raise authority.AuthorityValidationError(
                    f"feature dataset coverage missing: {ds} has no governed connector configured"
                )
            try:
                timeout = float(source.get("PERSONA_POOL_SOURCE_AUTHORITY_TIMEOUT_SECONDS", "5"))
                headers = source_authority.source_read_headers(
                    source.get("PERSONA_POOL_SOURCE_READ_TOKEN_FILE"), source.get("PERSONA_POOL_SOURCE_READ_TENANT_ID"))
                materialized = source_authority.materialize_source_dataset_version(
                    http_get=lambda url: source_authority.urllib_json_get(url, timeout_seconds=timeout, headers=headers),
                    source_api_url=source_url, connector_id=connector, dataset_id=normalized[ds],
                    source_volume_root=source_root,
                    # Feature datasets live in their own directory so the canonical price
                    # dataset-version pointer (newest dataset-version-*.json in output_root)
                    # can never be repointed at a feature dataset.
                    output_root=str(Path(output_root) / "feature-datasets" / ds),
                    trusted_now=now(), clock=now,
                )
            except (source_authority.SourceDatasetAuthorityError, ValueError) as exc:
                raise authority.AuthorityValidationError(
                    f"feature dataset coverage missing: {ds} source authority rejected: {exc}") from exc
            out[ds] = list(materialized.payload.get("feature_observations") or ())
        return out

    def _load_price(persona: Mapping, candidate: Mapping) -> dict:
        # Preserve the existing explicit override contract without giving it a
        # deployed default or falling back to it when source authority rejects.
        if source.get("PERSONA_POOL_CANONICAL_DATASET_PATH") or source.get("TRAINING_SESSION_CANONICAL_DATASET_PATH"):
            from services.persona.pool_autonomy_cycle import governed_dataset_loader as path_loader

            return path_loader(source)(persona, candidate)

        authority = importlib.import_module("services.training-session.evaluation_authority")
        source_authority = importlib.import_module("services.training-session.source_dataset_authority")
        policy_path = str(source.get("PERSONA_POOL_THRESHOLD_POLICY_PATH") or source.get("TRAINING_SESSION_THRESHOLD_POLICY_PATH") or "").strip()
        source_url = str(source.get("SOURCE_INGEST_API_URL") or "").strip()
        connector_id = str(source.get("PERSONA_POOL_SOURCE_CONNECTOR_ID") or "").strip()
        dataset_id = str(source.get("PERSONA_POOL_SOURCE_DATASET_ID") or "").strip()
        source_root = str(source.get("PERSONA_POOL_SOURCE_VOLUME_ROOT") or "").strip()
        output_root = str(source.get("PERSONA_POOL_DATASET_AUTHORITY_OUTPUT_ROOT") or "").strip()
        if not all((policy_path, source_url, connector_id, dataset_id, source_root, output_root)):
            raise authority.AuthorityValidationError("source DatasetVersion authority configuration is incomplete")
        trusted_now = now()
        try:
            timeout = float(source.get("PERSONA_POOL_SOURCE_AUTHORITY_TIMEOUT_SECONDS", "5"))
            headers = source_authority.source_read_headers(
                source.get("PERSONA_POOL_SOURCE_READ_TOKEN_FILE"),
                source.get("PERSONA_POOL_SOURCE_READ_TENANT_ID"),
            )
            materialized = source_authority.materialize_source_dataset_version(
                http_get=lambda url: source_authority.urllib_json_get(url, timeout_seconds=timeout, headers=headers),
                source_api_url=source_url,
                connector_id=connector_id,
                dataset_id=dataset_id,
                source_volume_root=source_root,
                output_root=output_root,
                trusted_now=trusted_now,
                clock=now,
                policy_path=policy_path,
            )
        except (source_authority.SourceDatasetAuthorityError, ValueError) as exc:
            raise authority.AuthorityValidationError(f"source DatasetVersion authority rejected: {exc}") from exc
        snapshot = authority.load_evaluation_authority(
            materialized.path,
            policy_path,
            trusted_now=now(),
            strategy_id=str(candidate["artifact_ref"]),
            authority_root=Path(materialized.path).parent,
        )
        return dict(snapshot.vectorbt_dataset)

    return load
