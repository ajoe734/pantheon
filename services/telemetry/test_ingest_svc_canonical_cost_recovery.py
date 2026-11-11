"""Startup cost recovery from canonical (Postgres-shaped) fill history."""
import json
import tempfile
import unittest
from pathlib import Path

from services.telemetry.ingest_svc import CanonicalFillHistoryReader
from services.telemetry.runtime_summary import RuntimeSummaryProjectionStore
from services.telemetry import test_runtime_summary_projection as _trsp

_T = _trsp.TestPositionNetPnlRecognizedCosts


class _Empty:
    def retained_paper_fill_events(self, **_kw):
        return []


class CanonicalCostRecoveryTest(unittest.TestCase):
    def _setup(self, tmp):
        path, fill, _ = _T()._legacy_file(tmp, retained=False)
        return path, fill

    def _reader(self, rows):
        calls = []

        def be(bid, tenant_id=None, limit=50):
            calls.append((bid, tenant_id))
            return rows
        return CanonicalFillHistoryReader(_Empty(), be), calls

    def test_known_srf_fill_recovered_across_restart_and_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, fill = self._setup(tmp)
            reader, calls = self._reader([fill, dict(fill)])
            store = RuntimeSummaryProjectionStore(path=path)
            self.assertEqual(store.backfill_open_position_costs(reader), 1)
            store.project_event(dict(fill))
            row = store.project_event(_T._mark(97.1, 1))["positions"][0]
            self.assertEqual(row["costs"], 19.884)
            self.assertEqual(row["realized_pnl"], -19.884)
            self.assertEqual(calls[0], ("rb-cost-001", "tenant-dev"))
            again = RuntimeSummaryProjectionStore(path=path)
            self.assertEqual(again.backfill_open_position_costs(reader), 0)
            row = again.project_event(_T._mark(97.1, 1))["positions"][0]
            self.assertEqual(row["realized_pnl"], -19.884)

    def test_foreign_tenant_runtime_binding_ignored_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, fill = self._setup(tmp)
            bad = []
            for k, v in (("tenant_id", "other"), ("runtime_id", "rt-x"), ("binding_id", "rb-x")):
                e = json.loads(json.dumps(fill)); e[k] = v; bad.append(e)
            reader, _ = self._reader(bad)
            store = RuntimeSummaryProjectionStore(path=path)
            store.backfill_open_position_costs(reader)
            row = store.project_event(_T._mark(97.1, 1))["positions"][0]
            self.assertEqual(row["costs_status"], "unknown")
            self.assertNotIn("realized_pnl", row)

    def test_reader_failure_or_missing_reader_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, fill = self._setup(tmp)

            def boom(*a, **k):
                raise RuntimeError("db down")
            for r in (CanonicalFillHistoryReader(_Empty(), boom), CanonicalFillHistoryReader(_Empty(), None)):
                store = RuntimeSummaryProjectionStore(path=path)
                store.backfill_open_position_costs(r)
                row = store.project_event(_T._mark(97.1, 1))["positions"][0]
                self.assertEqual(row["costs_status"], "unknown")


class _SqlSemanticsEvents:
    """Emulates production SQL: legacy binding reader = newest-100 of ALL types;
    `paper_fills` = event_type/tenant/binding filter applied BEFORE limit/offset."""

    def __init__(self, events):
        self.events = list(events)  # newest first
        self.fill_calls = []

    def __call__(self, bid, tenant_id=None, limit=50):
        return self.events[: min(limit, 100)]

    def paper_fills(self, bid, tenant_id=None, limit=100, offset=0):
        self.fill_calls.append((bid, tenant_id, limit, offset))
        fills = [e for e in self.events if e.get("event_type") == "paper_fill_simulated"]
        return fills[offset: offset + limit]


