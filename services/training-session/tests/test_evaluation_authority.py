from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pytest


SERVICE_DIR = Path(__file__).resolve().parents[1]
MODULE_NAME = "training_session_evaluation_authority"
SPEC = importlib.util.spec_from_file_location(MODULE_NAME, SERVICE_DIR / "evaluation_authority.py")
assert SPEC and SPEC.loader
AUTHORITY = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE_NAME] = AUTHORITY
SPEC.loader.exec_module(AUTHORITY)

AuthorityValidationError = AUTHORITY.AuthorityValidationError
load_evaluation_authority = AUTHORITY.load_evaluation_authority
stable_json_sha256 = AUTHORITY.stable_json_sha256

NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)


def _valid_dataset() -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    first_date = date(2026, 6, 16)
    for offset in range(30):
        bar_date = (first_date + timedelta(days=offset)).isoformat()
        for instrument, base in (("BTCUSD", 60_000.0), ("ETHUSD", 3_000.0)):
            open_price = base + offset
            records.append(
                {
                    "instrument": instrument,
                    "date": bar_date,
                    "open": open_price,
                    "high": open_price + 10.0,
                    "low": open_price - 10.0,
                    "close": open_price + 1.0,
                    "volume": 1_000.0 + offset,
                }
            )
    return {
        "dataset_version_id": "dataset-version-20260715-001",
        "market_scope": ["CRYPTO_SPOT"],
        "instrument_scope": ["BTCUSD", "ETHUSD"],
        "raw_dataset_refs": ["raw-dataset-20260715-001"],
        "normalized_dataset_refs": ["normalized-dataset-20260715-001"],
        "feature_dataset_refs": ["feature-dataset-20260715-001"],
        "frozen_at": "2026-07-15T11:30:00Z",
        "metadata_json": {"authority_status": "authoritative"},
        "created_at": "2026-07-15T11:00:00Z",
        "source_controller_readback_ref": "source-controller-readback:20260715-001",
        "source_evidence_bundle_ids": ["evbundle-source-20260715-001"],
        "normalized_storage_refs": [
            {
                "uri": "s3://pantheon-authority/normalized/2026-07-15.jsonl",
                "sha256": "a" * 64,
                "row_count": len(records),
            }
        ],
        "records": records,
    }


def _valid_policy() -> dict[str, Any]:
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


def _write_authorities(
    tmp_path: Path,
    *,
    dataset: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> tuple[Path, Path, Path]:
    authority_dir = tmp_path / "authority"
    storage_dir = authority_dir / "normalized"
    storage_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = authority_dir / "dataset.json"
    policy_path = authority_dir / "policy.json"
    selected_dataset = dataset if dataset is not None else _valid_dataset()
    storage_path = storage_dir / "canonical-ohlcv.jsonl"
    storage_bytes = (
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in selected_dataset.get("records", [])
        )
    ).encode("utf-8")
    storage_path.write_bytes(storage_bytes)
    storage_refs = selected_dataset.get("normalized_storage_refs")
    if isinstance(storage_refs, list) and storage_refs and isinstance(storage_refs[0], dict):
        storage_refs[0]["uri"] = storage_path.resolve().as_posix()
        if "sha256" in storage_refs[0]:
            storage_refs[0]["sha256"] = hashlib.sha256(storage_bytes).hexdigest()
        if "row_count" in storage_refs[0]:
            storage_refs[0]["row_count"] = len(selected_dataset.get("records", []))
    dataset_path.write_text(
        json.dumps(selected_dataset), encoding="utf-8"
    )
    policy_path.write_text(
        json.dumps(policy if policy is not None else _valid_policy()), encoding="utf-8"
    )
    return dataset_path, policy_path, storage_path


def _load(tmp_path: Path, dataset: dict[str, Any], policy: dict[str, Any] | None = None):
    dataset_path, policy_path, _ = _write_authorities(
        tmp_path,
        dataset=dataset,
        policy=policy,
    )
    return load_evaluation_authority(
        dataset_path,
        policy_path,
        trusted_now=NOW,
        strategy_id="preview-session-001",
    )


