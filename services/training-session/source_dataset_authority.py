"""Materialize a source-ingest run as a strict, immutable DatasetVersion.

The source-ingest storage manifest is useful lineage, but its normalized files
contain SourceRecord wrappers rather than the canonical OHLCV rows consumed by
``evaluation_authority``.  This module validates the authoritative controller
readback, pins every source file by checksum, and atomically writes both a
canonical OHLCV JSONL artifact and a content-addressed DatasetVersion.

There are deliberately no environment or wall-clock defaults.  The caller
must supply the source endpoint, connector and dataset identities, both roots,
an HTTP GET transport, and an aware trusted UTC clock.
"""

from __future__ import annotations

import copy
import fcntl
import gzip
import hashlib
import io
import json
import math
import os
import re
import stat
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class SourceDatasetAuthorityError(RuntimeError):
    """Raised when source truth cannot be admitted without guessing."""


HttpGetTransport = Callable[[str], Any]

_UTC = timezone.utc
_READBACK_SCHEMA = "source_ingest_controller_readback.v1"
_CONTROLLER_STATE_SCHEMAS = frozenset(
    {"source_ingest_controller_state.v1", "source_ingest_controller_state.v2"}
)
_REQUIREMENT_SCHEMA = "source_ingest_requirement_snapshot.v1"
_CONNECTOR_SCHEMA = "source_connector.v2"
_STORAGE_SCHEMA = "market_data_storage_manifest.v1"
_OUTPUT_SCHEMA = "source_dataset_authority.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UNTRUSTED_IDENTITIES = frozenset({"unknown", "local-dev", "unresolved"})
_DEPLOYMENT_IDENTITY_FIELDS = (
    "git_sha",
    "image_digest",
    "build_time",
    "deployment_id",
    "runtime_instance_id",
)
_DLQ_STATUSES = frozenset(
    {"pending", "replayed", "duplicate_skipped", "replay_failed", "schema_rejected"}
)
_MAX_SOURCE_REFS = 256
_MAX_FILE_BYTES = 128 * 1024 * 1024
_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
_MAX_ROWS = 1_000_000


@dataclass(frozen=True)
class MaterializedDatasetVersion:
    """A durable authority file and the checksums needed to consume it."""

    path: Path
    payload: Mapping[str, Any]
    payload_sha256: str
    canonical_ohlcv_path: Path
    canonical_ohlcv_sha256: str
    ingest_run_id: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path.as_posix(),
            "payload": copy.deepcopy(dict(self.payload)),
            "payload_sha256": self.payload_sha256,
            "canonical_ohlcv_path": self.canonical_ohlcv_path.as_posix(),
            "canonical_ohlcv_sha256": self.canonical_ohlcv_sha256,
            "ingest_run_id": self.ingest_run_id,
        }


def urllib_json_get(url: str, *, timeout_seconds: float = 5.0) -> Any:
    """Small strict GET transport for callers that do not inject one."""

    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool):
        raise SourceDatasetAuthorityError("timeout_seconds must be numeric")
    if not math.isfinite(float(timeout_seconds)) or float(timeout_seconds) <= 0:
        raise SourceDatasetAuthorityError("timeout_seconds must be finite and greater than zero")
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=float(timeout_seconds)) as response:  # noqa: S310
            body = response.read(_MAX_FILE_BYTES + 1)
    except Exception as exc:  # urllib exposes several transport-specific subclasses
        detail = str(exc).strip()
        suffix = f": {type(exc).__name__}: {detail}" if detail else f": {type(exc).__name__}"
        raise SourceDatasetAuthorityError(f"source authority GET failed for {url}{suffix}") from exc
    if len(body) > _MAX_FILE_BYTES:
        raise SourceDatasetAuthorityError("source authority HTTP response is too large")
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceDatasetAuthorityError("source authority HTTP response is not UTF-8") from exc
    return _strict_json_loads(text, f"HTTP response from {url}")


