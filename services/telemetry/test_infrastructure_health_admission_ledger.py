"""TELEMETRY-STORAGE-BOUNDED-20261021 — bounded infrastructure health ledger.

Proves the dedupe horizon: events inside it stay idempotent across compaction
and restart, events past it are refused, and a live reservation is never
compacted away.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from services.telemetry.ingest_svc import (
    InfrastructureHealthAdmissionLedger,
    TelemetryIngestService,
)
from services.telemetry.test_infrastructure_health_ingest import (
    _DurableFileBroker,
    _SCHEMA_PATH,
    _infra_event,
)

HORIZON = 3600.0


def _stamp(delta_seconds: float) -> str:
    moment = datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _lines(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _admit(ledger, event_id: str, created_at: str, *, commit: bool = True):
    fingerprint = f"sha256:{event_id}"
    reservation = ledger.begin(event_id, fingerprint, attributes={"created_at": created_at})
    if commit:
        ledger.commit(event_id, fingerprint, reservation.token)
    return fingerprint, reservation


class LedgerHorizonTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = str(Path(self._tmp.name) / "ledger.jsonl")

    def _ledger(self, **kwargs):
        return InfrastructureHealthAdmissionLedger(
            self.path, dedupe_horizon_seconds=HORIZON, **kwargs
        )

    def test_within_horizon(self):
        ledger = self._ledger()
        self.assertTrue(ledger.within_horizon(_stamp(-60)))
        self.assertTrue(ledger.within_horizon(_stamp(60)))
        self.assertFalse(ledger.within_horizon(_stamp(-HORIZON - 60)))

    def test_duplicate_inside_horizon_is_idempotent_after_compaction_and_restart(self):
        ledger = self._ledger()
        fingerprint, _ = _admit(ledger, "fresh-1", _stamp(-60))
        _admit(ledger, "old-1", _stamp(-HORIZON - 600))

        ledger.compact()
        self.assertEqual({r["event_id"] for r in _lines(self.path)}, {"fresh-1"})
        self.assertEqual(ledger.begin("fresh-1", fingerprint).outcome, ledger.OUTCOME_COMMITTED)

        restarted = self._ledger()
        self.assertEqual(restarted.begin("fresh-1", fingerprint).outcome, restarted.OUTCOME_COMMITTED)
        self.assertEqual(restarted.stats()["committed_event_ids"], 1)

    def test_live_reservation_survives_compaction_even_if_event_is_old(self):
        ledger = self._ledger()
        fingerprint, reservation = _admit(
            ledger, "held-1", _stamp(-HORIZON - 600), commit=False
        )
        self.assertEqual(reservation.outcome, ledger.OUTCOME_RESERVED)
        ledger.compact()
        self.assertEqual(ledger.state_of("held-1"), ledger.STATE_RESERVED)
        self.assertEqual([r["event_id"] for r in _lines(self.path)], ["held-1"])
        self.assertEqual(
            ledger.commit("held-1", fingerprint, reservation.token), ledger.OUTCOME_COMMITTED
        )

    def test_expired_old_reservation_and_released_records_are_dropped(self):
        ledger = self._ledger(lease_seconds=0.1)
        _admit(ledger, "stale-1", _stamp(-HORIZON - 600), commit=False)
        _, reservation = _admit(ledger, "released-1", _stamp(-60), commit=False)
        ledger.release("released-1", reservation.token)
        time.sleep(0.2)
        ledger.compact()
        self.assertEqual(_lines(self.path), [])

    def test_startup_replay_does_not_retain_expired_records(self):
        ledger = self._ledger()
        for index in range(20):
            _admit(ledger, f"old-{index}", _stamp(-HORIZON - 600))
        _admit(ledger, "fresh-1", _stamp(-60))
        restarted = self._ledger()
        self.assertEqual(set(restarted._records), {"fresh-1"})

    def test_second_replica_reopens_after_compaction(self):
        first = self._ledger()
        second = self._ledger()
        _admit(first, "old-1", _stamp(-HORIZON - 600))
        fingerprint, _ = _admit(first, "fresh-1", _stamp(-60))
        first.compact()
        self.assertEqual(second.begin("fresh-1", fingerprint).outcome, second.OUTCOME_COMMITTED)
        _admit(second, "fresh-2", _stamp(-30))
        self.assertEqual({r["event_id"] for r in _lines(self.path)}, {"fresh-1", "fresh-2"})

    def test_append_between_replace_and_bookkeeping_is_still_deduped(self):
        first = self._ledger()
        second = self._ledger()
        _admit(first, "old-1", _stamp(-HORIZON - 600))
        _admit(first, "fresh-1", _stamp(-60))
        real_replace = os.replace
        racer_fp = None

        def replace_then_append(src, dst):
            nonlocal racer_fp
            real_replace(src, dst)
            racer_fp, _ = _admit(second, "racer-1", _stamp(-10))

        with patch("services.telemetry.ingest_svc.os.replace", side_effect=replace_then_append):
            first.compact()

        self.assertIsNotNone(racer_fp)
        self.assertEqual(first.begin("racer-1", racer_fp).outcome, first.OUTCOME_COMMITTED)


class ServiceHorizonTest(unittest.TestCase):
    def test_event_older_than_horizon_is_rejected_and_recent_is_admitted(self):
        async def run():
            with tempfile.TemporaryDirectory() as tmp:
                svc = TelemetryIngestService(
                    schema_path=_SCHEMA_PATH,
                    storage_dir=tmp,
                    buffer=_DurableFileBroker(str(Path(tmp) / "broker.jsonl")),
                    infrastructure_health_dedupe_horizon_seconds=HORIZON,
                )
                await svc.start()
                try:
                    old = await svc.ingest_infrastructure_health(
                        _infra_event("too-old-1", created_at=_stamp(-HORIZON - 600))
                    )
                    fresh_event = _infra_event("fresh-1", created_at=_stamp(-60))
                    fresh = await svc.ingest_infrastructure_health(fresh_event)
                    again = await svc.ingest_infrastructure_health(fresh_event)
                    state = svc._infrastructure_health_ledger.state_of("too-old-1")
                    return old, fresh, again, state
                finally:
                    await svc.stop()

        old, fresh, again, old_state = asyncio.run(run())
        self.assertEqual(old["status"], "rejected")
        self.assertEqual(old["code"], "INFRA_EVENT_TOO_OLD")
        self.assertIsNone(old_state)
        self.assertEqual(fresh["status"], "accepted")
        self.assertEqual(again["status"], "duplicate")


if __name__ == "__main__":
    unittest.main()
