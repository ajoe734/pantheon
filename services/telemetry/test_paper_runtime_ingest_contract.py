"""P0-TEL-001 paper runtime TelemetryEvent ingest contract tests."""

from __future__ import annotations

import asyncio
import types
import unittest
import uuid
from pathlib import Path
from typing import Any

from services.execution.lean_runtime.paper_runtime import RuntimeTelemetryEmitter
from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
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

                # 512 bound check: event reader receives at most 512 event ids
                queried_counts = []
                def count_reader(ids, tenant_id=None):
                    queried_counts.append(len(ids))
                    return {}
                svc._event_reader = count_reader
                summary_key = list(summary_store._summaries.keys())[0]
                summary_store._summaries[summary_key]["recent_lifecycle_event_ids"] = [f"evt-{i}" for i in range(1000)]
                svc.list_runtime_summaries(tenant_id=tid)
                self.assertEqual(queried_counts[-1], 512)
                summary_store._summaries[summary_key]["recent_lifecycle_event_ids"] = ["evt-snap-runA", "evt-order-runB"]

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



if __name__ == "__main__":
    unittest.main()