def materialize_source_dataset_version(
    *,
    http_get: HttpGetTransport,
    source_api_url: str,
    connector_id: str,
    dataset_id: str,
    source_volume_root: str | Path,
    output_root: str | Path,
    trusted_now: datetime,
    max_readback_age_seconds: int = 300,
) -> MaterializedDatasetVersion:
    """Resolve one terminal source run into an evaluator-compatible DatasetVersion.

    A prior DatasetVersion for the same connector/dataset/run is an immutable
    pin.  If source bytes or authority bindings later differ, materialization
    fails instead of blessing the mutation as a new version.
    """

    if not callable(http_get):
        raise SourceDatasetAuthorityError("http_get must be callable")
    now = _trusted_utc(trusted_now)
    age_limit = _strict_int(
        max_readback_age_seconds,
        "max_readback_age_seconds",
        minimum=1,
        maximum=300,
    )
    connector = _required_text(connector_id, "connector_id")
    dataset = _required_text(dataset_id, "dataset_id")
    base_url = _normalize_api_url(source_api_url)
    source_root = _existing_directory(source_volume_root, "source_volume_root")
    authority_root = _output_directory(output_root)

    readback_url = f"{base_url}/api/source-ingest/controller/readback"
    readback = _http_get_object(http_get, readback_url, "controller readback")
    controller_binding, connector_readback = _validate_controller_readback(
        readback,
        connector_id=connector,
        dataset_id=dataset,
        trusted_now=now,
        max_age_seconds=age_limit,
    )

    dataset_resolution = _resolve_dataset_identity(
        http_get,
        base_url=base_url,
        connector_readback=connector_readback,
        requested_dataset_id=dataset,
        connector_id=connector,
    )
    (
        run_id,
        storage_manifest,
        market,
        health_success_at,
        health_row_count,
    ) = _validate_connector_terminal_truth(
        connector_readback,
        connector_id=connector,
        desired_dataset_id=dataset_resolution["desired_dataset_id"],
        normalized_targets=set(dataset_resolution["normalized_targets"]),
        trusted_now=now,
    )
    run_url = f"{base_url}/api/source-ingest/jobs/{urllib.parse.quote(run_id, safe='')}"
    run_payload = _http_get_object(http_get, run_url, "source ingest run")
    run = _validate_run(
        run_payload,
        connector_id=connector,
        run_id=run_id,
        storage_manifest=storage_manifest,
        expected_last_success_at=health_success_at,
        expected_normalized_count=health_row_count,
        trusted_now=now,
    )

    bundles_url = f"{base_url}/api/source-ingest/evidence/bundles"
    bundles_payload = _http_get_object(http_get, bundles_url, "source evidence bundles")
    evidence_bindings = _select_evidence_bundles(
        bundles_payload,
        connector_id=connector,
        run_id=run_id,
        trusted_now=now,
    )
    for binding in evidence_bindings:
        bundle_id = str(binding["evidence_bundle_id"])
        binding["ref"] = (
            f"{base_url}/api/source-ingest/evidence/bundles/"
            f"{urllib.parse.quote(bundle_id, safe='')}"
        )

    storage = _validate_and_read_storage_manifest(
        storage_manifest,
        source_root=source_root,
        connector_id=connector,
        normalized_targets=set(dataset_resolution["normalized_targets"]),
        run_id=run_id,
        expected_market=market,
        trusted_now=now,
    )
    records = storage["records"]
    canonical_bytes = b"".join(_canonical_json_bytes(record) + b"\n" for record in records)
    canonical_digest = hashlib.sha256(canonical_bytes).hexdigest()
    canonical_path = authority_root / f"canonical-ohlcv-{canonical_digest}.jsonl"

    controller_binding_digest = stable_json_sha256(controller_binding)
    storage_binding = {
        "schema_version": _STORAGE_SCHEMA,
        "ingest_run_id": run_id,
        "created_at": storage["created_at"],
        "raw_refs": storage["raw_refs"],
        "normalized_refs": storage["normalized_refs"],
        "feature_refs": storage["feature_refs"],
    }
    storage_binding_digest = stable_json_sha256(storage_binding)
    evidence_bundle_ids = [item["evidence_bundle_id"] for item in evidence_bindings]
    evidence_binding_digest = stable_json_sha256(evidence_bindings)
    readback_ref = readback_url

    payload_without_id: dict[str, Any] = {
        "market_scope": [storage["market"]],
        "instrument_scope": sorted({str(record["instrument"]) for record in records}),
        "raw_dataset_refs": [item["uri"] for item in storage["raw_refs"]],
        "normalized_dataset_refs": [item["uri"] for item in storage["normalized_refs"]],
        "feature_dataset_refs": [item["uri"] for item in storage["feature_refs"]],
        "frozen_at": storage["created_at"],
        "metadata_json": {
            "schema_version": _OUTPUT_SCHEMA,
            "authority_status": "authoritative",
            "source_api_url": base_url,
            "source_connector_id": connector,
            "source_dataset_id": dataset,
            "source_dataset_resolution": dataset_resolution,
            "source_ingest_run_id": run_id,
            "source_run_ref": run_url,
            "source_run_finished_at": run["finished_at"],
            "source_controller_binding_sha256": controller_binding_digest,
            "source_storage_binding_sha256": storage_binding_digest,
            "source_evidence_binding_sha256": evidence_binding_digest,
            "canonical_ohlcv_sha256": canonical_digest,
            "source_storage_refs": {
                "raw": storage["raw_refs"],
                "normalized": storage["normalized_refs"],
                "feature": storage["feature_refs"],
            },
            "source_evidence_bundle_refs": evidence_bindings,
            "controller_identity": controller_binding,
        },
        "created_at": storage["created_at"],
        "source_controller_readback_ref": readback_ref,
        "source_evidence_bundle_ids": evidence_bundle_ids,
        "normalized_storage_refs": [
            {
                "uri": canonical_path.resolve(strict=False).as_posix(),
                "sha256": canonical_digest,
                "row_count": len(records),
            }
        ],
        "records": records,
    }
    identity_digest = stable_json_sha256(payload_without_id)
    payload = {
        "dataset_version_id": f"dataset-version-{identity_digest}",
        **payload_without_id,
    }
    payload_digest = stable_json_sha256(payload)
    dataset_path = authority_root / f"dataset-version-{payload_digest}.json"
    dataset_bytes = _canonical_json_bytes(payload) + b"\n"

    lock_path = authority_root / ".source-dataset-authority.lock"
    lock_fd = _open_lock_file(lock_path)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        _assert_run_not_rebound(
            authority_root,
            connector_id=connector,
            dataset_id=dataset,
            run_id=run_id,
            expected_storage_binding_digest=storage_binding_digest,
            expected_payload=payload,
        )
        _atomic_materialize(canonical_path, canonical_bytes, authority_root)
        _atomic_materialize(dataset_path, dataset_bytes, authority_root)
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    return MaterializedDatasetVersion(
        path=dataset_path,
        payload=copy.deepcopy(payload),
        payload_sha256=payload_digest,
        canonical_ohlcv_path=canonical_path,
        canonical_ohlcv_sha256=canonical_digest,
        ingest_run_id=run_id,
    )


