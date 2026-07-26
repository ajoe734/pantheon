"""Test-only support for strict training-session HTTP tests.

Nothing in this module is production evidence.  It materializes ephemeral,
internally consistent authority inputs beneath a test temporary directory and
can replace the expensive vectorbt call with an explicit unit-test fake.  The
fake carries the provenance fields that the HTTP boundary validates, but it is
not proof that vectorbt ran and must never be copied into deployment evidence.

This file intentionally does not contain tests so pytest will not collect it as
a product-behavior assertion.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping, Sequence
from unittest import mock


__test__ = False
TEST_ONLY = True
FIXED_TRUSTED_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
_INSTRUMENTS = ("BTCUSD", "ETHUSD")
_UTC = timezone.utc


@dataclass(frozen=True)
class StrictAuthorityFixture:
    """Paths and payloads for one ephemeral strict-authority test fixture."""

    root: Path
    data_dir: Path
    dataset_path: Path
    policy_path: Path
    normalized_jsonl_path: Path
    runtime_evidence_path: Path
    trusted_now: datetime
    dataset: Mapping[str, Any]
    policy: Mapping[str, Any]

    def environment(self) -> dict[str, str]:
        """Return the service environment needed to consume this fixture."""

        return {
            "TRAINING_SESSION_DATA_DIR": str(self.data_dir),
            "TRAINING_SESSION_CANONICAL_DATASET_PATH": str(self.dataset_path),
            "TRAINING_SESSION_THRESHOLD_POLICY_PATH": str(self.policy_path),
            "TRAINING_SESSION_RUNTIME_EVIDENCE_PATH": str(self.runtime_evidence_path),
            "TRAINING_SESSION_PREVIEW_JOB_LEASE_SECONDS": "120",
            "TRAINING_SESSION_PREVIEW_JOB_MAX_ATTEMPTS": "3",
            "TRAINING_SESSION_AUTH_DISABLED": "true",
            "TRAINING_SESSION_TEST_TENANT_ID": "tenant-test",
            # Production code must still request VectorbtBackend explicitly.
            # The unit-test fake below intercepts before the package is loaded.
            "PANTHEON_VECTORBT_BACKEND": "real",
        }


def materialize_strict_authority(
    root: str | Path,
    *,
    trusted_now: datetime = FIXED_TRUSTED_NOW,
    bars_per_instrument: int = 35,
) -> StrictAuthorityFixture:
    """Create matching DatasetVersion, canonical OHLCV JSONL, and policy files.

    The normalized file contains bare canonical OHLCV rows, not source-ingest
    wrappers.  Its byte checksum and row count are bound into the DatasetVersion.
    All paths stay below ``root`` so the authority loader's path boundary is
    exercised rather than bypassed.
    """

    now = _aware_utc(trusted_now, "trusted_now")
    if isinstance(bars_per_instrument, bool) or not isinstance(bars_per_instrument, int):
        raise ValueError("bars_per_instrument must be an integer")
    if bars_per_instrument < 35:
        raise ValueError("bars_per_instrument must be at least 35")

    authority_root = Path(root)
    authority_root.mkdir(parents=True, exist_ok=True)
    authority_root = authority_root.resolve(strict=True)
    normalized_dir = authority_root / "normalized"
    runtime_dir = authority_root / "runtime"
    data_dir = authority_root / "state"
    normalized_dir.mkdir(parents=True, exist_ok=True)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    records = _canonical_records(now.date(), bars_per_instrument)
    normalized_jsonl_path = normalized_dir / "frozen-canonical-ohlcv.jsonl"
    normalized_bytes = b"".join(
        _canonical_json(record).encode("utf-8") + b"\n" for record in records
    )
    normalized_jsonl_path.write_bytes(normalized_bytes)
    normalized_sha256 = hashlib.sha256(normalized_bytes).hexdigest()

    version_suffix = now.strftime("%Y%m%dT%H%M%SZ")
    dataset: dict[str, Any] = {
        "dataset_version_id": f"teaching-dataset-{version_suffix}",
        "market_scope": ["CRYPTO_SPOT"],
        "instrument_scope": list(_INSTRUMENTS),
        "raw_dataset_refs": [f"raw-market-data-{version_suffix}"],
        "normalized_dataset_refs": [f"normalized-market-data-{version_suffix}"],
        "feature_dataset_refs": [f"ohlcv-feature-view-{version_suffix}"],
        "frozen_at": _utc_iso(now - timedelta(minutes=1)),
        "metadata_json": {
            "authority_status": "authoritative",
            "test_only": True,
        },
        "created_at": _utc_iso(now - timedelta(minutes=2)),
        "source_controller_readback_ref": f"test-source-controller-readback:{version_suffix}",
        "source_evidence_bundle_ids": [f"test-source-evidence:{version_suffix}"],
        "normalized_storage_refs": [
            {
                "uri": normalized_jsonl_path.as_posix(),
                "sha256": normalized_sha256,
                "row_count": len(records),
            }
        ],
        "records": copy.deepcopy(records),
    }
    policy: dict[str, Any] = {
        "policy_id": "test-persona-teaching-evaluation",
        "policy_version": f"test-{version_suffix}",
        "status": "active",
        "approval_decision_ref": f"test-approval:persona-teaching:{version_suffix}",
        "effective_from": _utc_iso(now - timedelta(days=1)),
        "effective_until": _utc_iso(now + timedelta(days=1)),
        "required_backend": "vectorbt_portfolio",
        "proof_ttl_seconds": 3600,
        "max_future_skew_seconds": 60,
        "max_staleness_days": 1,
        "min_bars_per_instrument": bars_per_instrument,
        "min_instruments": 2,
        "required_instruments": list(_INSTRUMENTS),
        "min_sharpe_ratio": 0.5,
        "min_total_return": 0.0,
        "max_drawdown": 0.2,
    }

    dataset_path = authority_root / "dataset-version.json"
    policy_path = authority_root / "threshold-policy.json"
    dataset_path.write_text(_pretty_json(dataset), encoding="utf-8")
    policy_path.write_text(_pretty_json(policy), encoding="utf-8")
    runtime_evidence_path = runtime_dir / "training-session-evidence.jsonl"
    return StrictAuthorityFixture(
        root=authority_root,
        data_dir=data_dir,
        dataset_path=dataset_path,
        policy_path=policy_path,
        normalized_jsonl_path=normalized_jsonl_path,
        runtime_evidence_path=runtime_evidence_path,
        trusted_now=now,
        dataset=copy.deepcopy(dataset),
        policy=copy.deepcopy(policy),
    )


@contextmanager
def configure_loaded_main_module(
    module: Any,
    fixture_or_root: StrictAuthorityFixture | str | Path,
    *,
    trusted_now: datetime | None = None,
    fake_vectorbt: bool | Callable[..., Any] = True,
    fake_target_precondition: bool | Callable[..., Any] = True,
    fake_persona_commit: bool | Callable[..., Any] = False,
) -> Iterator[StrictAuthorityFixture]:
    """Temporarily configure an already-loaded training-session ``main`` module.

    The context replaces the module store with an isolated file store, pins its
    service-owned clock, installs authority/evidence environment paths, and—by
    default—patches ``module.run_vectorbt_workflow`` and the persona target
    pre-readback with explicit test fakes.  ``fake_persona_commit=True`` opts
    into an exact terminal-receipt fake for end-to-end HTTP lifecycle tests.

    These persona fakes cover only main's integration boundary.  Real transport,
    authority validation, negative approvals, idempotency, and terminal readback
    are independently exercised by ``test_persona_target.py``.  All module and
    process state is restored on exit.
    """

    fixture = (
        fixture_or_root
        if isinstance(fixture_or_root, StrictAuthorityFixture)
        else materialize_strict_authority(fixture_or_root, trusted_now=trusted_now or FIXED_TRUSTED_NOW)
    )
    now = _aware_utc(trusted_now or fixture.trusted_now, "trusted_now")
    workflow: Callable[..., Any] | None
    if fake_vectorbt is True:
        workflow = make_fake_real_vectorbt_workflow()
    elif fake_vectorbt is False:
        workflow = None
    elif callable(fake_vectorbt):
        workflow = fake_vectorbt
    else:
        raise TypeError("fake_vectorbt must be a bool or callable")

    precondition_reader: Callable[..., Any] | None
    if fake_target_precondition is True:
        precondition_reader = make_fake_target_precondition_reader()
    elif fake_target_precondition is False:
        precondition_reader = None
    elif callable(fake_target_precondition):
        precondition_reader = fake_target_precondition
    else:
        raise TypeError("fake_target_precondition must be a bool or callable")

    persona_committer: Callable[..., Any] | None
    if fake_persona_commit is True:
        persona_committer = make_fake_persona_target_commit()
    elif fake_persona_commit is False:
        persona_committer = None
    elif callable(fake_persona_commit):
        persona_committer = fake_persona_commit
    else:
        raise TypeError("fake_persona_commit must be a bool or callable")

    required_attributes = (
        "_trusted_now",
        "_read_target_precondition",
        "run_vectorbt_workflow",
        "TrainingSessionStore",
        "store",
    )
    if persona_committer is not None:
        required_attributes += ("_commit_authoritative_persona_target",)
    missing = [name for name in required_attributes if not hasattr(module, name)]
    if missing:
        raise TypeError("loaded main module is missing test hooks: " + ", ".join(missing))

    original_store = module.store
    with ExitStack() as stack:
        stack.enter_context(mock.patch.dict(os.environ, fixture.environment(), clear=False))
        stack.enter_context(mock.patch.object(module, "_trusted_now", lambda: now))
        if workflow is not None:
            stack.enter_context(mock.patch.object(module, "run_vectorbt_workflow", workflow))
        if precondition_reader is not None:
            stack.enter_context(
                mock.patch.object(module, "_read_target_precondition", precondition_reader)
            )
        if persona_committer is not None:
            stack.enter_context(
                mock.patch.object(
                    module,
                    "_commit_authoritative_persona_target",
                    persona_committer,
                )
            )
        module.store = module.TrainingSessionStore(fixture.data_dir)
        try:
            yield fixture
        finally:
            module.store = original_store


def seed_changed_supported_controls(
    module: Any,
    session_id: str,
    *,
    baseline_short_window: int = 5,
    short_window: int = 7,
    baseline_long_window: int = 20,
    long_window: int = 21,
    changed_at: datetime = FIXED_TRUSTED_NOW,
) -> dict[str, Any]:
    """Seed changed controls that are actually consumed by the vectorbt strategy."""

    session_key = str(session_id or "").strip()
    if not session_key:
        raise ValueError("session_id is required")
    values = (
        baseline_short_window,
        short_window,
        baseline_long_window,
        long_window,
    )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 1 for value in values):
        raise ValueError("window controls must be positive integers")
    if short_window >= long_window:
        raise ValueError("short_window must be less than long_window")
    if short_window == baseline_short_window or long_window == baseline_long_window:
        raise ValueError("both supported controls must contain an effective change")
    timestamp = _utc_iso(_aware_utc(changed_at, "changed_at"))
    controls = [
        {
            "parameter_key": "short_window",
            "display_label": "Short moving-average window",
            "baseline_value": baseline_short_window,
            "current_value": short_window,
            "allowed_range": {"min": 1, "max": 64},
            "last_modified_at": timestamp,
        },
        {
            "parameter_key": "long_window",
            "display_label": "Long moving-average window",
            "baseline_value": baseline_long_window,
            "current_value": long_window,
            "allowed_range": {"min": 2, "max": 256},
            "last_modified_at": timestamp,
        },
    ]
    return module.store.put_controls(
        session_key,
        {
            "session_id": session_key,
            "tenant_id": (
                module.store.get_session(session_key) or {}
            ).get("tenant_id", "tenant-test"),
            "controls": controls,
        },
    )


def make_fake_real_vectorbt_workflow(
    *,
    total_return: float = 0.08,
    sharpe_ratio: float = 1.4,
    max_drawdown: float = 0.1,
    run_id: str = "vbt-real-test-fixture-0001",
) -> Callable[..., Any]:
    """Build a deterministic unit fake with production-shaped provenance.

    This callable does not execute vectorbt.  It verifies that production code
    requested ``VectorbtBackend`` and returns only the attributes consumed by
    the HTTP service.  Its ``vbt-real-`` identifier is contract-shaped test
    data, not a claim suitable for product or deployment evidence.
    """

    aggregate_metrics = {
        "mean_total_return": _finite_float(total_return, "total_return"),
        "mean_sharpe_ratio": _finite_float(sharpe_ratio, "sharpe_ratio"),
        "mean_max_drawdown": _finite_float(max_drawdown, "max_drawdown"),
    }
    if not 0.0 <= aggregate_metrics["mean_max_drawdown"] <= 1.0:
        raise ValueError("max_drawdown must be between 0 and 1")
    normalized_run_id = str(run_id or "").strip()
    if not normalized_run_id.startswith("vbt-real-"):
        raise ValueError("run_id must start with 'vbt-real-'")

    def fake_run_vectorbt_workflow(
        dataset: Mapping[str, Any],
        *,
        backend: Any = None,
        config: Any = None,
    ) -> Any:
        if backend is None or backend.__class__.__name__ != "VectorbtBackend":
            raise AssertionError("production code must explicitly request VectorbtBackend")
        records_value = dataset.get("records") if isinstance(dataset, Mapping) else None
        if (
            isinstance(records_value, (str, bytes))
            or not isinstance(records_value, Sequence)
            or not records_value
        ):
            raise AssertionError("fake vectorbt input requires canonical OHLCV records")
        instruments = sorted(
            {
                str(record.get("instrument"))
                for record in records_value
                if isinstance(record, Mapping) and record.get("instrument")
            }
        )
        if instruments != sorted(_INSTRUMENTS):
            raise AssertionError("fake vectorbt input requires the two strict authority instruments")
        strategy_params = dict(getattr(config, "strategy_params", {}) or {})
        short_window = strategy_params.get("short_window")
        long_window = strategy_params.get("long_window")
        if not isinstance(short_window, int) or not isinstance(long_window, int):
            raise AssertionError("fake vectorbt input requires supported window controls")
        if short_window >= long_window:
            raise AssertionError("fake vectorbt input requires short_window < long_window")

        per_instrument_metrics = {
            instrument: {
                "total_return": aggregate_metrics["mean_total_return"],
                "sharpe_ratio": aggregate_metrics["mean_sharpe_ratio"],
                "max_drawdown": aggregate_metrics["mean_max_drawdown"],
                "trade_count": 3,
                "num_bars": sum(
                    1
                    for record in records_value
                    if isinstance(record, Mapping) and record.get("instrument") == instrument
                ),
            }
            for instrument in instruments
        }
        checksum_payload = {
            "dataset_id": dataset.get("dataset_id"),
            "source_dataset_refs": list(dataset.get("source_dataset_refs") or []),
            "strategy_params": strategy_params,
            "aggregate_metrics": aggregate_metrics,
            "run_id": normalized_run_id,
        }
        registry_checksum = "sha256:" + hashlib.sha256(
            _canonical_json(checksum_payload).encode("utf-8")
        ).hexdigest()
        backtest_result = SimpleNamespace(
            backend="vectorbt_portfolio",
            run_id=normalized_run_id,
            per_instrument_metrics=per_instrument_metrics,
            aggregate_metrics=dict(aggregate_metrics),
            notes=("TEST-ONLY fake; vectorbt was not executed.",),
        )
        return SimpleNamespace(
            prepared_dataset=SimpleNamespace(
                dataset_id=dataset.get("dataset_id"),
                strategy_id=dataset.get("strategy_id"),
                source_dataset_refs=tuple(dataset.get("source_dataset_refs") or ()),
                instruments=tuple(instruments),
                num_instruments=len(instruments),
                total_bars=len(records_value),
            ),
            backtest_result=backtest_result,
            artifact_bundle={
                "schema_version": "test-only.vectorbt-artifact.v1",
                "producer_run_id": normalized_run_id,
                "aggregate_metrics": dict(aggregate_metrics),
                "test_only": True,
            },
            registry_entry={
                "artifact_type": "backtest_result",
                "artifact_state": "draft",
                "producer_run_id": normalized_run_id,
                "checksum": registry_checksum,
                "aggregate_metrics": dict(aggregate_metrics),
                "metadata": {
                    "framework": "vectorbt",
                    "backtest_backend": "vectorbt_portfolio",
                    "test_only": True,
                },
            },
        )

    fake_run_vectorbt_workflow.__name__ = "test_only_fake_real_vectorbt_workflow"
    return fake_run_vectorbt_workflow


def make_fake_target_precondition_reader(
    *,
    current_generation: int = 1,
) -> Callable[..., dict[str, Any]]:
    """Build a TEST-ONLY authoritative persona pre-readback boundary fake."""

    if (
        isinstance(current_generation, bool)
        or not isinstance(current_generation, int)
        or current_generation < 0
    ):
        raise ValueError("current_generation must be a non-negative integer")

    def fake_read_target_precondition(
        *,
        session: Mapping[str, Any],
        session_id: str,
        trusted_now: datetime,
    ) -> dict[str, Any]:
        persona_id = str(session.get("persona_id") or "").strip()
        tenant_id = str(session.get("tenant_id") or "").strip()
        normalized_session_id = str(session_id or "").strip()
        if not persona_id or not tenant_id or not normalized_session_id:
            raise ValueError(
                "test target precondition requires persona_id, tenant_id, and session_id"
            )
        now = _aware_utc(trusted_now, "trusted_now")
        controller_record_ref = (
            f"test-persona-controller:{persona_id}:generation:{current_generation}"
        )
        authoritative_record = {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "status": "active",
            "generation": current_generation,
            "controller_record_ref": controller_record_ref,
            "recorded_at": _utc_iso(now),
        }
        return {
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "status": "active",
            "current_generation": current_generation,
            "expected_previous_generation": current_generation,
            "target_generation": current_generation + 1,
            "precondition_digest": hashlib.sha256(
                _canonical_json(authoritative_record).encode("utf-8")
            ).hexdigest(),
            "controller_record_ref": controller_record_ref,
            "recorded_at": _utc_iso(now),
        }

    fake_read_target_precondition.__name__ = "test_only_fake_target_precondition_reader"
    return fake_read_target_precondition


def make_fake_persona_target_commit() -> Callable[..., dict[str, Any]]:
    """Build an opt-in TEST-ONLY exact terminal persona receipt fake.

    The persona target transport and its full fail-closed admission contract are
    covered in ``test_persona_target.py``.  This fake exists only so main HTTP
    lifecycle tests can verify how a terminal receipt is persisted and exposed.
    """

    def fake_commit_authoritative_persona_target(
        *,
        session_id: str,
        replay: Mapping[str, Any],
        proof: Mapping[str, Any],
        idempotency_key: str,
        trusted_now: datetime,
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        persona_id = str(replay.get("persona_id") or "").strip()
        tenant_id = str(replay.get("tenant_id") or "").strip()
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_session_id or not persona_id or not tenant_id or not normalized_key:
            raise ValueError(
                "test persona commit requires session, persona, tenant, and idempotency key"
            )
        now = _aware_utc(trusted_now, "trusted_now")
        precondition = proof.get("target_precondition")
        authority = proof.get("authority")
        policy = authority.get("policy") if isinstance(authority, Mapping) else None
        if not isinstance(precondition, Mapping) or not isinstance(policy, Mapping):
            raise ValueError("test persona commit requires proof authority and target precondition")
        generation = precondition.get("target_generation")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("test persona commit requires a positive target generation")
        digests = {
            "candidate_digest": _required_digest(proof.get("candidate_digest"), "candidate_digest"),
            "control_digest": _required_digest(proof.get("controls_digest"), "controls_digest"),
            "proof_digest": _required_digest(proof.get("proof_digest"), "proof_digest"),
            "expected_precondition_digest": _required_digest(
                precondition.get("precondition_digest"),
                "expected_precondition_digest",
            ),
        }
        approval_decision_ref = str(policy.get("approval_decision_ref") or "").strip()
        precondition_record_ref = str(precondition.get("controller_record_ref") or "").strip()
        if not approval_decision_ref or not precondition_record_ref:
            raise ValueError("test persona commit requires approval and precondition record refs")
        identity_suffix = digests["proof_digest"][:16]
        approval_decision_id = f"test-approval-{identity_suffix}"
        approval_controller_record_ref = (
            f"test-approval-controller:{persona_id}:{approval_decision_id}"
        )
        target_controller_record_ref = (
            f"test-persona-controller:{persona_id}:generation:{generation}:{identity_suffix}"
        )
        approval_digest = hashlib.sha256(
            _canonical_json(
                {
                    "approval_decision_id": approval_decision_id,
                    "approval_decision_ref": approval_decision_ref,
                    "persona_id": persona_id,
                    "tenant_id": tenant_id,
                    "session_id": normalized_session_id,
                    "candidate_digest": digests["candidate_digest"],
                    "proof_digest": digests["proof_digest"],
                }
            ).encode("utf-8")
        ).hexdigest()
        return {
            "status": "committed",
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "session_id": normalized_session_id,
            "candidate_digest": digests["candidate_digest"],
            "control_digest": digests["control_digest"],
            "proof_digest": digests["proof_digest"],
            "approval_digest": approval_digest,
            "generation": generation,
            "approval_decision_id": approval_decision_id,
            "approval_decision_ref": approval_decision_ref,
            "approval_controller_record_ref": approval_controller_record_ref,
            "target_controller_record_ref": target_controller_record_ref,
            "target_recorded_at": _utc_iso(now),
            "expected_precondition_digest": digests["expected_precondition_digest"],
            "expected_precondition_record_ref": precondition_record_ref,
            "idempotency_key": normalized_key,
            "pre_generation": generation - 1,
            "replayed": False,
        }

    fake_commit_authoritative_persona_target.__name__ = (
        "test_only_fake_commit_authoritative_persona_target"
    )
    return fake_commit_authoritative_persona_target


def _canonical_records(last_date: date, bars_per_instrument: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    first_date = last_date - timedelta(days=bars_per_instrument - 1)
    for offset in range(bars_per_instrument):
        bar_date = (first_date + timedelta(days=offset)).isoformat()
        for instrument, base_price in (("BTCUSD", 60_000.0), ("ETHUSD", 3_000.0)):
            open_price = base_price + float(offset * 3)
            records.append(
                {
                    "instrument": instrument,
                    "date": bar_date,
                    "open": open_price,
                    "high": open_price + 12.0,
                    "low": open_price - 12.0,
                    "close": open_price + (2.0 if offset % 2 == 0 else -1.0),
                    "volume": 10_000.0 + float(offset),
                }
            )
    return records


def _finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite")
    return normalized


def _required_digest(value: Any, label: str) -> str:
    normalized = str(value or "").strip()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return normalized


def _aware_utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value.astimezone(_UTC)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(_UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _pretty_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        indent=2,
    ) + "\n"


__all__ = [
    "FIXED_TRUSTED_NOW",
    "StrictAuthorityFixture",
    "configure_loaded_main_module",
    "make_fake_persona_target_commit",
    "make_fake_real_vectorbt_workflow",
    "make_fake_target_precondition_reader",
    "materialize_strict_authority",
    "seed_changed_supported_controls",
]
