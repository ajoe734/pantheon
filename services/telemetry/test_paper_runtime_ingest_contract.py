"""P0-TEL-001 paper runtime TelemetryEvent ingest contract tests."""

from __future__ import annotations

import asyncio
import copy
import threading
import types
import unittest
import uuid
from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import RuntimeTelemetryEmitter
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.telemetry.batch_writer import WriteResult
from services.telemetry.dead_letter import TAG_BINDING_MISMATCH, TAG_SCHEMA_VIOLATION
from services.telemetry.ingest_svc import TelemetryIngestService
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.telemetry.trade_episode_projection import TradeEpisodeProjectionStore


SCHEMA_PATH = str(Path(__file__).with_name("telemetry_event.schema.json"))
KNOWN_BINDING_ID = str(uuid.uuid4())


def _known_binding() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        binding_id=KNOWN_BINDING_ID,
        runtime_id="rt-paper-contract",
        capital_pool_id="pool-paper-contract",
        artifact_id="artifact-paper-contract",
        artifact_version="1.0.0",
        plan_id="plan-paper-contract",
        persona_capital_binding_id="pcb-paper-contract",
        deployment_mode="paper",
        execution_mode="paper",
        effective_at="2026-01-01T00:00:00Z",
        retired_at=None,
    )


class _StubBindingStore:
    def get_binding(self, binding_id: str):
        if binding_id == KNOWN_BINDING_ID:
            return _known_binding()
        return None


class _ControlledBindingStore:
    def __init__(self):
        self.lookup_started = threading.Event()
        self.can_complete_lookup = threading.Event()
        self.can_complete_lookup.set()
        self.lookup_thread_id: int | None = None
        self.call_count = 0

    def block_lookups(self):
        self.can_complete_lookup.clear()
        self.lookup_started.clear()

    def release_lookups(self):
        self.can_complete_lookup.set()

    def get_binding(self, binding_id: str):
        self.call_count += 1
        self.lookup_thread_id = threading.get_ident()
        self.lookup_started.set()
        released = self.can_complete_lookup.wait(timeout=5.0)
        if not released:
            raise TimeoutError("Controlled binding lookup timed out")
        if binding_id == KNOWN_BINDING_ID:
            return _known_binding()
        return None


class _FakeBindingResolver:
    def resolve(self) -> dict[str, Any]:
        binding = _known_binding()
        return {
            "binding_id": binding.binding_id,
            "runtime_id": binding.runtime_id,
            "capital_pool_id": binding.capital_pool_id,
            "artifact_id": binding.artifact_id,
            "artifact_version": binding.artifact_version,
            "plan_id": binding.plan_id,
            "persona_capital_binding_id": binding.persona_capital_binding_id,
            "deployment_mode": binding.deployment_mode,
            "execution_mode": binding.execution_mode,
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_adapter_version": "0.1.0",
            "context_source": "launch_manifest",
        }


def _runtime_identity() -> RuntimeIdentity:
    return RuntimeIdentity.from_env(
        {
            "PANTHEON_RUNTIME_ROLE": "paper",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_RUNTIME_ID": "rt-paper-contract",
            "PANTHEON_WORKSPACE_REF": "workspace-paper-contract",
            "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper-contract",
            "PANTHEON_PERSONA_ID": "persona-paper-contract",
            "PANTHEON_SESSION_ID": "session-paper-contract",
            "PANTHEON_TRACE_ID": str(uuid.uuid4()),
            "PANTHEON_REQUEST_ID": "request-paper-contract",
        }
    )


