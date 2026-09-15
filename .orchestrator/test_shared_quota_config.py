"""Source-config regression: shared quota never merges worker identities.

All observations are synthetic, in memory or in the existing temporary
TaskStore harness. Account labels are read from config, never printed.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervisor
import test_supervisor as fixtures
from rewrite import dispatch_admission as admission
from rewrite import provider_health as health


class SharedQuotaConfigTests(unittest.TestCase):
    def setUp(self):
        self.environment = mock.patch.dict(os.environ, {
            k: v for k, v in os.environ.items() if not k.startswith("PANTHEON_")
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = json.loads(Path(__file__).with_name("config.json").read_text())
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        self.clock = mock.patch.object(health, "_utc_now", side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.lanes = [supervisor.delivery_lane_for_agent(self.config, name)
                      for name in ("claude", "claude2")]

    def healthy(self, names=("claude", "claude2")):
        snapshot = health.empty_delivery_health()
        for name in names:
            for endpoint in supervisor.delivery_lane_for_agent(self.config, name).endpoints:
                snapshot = health.apply_probe(
                    snapshot, endpoint_id=endpoint.endpoint_id,
                    account_id=endpoint.account_id,
                    probe={"source": "live", "ready": True},
                    observed_at=self.now, valid_for_seconds=3600)
        return {"workers": {}, "queue": {"events": {}}, "delivery_health": snapshot}

    def decision(self, state, lane, **reservations):
        snapshot = admission.AdmissionSnapshot(
            now=self.now,
            endpoint_health=supervisor._admission_health_records(state["delivery_health"], "endpoints"),
            account_health=supervisor._admission_health_records(state["delivery_health"], "accounts"),
            account_limits=self.config["ready_dispatcher"]["max_concurrent_per_account"],
            **reservations)
        return admission.evaluate_dispatch_intent(
            admission.TaskIntent("QUOTA-TEST", "in_progress", lane.assignment_identity,
                                 "Codex2", True), lane, snapshot)

    def fail_quota(self, state, lane, retry_at=None):
        self.assertTrue(supervisor.record_delivery_health_failure(
            self.config, state, agent_id=lane.lane_id,
            failure_kind="quota_terminal", retry_at=retry_at))

    def test_real_schema_shared_account_and_independent_identity(self):
        try:
            supervisor.validate_provider_accounts(self.config)
        except ValueError:
            self.fail("Source provider-account schema invalid (details redacted)")
        left, right = self.lanes
        a, b = left.endpoints[0], right.endpoints[0]
        self.assertTrue(bool(a.account_id) and a.account_id == b.account_id,
                        "Verified lanes must share the existing account")
        self.assertTrue(a.provider_id != b.provider_id)
        self.assertTrue(a.endpoint_id != b.endpoint_id)
        self.assertNotEqual(left.assignment_identity, right.assignment_identity)
        # Shared quota does not disqualify independent review after health returns.
        self.assertEqual(supervisor.plan_task_assignment_pair(
            self.config, {"owner": "Claude", "reviewer": "Claude2", "status": "todo"},
            state=self.healthy(), fixed_owner="Claude", allowed_reviewers=["Claude2"]),
            ("Claude", "Claude2"))

    def test_active_counts_and_capacity_are_shared(self):
        state = self.healthy()
        account = self.lanes[0].endpoints[0].account_id
        limit = self.config["ready_dispatcher"]["max_concurrent_per_account"][account]
        self.assertGreaterEqual(limit, 2)
        state["workers"] = {
            str(i): {"provider": self.lanes[i % 2].endpoints[0].provider_id, "status": "running"}
            for i in range(limit)
        }
        state["workers"]["finished"] = {
            "provider": self.lanes[0].endpoints[0].provider_id, "status": "completed"}
        counts = supervisor.active_account_counts(self.config, state, {"running"})
        self.assertEqual(len(counts), 1)
        self.assertEqual(counts.get(account), limit)
        for lane in self.lanes:
            self.assertEqual(self.decision(state, lane, account_reserved=counts).reason,
                             admission.DispatchBlockReason.ACCOUNT_CAPACITY_REACHED)

    def test_either_terminal_quota_blocks_both_until_fresh_reset_probe(self):
        for failed_lane in self.lanes:
            for explicit_reset in (False, True):
                with self.subTest(lane=failed_lane.lane_id, explicit_reset=explicit_reset):
                    state = self.healthy()
                    for lane in self.lanes:
                        self.assertTrue(self.decision(state, lane).eligible)
                    retry = self.now + timedelta(seconds=120)
                    self.fail_quota(state, failed_lane, retry.isoformat() if explicit_reset else None)
                    account = failed_lane.endpoints[0].account_id
                    entry = state["delivery_health"]["accounts"][account]
                    self.assertEqual(len(state["delivery_health"]["accounts"]), 1)
                    self.assertEqual(entry["reason_kind"], "quota_terminal")
                    for lane in self.lanes:
                        self.assertEqual(self.decision(state, lane).reason,
                                         admission.DispatchBlockReason.ACCOUNT_RETRY_AFTER)
                    self.now = datetime.fromisoformat(entry["retry_at"].replace("Z", "+00:00")) + timedelta(seconds=1)
                    for lane in self.lanes:
                        decision = self.decision(state, lane)
                        self.assertFalse(decision.eligible)
                        self.assertEqual(decision.reason, admission.DispatchBlockReason.HEALTH_REFRESH_REQUIRED)
                    # Reset time alone is not health evidence. Existing live-probe
                    # semantics restore the shared account; cached evidence cannot.
                    endpoint = failed_lane.endpoints[0]
                    for source in ("cached", "live"):
                        state["delivery_health"] = health.apply_probe(
                            state["delivery_health"], endpoint_id=endpoint.endpoint_id,
                            account_id=account, probe={"source": source, "ready": True},
                            observed_at=self.now)
                        for lane in self.lanes:
                            self.assertEqual(self.decision(state, lane).eligible, source == "live")

    def test_auth_failure_remains_endpoint_local(self):
        for failed_lane, peer in (self.lanes, self.lanes[::-1]):
            state = self.healthy()
            supervisor.record_delivery_health_failure(
                self.config, state, agent_id=failed_lane.lane_id, failure_kind="auth")
            self.assertFalse(self.decision(state, failed_lane).eligible)
            self.assertTrue(self.decision(state, peer).eligible)

    def test_recovery_fallback_and_duplicate_lease_admission(self):
        for failed_lane in self.lanes:
            state = self.healthy(("claude", "claude2", "antigravity"))
            self.fail_quota(state, failed_lane)
            task = fixtures.task_fixture(owner=failed_lane.assignment_identity, reviewer="Human/Ops")
            receipt = {"status": "pending", "recovery_role": "owner",
                       "worker": {"agent": failed_lane.assignment_identity}}
            status = {"tasks": [task]}
            before = copy.deepcopy((task, state))
            self.assertEqual(supervisor.worker_recovery_assignment_pair(
                self.config, state, status, task, receipt), ("Antigravity", "Human/Ops"))
            self.assertTrue((task, state) == before, "Selection must not mutate authority")
            fallback = supervisor.delivery_lane_for_agent(self.config, "antigravity")
            self.assertTrue(self.decision(state, fallback).eligible)
            self.assertEqual(self.decision(state, fallback, leased_task_ids=frozenset({"QUOTA-TEST"})).reason,
                             admission.DispatchBlockReason.TASK_LEASED)
            self.assertEqual(self.decision(state, fallback, pending_task_ids=frozenset({"QUOTA-TEST"})).reason,
                             admission.DispatchBlockReason.TASK_PENDING)
            # A healthy eligible lane with no spare shared capacity cannot be selected.
            account = fallback.endpoints[0].account_id
            limit = self.config["ready_dispatcher"]["max_concurrent_per_account"][account]
            state["workers"] = {str(i): {
                "provider": fallback.endpoints[0].provider_id, "agent_id": "antigravity",
                "task_id": f"OCCUPIED-{i}", "status": "running"} for i in range(limit)}
            self.assertIsNone(supervisor.worker_recovery_assignment_pair(
                self.config, state, status, task, receipt))

    def test_canonical_recovery_holds_then_reserves_one_fallback(self):
        # Reuse the existing isolated authoritative TaskStore harness, retaining
        # the complete real source topology, capacity and fallback configuration.
        harness = fixtures.DurableWorkerRecoveryTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        self.config["paths"] = harness.config["paths"]
        self.config["task_state_store"] = harness.config["task_state_store"]
        harness.config = self.config
        task = fixtures.task_fixture(owner="Claude", reviewer="Human/Ops", status="in_progress")
        supervisor.write_status(self.config, {"tasks": [task], "blockers": [], "handoffs": []},
                                source="test-shared-quota-seed")
        state = self.healthy()
        worker = harness._worker(agent_id="claude")
        harness._store_started(state, worker)
        self.fail_quota(state, self.lanes[0])
        with mock.patch.object(supervisor, "sync_status_pipeline", side_effect=harness._drain_status_outbox):
            self.assertTrue(supervisor.recover_lost_worker_lease(
                self.config, state, worker, reason_kind="worker_process_missing", reason="synthetic terminal quota"))
            held = supervisor.load_status(self.config)
            held_task = held["tasks"][0]
            self.assertTrue(supervisor.task_has_pending_worker_recovery(held_task))
            self.assertEqual(held_task["owner"], "Claude")
            self.assertEqual(supervisor.build_dispatch_plan(
                self.config, state, held, supervisor.queue_events(state), live_total=0)["events"], [])
            # Only the unrelated eligible fallback gains fresh health. Shared
            # lanes remain exhausted throughout canonical reassignment/replay.
            refreshed = self.healthy(("antigravity",))["delivery_health"]
            for bucket in ("accounts", "endpoints"):
                state["delivery_health"][bucket].update(refreshed[bucket])
            for _ in range(3):
                supervisor.reconcile_pending_worker_recoveries(self.config, state)
            latest = supervisor.load_status(self.config)
            self.assertEqual(latest["tasks"][0]["owner"], "Antigravity")
            self.assertEqual(latest["tasks"][0]["generation"], 3)
            receipts = latest[supervisor.WORKER_RECOVERY_RECEIPTS_KEY]
            self.assertEqual(len(receipts), 1)
            receipt_id = next(iter(receipts))
            self.assertEqual(receipts[receipt_id]["status"], "reassigned")
            events = [e for e in supervisor.queue_events(state) if e.get("recovery_receipt_id") == receipt_id]
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["target_agent"], "antigravity")
            self.assertEqual(worker["status"], "superseded")
            self.assertEqual(len(state["workers"]), 1, "Recovery must leave launch to the existing planner")


if __name__ == "__main__":
    unittest.main()
