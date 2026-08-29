from __future__ import annotations

import copy
import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest


SERVICE_DIR = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
BASE_URL = "http://source-ingest.test:8097"
CONNECTOR_ID = "crypto-authoritative-ohlcv"
DATASET_ID = "ds-authoritative-crypto-spot"
DESIRED_DATASET_ID = "crypto_spot_daily_and_price"
NORMALIZED_DATASET_ID = "crypto_spot_daily"
RUN_ID = "ingest-authority-001"
DESIRED_SHA = "a" * 64


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, SERVICE_DIR / "source_dataset_authority.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


AUTHORITY = _load_module("training_session_source_dataset_authority_test")
SourceDatasetAuthorityError = AUTHORITY.SourceDatasetAuthorityError


@dataclass
class AuthorityCase:
    source_root: Path
    output_root: Path
    responses: dict[str, dict[str, Any]]
    raw_path: Path
    normalized_path: Path
    feature_path: Path
    calls: list[str]

    def get(self, url: str) -> dict[str, Any]:
        self.calls.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected GET {url}")
        return copy.deepcopy(self.responses[url])


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _ohlcv_wrappers() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    first_date = date(2026, 6, 16)
    for offset in range(30):
        trade_date = (first_date + timedelta(days=offset)).isoformat()
        for instrument, base in (("BTCUSD", 60_000.0), ("ETHUSD", 3_000.0)):
            open_price = base + offset
            rows.append(
                {
                    "source_id": f"source-{instrument}-{trade_date}",
                    "connector_id": CONNECTOR_ID,
                    "dataset": NORMALIZED_DATASET_ID,
                    "content_ref": f"source://{instrument}/{trade_date}",
                    "metadata": {
                        "instrument": instrument,
                        "trade_date": trade_date,
                        "market": "CRYPTO_SPOT",
                        "open": open_price,
                        "high": open_price + 10.0,
                        "low": open_price - 10.0,
                        "close": open_price + 1.0,
                        "volume": 1_000.0 + offset,
                    },
                }
            )
    return rows


