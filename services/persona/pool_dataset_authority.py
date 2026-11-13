"""Pool admission inputs from the training service's source DatasetVersion authority.

Direct paths are explicit test/dev overrides. The normal service path pins the
source owner's bytes and validates the same evaluation policy as training; it
never substitutes generated bars or a previous snapshot after rejection.
"""
from __future__ import annotations

import importlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping


def governed_dataset_loader(
    env: Mapping[str, str] | None = None,
    *,
    clock: Callable[[], datetime] | None = None,
) -> Callable[[Mapping, Mapping], dict]:
    source = os.environ if env is None else env
    now = clock or (lambda: datetime.now(timezone.utc))

    def load(persona: Mapping, candidate: Mapping) -> dict:
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
