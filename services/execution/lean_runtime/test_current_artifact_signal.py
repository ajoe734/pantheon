"""Acceptance tests for current-artifact-driven paper signals."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    ProvisioningRecord,
)
from services.control_plane.bff.persona_provisioning_coordinator import (
    PersonaProvisioningCoordinator,
    deterministic_provisioning_ids,
)
from services.execution.artifact_loader import ArtifactLoader
from services.execution.lean_runtime.paper_runtime import PaperRuntimeService
from services.execution.lean_runtime.paper_signal_producer import (
    BoundedPaperStrategy,
    CurrentArtifactStrategy,
    PaperSignalProducer,
    SignalDecisionUnavailable,
    SmokeStrategy,
    _runner_strategy,
    main,
)
from services.execution.lean_runtime.pending_signal_store import (
    InMemoryPendingSignalStore,
)
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.execution.lean_runtime.symbol_parser import (
    is_taiwan_venue_symbol,
    parse as parse_executor_symbol,
    validate_executable_symbol,
)
from services.registry.strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    load_strategy_artifact_registration,
    validate_strategy_artifact,
)
from services.trade_journey.correlation_envelope import propagate_envelope

_NOW = "2026-08-14T08:00:00Z"


def _artifact(*, bearish: bool = False) -> dict[str, Any]:
    registration = load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )
    artifact = copy.deepcopy(registration["strategy_artifact"])
    artifact["parameters"]["symbols"] = ["AAPL.US"]
    artifact["parameters"]["data_source"] = (
        "source-ingest:normalized/us-equity-price/daily"
    )
    artifact["lineage"]["source_dataset_refs"] = [
        "source-ingest:normalized/us-equity-price/daily"
    ]
    artifact["parameters"]["order_quantity"] = 3
    if bearish:
        artifact["artifact_id"] = "artifact-current-bearish-v1"
        artifact["strategy_id"] = "current_bearish"
        artifact["strategy_logic"]["positive_action"] = "SELL"
    return artifact


def _projection(
    artifact: dict[str, Any],
    *,
    include_checksum: bool = True,
) -> tuple[dict[str, Any], str | None]:
    payload = json.dumps(
        artifact,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checksum = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    metadata = {
        "registry_id": artifact["artifact_id"],
        "strategy_id": artifact["strategy_id"],
        "version": artifact["version"],
        "artifact_type": "execution_bundle",
        "artifact_state": "approved",
        "deployment_stage": "paper",
        "promotion_state": "paper",
        "lineage": copy.deepcopy(artifact["lineage"]),
        "created_at": "2026-08-14T07:59:00Z",
    }
    if include_checksum:
        metadata["checksum"] = checksum
    projection = ArtifactLoader.build_projection(
        artifact["strategy_id"], artifact["version"]
    )
    return {
        projection.metadata_key: metadata,
        projection.artifact_key: payload,
    }, checksum if include_checksum else None


def _binding(
    artifact: dict[str, Any],
    *,
    binding_id: str,
    include_checksum: bool = True,
    include_market_input: bool = True,
) -> dict[str, Any]:
    object_store, checksum = _projection(
        artifact,
        include_checksum=include_checksum,
    )
    binding = {
        "binding_id": binding_id,
        "runtime_id": f"runtime-{binding_id}",
        "capital_pool_id": f"pool-{binding_id}",
        "artifact_id": artifact["artifact_id"],
        "artifact_version": artifact["version"],
        "strategy_id": artifact["strategy_id"],
        "deployment_mode": "paper",
        "plan_id": f"plan-{binding_id}",
        "persona_capital_binding_id": f"pcb-{binding_id}",
        "status": "active",
        "object_store": object_store,
    }
    if checksum is not None:
        binding["artifact_checksum"] = checksum
    if include_market_input:
        binding["market_input"] = {
            "symbol": "AAPL.US",
            "closes": [100.0, 110.0],
            "source_ref": "source-ingest://normalized/us-price/AAPL",
            "observed_at": _NOW,
        }
    return binding


def _runtime_binding(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in binding.items()
        if key
        not in {
            "artifact_checksum",
            "market_input",
            "object_store",
            "strategy_id",
        }
    }


class _RuntimeManager:
    def __init__(self, binding: dict[str, Any]) -> None:
        self._binding = dict(binding)

    def list_all(self) -> list[dict[str, Any]]:
        return [dict(self._binding)]


class _Telemetry:
    def __init__(self) -> None:
        self.enabled = True
        self.events: list[dict[str, Any]] = []

    def build_event(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        *,
        event_id: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        event_metrics = dict(metrics)
        stamp_key = (
            "pnl_as_of" if event_type == "pnl_snapshot" else "drawdown_as_of"
        )
        stamp = event_metrics.pop(stamp_key, None)
        payload = {
            "event_id": event_id,
            "event_type": event_type,
            "created_at": created_at,
            "binding_id": (metadata or {}).get("runtime_binding_id"),
            stamp_key: stamp,
            "metrics": event_metrics,
            "metadata": dict(metadata or {}),
        }
        event_metadata = payload["metadata"]
        incoming_envelope = event_metadata.get("correlation_envelope")
        sequence_no = event_metadata.get("sequence_no")
        causal_parent_id = event_metadata.get("causal_parent_id")
        if (
            isinstance(incoming_envelope, dict)
            and isinstance(sequence_no, int)
            and event_id
            and created_at
            and causal_parent_id
        ):
            outgoing_envelope = propagate_envelope(
                incoming_envelope,
                producer="execution.paper_runtime",
                event_id=event_id,
                event_time=created_at,
            )
            payload.update(
                {
                    "aggregate_type": "trade_journey",
                    "aggregate_id": outgoing_envelope["journey_id"],
                    "sequence_no": sequence_no,
                    "causal_parent_id": causal_parent_id,
                    "correlation_envelope": outgoing_envelope,
                }
            )
        return payload

    def emit_payload(self, payload: dict[str, Any]) -> bool:
        self.events.append(json.loads(json.dumps(payload)))
        return True

    def emit(
        self,
        event_type: str,
        metrics: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        self.events.append(
            {
                "event_type": event_type,
                "metrics": dict(metrics),
                "metadata": dict(metadata or {}),
            }
        )
        return True

    def emit_heartbeat(self, metadata: dict[str, Any] | None = None) -> bool:
        return self.emit("heartbeat", {"heartbeat": 1}, metadata)

    def emit_pnl_snapshot(
        self,
        pnl: float,
        metadata: dict[str, Any] | None = None,
        extra_metrics: dict[str, Any] | None = None,
    ) -> bool:
        metrics = {"pnl": float(pnl), **dict(extra_metrics or {})}
        return self.emit("pnl_snapshot", metrics, metadata)

    def snapshot(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "url": "memory://current-artifact-test",
            "sent": len(self.events),
            "failed": 0,
            "last_error": None,
        }


def _identity(binding: dict[str, Any]) -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "pantheon-paper-execution-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": binding["runtime_id"],
            "PANTHEON_RUNTIME_BINDING_ID": binding["binding_id"],
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
            "PANTHEON_WORKSPACE_REF": "workspace-current-artifact",
            "PANTHEON_AUTH_PROFILE_REF": "auth-profile-current-artifact",
            "PANTHEON_PERSONA_ID": "persona-current-artifact",
            "PANTHEON_SESSION_ID": "session-current-artifact",
            "PANTHEON_TRACE_ID": str(uuid.uuid4()),
            "PANTHEON_REQUEST_ID": "request-current-artifact",
        }
    )


class CurrentArtifactSignalTest(unittest.TestCase):
    def test_missing_artifact_checksum_enqueues_zero_and_reports_degraded(self) -> None:
        binding = _binding(
            _artifact(),
            binding_id="rb-missing-checksum",
            include_checksum=False,
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )

        self.assertEqual(producer.produce(binding, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertIn("rb-missing-checksum", producer.degraded_bindings)
        self.assertIn(
            "artifact_unavailable",
            producer.degraded_bindings["rb-missing-checksum"],
        )

    def test_missing_market_input_enqueues_zero_and_reports_degraded(self) -> None:
        binding = _binding(
            _artifact(),
            binding_id="rb-missing-market",
            include_market_input=False,
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )

        self.assertEqual(producer.produce(binding, _NOW), 0)
        self.assertEqual(store.queue_depth(), 0)
        self.assertEqual(
            producer.degraded_bindings["rb-missing-market"].split(":", 1)[0],
            "market_input_missing",
        )

    def test_two_exact_artifacts_drive_different_non_hardcoded_decisions(self) -> None:
        bullish = _binding(_artifact(), binding_id="rb-current-bullish")
        bearish = _binding(
            _artifact(bearish=True),
            binding_id="rb-current-bearish",
        )
        stores = {
            bullish["binding_id"]: InMemoryPendingSignalStore(),
            bearish["binding_id"]: InMemoryPendingSignalStore(),
        }
        producer = PaperSignalProducer(
            store_for=lambda binding: stores[binding["binding_id"]],
            strategy=CurrentArtifactStrategy(),
        )

        counts = producer.tick([bullish, bearish], _NOW)

        self.assertEqual(counts, {"rb-current-bullish": 1, "rb-current-bearish": 1})
        [bullish_signal] = stores["rb-current-bullish"].get_pending()
        [bearish_signal] = stores["rb-current-bearish"].get_pending()
        self.assertEqual(bullish_signal["action"], "BUY")
        self.assertEqual(bearish_signal["action"], "SELL")
        self.assertEqual(bullish_signal["quantity"], 3)
        self.assertEqual(bearish_signal["direction"], "SHORT")
        self.assertEqual(
            bearish_signal["metadata"]["artifact_id"],
            bearish["artifact_id"],
        )
        self.assertEqual(producer.degraded_bindings, {})

    def test_default_runner_uses_current_artifact_not_smoke(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            selected = _runner_strategy()
        self.assertIsInstance(selected, CurrentArtifactStrategy)
        self.assertNotIsInstance(selected, (SmokeStrategy, BoundedPaperStrategy))

        with patch.dict(os.environ, {"PAPER_SIGNAL_STRATEGY": "smoke"}):
            self.assertIsInstance(_runner_strategy(), BoundedPaperStrategy)

    def test_missing_artifact_health_is_degraded_with_zero_signals(self) -> None:
        binding = _binding(
            _artifact(),
            binding_id="rb-health-missing-checksum",
            include_checksum=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            health_file = Path(directory) / "paper-producer-health.json"
            env = {
                "SIGNAL_STORE_URL": "redis://signal-store:6379",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PAPER_PRODUCER_INTERVAL_SECONDS": "1",
                "PAPER_PRODUCER_MAX_TICKS": "1",
                "PAPER_PRODUCER_HEALTH_FILE": str(health_file),
                "PANTHEON_LIVE_BROKER_ENABLED": "false",
                "PANTHEON_CANARY_EXECUTION_ENABLED": "false",
            }
            store = InMemoryPendingSignalStore()
            with patch.dict(os.environ, env, clear=False), patch(
                "services.execution.lean_runtime.paper_signal_producer."
                "fetch_eligible_paper_bindings",
                return_value=[binding],
            ), patch(
                "services.execution.lean_runtime.paper_signal_producer."
                "_redis_store_factory",
                return_value=lambda _: store,
            ):
                self.assertEqual(main(), 0)

            health = json.loads(health_file.read_text(encoding="utf-8"))
            self.assertEqual(health["status"], "degraded")
            self.assertEqual(health["enqueued_signal_count"], 0)
            self.assertEqual(health["degraded_binding_count"], 1)
            self.assertIn(binding["binding_id"], health["degraded_bindings"])
            self.assertEqual(store.queue_depth(), 0)

    def test_approved_artifact_drives_order_fill_and_heartbeat(self) -> None:
        binding = _binding(_artifact(), binding_id="rb-current-runtime")
        runtime_binding = _runtime_binding(binding)
        now_iso = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        self.assertEqual(producer.produce(binding, now_iso), 1)

        telemetry = _Telemetry()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "PANTHEON_LIFECYCLE_OUTBOX_PATH": str(
                    Path(directory) / "lifecycle-outbox.json"
                )
            },
        ):
            service = PaperRuntimeService(
                store=store,
                identity=_identity(binding),
                runtime_manager_client=_RuntimeManager(runtime_binding),
                telemetry_emitter=telemetry,
                poll_interval_seconds=3600,
            )
            # A decision run_id uses the canonical three-bar rebalance buffer.
            # Drain through that boundary before asserting execution evidence.
            for _ in range(3):
                snapshot = service.drain_once()

        event_types = [event["event_type"] for event in telemetry.events]
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(
            snapshot["paper_state"]["processed_signal_count"],
            1,
            snapshot,
        )
        self.assertGreaterEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertIn("heartbeat", event_types)
        self.assertIn("order_submitted", event_types)
        self.assertIn("paper_fill_simulated", event_types)
        [fill] = [
            event
            for event in telemetry.events
            if event["event_type"] == "paper_fill_simulated"
        ]
        self.assertFalse(fill["metadata"]["submitted_to_broker"])

    def test_natural_spy_artifact_normalizes_to_executable_symbol_and_fills(self) -> None:
        """A natural SPY artifact produced by PersonaProvisioningCoordinator with explicit market 'US'
        normalizes bare symbol 'SPY' to 'SPY.US' and executes through the real executor parser without guessing.
        """
        record = ProvisioningRecord(
            tenant_id="tenant-dev",
            idempotency_key="dev-paper-release-37553990288-1",
            request_hash="sha256:test-request-hash",
            normalized_name="trader-natural-spy",
            persona_id="persona-paper-6581a1d6560eaa3a3e5c",
            request_payload={
                "name": "Natural SPY Trader",
                "market": "US",
                "symbols": ["SPY"],
                "data_source": "source-ingest:normalized/us-equity-price/daily",
            },
        )
        ids = deterministic_provisioning_ids(record)
        coordinator = PersonaProvisioningCoordinator(
            store=MemoryPersonaProvisioningStore(),
            transport=None,
            schedule_registrar=lambda *_: {},
            lease_owner="test-coordinator",
        )
        candidate_payload = coordinator._strategy_artifact_payload(record, ids)
        artifact = candidate_payload["strategy_artifact"]

        # Validate that the coordinator produced artifact with explicit market
        self.assertEqual(artifact["parameters"]["market"], "US")
        self.assertIn("market", artifact["mutation_surface"]["immutable_parameters"])
        self.assertEqual(artifact["parameters"]["symbols"], ["SPY"])
        validate_strategy_artifact(artifact)

        # Binding references the coordinator's artifact projection WITHOUT hand-fabricated market on binding
        binding = _binding(artifact, binding_id="rb-natural-spy-runtime", include_market_input=False)
        binding["symbol"] = "SPY"
        # Note: binding["market"] is NOT set - market comes strictly from artifact.parameters["market"]
        binding["market_input"] = {
            "symbol": "SPY",
            "closes": [400.0, 410.0],
            "source_ref": "source-ingest://normalized/us-price/SPY",
            "observed_at": _NOW,
        }
        runtime_binding = _runtime_binding(binding)
        now_iso = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        self.assertEqual(producer.produce(binding, now_iso), 1)

        self.assertEqual(store.queue_depth(), 1)
        signal = store._pending[0]
        self.assertEqual(signal["symbol"], "SPY.US")
        self.assertEqual(signal["metadata"]["raw_symbol"], "SPY")
        self.assertEqual(signal["metadata"]["artifact_checksum"], binding["artifact_checksum"])

        # Validate with real executor parser
        parsed = parse_executor_symbol(signal["symbol"])
        self.assertEqual(parsed.ticker, "SPY")
        self.assertEqual(parsed.lean_market, "Market.USA")
        self.assertEqual(parsed.lean_security_type, "SecurityType.Equity")
        validate_executable_symbol(signal["symbol"])

        telemetry = _Telemetry()
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "PANTHEON_LIFECYCLE_OUTBOX_PATH": str(
                    Path(directory) / "lifecycle-outbox.json"
                )
            },
        ):
            service = PaperRuntimeService(
                store=store,
                identity=_identity(binding),
                runtime_manager_client=_RuntimeManager(runtime_binding),
                telemetry_emitter=telemetry,
                poll_interval_seconds=3600,
            )
            for _ in range(3):
                snapshot = service.drain_once()

        event_types = [event["event_type"] for event in telemetry.events]
        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(
            snapshot["paper_state"]["processed_signal_count"],
            1,
            snapshot,
        )
        self.assertGreaterEqual(snapshot["paper_state"]["execution_event_count"], 1)
        self.assertIn("heartbeat", event_types)
        self.assertIn("order_submitted", event_types)
        self.assertIn("paper_fill_simulated", event_types)
        [fill] = [
            event
            for event in telemetry.events
            if event["event_type"] == "paper_fill_simulated"
        ]
        self.assertFalse(fill["metadata"]["submitted_to_broker"])

    def test_natural_taiwan_artifact_normalizes_to_taiwan_venue_symbol(self) -> None:
        """A natural TW artifact with bare ticker '2330' and market 'TW' produces '2330.TW'."""
        record = ProvisioningRecord(
            tenant_id="tenant-dev",
            idempotency_key="dev-paper-tw-1",
            request_hash="sha256:tw-hash",
            normalized_name="trader-tw",
            persona_id="persona-tw-1",
            request_payload={
                "name": "Taiwan Trader",
                "market": "TW",
                "symbols": ["2330"],
                "data_source": "source-ingest:normalized/tw-equity-price/daily",
            },
        )
        ids = deterministic_provisioning_ids(record)
        coordinator = PersonaProvisioningCoordinator(
            store=MemoryPersonaProvisioningStore(),
            transport=None,
            schedule_registrar=lambda *_: {},
            lease_owner="test-coordinator",
        )
        candidate_payload = coordinator._strategy_artifact_payload(record, ids)
        artifact = candidate_payload["strategy_artifact"]
        validate_strategy_artifact(artifact)
        self.assertEqual(artifact["parameters"]["market"], "TW")
        self.assertEqual(artifact["parameters"]["symbols"], ["2330"])

        binding = _binding(artifact, binding_id="rb-natural-tw-runtime", include_market_input=False)
        binding["symbol"] = "2330"
        binding["market_input"] = {
            "symbol": "2330",
            "closes": [500.0, 520.0],
            "source_ref": "source-ingest://normalized/tw-price/2330",
            "observed_at": _NOW,
        }
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        now_iso = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.assertEqual(producer.produce(binding, now_iso), 1)
        signal = store._pending[0]
        self.assertEqual(signal["symbol"], "2330.TW")
        self.assertTrue(is_taiwan_venue_symbol(signal["symbol"]))
        validate_executable_symbol(signal["symbol"])

    def test_natural_crypto_artifact_normalizes_to_executable_crypto_symbol(self) -> None:
        """A natural Crypto artifact with market 'CRYPTO' produces valid crypto executable symbol."""
        record = ProvisioningRecord(
            tenant_id="tenant-dev",
            idempotency_key="dev-paper-crypto-1",
            request_hash="sha256:crypto-hash",
            normalized_name="trader-crypto",
            persona_id="persona-crypto-1",
            request_payload={
                "name": "Crypto Trader",
                "market": "CRYPTO",
                "symbols": ["BTCUSDT"],
                "data_source": "source-ingest:normalized/crypto-price/daily",
            },
        )
        ids = deterministic_provisioning_ids(record)
        coordinator = PersonaProvisioningCoordinator(
            store=MemoryPersonaProvisioningStore(),
            transport=None,
            schedule_registrar=lambda *_: {},
            lease_owner="test-coordinator",
        )
        candidate_payload = coordinator._strategy_artifact_payload(record, ids)
        artifact = candidate_payload["strategy_artifact"]
        validate_strategy_artifact(artifact)
        self.assertEqual(artifact["parameters"]["market"], "CRYPTO")
        self.assertEqual(artifact["parameters"]["symbols"], ["BTCUSDT"])

        binding = _binding(artifact, binding_id="rb-natural-crypto-runtime", include_market_input=False)
        binding["symbol"] = "BTCUSDT"
        binding["market_input"] = {
            "symbol": "BTCUSDT",
            "closes": [60000.0, 61000.0],
            "source_ref": "source-ingest://normalized/crypto-price/BTCUSDT",
            "observed_at": _NOW,
        }
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        now_iso = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        self.assertEqual(producer.produce(binding, now_iso), 1)
        signal = store._pending[0]
        self.assertEqual(signal["symbol"], "BTCUSDT")
        parsed = parse_executor_symbol(signal["symbol"])
        self.assertEqual(parsed.ticker, "BTCUSDT")
        self.assertEqual(parsed.lean_market, "Market.Kraken")
        self.assertEqual(parsed.lean_security_type, "SecurityType.Crypto")
        validate_executable_symbol(signal["symbol"])

    def test_legacy_coordinator_artifact_without_market_fails_closed_market_context_missing(self) -> None:
        """Legacy artifact without explicit market fails closed rather than guessing market."""
        record = ProvisioningRecord(
            tenant_id="tenant-dev",
            idempotency_key="dev-paper-legacy-1",
            request_hash="sha256:legacy-hash",
            normalized_name="trader-legacy",
            persona_id="persona-legacy-1",
            request_payload={
                "name": "Legacy Trader",
                "symbols": ["SPY"],
            },
        )
        ids = deterministic_provisioning_ids(record)
        coordinator = PersonaProvisioningCoordinator(
            store=MemoryPersonaProvisioningStore(),
            transport=None,
            schedule_registrar=lambda *_: {},
            lease_owner="test-coordinator",
        )
        candidate_payload = coordinator._strategy_artifact_payload(record, ids)
        artifact = candidate_payload["strategy_artifact"]
        self.assertNotIn("market", artifact["parameters"])

        binding = _binding(artifact, binding_id="rb-legacy-runtime", include_market_input=False)
        binding["symbol"] = "SPY"
        binding["market_input"] = {
            "symbol": "SPY",
            "closes": [400.0, 410.0],
            "source_ref": "source-ingest://normalized/us-price/SPY",
            "observed_at": _NOW,
        }
        store = InMemoryPendingSignalStore()
        producer = PaperSignalProducer(
            store_for=lambda _: store,
            strategy=CurrentArtifactStrategy(),
        )
        now_iso = (
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
        strategy = CurrentArtifactStrategy()
        with self.assertRaises(SignalDecisionUnavailable) as ctx:
            strategy(binding, now_iso)
        self.assertEqual(ctx.exception.code, "market_context_missing")

        # Producer catches degradation and queues 0 signals
        self.assertEqual(producer.produce(binding, now_iso), 0)
        self.assertEqual(store.queue_depth(), 0)



if __name__ == "__main__":
    unittest.main()