def _make_case(tmp_path: Path) -> AuthorityCase:
    source_root = tmp_path / "source-volume"
    output_root = tmp_path / "authority"
    raw_path = source_root / "raw" / CONNECTOR_ID / NORMALIZED_DATASET_ID / "date=2026-07-15" / f"{RUN_ID}.jsonl"
    normalized_path = source_root / "normalized" / NORMALIZED_DATASET_ID / "date=2026-07-15" / f"{RUN_ID}.jsonl"
    feature_path = source_root / "features" / "returns" / "date=2026-07-15" / f"{RUN_ID}.jsonl"
    wrappers = _ohlcv_wrappers()
    raw_rows = [
        {
            "source_id": row["source_id"],
            "connector_id": CONNECTOR_ID,
            "source_type": "market",
            "title": row["source_id"],
            "content_ref": row["content_ref"],
            "status": "normalized",
            "metadata": {**row["metadata"], "dataset": NORMALIZED_DATASET_ID},
            "trace_id": "trace-authority-001",
            "created_at": "2026-07-15T11:58:30Z",
        }
        for row in wrappers
    ]
    feature_rows = [
        {
            "source_id": row["source_id"],
            "connector_id": CONNECTOR_ID,
            "source_dataset": NORMALIZED_DATASET_ID,
            "feature_dataset": "returns",
            "feature_as_of_time": row["metadata"]["trade_date"],
        }
        for row in wrappers
    ]
    _write_jsonl(raw_path, raw_rows)
    _write_jsonl(normalized_path, wrappers)
    _write_jsonl(feature_path, feature_rows)

    storage_manifest = {
        "schema_version": "market_data_storage_manifest.v1",
        "ingest_run_id": RUN_ID,
        "created_at": "2026-07-15T11:59:30Z",
        "raw_refs": [
            {
                "ref_type": "raw_object",
                "source": CONNECTOR_ID,
                "dataset": NORMALIZED_DATASET_ID,
                "date": "2026-07-15",
                "uri": raw_path.as_posix(),
                "row_count": len(wrappers),
                "compression": "none",
            }
        ],
        "normalized_refs": [
            {
                "ref_type": "normalized_rows",
                "dataset": NORMALIZED_DATASET_ID,
                "date": "2026-07-15",
                "uri": normalized_path.as_posix(),
                "row_count": len(wrappers),
                "schema_hash": "canonical-ohlcv-wrapper.v1",
            }
        ],
        "feature_refs": [
            {
                "ref_type": "feature_rows",
                "dataset": "returns",
                "source_dataset": NORMALIZED_DATASET_ID,
                "date": "2026-07-15",
                "uri": feature_path.as_posix(),
                "row_count": len(wrappers),
                "feature_as_of_time": "2026-07-15",
            }
        ],
        "summary": {
            "raw_ref_count": 1,
            "normalized_ref_count": 1,
            "feature_ref_count": 1,
            "normalized_row_count": len(wrappers),
        },
    }
    readback = {
        "schema_version": "source_ingest_controller_readback.v1",
        "captured_at": "2026-07-15T11:59:59Z",
        "controller_state": {
            "schema_version": "source_ingest_controller_state.v1",
            "controller_id": "source-ingestion:prod",
            "controller_name": "source-ingestion-controller",
            "environment": "dev",
            "tenant_id": "tenant-pantheon",
            "sequence_no": 42,
            "deployment": {
                "git_sha": "abc123def456",
                "image_digest": "sha256:image-authority",
                "build_time": "2026-07-15T08:00:00Z",
                "deployment_id": "source-ingest-dev-20260715",
                "runtime_instance_id": "source-ingest-instance-001",
                "identity_complete": True,
            },
        },
        "requirement_snapshot": {
            "schema_version": "source_ingest_requirement_snapshot.v1",
            "sequence": 7,
            "desired_state_sha256": DESIRED_SHA,
            "bindings": {"requirement-crypto-daily": CONNECTOR_ID},
            "binding_count": 1,
            "persona_count": 1,
            "authority": "deployment://source-ingest/persona-requirements",
            "authoritative": True,
        },
        "connector_count": 1,
        "source_record_count": len(wrappers),
        "dlq_count": 0,
        "pending_dlq_count": 0,
        "unresolved_dlq_count": 0,
        "dlq_status_counts": {
            "pending": 0,
            "replayed": 0,
            "duplicate_skipped": 0,
            "replay_failed": 0,
            "schema_rejected": 0,
        },
        "frontier_backlog": 0,
        "max_lag_seconds": 1,
        "connectors": [
            {
                "connector_id": CONNECTOR_ID,
                "configured": True,
                "connector": {
                    "schema_version": "source_connector.v2",
                    "connector_id": CONNECTOR_ID,
                    "status": "enabled",
                    "metadata": {"normalized_datasets": [NORMALIZED_DATASET_ID]},
                },
                "desired_state": {
                    "dataset": DESIRED_DATASET_ID,
                    "market": "CRYPTO_SPOT",
                    "cadence": "daily",
                    "source_class": "live_pull",
                },
                "desired_state_sha256": DESIRED_SHA,
                "schedule": {
                    "connector_id": CONNECTOR_ID,
                    "enabled": True,
                    "interval_seconds": 86_400,
                },
                "freshness": {
                    "status": "fresh",
                    "is_due": False,
                    "staleness_seconds": 1,
                    "last_ingest_run_id": RUN_ID,
                    "latest_run": {"ingest_run_id": RUN_ID, "status": "completed"},
                },
                "latest_source_record": {
                    "source_id": wrappers[-1]["source_id"],
                    "connector_id": CONNECTOR_ID,
                    "status": "normalized",
                    "content_ref": wrappers[-1]["content_ref"],
                    "trace_id": "trace-authority-001",
                    "created_at": "2026-07-15T11:58:30Z",
                    "provenance": {
                        "provider": "authoritative-market-provider",
                        "dataset": NORMALIZED_DATASET_ID,
                        "available_time": "2026-07-15T11:58:00Z",
                        "api_endpoint": "https://market.example.test/ohlcv",
                        "access_scope": "public",
                        "license_scope": "open",
                        "schema_hash": "canonical-ohlcv-wrapper.v1",
                        "source_ingest_run_id": RUN_ID,
                    },
                },
                "source_health": {
                    "source_id": CONNECTOR_ID,
                    "status": "ok",
                    "last_success_at": "2026-07-15T11:59:00Z",
                    "row_count_last_run": len(wrappers),
                    "rejected_count_last_run": 0,
                    "metadata": {
                        "last_ingest_run_id": RUN_ID,
                        "last_run_status": "completed",
                        "storage_refs": storage_manifest,
                    },
                },
            }
        ],
    }
    run = {
        "run": {
            "ingest_run_id": RUN_ID,
            "connector_id": CONNECTOR_ID,
            "source_type": "market",
            "trigger_type": "scheduled",
            "status": "completed",
            "started_at": "2026-07-15T11:58:00Z",
            "finished_at": "2026-07-15T11:59:00Z",
            "raw_count": len(wrappers),
            "normalized_count": len(wrappers),
            "rejected_count": 0,
            "trace_id": "trace-authority-001",
            "events": [],
        }
    }
    bundles = {
        "bundles": [
            {
                "evidence_bundle_id": "evbundle-ingest-authority-001",
                "source_ids": [row["source_id"] for row in wrappers],
                "evidence_item_ids": [f"evi-{index}" for index in range(len(wrappers))],
                "summary": "Authoritative source run evidence.",
                "citation_refs": ["source://authority/run/001"],
                "confidence": 1.0,
                "license_scope": "open",
                "access_scope": ["public"],
                "created_by": "source-ingest",
                "created_at": "2026-07-15T11:59:20Z",
                "available_time": "2026-07-15T11:58:00Z",
                "entitlement_tags": [],
                "trace_refs": ["trace-authority-001"],
                "metadata": {"connector_id": CONNECTOR_ID, "ingest_run_id": RUN_ID},
            }
        ]
    }
    catalog = {
        "schema_version": "financial_data_source_catalog.v1",
        "catalog_updated_at": "2026-07-15T00:00:00Z",
        "catalog_status": "template_only_not_live_ingestion_claim",
        "entries": [
            {
                "schema_version": "data_source_registry.v1",
                "data_source_id": DATASET_ID,
                "source_kind": "data_source",
                "provider": "Authoritative Provider",
                "source_class": "market_daily",
                "datasets": [
                    {
                        "dataset_id": NORMALIZED_DATASET_ID,
                        "dataset_class": "market_daily",
                        "storage_targets": [f"normalized/{NORMALIZED_DATASET_ID}"],
                    }
                ],
                "connector_id": CONNECTOR_ID,
            }
        ],
        "config_templates": [
            {
                "schema_version": "financial_data_source_config_template.v1",
                "template_id": "template-authoritative-crypto",
                "data_source_id": DATASET_ID,
                "connector_id": CONNECTOR_ID,
                "fetch": {"dataset": DESIRED_DATASET_ID},
            }
        ],
    }
    return AuthorityCase(
        source_root=source_root,
        output_root=output_root,
        responses={
            f"{BASE_URL}/api/source-ingest/controller/readback": readback,
            f"{BASE_URL}/api/source-ingest/data-sources/financial-catalog": catalog,
            f"{BASE_URL}/api/source-ingest/jobs/{RUN_ID}": run,
            f"{BASE_URL}/api/source-ingest/evidence/bundles": bundles,
        },
        raw_path=raw_path,
        normalized_path=normalized_path,
        feature_path=feature_path,
        calls=[],
    )


