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


if __name__ == "__main__":
    unittest.main()