class CanonicalCostRecoveryPagingTest(unittest.TestCase):
    def _noise(self, n):
        return [
            {"event_type": "runtime_heartbeat", "event_id": f"hb-{i}", "tenant_id": "tenant-dev",
             "runtime_id": "rt-x", "binding_id": "rb-cost-001"}
            for i in range(n)
        ]

    def test_old_fill_hidden_by_101_newer_heartbeats_is_recovered(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, fill = CanonicalCostRecoveryTest()._setup(tmp)
            fill = dict(fill, event_type="paper_fill_simulated")
            be = _SqlSemanticsEvents(self._noise(101) + [fill])
            self.assertEqual(be("rb-cost-001", limit=100)[-1]["event_type"], "runtime_heartbeat")
            store = RuntimeSummaryProjectionStore(path=path)
            self.assertEqual(store.backfill_open_position_costs(CanonicalFillHistoryReader(_Empty(), be)), 1)
            row = store.project_event(_T._mark(97.1, 1))["positions"][0]
            self.assertEqual(row["costs"], 19.884)
            self.assertEqual(row["realized_pnl"], -19.884)
            again = RuntimeSummaryProjectionStore(path=path)
            self.assertEqual(again.project_event(_T._mark(97.1, 1))["positions"][0]["realized_pnl"], -19.884)

    def test_main_build_service_startup_recovers_fill_hidden_by_newer_nonfill_events(self):
        import os
        from unittest.mock import patch
        import services.telemetry.main as _main
        with tempfile.TemporaryDirectory() as tmp:
            path, fill = CanonicalCostRecoveryTest()._setup(tmp)
            fill = dict(fill, event_type="paper_fill_simulated")
            be = _SqlSemanticsEvents(self._noise(101) + [fill])
            (Path(tmp) / "p.json").write_text("{}", encoding="utf-8")
            (Path(tmp) / "e.json").write_text("{}", encoding="utf-8")
            env = {
                "TELEMETRY_STORAGE_DIR": tmp,
                "TELEMETRY_RUNTIME_SUMMARY_STORE": str(path),
                "TELEMETRY_TRADE_EPISODE_PROJECTION_STORE": str(Path(tmp) / "p.json"),
                "TELEMETRY_TRADE_EPISODE_EVENTS_STORE": str(Path(tmp) / "e.json"),
                "TELEMETRY_BUFFER_BACKEND": "memory",
                "TELEMETRY_DB_DSN": "postgresql://unused/unused",
            }
            with patch.dict(os.environ, env), patch.object(
                _main, "build_postgres_write_fn", return_value=None
            ), patch.object(
                _main, "build_postgres_event_reader", return_value=(lambda *a, **k: None, be)
            ):
                _main._build_service()
            reloaded = RuntimeSummaryProjectionStore(path=path)
            row = reloaded.project_event(_T._mark(97.1, 1))["positions"][0]
            self.assertEqual(row["costs"], 19.884)
            self.assertEqual(row["realized_pnl"], -19.884)

    def test_fill_sequence_beyond_one_page_is_fully_read(self):
        fill = {"event_type": "paper_fill_simulated", "event_id": "f0", "tenant_id": "tenant-dev",
                "runtime_id": "rt-cost-001", "binding_id": "rb-cost-001"}
        fills = []
        for i in range(250):
            e = dict(fill, event_id=f"f{i}", metadata={"ledger_fill_count": 250 - i})
            fills.append(e)
        be = _SqlSemanticsEvents(self._noise(150) + fills)
        out = CanonicalFillHistoryReader(_Empty(), be).retained_paper_fill_events(
            tenant_id="tenant-dev", runtime_id="rt-cost-001", binding_id="rb-cost-001")
        self.assertEqual(len(out), 250)
        self.assertEqual([c[3] for c in be.fill_calls], [0, 100, 200])

    def test_paging_failure_midway_returns_no_partial_history(self):
        calls = []

        def be(*a, **k):
            raise AssertionError("legacy reader must not be used")

        def paper_fills(bid, tenant_id=None, limit=100, offset=0):
            calls.append(offset)
            if offset:
                raise RuntimeError("db down")
            return [{"event_type": "paper_fill_simulated", "event_id": str(i)} for i in range(limit)]
        be.paper_fills = paper_fills
        out = CanonicalFillHistoryReader(_Empty(), be).retained_paper_fill_events(
            tenant_id="t", runtime_id="r", binding_id="b")
        self.assertEqual(out, [])
        self.assertEqual(calls, [0, 100])

    def test_postgres_reader_filters_event_type_before_limit(self):
        import sys, types
        from unittest.mock import AsyncMock, patch
        from services.telemetry.ingest_svc import build_postgres_event_reader
        captured = []

        class Conn:
            async def fetch(self, sql, *args, timeout=None):
                captured.append((sql, args))
                return []

        class Pool:
            def acquire(self, timeout=None):
                class C:
                    async def __aenter__(_s): return Conn()
                    async def __aexit__(_s, *e): return False
                return C()

        fake = types.SimpleNamespace(create_pool=AsyncMock(return_value=Pool()))
        with patch.dict(sys.modules, {"asyncpg": fake}):
            _, binding = build_postgres_event_reader("postgresql://x/db")
            self.assertEqual(binding.paper_fills("rb-1", tenant_id="t-1", limit=500, offset=200), [])
        sql, args = captured[0]
        self.assertIn("event_type = 'paper_fill_simulated'", sql)
        self.assertLess(sql.index("event_type"), sql.index("LIMIT"))
        self.assertEqual(args, ("rb-1", "t-1", 100, 200))


if __name__ == "__main__":
    unittest.main()