def _materialize(case: AuthorityCase, module=AUTHORITY):
    return module.materialize_source_dataset_version(
        http_get=case.get,
        source_api_url=BASE_URL,
        connector_id=CONNECTOR_ID,
        dataset_id=DATASET_ID,
        source_volume_root=case.source_root,
        output_root=case.output_root,
        trusted_now=NOW,
    )


def _policy() -> dict[str, Any]:
    return {
        "policy_id": "persona-teaching-evaluation",
        "policy_version": "2026-07-15.1",
        "status": "active",
        "approval_decision_ref": "approval:persona-teaching-evaluation:20260715",
        "effective_from": "2026-07-15T00:00:00Z",
        "effective_until": "2026-07-16T00:00:00Z",
        "required_backend": "vectorbt_portfolio",
        "proof_ttl_seconds": 900,
        "max_future_skew_seconds": 300,
        "max_staleness_days": 1,
        "min_bars_per_instrument": 30,
        "min_instruments": 2,
        "required_instruments": ["BTCUSD", "ETHUSD"],
        "min_sharpe_ratio": 0.5,
        "min_total_return": -0.1,
        "max_drawdown": 0.2,
    }


def test_success_materializes_strict_evaluator_compatible_dataset(tmp_path: Path) -> None:
    case = _make_case(tmp_path)

    result = _materialize(case)

    assert result.path.exists()
    assert result.path.name == f"dataset-version-{result.payload_sha256}.json"
    assert result.canonical_ohlcv_path.exists()
    assert result.payload["dataset_version_id"].startswith("dataset-version-")
    assert result.payload["market_scope"] == ["CRYPTO_SPOT"]
    assert result.payload["instrument_scope"] == ["BTCUSD", "ETHUSD"]
    assert result.payload["metadata_json"]["authority_status"] == "authoritative"
    assert result.payload["metadata_json"]["source_ingest_run_id"] == RUN_ID
    assert result.payload["metadata_json"]["source_dataset_id"] == DATASET_ID
    assert result.payload["metadata_json"]["source_dataset_resolution"]["normalized_targets"] == [
        NORMALIZED_DATASET_ID
    ]
    assert result.payload["source_evidence_bundle_ids"] == ["evbundle-ingest-authority-001"]
    assert len(result.payload["records"]) == 60
    assert set(result.payload["records"][0]) == {
        "instrument",
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    canonical_rows = [json.loads(line) for line in result.canonical_ohlcv_path.read_text().splitlines()]
    assert canonical_rows == result.payload["records"]
    assert case.calls == [
        f"{BASE_URL}/api/source-ingest/controller/readback",
        f"{BASE_URL}/api/source-ingest/data-sources/financial-catalog",
        f"{BASE_URL}/api/source-ingest/jobs/{RUN_ID}",
        f"{BASE_URL}/api/source-ingest/evidence/bundles",
    ]

    # Load the evaluator contract under a unique module name.
    spec = importlib.util.spec_from_file_location(
        "training_session_evaluation_authority_contract_test",
        SERVICE_DIR / "evaluation_authority.py",
    )
    assert spec and spec.loader
    evaluator = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = evaluator
    spec.loader.exec_module(evaluator)
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(_policy()), encoding="utf-8")
    snapshot = evaluator.load_evaluation_authority(
        result.path,
        policy_path,
        trusted_now=NOW,
        strategy_id="strategy-source-authority",
        authority_root=result.path.parent,
    )
    assert snapshot.dataset_version_id == result.payload["dataset_version_id"]
    assert snapshot.dataset_digest == result.payload_sha256
    assert len(snapshot.vectorbt_dataset["records"]) == 60


