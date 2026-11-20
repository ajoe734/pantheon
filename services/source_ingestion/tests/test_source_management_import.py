"""Tests for configured connector import into SourceManagementStore (SD-SRCM § 10.1).

Covers:
1. Step 2 code definition projection, adapter allowlist, and catalog consistency.
2. Step 3 import of actual configured connectors only (39 hosted-shaped connectors;
   no catalog-only definitions or test fixtures).
3. Draft-07 JSON Schema validation for every imported DataSourceEntryV2,
   SourceDesiredState, SourceManagementReceipt, and SourceObservedState.
4. Step 4 schedule and non-secret public config import + inline secret redaction
   and rejection modes.
5. Step 5 observed health, freshness, watermark, last_run, and DLQ capture with
   source="legacy_projection".
6. Repeatability and idempotency (second run changes nothing, checksum identical).
7. Step 8 zero mutation of connector_store and schedule_config_store before/after.
8. Guarded startup execution on SourceIngestionRuntime and one-shot CLI entrypoint.
9. Runtime budget bounding of old observed snapshots in source_management_store.py.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.source_ingestion.configured import (
    JsonlConfiguredConnectorStore,
    JsonlConnectorScheduleStore,
)
from services.source_ingestion.connectors import (
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    SourceConnector,
    SourceType,
)
from services.source_ingestion.controller_worker import load_desired_state
from services.source_ingestion.persona_source_reconciler import (
    SourceProvisioningReconciler,
)
from services.source_ingestion.routers.management import create_management_router
from services.source_ingestion.runtime import SourceIngestionRuntime
from services.source_ingestion.scheduler import (
    IngestReceipt,
    IngestRun,
    IngestRunStatus,
    JsonlIngestScheduleStore,
    SourceWatermark,
)
from services.source_ingestion.source_management_import import (
    LEGACY_PROJECTION_SOURCE,
    import_configured_connectors,
    main as import_cli_main,
    verify_definitions_and_catalog_consistency,
)
from services.source_ingestion.source_management_models import (
    CanaryState,
    CredentialState,
    DesiredLifecycleState,
    EffectiveLifecycleState,
    HealthState,
    ReconciliationStatus,
    SourceObservedState,
    ValidationState,
)
from services.source_ingestion.source_management_store import (
    JsonlSourceManagementStore,
    PostgresSourceManagementStore,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACTS_DIR = REPO_ROOT / "docs" / "contracts"


def _load_contract_validator(schema_filename: str) -> jsonschema.Draft7Validator:
    schema_path = CONTRACTS_DIR / schema_filename
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    jsonschema.Draft7Validator.check_schema(schema)
    return jsonschema.Draft7Validator(schema)


def _populate_hosted_39_configured_connectors(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore, JsonlIngestScheduleStore]:
    """Populate the exact 39 hosted-shaped configured connectors and schedules."""
    monkeypatch.setenv("PANTHEON_COLLECTOR_HOST", "127.0.0.1:19999")
    monkeypatch.setenv("PANTHEON_COLLECTOR_BUNDLE_TOKEN", "test-collector-bundle-token")
    monkeypatch.setenv("FINMIND_API_TOKEN", "test-finmind-token-from-env")

    connector_store = JsonlConfiguredConnectorStore(data_dir / "connector_config.jsonl")
    schedule_config_store = JsonlConnectorScheduleStore(data_dir / "connector_schedule.jsonl")
    schedule_store = JsonlIngestScheduleStore(data_dir / "ingest_schedule.jsonl")

    personas, _meta = load_desired_state()
    from services.source_ingestion.ingest_manager import IngestManager

    reconciler = SourceProvisioningReconciler(
        manager=IngestManager(),
        connector_store=connector_store,
        schedule_store=schedule_config_store,
    )
    reconciler.reconcile_personas(personas)

    # Add the remaining 8 configured connectors present on hosted dev (total = 31 + 8 = 39):
    # 1. agora-platform-source (enabled)
    # 2. collector-library-books (held disabled by reconcile)
    # 3. tw-taifex-futures-options-chip (enabled)
    # 4-8. 5 hosted simulation/E2E connectors from hosted_registry_20261112.json
    extra_connectors: list[tuple[SourceConnector, dict[str, Any], int, bool]] = [
        (
            SourceConnector(
                connector_id="agora-platform-source",
                provider="pantheon-agora",
                source_type=SourceType.SOCIAL,
                license_scope="internal_platform",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.ENABLED,
                metadata={"owner": "persona_source_reconciler", "market": "GLOBAL"},
            ),
            {
                "mode": "provider_owned_adapter",
                "adapter": "AgoraOwnerAdapter",
                "adapter_config": {"base_url": "http://agora-service:8095"},
            },
            300,
            True,
        ),
        (
            SourceConnector(
                connector_id="collector-library-books",
                provider="collector-bundle",
                source_type=SourceType.NEWS,
                license_scope="collector_public_web",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.DISABLED,
                metadata={"owner": "persona_source_reconciler", "held_reason": "corpus_hold"},
            ),
            {
                "mode": "provider_owned_adapter",
                "adapter": "CollectorBundleAdapter",
                "adapter_config": {"host": "http://127.0.0.1:19999", "source_id": "library-books"},
            },
            86400,
            False,
        ),
        (
            SourceConnector(
                connector_id="dev-paper-us-equity-simulation",
                provider="dev-paper-sim",
                source_type=SourceType.MARKET,
                license_scope="internal_simulation",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.ENABLED,
                metadata={"environment": "dev", "purpose": "paper_simulation"},
            ),
            {"mode": "static_records", "records": []},
            300,
            True,
        ),
        (
            SourceConnector(
                connector_id="conn-dev-product-20261110t102427z",
                provider="finmind",
                source_type=SourceType.MARKET,
                license_scope="finmind_public",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.ENABLED,
                metadata={"environment": "dev"},
            ),
            {"mode": "static_records", "records": []},
            300,
            False,
        ),
        (
            SourceConnector(
                connector_id="conn-dev-product-20261110t105239z",
                provider="finmind",
                source_type=SourceType.MARKET,
                license_scope="finmind_public",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.ENABLED,
                metadata={"environment": "dev"},
            ),
            {"mode": "static_records", "records": []},
            300,
            False,
        ),
        (
            SourceConnector(
                connector_id="hosted-all12-20261108t034716z",
                provider="finmind",
                source_type=SourceType.MARKET,
                license_scope="finmind_public",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.ENABLED,
                metadata={"environment": "dev"},
            ),
            {"mode": "static_records", "records": []},
            300,
            False,
        ),
        (
            SourceConnector(
                connector_id="hosted-all12-20261108t035428z",
                provider="finmind",
                source_type=SourceType.MARKET,
                license_scope="finmind_public",
                auth_type=AuthType.NONE,
                supported_modes=(ConnectorMode.BATCH,),
                status=ConnectorStatus.ENABLED,
                metadata={"environment": "dev"},
            ),
            {"mode": "static_records", "records": []},
            300,
            False,
        ),
    ]

    for conn, fetch_cfg, interval_sec, sched_enabled in extra_connectors:
        meta = dict(conn.metadata)
        meta.setdefault("entitlement_tags", ["internal_research"])
        object.__setattr__(conn, "metadata", meta)
        connector_store.upsert_config(conn, fetch_cfg)
        schedule_config_store.upsert_schedule(
            conn.connector_id,
            interval_seconds=interval_sec,
            enabled=sched_enabled,
        )

    # Seed sample runs, receipts, and watermarks in schedule_store for observed health/watermark projection
    configs, _ = connector_store.read_snapshot()
    assert len(configs) == 39
    for idx, cfg in enumerate(sorted(configs, key=lambda c: c.connector.connector_id)[:10]):
        cid = cfg.connector.connector_id
        run_id = f"run-import-seed-{idx:02d}"
        ts = "2026-11-20T02:30:00Z"
        wm_val = f"2026-11-20T02:30:00Z::{cid}"
        run = IngestRun.from_dict(
            {
                "ingest_run_id": run_id,
                "connector_id": cid,
                "source_type": cfg.connector.source_type.value,
                "trigger_type": "scheduled",
                "status": IngestRunStatus.COMPLETED.value,
                "started_at": "2026-11-20T02:29:50Z",
                "finished_at": ts,
                "raw_count": 25,
                "normalized_count": 25,
                "rejected_count": 0,
                "trace_id": f"trace-{run_id}",
            }
        )
        schedule_store.upsert_run(run)
        schedule_store.upsert_receipt(
            IngestReceipt(
                ingest_run_id=run_id,
                connector_id=cid,
                status="completed",
                trigger_type="scheduled",
                trace_id=f"trace-{run_id}",
                started_at="2026-11-20T02:29:50Z",
                finished_at=ts,
                raw_count=25,
                normalized_count=25,
                rejected_count=0,
                watermark=wm_val,
                source_timestamp=ts,
                source_timestamp_status="valid",
            )
        )
        schedule_store.update_watermark(
            SourceWatermark(
                connector_id=cid,
                source_type=cfg.connector.source_type,
                value=wm_val,
                updated_at=ts,
                last_ingest_run_id=run_id,
            )
        )

    return connector_store, schedule_config_store, schedule_store


def test_verify_definitions_and_catalog_consistency() -> None:
    """SD-SRCM § 10.1 Step 2: verify all 58 code definitions and catalog consistency."""
    result = verify_definitions_and_catalog_consistency()
    assert result["definition_count"] == 58
    assert len(result["definitions_fingerprint"]) == 64
    assert result["adapter_count"] >= 20
    assert result["catalog_template_count"] > 0


def test_import_hosted_shaped_39_configured_connectors_only_and_schema_compliance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SD-SRCM § 10.1 Steps 2-5: import 39 configured connectors, skip catalog-only, validate schemas."""
    connector_store, schedule_config_store, schedule_store = _populate_hosted_39_configured_connectors(
        tmp_path, monkeypatch
    )
    management_store = JsonlSourceManagementStore(tmp_path)

    report = import_configured_connectors(
        management_store=management_store,
        connector_store=connector_store,
        schedule_config_store=schedule_config_store,
        schedule_store=schedule_store,
        deployment_sha="9d463652c25e05a834055ddf2e1dec7e4dd7f22f",
        now_iso="2026-11-20T03:00:00Z",
    )

    assert report.definition_count == 58
    assert report.configured_connector_count == 39
    assert report.imported_count == 39
    assert report.already_present_count == 0
    assert len(report.imported_ids) == 39
    assert len(report.imported_instance_ids) == 39
    assert report.redaction_count == 0
    assert len(report.checksum) == 64

    # Catalog-only definitions (from the 58 definitions not among the 39 configured) and example-alpha-db
    # must be recorded as skipped and NEVER imported into management_store.
    skipped_ids = {entry["id"] for entry in report.skipped_entries}
    assert "example-alpha-db" in skipped_ids
    assert "tw-anue-news-rss" in skipped_ids
    assert "crypto-coingecko-spot" in skipped_ids
    assert management_store.get_instance_by_connector_id("example-alpha-db") is None
    assert management_store.get_instance_by_connector_id("tw-anue-news-rss") is None

    # Validate every imported instance, desired state, receipt, and observed state against JSON contracts
    entry_v2_validator = _load_contract_validator("data_source_registry_entry.v2.schema.json")
    desired_validator = _load_contract_validator("source_desired_state.schema.json")
    receipt_validator = _load_contract_validator("source_management_receipt.schema.json")
    observed_validator = _load_contract_validator("source_observed_state.schema.json")

    instances = management_store.list_instances()
    assert len(instances) == 39

    seeded_watermark_count = 0
    for inst in instances:
        entry_v2_validator.validate(inst.to_dict())
        assert inst.metadata["source"] == LEGACY_PROJECTION_SOURCE

        desired = management_store.get_desired_state(inst.data_source_id)
        assert desired is not None
        desired_validator.validate(desired.to_dict())
        assert desired.metadata["source"] == LEGACY_PROJECTION_SOURCE

        observed = management_store.get_latest_observed_snapshot(inst.data_source_id)
        assert observed is not None
        observed_validator.validate(observed.to_dict())
        assert observed.quota["source"] == LEGACY_PROJECTION_SOURCE
        assert observed.usage["source"] == LEGACY_PROJECTION_SOURCE
        if observed.freshness.get("watermark"):
            seeded_watermark_count += 1
            if desired.desired_lifecycle == DesiredLifecycleState.ENABLED:
                assert observed.health_state == HealthState.HEALTHY
            assert observed.last_run["row_count"] == 25

        receipts = management_store.list_receipts(source_instance_id=inst.data_source_id)
        assert len(receipts) == 1
        receipt_validator.validate(receipts[0].to_dict())
        assert receipts[0].readback["source"] == LEGACY_PROJECTION_SOURCE

    assert seeded_watermark_count == 10

    # Verify GET /api/source-ingest/management/sources lists all 39 rows
    runtime = SourceIngestionRuntime(data_dir=tmp_path)
    app = FastAPI()
    app.include_router(create_management_router(runtime))
    client = TestClient(app)
    resp = client.get("/api/source-ingest/management/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 39
    assert len(body["sources"]) == 39
    for row in body["sources"]:
        assert row["data_source_id"].startswith("src-")
        assert row["connector_id"] in report.imported_ids
        assert row["definition_id"]

    detail_resp = client.get(f"/api/source-ingest/management/sources/{body['sources'][0]['data_source_id']}")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    assert detail_body["source"] is not None
    assert detail_body["desired"] is not None
    assert detail_body["observed"] is not None


def test_import_is_repeatable_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running import a second time changes nothing and produces the exact same checksum."""
    connector_store, schedule_config_store, schedule_store = _populate_hosted_39_configured_connectors(
        tmp_path, monkeypatch
    )
    management_store = JsonlSourceManagementStore(tmp_path)

    first = import_configured_connectors(
        management_store=management_store,
        connector_store=connector_store,
        schedule_config_store=schedule_config_store,
        schedule_store=schedule_store,
        deployment_sha="9d463652c25e05a834055ddf2e1dec7e4dd7f22f",
        now_iso="2026-11-20T03:00:00Z",
    )
    assert first.imported_count == 39
    assert first.already_present_count == 0

    instances_bytes_before = (tmp_path / "data_source_instances.jsonl").read_bytes()
    desired_bytes_before = (tmp_path / "source_desired_states.jsonl").read_bytes()
    receipts_bytes_before = (tmp_path / "source_command_receipts.jsonl").read_bytes()
    observed_bytes_before = (tmp_path / "source_observed_snapshots.jsonl").read_bytes()

    second = import_configured_connectors(
        management_store=management_store,
        connector_store=connector_store,
        schedule_config_store=schedule_config_store,
        schedule_store=schedule_store,
        deployment_sha="9d463652c25e05a834055ddf2e1dec7e4dd7f22f",
        now_iso="2026-11-20T03:05:00Z",
    )
    assert second.imported_count == 0
    assert second.already_present_count == 39
    assert second.checksum == first.checksum

    assert (tmp_path / "data_source_instances.jsonl").read_bytes() == instances_bytes_before
    assert (tmp_path / "source_desired_states.jsonl").read_bytes() == desired_bytes_before
    assert (tmp_path / "source_command_receipts.jsonl").read_bytes() == receipts_bytes_before
    assert (tmp_path / "source_observed_snapshots.jsonl").read_bytes() == observed_bytes_before


def test_import_does_not_mutate_connector_or_schedule_stores(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SD-SRCM § 10.1 Step 8 & Acceptance 3: compare connector and schedule stores before and after."""
    connector_store, schedule_config_store, schedule_store = _populate_hosted_39_configured_connectors(
        tmp_path, monkeypatch
    )
    management_store = JsonlSourceManagementStore(tmp_path)

    connector_file = tmp_path / "connector_config.jsonl"
    schedule_file = tmp_path / "connector_schedule.jsonl"

    connector_bytes_before = connector_file.read_bytes()
    schedule_bytes_before = schedule_file.read_bytes()
    connector_sha_before = hashlib.sha256(connector_bytes_before).hexdigest()
    schedule_sha_before = hashlib.sha256(schedule_bytes_before).hexdigest()

    configs_before, states_before = connector_store.read_snapshot()
    schedules_before = [s.to_dict() for s in schedule_config_store.list_schedules()]

    report = import_configured_connectors(
        management_store=management_store,
        connector_store=connector_store,
        schedule_config_store=schedule_config_store,
        schedule_store=schedule_store,
    )
    assert report.imported_count == 39

    connector_bytes_after = connector_file.read_bytes()
    schedule_bytes_after = schedule_file.read_bytes()
    assert hashlib.sha256(connector_bytes_after).hexdigest() == connector_sha_before
    assert hashlib.sha256(schedule_bytes_after).hexdigest() == schedule_sha_before

    configs_after, states_after = connector_store.read_snapshot()
    schedules_after = [s.to_dict() for s in schedule_config_store.list_schedules()]
    assert [c.to_dict() for c in configs_after] == [c.to_dict() for c in configs_before]
    assert states_after == states_before
    assert schedules_after == schedules_before


def test_inline_secret_redaction_and_rejection_modes(tmp_path: Path) -> None:
    """SD-SRCM § 10.1 Step 4: inline secrets are redacted or rejected and never persisted in public config."""
    from services.source_ingestion.configured import ConfiguredConnector

    schedule_config_store = JsonlConnectorScheduleStore(tmp_path / "connector_schedule.jsonl")

    raw_secret_1 = "sk-proj-superSecretInlineTokenValue123456"
    raw_secret_2 = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secretPayload"

    conn_with_secret = SourceConnector(
        connector_id="tw-finmind-datasets",
        provider="finmind",
        source_type=SourceType.MARKET,
        license_scope="finmind_public",
        auth_type=AuthType.NONE,
        supported_modes=(ConnectorMode.BATCH,),
        status=ConnectorStatus.ENABLED,
        metadata={"market": "TW"},
    )
    # Simulate legacy unvalidated metadata containing both a secret key and a Bearer token value
    object.__setattr__(
        conn_with_secret,
        "metadata",
        {
            "client_secret": raw_secret_1,
            "note": raw_secret_2,
            "market": "TW",
        },
    )
    legacy_connector_map = {
        "tw-finmind-datasets": ConfiguredConnector(
            connector=conn_with_secret,
            fetch={
                "mode": "provider_owned_adapter",
                "adapter": "FinMindTaiwanDatasetAdapter",
                "adapter_config": {"dataset": "TaiwanStockPrice", "token_env": "FINMIND_API_TOKEN"},
            },
            updated_at="2026-11-20T02:00:00Z",
        )
    }
    schedule_config_store.upsert_schedule("tw-finmind-datasets", interval_seconds=300, enabled=True)

    # 1. Redact mode: strips inline secrets, records redactions + conflicts, downgrades to configured_disabled
    redact_dir = tmp_path / "redact_store"
    redact_store = JsonlSourceManagementStore(redact_dir)
    redact_report = import_configured_connectors(
        management_store=redact_store,
        connector_store=legacy_connector_map,
        schedule_config_store=schedule_config_store,
        on_inline_secret="redact",
    )
    assert redact_report.imported_count == 1
    assert redact_report.redaction_count == 2
    assert redact_report.conflict_count == 2

    # Ensure raw secret values never appear anywhere in report or persisted store files
    serialized_report = json.dumps(redact_report.to_dict())
    assert raw_secret_1 not in serialized_report
    assert raw_secret_2 not in serialized_report
    for jsonl_file in redact_dir.glob("*.jsonl"):
        content = jsonl_file.read_text(encoding="utf-8")
        assert raw_secret_1 not in content
        assert raw_secret_2 not in content

    inst = redact_store.get_instance_by_connector_id("tw-finmind-datasets")
    assert inst is not None
    assert inst.lifecycle_state.value == "configured_disabled"
    assert inst.metadata["credential_state"] == CredentialState.CREDENTIAL_UNAVAILABLE.value
    desired = redact_store.get_desired_state(inst.data_source_id)
    assert desired is not None
    assert desired.desired_lifecycle == DesiredLifecycleState.CONFIGURED_DISABLED
    assert desired.schedule["enabled"] is False
    assert "client_secret" not in desired.connector_config["public"].get("connector_metadata", {})
    assert "note" not in desired.connector_config["public"].get("connector_metadata", {})

    # 2. Reject mode: rejects the connector instance with inline secret and skips creation
    reject_dir = tmp_path / "reject_store"
    reject_store = JsonlSourceManagementStore(reject_dir)
    reject_report = import_configured_connectors(
        management_store=reject_store,
        connector_store=legacy_connector_map,
        schedule_config_store=schedule_config_store,
        on_inline_secret="reject",
    )
    assert reject_report.imported_count == 0
    assert reject_report.redaction_count == 2
    assert any(
        item["id"] == "tw-finmind-datasets" and item["reason"] == "inline_secret_rejected"
        for item in reject_report.skipped_entries
    )
    assert reject_store.get_instance_by_connector_id("tw-finmind-datasets") is None


def test_runtime_startup_import_guard_and_cli_entrypoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Acceptance 4: SourceIngestionRuntime runs guarded import at startup and CLI entrypoint works."""
    _populate_hosted_39_configured_connectors(tmp_path, monkeypatch)

    runtime = SourceIngestionRuntime(data_dir=tmp_path)
    instances = runtime.source_management_store.list_instances()
    assert len(instances) == 39
    assert runtime._last_import_report is not None
    assert runtime._last_import_report.imported_count == 39

    # Calling import_configured_connectors_if_changed again when nothing changed is a guarded no-op
    first_report = runtime._last_import_report
    second_report = runtime.import_configured_connectors_if_changed()
    assert second_report is first_report

    # CLI one-shot invocation works against the same directory and is idempotent
    report_out = tmp_path / "cli_import_report.json"
    rc = import_cli_main(["--data-dir", str(tmp_path), "--output", str(report_out)])
    assert rc == 0
    cli_payload = json.loads(report_out.read_text(encoding="utf-8"))
    assert cli_payload["configured_connector_count"] == 39
    assert cli_payload["imported_count"] == 0
    assert cli_payload["already_present_count"] == 39
    assert cli_payload["checksum"] == first_report.checksum


def test_observed_snapshot_cap_bounds_storage_in_source_management_store(tmp_path: Path) -> None:
    """Acceptance 7: old observed snapshots are bounded per instance in source_management_store.py."""
    store = JsonlSourceManagementStore(tmp_path, max_observed_snapshots_per_instance=5)
    instance_id = "src-tw-twse-tpex-official-market"

    for rev in range(1, 15):
        obs = SourceObservedState(
            source_instance_id=instance_id,
            desired_revision=1,
            observed_revision=rev,
            reconciliation_status=ReconciliationStatus.CONVERGED,
            effective_lifecycle=EffectiveLifecycleState.ENABLED,
            definition={
                "definition_id": "tw-twse-tpex-official-market",
                "deployment_sha": "9d463652c25e05a834055ddf2e1dec7e4dd7f22f",
                "state": "supported",
            },
            credential_state=CredentialState.NOT_REQUIRED,
            validation_state=ValidationState.PASSED,
            canary_state=CanaryState.PASSED,
            health_state=HealthState.HEALTHY,
            freshness={
                "last_success_at": "2026-11-20T03:00:00Z",
                "watermark": f"wm-{rev}",
                "age_seconds": 10,
                "sla_seconds": 3600,
            },
            last_run={
                "ingest_run_id": f"run-{rev}",
                "row_count": 10,
                "rejected_count": 0,
                "evidence_bundle_id": None,
                "search_snapshot_id": None,
                "started_at": "2026-11-20T02:59:50Z",
                "completed_at": "2026-11-20T03:00:00Z",
            },
            dlq_unresolved_count=0,
            quota={"source": LEGACY_PROJECTION_SOURCE},
            usage={"source": LEGACY_PROJECTION_SOURCE},
            observed_at="2026-11-20T03:00:00Z",
        )
        store.save_observed_snapshot(obs)

    snapshots = store.list_observed_snapshots(instance_id, limit=100)
    assert len(snapshots) == 5
    assert [s.observed_revision for s in snapshots] == [14, 13, 12, 11, 10]

    # File on disk also contains only 5 lines (pruned atomically)
    observed_lines = [
        line
        for line in (tmp_path / "source_observed_snapshots.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(observed_lines) == 5

    # Saving the exact same snapshot revision again does not append a duplicate line
    store.save_observed_snapshot(snapshots[0])
    observed_lines_after = [
        line
        for line in (tmp_path / "source_observed_snapshots.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(observed_lines_after) == 5

    # Verify PostgresSourceManagementStore also configures max_observed_snapshots_per_instance
    pg_store = PostgresSourceManagementStore("postgresql://localhost/test", bootstrap=False, max_observed_snapshots_per_instance=7)
    assert pg_store.max_observed_snapshots_per_instance == 7


def test_preserve_verified_desired_state_false_and_disabled_by_build_conflict(
    tmp_path: Path,
) -> None:
    """Acceptance 2: configured_disabled-only mode and disabled_by_build conflict handling."""
    from services.source_ingestion.configured import ConfiguredConnector
    from services.source_ingestion.connector_definitions import DefinitionState, list_connector_definitions

    disabled_defs = [
        d for d in list_connector_definitions() if d.definition_state == DefinitionState.DISABLED_BY_BUILD
    ]
    disabled_def_id = disabled_defs[0].definition_id if disabled_defs else "us-polygon-market-data"

    conn_enabled_on_disabled_def = SourceConnector(
        connector_id=disabled_def_id,
        provider="test-provider",
        source_type=SourceType.MARKET,
        license_scope="internal_research",
        auth_type=AuthType.NONE,
        supported_modes=(ConnectorMode.BATCH,),
        status=ConnectorStatus.ENABLED,
        metadata={"entitlement_tags": ["internal_research"], "market": "US"},
    )
    conn_normal_enabled = SourceConnector(
        connector_id="tw-twse-tpex-official-market",
        provider="twse-tpex",
        source_type=SourceType.MARKET,
        license_scope="taiwan_official_public",
        auth_type=AuthType.NONE,
        supported_modes=(ConnectorMode.BATCH,),
        status=ConnectorStatus.ENABLED,
        metadata={"entitlement_tags": ["internal_research"], "market": "TW"},
    )

    connector_map = {
        disabled_def_id: ConfiguredConnector(
            connector=conn_enabled_on_disabled_def,
            fetch={"mode": "static_records", "records": []},
            updated_at="2026-11-20T03:00:00Z",
        ),
        "tw-twse-tpex-official-market": ConfiguredConnector(
            connector=conn_normal_enabled,
            fetch={"mode": "static_records", "records": []},
            updated_at="2026-11-20T03:00:00Z",
        ),
    }

    # Mode A: preserve_verified_desired_state=True still downgrades disabled_by_build to configured_disabled
    store_a = JsonlSourceManagementStore(tmp_path / "store_a")
    report_a = import_configured_connectors(
        management_store=store_a,
        connector_store=connector_map,
        schedule_config_store={},
        preserve_verified_desired_state=True,
    )
    assert report_a.imported_count == 2
    if disabled_defs:
        assert any(
            c["connector_id"] == disabled_def_id
            and c["conflict_type"] == "enabled_connector_on_disabled_by_build_definition"
            for c in report_a.conflicts
        )
        inst_disabled_def = store_a.get_instance_by_connector_id(disabled_def_id)
        assert inst_disabled_def is not None
        assert inst_disabled_def.lifecycle_state.value == "configured_disabled"

    inst_normal_a = store_a.get_instance_by_connector_id("tw-twse-tpex-official-market")
    assert inst_normal_a is not None
    assert inst_normal_a.lifecycle_state.value == "enabled"

    # Mode B: preserve_verified_desired_state=False imports all as configured_disabled
    store_b = JsonlSourceManagementStore(tmp_path / "store_b")
    report_b = import_configured_connectors(
        management_store=store_b,
        connector_store=connector_map,
        schedule_config_store={},
        preserve_verified_desired_state=False,
    )
    assert report_b.imported_count == 2
    for inst in store_b.list_instances():
        assert inst.lifecycle_state.value == "configured_disabled"
        des = store_b.get_desired_state(inst.data_source_id)
        assert des is not None
        assert des.desired_lifecycle == DesiredLifecycleState.CONFIGURED_DISABLED
        assert des.schedule["enabled"] is False