def stable_json_sha256(payload: Any) -> str:
    """Digest finite canonical JSON using the evaluator's serialization."""

    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _validate_controller_readback(
    payload: Mapping[str, Any],
    *,
    connector_id: str,
    dataset_id: str,
    trusted_now: datetime,
    max_age_seconds: int,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    if payload.get("schema_version") != _READBACK_SCHEMA:
        raise SourceDatasetAuthorityError("controller readback schema must be v1")
    captured_at = _parse_timestamp(payload.get("captured_at"), "readback.captured_at")
    age = (trusted_now - captured_at).total_seconds()
    if age < 0:
        raise SourceDatasetAuthorityError("controller readback captured_at is in the future")
    if age > max_age_seconds:
        raise SourceDatasetAuthorityError("controller readback is stale")

    state = _required_mapping(payload.get("controller_state"), "readback.controller_state")
    if state.get("schema_version") not in _CONTROLLER_STATE_SCHEMAS:
        raise SourceDatasetAuthorityError("controller state schema must be v1 or v2")
    identity = {
        key: _trusted_identity(state.get(key), f"controller_state.{key}")
        for key in ("controller_id", "controller_name", "environment", "tenant_id")
    }
    identity["sequence_no"] = _strict_int(
        state.get("sequence_no"), "controller_state.sequence_no", minimum=1
    )
    deployment = _required_mapping(state.get("deployment"), "controller_state.deployment")
    if deployment.get("identity_complete") is not True:
        raise SourceDatasetAuthorityError("controller deployment identity is incomplete")
    deployment_binding: dict[str, Any] = {"identity_complete": True}
    for field in _DEPLOYMENT_IDENTITY_FIELDS:
        deployment_binding[field] = _trusted_identity(
            deployment.get(field), f"controller_state.deployment.{field}"
        )
    build_time = _parse_timestamp(
        deployment_binding["build_time"], "controller_state.deployment.build_time"
    )
    if build_time > trusted_now:
        raise SourceDatasetAuthorityError("controller deployment build_time is in the future")

    snapshot = _required_mapping(payload.get("requirement_snapshot"), "readback.requirement_snapshot")
    if snapshot.get("schema_version") != _REQUIREMENT_SCHEMA:
        raise SourceDatasetAuthorityError("requirement snapshot schema must be v1")
    if snapshot.get("authoritative") is not True:
        raise SourceDatasetAuthorityError("requirement snapshot is not authoritative")
    desired_digest = _required_digest(
        snapshot.get("desired_state_sha256"), "requirement_snapshot.desired_state_sha256"
    )
    bindings = _required_mapping(snapshot.get("bindings"), "requirement_snapshot.bindings")
    normalized_bindings: dict[str, str] = {}
    for key, value in bindings.items():
        requirement = _required_text(key, "requirement_snapshot binding id")
        normalized_bindings[requirement] = _required_text(
            value, f"requirement_snapshot.bindings.{requirement}"
        )
    if connector_id not in normalized_bindings.values():
        raise SourceDatasetAuthorityError("requirement snapshot does not bind the selected connector")
    if _strict_int(snapshot.get("binding_count"), "requirement_snapshot.binding_count", minimum=1) != len(
        normalized_bindings
    ):
        raise SourceDatasetAuthorityError("requirement snapshot binding_count does not match bindings")
    snapshot_binding = {
        "schema_version": _REQUIREMENT_SCHEMA,
        "sequence": _strict_int(snapshot.get("sequence"), "requirement_snapshot.sequence", minimum=1),
        "desired_state_sha256": desired_digest,
        "bindings": dict(sorted(normalized_bindings.items())),
        "binding_count": len(normalized_bindings),
        "persona_count": _strict_int(
            snapshot.get("persona_count"), "requirement_snapshot.persona_count", minimum=1
        ),
        "authority": _trusted_identity(snapshot.get("authority"), "requirement_snapshot.authority"),
        "authoritative": True,
    }

    _validate_dlq(payload)
    connectors = payload.get("connectors")
    if isinstance(connectors, (str, bytes)) or not isinstance(connectors, Sequence):
        raise SourceDatasetAuthorityError("controller readback connectors must be an array")
    if _strict_int(payload.get("connector_count"), "readback.connector_count", minimum=1) != len(connectors):
        raise SourceDatasetAuthorityError("controller readback connector_count does not match connectors")
    matches = [
        item
        for item in connectors
        if isinstance(item, Mapping) and item.get("connector_id") == connector_id
    ]
    if len(matches) != 1:
        raise SourceDatasetAuthorityError("selected connector must appear exactly once in readback")
    selected = matches[0]
    desired_state = _required_mapping(selected.get("desired_state"), "connector.desired_state")
    desired_dataset_id = _required_text(
        desired_state.get("dataset"), "connector.desired_state.dataset"
    )
    if selected.get("desired_state_sha256") != desired_digest:
        raise SourceDatasetAuthorityError("connector desired-state digest contradicts requirement snapshot")
    controller_binding = {
        "schema_version": _READBACK_SCHEMA,
        **identity,
        "deployment": deployment_binding,
        "requirement_snapshot": snapshot_binding,
        "connector_id": connector_id,
        "dataset_id": dataset_id,
        "desired_dataset_id": desired_dataset_id,
        "desired_state_sha256": desired_digest,
    }
    return controller_binding, selected


def _validate_dlq(payload: Mapping[str, Any]) -> None:
    total = _strict_int(payload.get("dlq_count"), "readback.dlq_count", minimum=0)
    pending = _strict_int(payload.get("pending_dlq_count"), "readback.pending_dlq_count", minimum=0)
    unresolved = _strict_int(
        payload.get("unresolved_dlq_count"), "readback.unresolved_dlq_count", minimum=0
    )
    counts = _required_mapping(payload.get("dlq_status_counts"), "readback.dlq_status_counts")
    if set(counts) != _DLQ_STATUSES:
        raise SourceDatasetAuthorityError("readback DLQ status schema is incomplete")
    normalized = {
        status_name: _strict_int(counts.get(status_name), f"dlq_status_counts.{status_name}", minimum=0)
        for status_name in _DLQ_STATUSES
    }
    if sum(normalized.values()) != total or normalized["pending"] != pending:
        raise SourceDatasetAuthorityError("readback DLQ counts are internally inconsistent")
    expected_unresolved = sum(normalized[name] for name in ("pending", "replay_failed", "schema_rejected"))
    if unresolved != expected_unresolved:
        raise SourceDatasetAuthorityError("readback unresolved DLQ count is inconsistent")
    if unresolved != 0:
        raise SourceDatasetAuthorityError("source authority has unresolved DLQ entries")


def _resolve_dataset_identity(
    http_get: HttpGetTransport,
    *,
    base_url: str,
    connector_readback: Mapping[str, Any],
    requested_dataset_id: str,
    connector_id: str,
) -> dict[str, Any]:
    """Separate registry identity from owner-declared normalized storage IDs."""

    desired = _required_mapping(connector_readback.get("desired_state"), "connector.desired_state")
    desired_dataset = _required_text(desired.get("dataset"), "connector.desired_state.dataset")
    connector = _required_mapping(connector_readback.get("connector"), "connector.connector")
    metadata = connector.get("metadata")
    connector_targets: list[str] = []
    if isinstance(metadata, Mapping):
        raw_targets = metadata.get("normalized_datasets")
        if isinstance(raw_targets, Sequence) and not isinstance(raw_targets, (str, bytes)):
            connector_targets.extend(
                _required_text(item, "connector.metadata.normalized_datasets[]")
                for item in raw_targets
            )
        for key in ("normalized_target", "dataset"):
            value = metadata.get(key)
            if value not in (None, ""):
                connector_targets.append(_required_text(value, f"connector.metadata.{key}"))
    connector_targets = sorted(set(connector_targets))

    if requested_dataset_id == desired_dataset:
        targets = connector_targets or [desired_dataset]
        return {
            "resolution_mode": "controller_desired_state",
            "requested_dataset_id": requested_dataset_id,
            "desired_dataset_id": desired_dataset,
            "normalized_targets": targets,
            "catalog_ref": None,
            "catalog_entry_sha256": None,
        }
    if requested_dataset_id in connector_targets:
        return {
            "resolution_mode": "connector_normalized_target",
            "requested_dataset_id": requested_dataset_id,
            "desired_dataset_id": desired_dataset,
            "normalized_targets": [requested_dataset_id],
            "catalog_ref": None,
            "catalog_entry_sha256": None,
        }

    catalog_url = f"{base_url}/api/source-ingest/data-sources/financial-catalog"
    catalog = _http_get_object(http_get, catalog_url, "financial source catalog")
    if catalog.get("schema_version") != "financial_data_source_catalog.v1":
        raise SourceDatasetAuthorityError("financial source catalog schema must be v1")
    entries = catalog.get("entries")
    if isinstance(entries, (str, bytes)) or not isinstance(entries, Sequence):
        raise SourceDatasetAuthorityError("financial source catalog entries must be an array")
    matches = [
        item
        for item in entries
        if isinstance(item, Mapping) and item.get("data_source_id") == requested_dataset_id
    ]
    if len(matches) != 1:
        raise SourceDatasetAuthorityError(
            "requested dataset is not uniquely mapped by controller or financial catalog"
        )
    entry = matches[0]
    if entry.get("connector_id") != connector_id:
        raise SourceDatasetAuthorityError("financial catalog dataset belongs to a different connector")
    catalog_datasets = entry.get("datasets")
    if (
        isinstance(catalog_datasets, (str, bytes))
        or not isinstance(catalog_datasets, Sequence)
        or not catalog_datasets
    ):
        raise SourceDatasetAuthorityError("financial catalog entry has no normalized datasets")
    catalog_targets: set[str] = set()
    for index, item in enumerate(catalog_datasets):
        dataset_entry = _required_mapping(item, f"financial catalog datasets[{index}]")
        catalog_targets.add(
            _required_text(dataset_entry.get("dataset_id"), f"financial catalog datasets[{index}].dataset_id")
        )

    templates = catalog.get("config_templates")
    if isinstance(templates, (str, bytes)) or not isinstance(templates, Sequence):
        raise SourceDatasetAuthorityError("financial source catalog config_templates must be an array")
    template_matches = [
        item
        for item in templates
        if isinstance(item, Mapping)
        and item.get("data_source_id") == requested_dataset_id
        and item.get("connector_id") == connector_id
    ]
    if len(template_matches) != 1:
        raise SourceDatasetAuthorityError(
            "financial catalog must uniquely bind dataset, connector, and desired fetch"
        )
    fetch = _required_mapping(template_matches[0].get("fetch"), "financial catalog template.fetch")
    if fetch.get("dataset") != desired_dataset:
        raise SourceDatasetAuthorityError(
            "controller desired dataset contradicts financial catalog owner mapping"
        )
    normalized_targets = catalog_targets
    if connector_targets:
        normalized_targets = catalog_targets.intersection(connector_targets)
    if not normalized_targets:
        raise SourceDatasetAuthorityError(
            "financial catalog and connector metadata have no shared normalized target"
        )
    return {
        "resolution_mode": "financial_catalog_mapping",
        "requested_dataset_id": requested_dataset_id,
        "desired_dataset_id": desired_dataset,
        "normalized_targets": sorted(normalized_targets),
        "catalog_ref": catalog_url,
        "catalog_entry_sha256": stable_json_sha256(entry),
        "catalog_template_sha256": stable_json_sha256(template_matches[0]),
    }


def _validate_connector_terminal_truth(
    payload: Mapping[str, Any],
    *,
    connector_id: str,
    desired_dataset_id: str,
    normalized_targets: set[str],
    trusted_now: datetime,
) -> tuple[str, Mapping[str, Any], str, datetime, int]:
    if payload.get("configured") is not True:
        raise SourceDatasetAuthorityError("selected connector is not configured")
    connector = _required_mapping(payload.get("connector"), "connector.connector")
    if connector.get("schema_version") != _CONNECTOR_SCHEMA:
        raise SourceDatasetAuthorityError("selected connector schema must be v2")
    if connector.get("connector_id") != connector_id or connector.get("status") != "enabled":
        raise SourceDatasetAuthorityError("selected connector is missing or disabled")
    schedule = _required_mapping(payload.get("schedule"), "connector.schedule")
    if schedule.get("connector_id") != connector_id or schedule.get("enabled") is not True:
        raise SourceDatasetAuthorityError("selected connector schedule is not enabled")
    interval = _strict_int(schedule.get("interval_seconds"), "connector.schedule.interval_seconds", minimum=1)
    desired = _required_mapping(payload.get("desired_state"), "connector.desired_state")
    if _required_text(desired.get("dataset"), "connector.desired_state.dataset") != desired_dataset_id:
        raise SourceDatasetAuthorityError("connector desired dataset mismatch")
    market = _required_text(desired.get("market"), "connector.desired_state.market")

    freshness = _required_mapping(payload.get("freshness"), "connector.freshness")
    if freshness.get("status") != "fresh" or freshness.get("is_due") is not False:
        raise SourceDatasetAuthorityError("connector freshness is not terminal fresh")
    staleness = _strict_int(
        freshness.get("staleness_seconds"), "connector.freshness.staleness_seconds", minimum=0
    )
    if staleness > interval:
        raise SourceDatasetAuthorityError("connector freshness exceeds its schedule interval")
    latest_run = _required_mapping(freshness.get("latest_run"), "connector.freshness.latest_run")
    if latest_run.get("status") != "completed":
        raise SourceDatasetAuthorityError("connector latest run is not completed")
    run_id = _required_text(latest_run.get("ingest_run_id"), "connector.latest_run.ingest_run_id")
    if _required_text(freshness.get("last_ingest_run_id"), "connector.freshness.last_ingest_run_id") != run_id:
        raise SourceDatasetAuthorityError("connector freshness run identities disagree")

    health = _required_mapping(payload.get("source_health"), "connector.source_health")
    if health.get("status") != "ok" or health.get("source_id") != connector_id:
        raise SourceDatasetAuthorityError("source health is not terminal ok for selected connector")
    health_success = _parse_timestamp(health.get("last_success_at"), "source_health.last_success_at")
    if health_success > trusted_now:
        raise SourceDatasetAuthorityError("source health last_success_at is in the future")
    health_row_count = _strict_int(
        health.get("row_count_last_run"), "source_health.row_count_last_run", minimum=1
    )
    if _strict_int(health.get("rejected_count_last_run"), "source_health.rejected_count_last_run", minimum=0):
        raise SourceDatasetAuthorityError("source health latest run contains rejected rows")
    health_metadata = _required_mapping(health.get("metadata"), "source_health.metadata")
    if health_metadata.get("last_ingest_run_id") != run_id or health_metadata.get("last_run_status") != "completed":
        raise SourceDatasetAuthorityError("source health does not identify the completed latest run")
    manifest = _required_mapping(health_metadata.get("storage_refs"), "source_health.metadata.storage_refs")
    if manifest.get("schema_version") != _STORAGE_SCHEMA or manifest.get("ingest_run_id") != run_id:
        raise SourceDatasetAuthorityError("source health storage manifest belongs to a different run")

    record = _required_mapping(payload.get("latest_source_record"), "connector.latest_source_record")
    if record.get("connector_id") != connector_id or record.get("status") != "normalized":
        raise SourceDatasetAuthorityError("latest normalized source record identity is invalid")
    for field in ("source_id", "content_ref", "trace_id"):
        _required_text(record.get(field), f"latest_source_record.{field}")
    provenance = _required_mapping(record.get("provenance"), "latest_source_record.provenance")
    if provenance.get("dataset") not in normalized_targets:
        raise SourceDatasetAuthorityError("latest source record belongs to a different dataset")
    if provenance.get("source_ingest_run_id") != run_id:
        raise SourceDatasetAuthorityError("latest source record belongs to a different run")
    for field in ("provider", "api_endpoint", "access_scope", "license_scope", "schema_hash"):
        _required_text(provenance.get(field), f"latest_source_record.provenance.{field}")
    available_at = _parse_timestamp(
        provenance.get("available_time"), "latest_source_record.provenance.available_time"
    )
    if available_at > trusted_now:
        raise SourceDatasetAuthorityError("latest source record available_time is in the future")
    created_at = _parse_timestamp(record.get("created_at"), "latest_source_record.created_at")
    if created_at > trusted_now:
        raise SourceDatasetAuthorityError("latest source record created_at is in the future")
    if market != _required_text(desired.get("market"), "connector.desired_state.market"):
        raise SourceDatasetAuthorityError("connector market identity is unstable")
    return run_id, manifest, market, health_success, health_row_count


def _validate_run(
    payload: Mapping[str, Any],
    *,
    connector_id: str,
    run_id: str,
    storage_manifest: Mapping[str, Any],
    expected_last_success_at: datetime,
    expected_normalized_count: int,
    trusted_now: datetime,
) -> dict[str, Any]:
    run = _required_mapping(payload.get("run"), "source run response.run")
    if run.get("ingest_run_id") != run_id or run.get("connector_id") != connector_id:
        raise SourceDatasetAuthorityError("source run readback identity mismatch")
    if run.get("status") != "completed":
        raise SourceDatasetAuthorityError("source run readback is not terminal completed")
    normalized_count = _strict_int(run.get("normalized_count"), "source run normalized_count", minimum=1)
    if normalized_count != expected_normalized_count:
        raise SourceDatasetAuthorityError("source health row count disagrees with completed run")
    if _strict_int(run.get("rejected_count"), "source run rejected_count", minimum=0):
        raise SourceDatasetAuthorityError("source run contains rejected rows")
    finished_at = _parse_timestamp(run.get("finished_at"), "source run finished_at")
    if finished_at != expected_last_success_at:
        raise SourceDatasetAuthorityError(
            "source health last_success_at disagrees with completed latest run"
        )
    if finished_at > trusted_now:
        raise SourceDatasetAuthorityError("source run finished_at is in the future")
    started_at = _parse_timestamp(run.get("started_at"), "source run started_at")
    if started_at > finished_at:
        raise SourceDatasetAuthorityError("source run started_at is after finished_at")
    summary = _required_mapping(storage_manifest.get("summary"), "storage manifest summary")
    manifest_created_at = _parse_timestamp(
        storage_manifest.get("created_at"), "storage manifest created_at"
    )
    if manifest_created_at < finished_at or manifest_created_at > trusted_now:
        raise SourceDatasetAuthorityError(
            "storage manifest created_at is outside the completed run boundary"
        )
    if (
        _strict_int(
            summary.get("normalized_row_count"),
            "storage manifest normalized_row_count",
            minimum=1,
        )
        != normalized_count
    ):
        raise SourceDatasetAuthorityError("storage manifest row count disagrees with completed run")
    return {
        "ingest_run_id": run_id,
        "connector_id": connector_id,
        "started_at": _iso(started_at),
        "finished_at": _iso(finished_at),
        "normalized_count": normalized_count,
        "trace_id": _required_text(run.get("trace_id"), "source run trace_id"),
    }


def _select_evidence_bundles(
    payload: Mapping[str, Any],
    *,
    connector_id: str,
    run_id: str,
    trusted_now: datetime,
) -> list[dict[str, Any]]:
    bundles = payload.get("bundles")
    if isinstance(bundles, (str, bytes)) or not isinstance(bundles, Sequence):
        raise SourceDatasetAuthorityError("source evidence bundles response must contain an array")
    matches: list[dict[str, Any]] = []
    for raw in bundles:
        if not isinstance(raw, Mapping):
            raise SourceDatasetAuthorityError("source evidence bundle must be an object")
        metadata = raw.get("metadata")
        if not isinstance(metadata, Mapping):
            continue
        if metadata.get("connector_id") != connector_id or metadata.get("ingest_run_id") != run_id:
            continue
        bundle_id = _required_text(raw.get("evidence_bundle_id"), "evidence_bundle_id")
        source_ids = _text_array(raw.get("source_ids"), "evidence_bundle.source_ids")
        item_ids = _text_array(raw.get("evidence_item_ids"), "evidence_bundle.evidence_item_ids")
        created_at = _parse_timestamp(raw.get("created_at"), "evidence_bundle.created_at")
        if created_at > trusted_now:
            raise SourceDatasetAuthorityError("source evidence bundle created_at is in the future")
        matches.append(
            {
                "evidence_bundle_id": bundle_id,
                "sha256": stable_json_sha256(raw),
                "source_ids": source_ids,
                "evidence_item_ids": item_ids,
            }
        )
    if len(matches) != 1:
        raise SourceDatasetAuthorityError(
            "exactly one source evidence bundle must bind the selected connector run"
        )
    return matches


def _validate_and_read_storage_manifest(
    manifest: Mapping[str, Any],
    *,
    source_root: Path,
    connector_id: str,
    normalized_targets: set[str],
    run_id: str,
    expected_market: str,
    trusted_now: datetime,
) -> dict[str, Any]:
    if manifest.get("schema_version") != _STORAGE_SCHEMA or manifest.get("ingest_run_id") != run_id:
        raise SourceDatasetAuthorityError("storage manifest identity mismatch")
    created_at = _parse_timestamp(manifest.get("created_at"), "storage_manifest.created_at")
    if created_at > trusted_now:
        raise SourceDatasetAuthorityError("storage manifest created_at is in the future")
    raw_refs = _storage_ref_array(manifest.get("raw_refs"), "raw_refs")
    normalized_refs = _storage_ref_array(manifest.get("normalized_refs"), "normalized_refs")
    feature_refs = _storage_ref_array(manifest.get("feature_refs"), "feature_refs")
    summary = _required_mapping(manifest.get("summary"), "storage_manifest.summary")
    for label, refs in (
        ("raw_ref_count", raw_refs),
        ("normalized_ref_count", normalized_refs),
        ("feature_ref_count", feature_refs),
    ):
        if _strict_int(summary.get(label), f"storage_manifest.summary.{label}", minimum=1) != len(refs):
            raise SourceDatasetAuthorityError(f"storage manifest {label} does not match refs")

    checked_raw: list[dict[str, Any]] = []
    checked_normalized: list[dict[str, Any]] = []
    checked_feature: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    seen_records: set[tuple[str, str]] = set()
    market: str | None = None
    total_source_rows = 0

    for label, refs, destination in (
        ("raw", raw_refs, checked_raw),
        ("normalized", normalized_refs, checked_normalized),
        ("feature", feature_refs, checked_feature),
    ):
        seen_uris: set[str] = set()
        for index, ref in enumerate(refs):
            ref_label = f"storage_manifest.{label}_refs[{index}]"
            uri = _required_text(ref.get("uri"), f"{ref_label}.uri")
            if uri in seen_uris:
                raise SourceDatasetAuthorityError(f"{ref_label}.uri is duplicated")
            seen_uris.add(uri)
            if label == "feature":
                if ref.get("source_dataset") not in normalized_targets:
                    raise SourceDatasetAuthorityError(f"{ref_label} belongs to a different source dataset")
                ref_dataset = _required_text(ref.get("dataset"), f"{ref_label}.dataset")
            else:
                if ref.get("dataset") not in normalized_targets:
                    raise SourceDatasetAuthorityError(f"{ref_label} belongs to a different dataset")
                ref_dataset = _required_text(ref.get("dataset"), f"{ref_label}.dataset")
            declared_rows = _strict_int(ref.get("row_count"), f"{ref_label}.row_count", minimum=1)
            compression = str(ref.get("compression") or "none") if label == "raw" else "none"
            if compression not in {"none", "gzip"}:
                raise SourceDatasetAuthorityError(f"{ref_label}.compression is unsupported")
            path = _resolve_source_path(uri, source_root, run_id=run_id, compression=compression)
            raw_bytes = _read_regular_file(path)
            checksum = hashlib.sha256(raw_bytes).hexdigest()
            rows = _decode_jsonl(raw_bytes, compression=compression, label=ref_label)
            total_source_rows += len(rows)
            if total_source_rows > _MAX_ROWS:
                raise SourceDatasetAuthorityError(
                    "storage manifest exceeds the bounded aggregate row limit"
                )
            if len(rows) != declared_rows:
                raise SourceDatasetAuthorityError(
                    f"{ref_label}.row_count does not match source bytes"
                )
            checked = {
                "uri": path.as_posix(),
                "sha256": checksum,
                "row_count": len(rows),
                "dataset": ref_dataset,
            }
            if label == "raw":
                checked["source"] = connector_id
                checked["compression"] = compression
            elif label == "feature":
                checked["source_dataset"] = _required_text(
                    ref.get("source_dataset"), f"{ref_label}.source_dataset"
                )
            destination.append(checked)
            if label == "normalized":
                for row_number, wrapper in enumerate(rows, start=1):
                    record, row_market = _ohlcv_from_wrapper(
                        wrapper,
                        label=f"{ref_label} line {row_number}",
                        connector_id=connector_id,
                        dataset_id=ref_dataset,
                        trusted_now=trusted_now,
                    )
                    key = (record["instrument"], record["date"])
                    if key in seen_records:
                        raise SourceDatasetAuthorityError(
                            f"normalized source contains duplicate instrument/date: {key[0]}/{key[1]}"
                        )
                    seen_records.add(key)
                    records.append(record)
                    if row_market:
                        if market is not None and market != row_market:
                            raise SourceDatasetAuthorityError("normalized rows disagree on market identity")
                        market = row_market
            elif label == "raw":
                if ref.get("source") != connector_id:
                    raise SourceDatasetAuthorityError(
                        f"{ref_label} belongs to a different source connector"
                    )
                for row_number, row in enumerate(rows, start=1):
                    if row.get("connector_id") != connector_id:
                        raise SourceDatasetAuthorityError(
                            f"{ref_label} line {row_number} belongs to a different connector"
                        )
                    metadata = _required_mapping(
                        row.get("metadata"), f"{ref_label} line {row_number}.metadata"
                    )
                    raw_dataset = metadata.get("dataset") or metadata.get("source_dataset")
                    if raw_dataset != ref_dataset:
                        raise SourceDatasetAuthorityError(
                            f"{ref_label} line {row_number} belongs to a different dataset"
                        )
            else:
                for row_number, row in enumerate(rows, start=1):
                    if row.get("connector_id") != connector_id:
                        raise SourceDatasetAuthorityError(
                            f"{ref_label} line {row_number} belongs to a different connector"
                        )
                    if row.get("source_dataset") != ref.get("source_dataset"):
                        raise SourceDatasetAuthorityError(
                            f"{ref_label} line {row_number} belongs to a different source dataset"
                        )

    if not records:
        raise SourceDatasetAuthorityError("normalized source has no OHLCV records")
    if sum(item["row_count"] for item in checked_normalized) != _strict_int(
        summary.get("normalized_row_count"),
        "storage_manifest.summary.normalized_row_count",
        minimum=1,
    ):
        raise SourceDatasetAuthorityError("storage manifest normalized row summary is inconsistent")
    if market is not None and market != expected_market:
        raise SourceDatasetAuthorityError(
            "normalized row market contradicts connector desired-state market"
        )
    return {
        "created_at": _iso(created_at),
        "raw_refs": checked_raw,
        "normalized_refs": checked_normalized,
        "feature_refs": checked_feature,
        "records": sorted(records, key=lambda item: (item["date"], item["instrument"])),
        "market": expected_market,
    }


def _ohlcv_from_wrapper(
    wrapper: Mapping[str, Any],
    *,
    label: str,
    connector_id: str,
    dataset_id: str,
    trusted_now: datetime,
) -> tuple[dict[str, Any], str | None]:
    if wrapper.get("connector_id") != connector_id or wrapper.get("dataset") != dataset_id:
        raise SourceDatasetAuthorityError(f"{label} wrapper identity mismatch")
    _required_text(wrapper.get("source_id"), f"{label}.source_id")
    _required_text(wrapper.get("content_ref"), f"{label}.content_ref")
    metadata = _required_mapping(wrapper.get("metadata"), f"{label}.metadata")
    instrument = _required_text(metadata.get("instrument"), f"{label}.metadata.instrument")
    date_value = metadata.get("date")
    trade_date = metadata.get("trade_date")
    if date_value not in (None, "") and trade_date not in (None, "") and date_value != trade_date:
        raise SourceDatasetAuthorityError(f"{label} date and trade_date disagree")
    date_text = _required_text(date_value or trade_date, f"{label}.metadata.date/trade_date")
    if _DATE_RE.fullmatch(date_text) is None:
        raise SourceDatasetAuthorityError(f"{label} date must use YYYY-MM-DD")
    try:
        bar_date = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=_UTC)
    except ValueError as exc:
        raise SourceDatasetAuthorityError(f"{label} date must use YYYY-MM-DD") from exc
    if bar_date > trusted_now:
        raise SourceDatasetAuthorityError(f"{label} contains a future bar")
    values = {
        key: _finite_number(metadata.get(key), f"{label}.metadata.{key}")
        for key in ("open", "high", "low", "close", "volume")
    }
    if any(values[key] <= 0 for key in ("open", "high", "low", "close")):
        raise SourceDatasetAuthorityError(f"{label} OHLC prices must be positive")
    if values["volume"] < 0:
        raise SourceDatasetAuthorityError(f"{label} volume must be non-negative")
    if values["high"] < max(values["open"], values["low"], values["close"]):
        raise SourceDatasetAuthorityError(f"{label} high is inconsistent with OHLC")
    if values["low"] > min(values["open"], values["high"], values["close"]):
        raise SourceDatasetAuthorityError(f"{label} low is inconsistent with OHLC")
    market_value = metadata.get("market")
    market = _required_text(market_value, f"{label}.metadata.market") if market_value not in (None, "") else None
    return {"instrument": instrument, "date": date_text, **values}, market