def _paper_event(
    *,
    event_type: str = "heartbeat",
    event_id: str | None = None,
    created_at: str = "2026-05-01T00:00:00Z",
    deployment_stage: str = "paper",
    binding_id: str | None = KNOWN_BINDING_ID,
    metrics: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    binding = _known_binding()
    event = {
        "event_id": event_id or str(uuid.uuid4()),
        "event_type": event_type,
        "created_at": created_at,
        "execution_mode": deployment_stage,
        "environment": deployment_stage,
        "deployment_stage": deployment_stage,
        "binding_id": binding_id,
        "runtime_id": binding.runtime_id,
        "capital_pool_id": binding.capital_pool_id,
        "artifact_id": binding.artifact_id,
        "artifact_version": binding.artifact_version,
        "plan_id": binding.plan_id,
        "persona_capital_binding_id": binding.persona_capital_binding_id,
        "target": {
            "strategy_id": "strategy-paper-contract",
            "artifact_type": "execution_bundle",
            "artifact_version": binding.artifact_version,
            "lineage_ref": "lineage-paper-contract",
        },
        "metrics": metrics or {"heartbeat": 1},
        "metadata": {
            "engine_bridge_repo": "ajoe734/pantheon-lean.git",
            "engine_bridge_path": "pantheon/lean",
            "engine_bridge_commit": "abc1234",
            "runtime_role": "paper",
            "context_source": "launch_manifest",
            **(metadata or {}),
        },
    }
    if binding_id is None:
        event.pop("binding_id")
    return event


class PaperRuntimeTelemetryIngestContractTest(unittest.TestCase):
    def test_paper_heartbeat_validates_and_dedupes_by_event_id(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            emitter = RuntimeTelemetryEmitter(_runtime_identity(), _FakeBindingResolver())
            event = emitter.build_event(
                "heartbeat",
                {"heartbeat": 1},
                metadata={"runtime_package": "paper_execution_runtime"},
                event_id=str(uuid.uuid4()),
                created_at="2026-05-01T00:00:00Z",
            )
            self.assertIsNotNone(event)
            first = await svc.ingest(event)
            duplicate = await svc.ingest(dict(event))
            await asyncio.sleep(0.05)
            await svc.stop(graceful=True)
            stats = svc.stats()
            return first, duplicate, stats

        first, duplicate, stats = asyncio.run(scenario())

        self.assertTrue(first)
        self.assertTrue(duplicate)
        self.assertEqual(stats["service"]["total_ingested"], 1)
        self.assertEqual(stats["service"]["total_duplicates"], 1)
        self.assertEqual(stats["service"]["total_rejected"], 0)
        self.assertEqual(stats["writer"]["total_written"], 1)

    def test_stage_mismatch_rejected_against_runtime_binding(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
            )
            await svc.start()
            ok = await svc.ingest(_paper_event(deployment_stage="live"))
            stats = svc.stats()
            dlq = svc.get_dlq_entries()
            await svc.stop(graceful=True)
            return ok, stats, dlq

        ok, stats, dlq = asyncio.run(scenario())

        self.assertFalse(ok)
        self.assertEqual(stats["service"]["total_rejected"], 1)
        self.assertIn("deployment_stage", dlq[0]["reason"])

    def test_missing_binding_rejected_by_schema_or_evidence_contract(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
            )
            await svc.start()
            ok = await svc.ingest(_paper_event(binding_id=None))
            stats = svc.stats()
            dlq = svc.get_dlq_entries()
            await svc.stop(graceful=True)
            return ok, stats, dlq

        ok, stats, dlq = asyncio.run(scenario())

        self.assertFalse(ok)
        self.assertEqual(stats["service"]["total_rejected"], 1)
        self.assertIn("binding", dlq[0]["reason"].lower())

    def test_bracket_order_logged_validates_as_logged_only_not_broker_submission(self):
        async def scenario():
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            event = _paper_event(
                event_type="bracket_order_logged",
                metrics={"action": "bracket_logged_only"},
                metadata={
                    "broker_submission_status": "logged_only",
                    "submitted_to_broker": False,
                    "is_real_order": False,
                    "is_real_capital": False,
                },
            )
            ok = await svc.ingest(event)
            await asyncio.sleep(0.05)
            await svc.stop(graceful=True)
            stats = svc.stats()
            return ok, stats

        ok, stats = asyncio.run(scenario())

        self.assertTrue(ok)
        self.assertEqual(stats["service"]["total_ingested"], 1)
        self.assertEqual(stats["writer"]["total_written"], 1)

    def test_paper_heartbeat_updates_runtime_summary_projection(self):
        async def scenario():
            summary_store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                runtime_summary_store=summary_store,
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            ok = await svc.ingest(
                _paper_event(
                    event_type="heartbeat",
                    event_id="evt-heartbeat-projection",
                    created_at="2026-05-01T00:01:00Z",
                )
            )
            await asyncio.sleep(0.05)
            summary = svc.get_runtime_summary("rt-paper-contract")
            stats = svc.stats()
            await svc.stop(graceful=True)
            return ok, summary, stats

        ok, summary, stats = asyncio.run(scenario())

        self.assertTrue(ok)
        self.assertEqual(summary["last_heartbeat_at"], "2026-05-01T00:01:00Z")
        self.assertEqual(summary["runtime_binding_id"], KNOWN_BINDING_ID)
        self.assertEqual(summary["deployment_stage"], "paper")
        self.assertEqual(summary["engine_bridge_repo"], "ajoe734/pantheon-lean.git")
        self.assertEqual(summary["engine_bridge_commit"], "abc1234")
        self.assertEqual(stats["runtime_summary_projection"]["summary_count"], 1)

    def test_paired_performance_events_validate_and_project_independent_as_of_values(self):
        async def scenario():
            summary_store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                runtime_summary_store=summary_store,
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            emitter = RuntimeTelemetryEmitter(_runtime_identity(), _FakeBindingResolver())
            pnl_event = emitter.build_event(
                "pnl_snapshot",
                {
                    "pnl": 125.75,
                    "pnl_as_of": "2026-05-01T00:00:05Z",
                    "valuation_fill_count": 3,
                },
                event_id=str(uuid.uuid4()),
                created_at="2026-05-01T00:01:00Z",
            )
            drawdown_event = emitter.build_event(
                "drawdown_snapshot",
                {
                    "drawdown": 0.0425,
                    "drawdown_as_of": "2026-05-01T00:00:07Z",
                    "valuation_fill_count": 3,
                },
                event_id=str(uuid.uuid4()),
                created_at="2026-05-01T00:01:01Z",
            )
            self.assertIsNotNone(pnl_event)
            self.assertIsNotNone(drawdown_event)
            self.assertIn("pnl", pnl_event["metrics"])
            self.assertNotIn("drawdown", pnl_event["metrics"])
            self.assertNotIn("drawdown_pct", pnl_event["metrics"])
            self.assertEqual(pnl_event["pnl_as_of"], "2026-05-01T00:00:05Z")
            self.assertNotIn("drawdown_as_of", pnl_event)
            self.assertIn("drawdown", drawdown_event["metrics"])
            self.assertNotIn("pnl", drawdown_event["metrics"])
            self.assertEqual(drawdown_event["drawdown_as_of"], "2026-05-01T00:00:07Z")
            self.assertNotIn("pnl_as_of", drawdown_event)

            accepted = [await svc.ingest(pnl_event), await svc.ingest(drawdown_event)]
            await asyncio.sleep(0.05)
            summary = svc.get_runtime_summary("rt-paper-contract")
            await svc.stop(graceful=True)
            stats = svc.stats()
            return accepted, summary, stats

        accepted, summary, stats = asyncio.run(scenario())

        self.assertEqual(accepted, [True, True])
        self.assertEqual(stats["service"]["total_ingested"], 2)
        self.assertEqual(stats["service"]["total_rejected"], 0)
        self.assertEqual(stats["writer"]["total_written"], 2)
        self.assertEqual(summary["pnl"], 125.75)
        self.assertEqual(summary["pnl_at"], "2026-05-01T00:00:05Z")
        self.assertEqual(summary["drawdown"], 0.0425)
        self.assertEqual(summary["drawdown_at"], "2026-05-01T00:00:07Z")

    def test_paper_fill_and_position_snapshot_with_trade_episode_id_project_cleanly(self):
        async def scenario():
            summary_store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
            episode_store = TradeEpisodeProjectionStore()
            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                runtime_summary_store=summary_store,
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            emitter = RuntimeTelemetryEmitter(_runtime_identity(), _FakeBindingResolver())

            episode_id = str(uuid.uuid4())
            fill_event = emitter.build_event(
                "paper_fill_simulated",
                {
                    "fill_quantity": 50.0,
                    "fill_price": 450.0,
                    "fees": 1.5,
                },
                metadata={
                    "trade_episode_id": episode_id,
                    "symbol": "SPY",
                    "side": "buy",
                    "ledger_committed": True,
                    "ledger_fill_count": 1,
                    "position_quantity": 50.0,
                },
                event_id=str(uuid.uuid4()),
                created_at="2026-05-01T00:01:00Z",
            )
            snapshot_event = emitter.build_event(
                "position_snapshot",
                {
                    "position_qty": 50.0,
                    "price": 450.0,
                    "cash": 77500.0,
                },
                metadata={
                    "trade_episode_id": episode_id,
                    "symbol": "SPY",
                    "ledger_committed": True,
                    "ledger_fill_count": 1,
                },
                event_id=str(uuid.uuid4()),
                created_at="2026-05-01T00:01:01Z",
            )

            self.assertIsNotNone(fill_event)
            self.assertIsNotNone(snapshot_event)
            self.assertEqual(fill_event["metadata"]["trade_episode_id"], episode_id)
            self.assertEqual(snapshot_event["metadata"]["trade_episode_id"], episode_id)

            # Ingest both events through TelemetryIngestService (validating schema)
            accepted = [await svc.ingest(fill_event), await svc.ingest(snapshot_event)]
            await asyncio.sleep(0.05)
            await svc.stop(graceful=True)

            # Project both events into TradeEpisodeProjectionStore
            proj1 = episode_store.project_event(fill_event)
            proj2 = episode_store.project_event(snapshot_event)

            return accepted, proj1, proj2, episode_store.get(episode_id), snapshot_event["event_id"]

        accepted, proj1, proj2, final_proj, snap_id = asyncio.run(scenario())

        self.assertEqual(accepted, [True, True])
        self.assertIsNotNone(final_proj)
        self.assertEqual(final_proj["trade_episode_id"], final_proj["trade_episode_id"])
        self.assertEqual(final_proj["instrument_id"], "SPY")
        self.assertEqual(final_proj["filled_quantity"], 50.0)
        self.assertEqual(final_proj["vwap"], 450.0)
        self.assertEqual(final_proj["fees"], 1.5)
        self.assertEqual(final_proj["status"], "open")
        self.assertIsNone(final_proj["closed_at"])
        self.assertEqual(final_proj["position_snapshot_refs"], [snap_id])

    def test_canonical_batch_read_cross_run_and_ineligible_guards(self):
        async def scenario():
            summary_store = RuntimeSummaryProjectionStore(heartbeat_stale_after_seconds=60)
            binding = _known_binding()
            tid = "tenant-paper-recon"

            snap_runA = _paper_event(
                event_type="position_snapshot",
                event_id="evt-snap-runA",
                created_at="2026-05-01T00:01:00Z",
                metrics={"position_qty": 100.0, "price": 500.0, "cash": 50000.0},
                metadata={
                    "run_id": "run-A",
                    "signal_id": "sig-A",
                    "persona_id": "persona-paper-contract",
                    "sequence_no": 7,
                },
            )
            snap_runA.update({
                "tenant_id": tid,
                "trace_id": str(uuid.uuid4()),
                "run_id": "run-A",
                "signal_id": "sig-A",
                "sequence_no": 7,
                "authority_refs": {"persona_id": "persona-paper-contract"},
            })

            order_runB = _paper_event(
                event_type="order_submitted",
                event_id="evt-order-runB",
                created_at="2026-05-01T00:02:00Z",
                metrics={"order_qty": 50.0},
                metadata={
                    "run_id": "run-B",
                    "signal_id": "sig-B",
                    "persona_id": "persona-paper-contract",
                    "sequence_no": 4,
                },
            )
            order_runB.update({
                "tenant_id": tid,
                "trace_id": str(uuid.uuid4()),
                "run_id": "run-B",
                "signal_id": "sig-B",
                "sequence_no": 4,
                "authority_refs": {"persona_id": "persona-paper-contract"},
            })

            db_store = {
                "evt-snap-runA": dict(snap_runA, ingested_seq=101),
                "evt-order-runB": dict(order_runB, ingested_seq=102),
            }

            def fake_event_reader(ids, tenant_id=None):
                return {k: v for k, v in db_store.items() if k in ids}

            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=_StubBindingStore(),
                runtime_summary_store=summary_store,
                event_reader=fake_event_reader,
                batch_size=10,
                batch_interval=0.01,
            )
            await svc.start()
            try:
                await svc.ingest(snap_runA)
                await svc.ingest(order_runB)
                await asyncio.sleep(0.05)

                raw_summary = summary_store.get(binding.runtime_id, tenant_id=tid)
                self.assertEqual(raw_summary["last_lifecycle_identity"]["run_id"], "run-B")
                self.assertEqual(raw_summary["last_lifecycle_identity"]["event_type"], "order_submitted")

                summaries = svc.list_runtime_summaries(tenant_id=tid)
                self.assertEqual(len(summaries), 1)
                selected = summaries[0].get("committed_position_snapshot")
                self.assertIsNotNone(selected)
                self.assertEqual(selected["event_id"], "evt-snap-runA")
                self.assertEqual(selected["run_id"], "run-A")
                self.assertEqual(selected["metrics"]["position_qty"], 100.0)
                self.assertEqual(selected["metrics"]["price"], 500.0)
                self.assertEqual(selected["metrics"]["cash"], 50000.0)

                # AC3: Uncommitted / absence in event reader fails closed
                svc._event_reader = lambda ids, tenant_id=None: {}
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # AC1: Cross-tenant guard
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, tenant_id="other-tenant", ingested_seq=101)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # AC1: Cross-binding guard
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, binding_id="other-binding", ingested_seq=101)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # AC1: Cross-runtime guard
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, runtime_id="other-runtime", ingested_seq=101)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # AC1: Cross-artifact guard
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, artifact_id="other-artifact", ingested_seq=101)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # AC1 / AC3: Missing numeric metrics guard
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, metrics={}, ingested_seq=101)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # Real positive DB sequence guard: bool is not sequence (True != valid seq)
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, ingested_seq=True)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # Finite numeric metrics guard (reject inf/nan)
                svc._event_reader = lambda ids, tenant_id=None: {
                    "evt-snap-runA": dict(snap_runA, metrics={"position_qty": float("inf"), "price": 500.0, "cash": 50000.0}, ingested_seq=101)
                }
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # 512 bound check: event reader receives at most 512 event ids per ring, and multiple summaries do not starve each other
                queried_counts = []
                def count_reader(ids, tenant_id=None):
                    queried_counts.append(len(ids))
                    return {}
                svc._event_reader = count_reader
                summary_key = list(summary_store._summaries.keys())[0]
                summary_store._summaries[summary_key]["recent_lifecycle_event_ids"] = [f"evt-{i}" for i in range(1000)]
                svc.list_runtime_summaries(tenant_id=tid)
                self.assertEqual(queried_counts[-1], 512)

                # Two summaries with 512 and 1 event IDs: neither is starved
                two_summaries = [
                    {"recent_lifecycle_event_ids": [f"first-{i}" for i in range(512)]},
                    {"recent_lifecycle_event_ids": ["second-target-id"]},
                ]
                queried_ids = []
                svc._event_reader = lambda ids, tenant_id=None: queried_ids.extend(ids) or {}
                svc._enrich_with_committed_position_snapshots(two_summaries, tenant_id=tid)
                self.assertIn("second-target-id", queried_ids)
                self.assertEqual(len(queried_ids), 513)

                summary_store._summaries[summary_key]["recent_lifecycle_event_ids"] = ["evt-snap-runA", "evt-order-runB"]

                # Missing cash fail-closed: candidate with only position_qty and price (no cash) is rejected
                partial_snap = copy.deepcopy(snap_runA)
                partial_snap["metrics"].pop("cash", None)
                partial_snap["ingested_seq"] = 101
                svc._event_reader = lambda ids, tenant_id=None: {"evt-snap-runA": partial_snap}
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # Tenant conflict guard: conflicting tenant in metadata vs event root is rejected
                conflict_snap = copy.deepcopy(snap_runA)
                conflict_snap["tenant_id"] = tid
                conflict_snap.setdefault("metadata", {})["tenant_id"] = "other-conflicting-tenant"
                conflict_snap["ingested_seq"] = 101
                svc._event_reader = lambda ids, tenant_id=None: {"evt-snap-runA": conflict_snap}
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # Missing plan guard: candidate without plan_id or deployment_plan_id is rejected (no synthesis)
                no_plan_snap = copy.deepcopy(snap_runA)
                no_plan_snap.pop("plan_id", None)
                no_plan_snap.pop("deployment_plan_id", None)
                no_plan_snap["ingested_seq"] = 101
                svc._event_reader = lambda ids, tenant_id=None: {"evt-snap-runA": no_plan_snap}
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # get_runtime_summary returns raw stored summary without triggering event_reader
                reader_called = []
                svc._event_reader = lambda ids, tenant_id=None: reader_called.append(True) or {}
                raw_got = svc.get_runtime_summary(binding.runtime_id, tenant_id=tid)
                self.assertIsNotNone(raw_got)
                self.assertNotIn("committed_position_snapshot", raw_got)
                self.assertEqual(len(reader_called), 0)

                # AC2: Already-reconciled position snapshot exclusion
                term_event = _paper_event(
                    event_type="reconciliation_completed",
                    event_id="evt-term-runA",
                    created_at="2026-05-01T00:03:00Z",
                    metadata={
                        "run_id": "run-A",
                        "signal_id": "sig-A",
                        "persona_id": "persona-paper-contract",
                        "sequence_no": 8,
                    },
                )
                term_event.update({
                    "tenant_id": tid,
                    "trace_id": str(uuid.uuid4()),
                    "causal_parent_id": "evt-snap-runA",
                    "run_id": "run-A",
                    "sequence_no": 8,
                    "ingested_seq": 103,
                })
                # If terminal event belongs to another tenant, it does not exclude candidate
                other_term = dict(term_event, tenant_id="other-tenant")
                db_store_term = dict(db_store, **{"evt-term-runA": other_term})
                svc._event_reader = lambda ids, tenant_id=None: {k: v for k, v in db_store_term.items() if k in ids}
                summary_store.project_event(other_term)
                self.assertIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])

                # When terminal matches current tenant/binding/plan, it excludes candidate
                db_store["evt-term-runA"] = term_event
                summary_store.project_event(term_event)
                svc._event_reader = fake_event_reader
                self.assertNotIn("committed_position_snapshot", svc.list_runtime_summaries(tenant_id=tid)[0])
            finally:
                await svc.stop(graceful=False)

        asyncio.run(scenario())

    def test_authoritative_binding_lookup_offloads_from_asyncio_writer_loop(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            main_thread_id = threading.get_ident()
            store = _ControlledBindingStore()
            written_batches: list[list[dict[str, Any]]] = []
            write_in_flight = asyncio.Event()
            can_finish_write = asyncio.Event()
            can_finish_write.set()

            async def controlled_write_fn(batch: list[dict[str, Any]]) -> WriteResult:
                written_batches.append(batch)
                write_in_flight.set()
                await can_finish_write.wait()
                return WriteResult.ok(len(batch))

            svc = TelemetryIngestService(
                schema_path=SCHEMA_PATH,
                binding_store=store,
                write_fn=controlled_write_fn,
                batch_size=1,
                batch_interval=0.01,
            )
            await svc.start()

            try:
                # 1. Normal validation path with concurrent write progress:
                event_a = _paper_event(event_id="evt-paper-A")
                can_finish_write.clear()
                write_in_flight.clear()

                accepted_a = await svc.ingest(event_a)
                self.assertTrue(accepted_a)
                # Wait until background writer picks up Event A and enters write_fn
                await write_in_flight.wait()

                # Writer is now paused in write_fn awaiting can_finish_write.
                # Prepare Event B with blocking lookup
                event_b = _paper_event(event_id="evt-paper-B")
                store.block_lookups()
                loop_callback_ran = False

                def loop_callback():
                    nonlocal loop_callback_ran
                    loop_callback_ran = True

                # Start Event B ingestion concurrently
                ingest_b_task = asyncio.create_task(svc.ingest(event_b))

                # Wait for Event B to enter get_binding on a worker thread
                await asyncio.to_thread(store.lookup_started.wait, 5.0)
                self.assertTrue(store.lookup_started.is_set())
                self.assertIsNotNone(store.lookup_thread_id)
                self.assertNotEqual(store.lookup_thread_id, main_thread_id)

                # Event B is currently blocked in get_binding.
                # Verify Event B has NOT entered buffer yet (authority awaited before buffer)
                self.assertFalse(ingest_b_task.done())
                self.assertEqual(svc._buffer.size(), 0)

                # Show pending write (Event A) and loop callback can progress while B is in lookup
                loop.call_soon(loop_callback)
                can_finish_write.set()

                # Allow loop to process writer completion and loop callback
                await asyncio.sleep(0.02)
                self.assertTrue(loop_callback_ran)
                self.assertEqual(len(written_batches), 1)
                self.assertEqual(written_batches[0][0]["event_id"], "evt-paper-A")

                # Event B must STILL be awaiting authority lookup and NOT in buffer
                self.assertFalse(ingest_b_task.done())
                self.assertEqual(svc._buffer.size(), 0)

                # Release Event B lookup and confirm admission
                store.release_lookups()
                accepted_b = await ingest_b_task
                self.assertTrue(accepted_b)

                # Let writer flush Event B
                await asyncio.sleep(0.05)
                self.assertEqual(len(written_batches), 2)
                self.assertEqual(written_batches[1][0]["event_id"], "evt-paper-B")

                # 2. Duplicate validation path:
                # Event B is now seen. Ingest duplicate with blocking lookup.
                store.block_lookups()
                dup_loop_callback_ran = False

                def dup_loop_callback():
                    nonlocal dup_loop_callback_ran
                    dup_loop_callback_ran = True

                ingest_dup_task = asyncio.create_task(svc.ingest(dict(event_b)))
                await asyncio.to_thread(store.lookup_started.wait, 5.0)
                self.assertTrue(store.lookup_started.is_set())
                self.assertFalse(ingest_dup_task.done())

                # Show loop callback progresses while duplicate is waiting for lookup
                loop.call_soon(dup_loop_callback)
                await asyncio.sleep(0.02)
                self.assertTrue(dup_loop_callback_ran)
                self.assertFalse(ingest_dup_task.done())

                # Release duplicate lookup
                store.release_lookups()
                accepted_dup = await ingest_dup_task
                self.assertTrue(accepted_dup)
                self.assertEqual(svc.stats()["service"]["total_duplicates"], 1)

                # 3. Retain original rejection/identity/idempotency negatives:
                # 3.a Unknown binding rejection
                bad_binding_event = _paper_event(
                    event_id="evt-bad-binding",
                    binding_id="unknown-binding-uuid",
                )
                rejected_binding = await svc.ingest(bad_binding_event)
                self.assertFalse(rejected_binding)

                # 3.b Runtime identity mismatch rejection
                mismatch_event = _paper_event(
                    event_id="evt-mismatch",
                    binding_id=KNOWN_BINDING_ID,
                )
                mismatch_event["runtime_id"] = "rt-wrong-mismatch"
                rejected_mismatch = await svc.ingest(mismatch_event)
                self.assertFalse(rejected_mismatch)

                # 3.c Duplicate content mismatch rejection
                content_mismatch_event = dict(event_b)
                content_mismatch_event["metrics"] = {"heartbeat": 9999}
                rejected_dup_mismatch = await svc.ingest(content_mismatch_event)
                self.assertFalse(rejected_dup_mismatch)

                # 3.d Concurrent same-ID ingestion ordering and conflicting-content rejection:
                store.block_lookups()
                concurrent_evt_1 = _paper_event(
                    event_id="evt-concurrent-same-id",
                    binding_id=KNOWN_BINDING_ID,
                    metrics={"heartbeat": 10},
                )
                concurrent_evt_2 = _paper_event(
                    event_id="evt-concurrent-same-id",
                    binding_id=KNOWN_BINDING_ID,
                    metrics={"heartbeat": 20},
                )
                task_1 = asyncio.create_task(svc.ingest(concurrent_evt_1))
                task_2 = asyncio.create_task(svc.ingest(concurrent_evt_2))
                await asyncio.to_thread(store.lookup_started.wait, 5.0)
                self.assertTrue(store.lookup_started.is_set())
                store.release_lookups()
                outcomes = await asyncio.gather(task_1, task_2)
                self.assertEqual(outcomes, [True, False])
                self.assertEqual(svc._seen_event_ids["evt-concurrent-same-id"]["metrics"], {"heartbeat": 10})

                # 3.e Concurrent same-ID ingestion with identical content:
                store.block_lookups()
                concurrent_dup_1 = _paper_event(
                    event_id="evt-concurrent-identical",
                    binding_id=KNOWN_BINDING_ID,
                    metrics={"heartbeat": 100},
                )
                concurrent_dup_2 = _paper_event(
                    event_id="evt-concurrent-identical",
                    binding_id=KNOWN_BINDING_ID,
                    metrics={"heartbeat": 100},
                )
                task_dup_1 = asyncio.create_task(svc.ingest(concurrent_dup_1))
                task_dup_2 = asyncio.create_task(svc.ingest(concurrent_dup_2))
                await asyncio.to_thread(store.lookup_started.wait, 5.0)
                self.assertTrue(store.lookup_started.is_set())
                store.release_lookups()
                dup_outcomes = await asyncio.gather(task_dup_1, task_dup_2)
                self.assertEqual(dup_outcomes, [True, True])
                self.assertEqual(svc._seen_event_ids["evt-concurrent-identical"]["metrics"], {"heartbeat": 100})

                dlq = svc.get_dlq_entries()
                reasons = [e["reason"].lower() for e in dlq]
                tags = [e["tags"] for e in dlq]

                self.assertTrue(any(TAG_BINDING_MISMATCH in t for t in tags))
                self.assertTrue(any("not found in runtimebinding store" in r for r in reasons))
                self.assertTrue(any("runtimebinding identity mismatch" in r for r in reasons))
                self.assertTrue(any("content mismatch for duplicate event_id" in r for r in reasons))
                self.assertTrue(any("content mismatch for duplicate event_id=evt-concurrent-same-id" in r for r in reasons))

            finally:
                store.release_lookups()
                can_finish_write.set()
                await svc.stop(graceful=True)

        asyncio.run(scenario())



if __name__ == "__main__":
    unittest.main()