def test_v2_controller_state_readback_remains_authoritative(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
    readback["controller_state"]["schema_version"] = "source_ingest_controller_state.v2"

    result = _materialize(case)

    assert result.ingest_run_id == RUN_ID


def test_stale_controller_readback_fails_closed(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]["captured_at"] = (
        "2026-07-15T11:54:59Z"
    )

    with pytest.raises(SourceDatasetAuthorityError, match="stale"):
        _materialize(case)

    assert not case.output_root.exists() or not list(case.output_root.glob("dataset-version-*.json"))


def test_future_controller_readback_fails_closed(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]["captured_at"] = (
        "2026-07-15T12:00:01Z"
    )

    with pytest.raises(SourceDatasetAuthorityError, match="future"):
        _materialize(case)


def test_catalog_transport_timeout_is_surfaced_without_fallback(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    catalog_url = f"{BASE_URL}/api/source-ingest/data-sources/financial-catalog"

    def timeout_get(url: str) -> dict[str, Any]:
        if url == catalog_url:
            raise TimeoutError("authoritative catalog timed out")
        return case.get(url)

    with pytest.raises(SourceDatasetAuthorityError, match="TimeoutError.*timed out"):
        AUTHORITY.materialize_source_dataset_version(
            http_get=timeout_get,
            source_api_url=BASE_URL,
            connector_id=CONNECTOR_ID,
            dataset_id=DATASET_ID,
            source_volume_root=case.source_root,
            output_root=case.output_root,
            trusted_now=NOW,
        )

    assert not list(case.output_root.glob("dataset-version-*.json"))


@pytest.mark.parametrize("mismatch", ["dataset", "run"])
def test_wrong_dataset_or_run_fails_closed(tmp_path: Path, mismatch: str) -> None:
    case = _make_case(tmp_path)
    readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
    manifest = readback["connectors"][0]["source_health"]["metadata"]["storage_refs"]
    if mismatch == "dataset":
        manifest["normalized_refs"][0]["dataset"] = "different_dataset"
    else:
        manifest["ingest_run_id"] = "ingest-different-run"

    with pytest.raises(SourceDatasetAuthorityError, match="different|mismatch"):
        _materialize(case)


@pytest.mark.parametrize("unsafe_kind", ["escape", "symlink"])
def test_path_escape_and_symlink_are_rejected(tmp_path: Path, unsafe_kind: str) -> None:
    case = _make_case(tmp_path)
    readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
    ref = readback["connectors"][0]["source_health"]["metadata"]["storage_refs"][
        "normalized_refs"
    ][0]
    if unsafe_kind == "escape":
        unsafe = tmp_path / "outside" / f"{RUN_ID}.jsonl"
        unsafe.parent.mkdir()
        unsafe.write_bytes(case.normalized_path.read_bytes())
    else:
        unsafe = case.source_root / "normalized" / "linked" / f"{RUN_ID}.jsonl"
        unsafe.parent.mkdir()
        unsafe.symlink_to(case.normalized_path)
    ref["uri"] = unsafe.as_posix()

    with pytest.raises(SourceDatasetAuthorityError, match="escapes|symlink"):
        _materialize(case)


def test_malformed_normalized_row_is_rejected(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    case.normalized_path.write_text('{"source_id":', encoding="utf-8")

    with pytest.raises(SourceDatasetAuthorityError, match="invalid JSON"):
        _materialize(case)


def test_missing_ohlcv_field_is_rejected(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    rows = [json.loads(line) for line in case.normalized_path.read_text().splitlines()]
    rows[0]["metadata"].pop("volume")
    _write_jsonl(case.normalized_path, rows)

    with pytest.raises(SourceDatasetAuthorityError, match="volume must be numeric"):
        _materialize(case)


def test_future_ohlcv_bar_is_rejected(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    rows = [json.loads(line) for line in case.normalized_path.read_text().splitlines()]
    rows[0]["metadata"]["trade_date"] = "2026-07-16"
    _write_jsonl(case.normalized_path, rows)

    with pytest.raises(SourceDatasetAuthorityError, match="future bar"):
        _materialize(case)


def test_missing_deployment_identity_is_rejected(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
    readback["controller_state"]["deployment"]["git_sha"] = "unknown"

    with pytest.raises(SourceDatasetAuthorityError, match="unresolved"):
        _materialize(case)


def test_unresolved_dlq_is_rejected(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    readback = case.responses[f"{BASE_URL}/api/source-ingest/controller/readback"]
    readback["dlq_count"] = 1
    readback["pending_dlq_count"] = 1
    readback["unresolved_dlq_count"] = 1
    readback["dlq_status_counts"]["pending"] = 1

    with pytest.raises(SourceDatasetAuthorityError, match="unresolved DLQ"):
        _materialize(case)


def test_tampered_source_bytes_cannot_rebind_materialized_run(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    first = _materialize(case)
    rows = [json.loads(line) for line in case.normalized_path.read_text().splitlines()]
    rows[0]["metadata"]["close"] += 1.0
    _write_jsonl(case.normalized_path, rows)

    with pytest.raises(SourceDatasetAuthorityError, match="source run bytes changed"):
        _materialize(case)

    assert json.loads(first.path.read_text()) == first.payload


def test_restart_materialization_is_idempotent(tmp_path: Path) -> None:
    case = _make_case(tmp_path)
    first = _materialize(case)
    restarted = _load_module("training_session_source_dataset_authority_restart_test")

    second = _materialize(case, restarted)

    assert second.path == first.path
    assert second.payload == first.payload
    assert second.payload_sha256 == first.payload_sha256
    assert second.canonical_ohlcv_sha256 == first.canonical_ohlcv_sha256
    assert len(list(case.output_root.glob("dataset-version-*.json"))) == 1
    assert len(list(case.output_root.glob("canonical-ohlcv-*.jsonl"))) == 1