def _storage_ref_array(value: Any, label: str) -> list[Mapping[str, Any]]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise SourceDatasetAuthorityError(f"storage manifest {label} must be a non-empty array")
    if len(value) > _MAX_SOURCE_REFS:
        raise SourceDatasetAuthorityError(f"storage manifest {label} exceeds the bounded ref limit")
    result: list[Mapping[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise SourceDatasetAuthorityError(f"storage manifest {label}[{index}] must be an object")
        result.append(item)
    return result


def _resolve_source_path(
    uri: str,
    root: Path,
    *,
    run_id: str,
    compression: str,
) -> Path:
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme:
        if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
            raise SourceDatasetAuthorityError("source storage URI must be an absolute local path")
        if parsed.query or parsed.fragment:
            raise SourceDatasetAuthorityError("source storage URI must not contain query or fragment")
        path = Path(urllib.parse.unquote(parsed.path))
    else:
        path = Path(uri)
    if not path.is_absolute():
        raise SourceDatasetAuthorityError("source storage path must be absolute")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise SourceDatasetAuthorityError("source storage path escapes source_volume_root") from exc
    if any(part in (".", "..") for part in relative.parts):
        raise SourceDatasetAuthorityError("source storage path contains traversal")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise SourceDatasetAuthorityError("source storage path traverses a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceDatasetAuthorityError("source storage path does not exist") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SourceDatasetAuthorityError("resolved source storage path escapes source_volume_root") from exc
    if not resolved.is_file() or resolved.is_symlink():
        raise SourceDatasetAuthorityError("source storage path must be a regular non-symlink file")
    expected_name = f"{run_id}.jsonl.gz" if compression == "gzip" else f"{run_id}.jsonl"
    if resolved.name != expected_name:
        raise SourceDatasetAuthorityError("source storage ref does not belong to the selected run")
    return resolved


def _read_regular_file(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise SourceDatasetAuthorityError(f"cannot open source storage file: {path}") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SourceDatasetAuthorityError("source storage path is not a regular file")
        if info.st_size > _MAX_FILE_BYTES:
            raise SourceDatasetAuthorityError("source storage file exceeds bounded size")
        chunks: list[bytes] = []
        remaining = _MAX_FILE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _MAX_FILE_BYTES:
            raise SourceDatasetAuthorityError("source storage file exceeds bounded size")
        return data
    finally:
        os.close(fd)


def _decode_jsonl(raw_bytes: bytes, *, compression: str, label: str) -> list[Mapping[str, Any]]:
    if compression == "gzip":
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw_bytes), mode="rb") as handle:
                decoded = handle.read(_MAX_DECOMPRESSED_BYTES + 1)
        except (OSError, EOFError) as exc:
            raise SourceDatasetAuthorityError(f"{label} gzip payload is invalid") from exc
        if len(decoded) > _MAX_DECOMPRESSED_BYTES:
            raise SourceDatasetAuthorityError(f"{label} decompressed payload exceeds bounded size")
    else:
        decoded = raw_bytes
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceDatasetAuthorityError(f"{label} is not UTF-8 JSONL") from exc
    lines = text.splitlines()
    if not lines or len(lines) > _MAX_ROWS:
        raise SourceDatasetAuthorityError(f"{label} has an invalid bounded row count")
    rows: list[Mapping[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            raise SourceDatasetAuthorityError(f"{label} line {line_number} is blank")
        parsed = _strict_json_loads(line, f"{label} line {line_number}")
        if not isinstance(parsed, Mapping):
            raise SourceDatasetAuthorityError(f"{label} line {line_number} must be an object")
        rows.append(parsed)
    return rows


def _assert_run_not_rebound(
    root: Path,
    *,
    connector_id: str,
    dataset_id: str,
    run_id: str,
    expected_storage_binding_digest: str,
    expected_payload: Mapping[str, Any],
) -> None:
    for path in sorted(root.glob("dataset-version-*.json")):
        if path.is_symlink() or not path.is_file():
            raise SourceDatasetAuthorityError("existing DatasetVersion authority is not a regular file")
        try:
            existing = _strict_json_loads(path.read_text(encoding="utf-8"), f"existing {path.name}")
        except (OSError, UnicodeError) as exc:
            raise SourceDatasetAuthorityError("existing DatasetVersion authority is unreadable") from exc
        if not isinstance(existing, Mapping):
            raise SourceDatasetAuthorityError("existing DatasetVersion authority must be an object")
        filename_digest = path.stem.removeprefix("dataset-version-")
        if not _SHA256_RE.fullmatch(filename_digest) or stable_json_sha256(existing) != filename_digest:
            raise SourceDatasetAuthorityError("existing DatasetVersion filename digest is invalid")
        metadata = existing.get("metadata_json")
        if not isinstance(metadata, Mapping):
            continue
        same_run = (
            metadata.get("source_connector_id") == connector_id
            and metadata.get("source_dataset_id") == dataset_id
            and metadata.get("source_ingest_run_id") == run_id
        )
        if not same_run:
            continue
        if metadata.get("source_storage_binding_sha256") != expected_storage_binding_digest:
            raise SourceDatasetAuthorityError(
                "previously materialized source run bytes changed; refusing to rebind immutable run"
            )
        if stable_json_sha256(existing) != stable_json_sha256(expected_payload):
            raise SourceDatasetAuthorityError(
                "previously materialized source run has differing authority bindings"
            )


def _atomic_materialize(path: Path, content: bytes, root: Path) -> None:
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise SourceDatasetAuthorityError(f"materialized authority target is unsafe: {path}")
        try:
            existing = path.read_bytes()
        except OSError as exc:
            raise SourceDatasetAuthorityError(f"materialized authority target is unreadable: {path}") from exc
        if existing != content:
            raise SourceDatasetAuthorityError(
                f"refusing to overwrite differing materialized authority: {path}"
            )
        return
    temp_path = root / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(temp_path, flags, 0o600)
        try:
            view = memoryview(content)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise SourceDatasetAuthorityError("short write while materializing authority")
                view = view[written:]
            os.fsync(fd)
        finally:
            os.close(fd)
        if path.exists() or path.is_symlink():
            if path.is_symlink() or path.read_bytes() != content:
                raise SourceDatasetAuthorityError(
                    f"refusing to overwrite differing materialized authority: {path}"
                )
            temp_path.unlink()
            return
        os.replace(temp_path, path)
        directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except SourceDatasetAuthorityError:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise SourceDatasetAuthorityError(f"failed to atomically materialize authority: {path}") from exc


def _open_lock_file(path: Path) -> int:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(path, flags, 0o600)
    except OSError as exc:
        raise SourceDatasetAuthorityError("cannot open authority materialization lock") from exc


def _http_get_object(http_get: HttpGetTransport, url: str, label: str) -> Mapping[str, Any]:
    try:
        payload = http_get(url)
    except SourceDatasetAuthorityError:
        raise
    except Exception as exc:
        detail = str(exc).strip()
        suffix = f": {type(exc).__name__}: {detail}" if detail else f": {type(exc).__name__}"
        raise SourceDatasetAuthorityError(f"{label} GET failed{suffix}") from exc
    if not isinstance(payload, Mapping):
        raise SourceDatasetAuthorityError(f"{label} response must be a JSON object")
    return payload


def _normalize_api_url(value: Any) -> str:
    text = _required_text(value, "source_api_url").rstrip("/")
    parsed = urllib.parse.urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SourceDatasetAuthorityError("source_api_url must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise SourceDatasetAuthorityError("source_api_url must not contain credentials, query, or fragment")
    return text


def _existing_directory(value: str | Path, label: str) -> Path:
    path = Path(value)
    if path.is_symlink():
        raise SourceDatasetAuthorityError(f"{label} must not be a symlink")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceDatasetAuthorityError(f"{label} does not exist") from exc
    if not resolved.is_dir():
        raise SourceDatasetAuthorityError(f"{label} must be a directory")
    return resolved


def _output_directory(value: str | Path) -> Path:
    path = Path(value)
    if path.is_symlink():
        raise SourceDatasetAuthorityError("output_root must not be a symlink")
    try:
        path.mkdir(parents=True, exist_ok=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SourceDatasetAuthorityError("output_root cannot be created") from exc
    if not resolved.is_dir():
        raise SourceDatasetAuthorityError("output_root must be a directory")
    return resolved


def _canonical_json_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SourceDatasetAuthorityError("authority payload is not finite canonical JSON") from exc


def _strict_json_loads(text: str, label: str) -> Any:
    def reject_duplicates(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise SourceDatasetAuthorityError(f"{label} contains duplicate key: {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise SourceDatasetAuthorityError(f"{label} contains non-finite value: {value}")

    try:
        return json.loads(text, object_pairs_hook=reject_duplicates, parse_constant=reject_constant)
    except SourceDatasetAuthorityError:
        raise
    except json.JSONDecodeError as exc:
        raise SourceDatasetAuthorityError(f"{label} is invalid JSON") from exc


def _trusted_utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise SourceDatasetAuthorityError("trusted_now must be a timezone-aware datetime")
    return value.astimezone(_UTC)


def _parse_timestamp(value: Any, label: str) -> datetime:
    text = _required_text(value, label)
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError as exc:
        raise SourceDatasetAuthorityError(f"{label} must be an aware ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SourceDatasetAuthorityError(f"{label} must be an aware ISO 8601 timestamp")
    return parsed.astimezone(_UTC)


def _iso(value: datetime) -> str:
    return value.astimezone(_UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _required_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SourceDatasetAuthorityError(f"{label} must be an object")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SourceDatasetAuthorityError(f"{label} must be a non-empty string")
    normalized = value.strip()
    if any(character in normalized for character in ("\x00", "\n", "\r")):
        raise SourceDatasetAuthorityError(f"{label} contains a control character")
    return normalized


def _trusted_identity(value: Any, label: str) -> str:
    text = _required_text(value, label)
    if text.casefold() in _UNTRUSTED_IDENTITIES:
        raise SourceDatasetAuthorityError(f"{label} is unresolved")
    return text


def _required_digest(value: Any, label: str) -> str:
    text = _required_text(value, label)
    if _SHA256_RE.fullmatch(text) is None:
        raise SourceDatasetAuthorityError(f"{label} must be a lowercase SHA-256 digest")
    return text


def _strict_int(value: Any, label: str, *, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        bound = f" between {minimum} and {maximum}" if maximum is not None else f" at least {minimum}"
        raise SourceDatasetAuthorityError(f"{label} must be an integer{bound}")
    return value


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SourceDatasetAuthorityError(f"{label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise SourceDatasetAuthorityError(f"{label} must be finite")
    return normalized


def _text_array(value: Any, label: str) -> list[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise SourceDatasetAuthorityError(f"{label} must be a non-empty array")
    result = [_required_text(item, f"{label}[]") for item in value]
    if len(set(result)) != len(result):
        raise SourceDatasetAuthorityError(f"{label} contains duplicates")
    return result


__all__ = [
    "HttpGetTransport",
    "MaterializedDatasetVersion",
    "SourceDatasetAuthorityError",
    "materialize_source_dataset_version",
    "stable_json_sha256",
    "urllib_json_get",
]