def test_valid_authorities_return_digest_bound_admission_snapshot(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    policy = _valid_policy()

    snapshot = _load(tmp_path, dataset, policy)
    payload = snapshot.to_dict()

    assert payload["status"] == "admitted"
    assert payload["evaluated_at"] == "2026-07-15T12:00:00Z"
    assert payload["expires_at"] == "2026-07-15T12:15:00Z"
    assert payload["dataset"]["dataset_version_id"] == dataset["dataset_version_id"]
    assert payload["dataset"]["digest"] == stable_json_sha256(dataset)
    assert payload["policy"]["digest"] == stable_json_sha256(policy)
    assert payload["policy"]["status"] == "active"
    assert payload["policy"]["approval_decision_ref"] == policy["approval_decision_ref"]
    assert payload["policy"]["effective_from"] == policy["effective_from"]
    assert payload["policy"]["effective_until"] == policy["effective_until"]
    assert payload["policy"]["required_backend"] == "vectorbt_portfolio"
    assert payload["thresholds"]["required_instruments"] == ["BTCUSD", "ETHUSD"]
    assert payload["dataset"]["bars_per_instrument"] == {"BTCUSD": 30, "ETHUSD": 30}
    assert payload["vectorbt_dataset"]["dataset_id"] == dataset["dataset_version_id"]
    assert payload["vectorbt_dataset"]["strategy_id"] == "preview-session-001"
    assert payload["vectorbt_dataset"]["source_dataset_refs"] == dataset[
        "normalized_dataset_refs"
    ]
    assert len(payload["vectorbt_dataset"]["records"]) == 60
    assert {gate["state"] for gate in payload["gates"]} == {"passed"}
    assert stable_json_sha256({"b": 2, "a": 1}) == stable_json_sha256({"a": 1, "b": 2})


@pytest.mark.parametrize(
    "missing_field",
    ["dataset_version_id", "raw_dataset_refs", "source_controller_readback_ref"],
)
def test_dataset_rejects_missing_version_or_lineage(
    tmp_path: Path,
    missing_field: str,
) -> None:
    dataset = _valid_dataset()
    dataset.pop(missing_field)

    with pytest.raises(AuthorityValidationError, match="missing required fields"):
        _load(tmp_path, dataset)


@pytest.mark.parametrize(
    "mutate, message",
    [
        (
            lambda dataset: dataset["metadata_json"].update({"authority_status": "candidate"}),
            "authority_status",
        ),
        (
            lambda dataset: dataset["normalized_storage_refs"][0].pop("sha256"),
            "missing required fields: sha256",
        ),
    ],
)
def test_dataset_rejects_incomplete_authority_metadata(
    tmp_path: Path,
    mutate: Callable[[dict[str, Any]], Any],
    message: str,
) -> None:
    dataset = _valid_dataset()
    mutate(dataset)

    with pytest.raises(AuthorityValidationError, match=message):
        _load(tmp_path, dataset)


def test_dataset_rejects_when_one_required_instrument_is_stale(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    for record in dataset["records"]:
        if record["instrument"] == "ETHUSD":
            record["date"] = (
                date.fromisoformat(record["date"]) - timedelta(days=10)
            ).isoformat()

    with pytest.raises(AuthorityValidationError, match="instrument ETHUSD is stale"):
        _load(tmp_path, dataset)


def test_dataset_rejects_future_bar_beyond_policy_skew(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset["records"][-2]["date"] = "2026-07-16"

    with pytest.raises(AuthorityValidationError, match="future bar beyond permitted skew"):
        _load(tmp_path, dataset)


def test_dataset_rejects_tampered_normalized_authority_file(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset_path, policy_path, storage_path = _write_authorities(tmp_path, dataset=dataset)
    storage_path.write_bytes(storage_path.read_bytes() + b" ")

    with pytest.raises(AuthorityValidationError, match="sha256 does not match"):
        load_evaluation_authority(
            dataset_path,
            policy_path,
            trusted_now=NOW,
            strategy_id="preview-session-001",
        )


def test_dataset_rejects_normalized_file_outside_authority_root(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset_path, policy_path, storage_path = _write_authorities(tmp_path, dataset=dataset)
    outside_path = tmp_path / "outside-authority.jsonl"
    outside_path.write_bytes(storage_path.read_bytes())
    dataset["normalized_storage_refs"][0]["uri"] = outside_path.resolve().as_posix()
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    with pytest.raises(AuthorityValidationError, match="escapes authority_root"):
        load_evaluation_authority(
            dataset_path,
            policy_path,
            trusted_now=NOW,
            strategy_id="preview-session-001",
        )


def test_dataset_rejects_normalized_file_symlink(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset_path, policy_path, storage_path = _write_authorities(tmp_path, dataset=dataset)
    symlink_path = storage_path.parent / "canonical-link.jsonl"
    symlink_path.symlink_to(storage_path)
    dataset["normalized_storage_refs"][0]["uri"] = symlink_path.as_posix()
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    with pytest.raises(AuthorityValidationError, match="must not traverse a symlink"):
        load_evaluation_authority(
            dataset_path,
            policy_path,
            trusted_now=NOW,
            strategy_id="preview-session-001",
        )


def test_dataset_rejects_normalized_authority_row_count_mismatch(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset_path, policy_path, _ = _write_authorities(tmp_path, dataset=dataset)
    dataset["normalized_storage_refs"][0]["row_count"] -= 1
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    with pytest.raises(AuthorityValidationError, match="row_count does not match"):
        load_evaluation_authority(
            dataset_path,
            policy_path,
            trusted_now=NOW,
            strategy_id="preview-session-001",
        )


def test_dataset_rejects_storage_records_that_do_not_match_embedded_records(
    tmp_path: Path,
) -> None:
    dataset = _valid_dataset()
    dataset_path, policy_path, storage_path = _write_authorities(tmp_path, dataset=dataset)
    storage_records = copy.deepcopy(dataset["records"])
    storage_records[0]["close"] += 1.0
    storage_bytes = (
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
            for record in storage_records
        )
    ).encode("utf-8")
    storage_path.write_bytes(storage_bytes)
    dataset["normalized_storage_refs"][0]["sha256"] = hashlib.sha256(storage_bytes).hexdigest()
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    with pytest.raises(AuthorityValidationError, match="must exactly match dataset.records"):
        load_evaluation_authority(
            dataset_path,
            policy_path,
            trusted_now=NOW,
            strategy_id="preview-session-001",
        )


def test_dataset_rejects_duplicate_instrument_date(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset["records"].append(copy.deepcopy(dataset["records"][0]))
    dataset["normalized_storage_refs"][0]["row_count"] += 1

    with pytest.raises(AuthorityValidationError, match="duplicate instrument/date"):
        _load(tmp_path, dataset)


def test_dataset_rejects_stale_extra_instrument_in_instrument_scope(tmp_path: Path) -> None:
    dataset = _valid_dataset()
    dataset["instrument_scope"].append("LTCUSD")
    first_date = date(2026, 6, 6)
    for offset in range(30):
        open_price = 100.0 + offset
        dataset["records"].append(
            {
                "instrument": "LTCUSD",
                "date": (first_date + timedelta(days=offset)).isoformat(),
                "open": open_price,
                "high": open_price + 2.0,
                "low": open_price - 2.0,
                "close": open_price + 1.0,
                "volume": 500.0 + offset,
            }
        )

    with pytest.raises(AuthorityValidationError, match="instrument LTCUSD is stale"):
        _load(tmp_path, dataset)


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_dataset_rejects_nonfinite_ohlcv(tmp_path: Path, nonfinite: float) -> None:
    dataset = _valid_dataset()
    dataset["records"][0]["close"] = nonfinite

    with pytest.raises(AuthorityValidationError, match="non-finite JSON constant"):
        _load(tmp_path, dataset)


def test_policy_rejects_missing_required_field(tmp_path: Path) -> None:
    policy = _valid_policy()
    policy.pop("approval_decision_ref")

    with pytest.raises(AuthorityValidationError, match="missing required fields"):
        _load(tmp_path, _valid_dataset(), policy)


def test_policy_rejects_expired_authority(tmp_path: Path) -> None:
    policy = _valid_policy()
    policy["effective_until"] = "2026-07-15T11:59:59Z"

    with pytest.raises(AuthorityValidationError, match="policy is expired"):
        _load(tmp_path, _valid_dataset(), policy)


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_policy_rejects_nonfinite_threshold(tmp_path: Path, nonfinite: float) -> None:
    policy = _valid_policy()
    policy["min_sharpe_ratio"] = nonfinite

    with pytest.raises(AuthorityValidationError, match="non-finite JSON constant"):
        _load(tmp_path, _valid_dataset(), policy)


def test_policy_rejects_unknown_field(tmp_path: Path) -> None:
    policy = _valid_policy()
    policy["fallback_min_sharpe_ratio"] = -100.0

    with pytest.raises(AuthorityValidationError, match="unknown fields"):
        _load(tmp_path, _valid_dataset(), policy)
