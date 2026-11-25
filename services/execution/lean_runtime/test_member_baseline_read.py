"""Member baseline read: bounded reads, bounded logs, report-only, fail-closed on foreign entries."""
import copy
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.execution.lean_runtime.test_paper_runtime import (
    InMemoryPendingSignalStore, PaperRuntimeService, PaperRuntimeServiceTest,
    _FakeRuntimeManagerClient, _FakeTelemetryEmitter,
)

LOGGER = "services.execution.lean_runtime.paper_runtime"


class MemberBaselineReadTest(unittest.TestCase):
    def _service(self, reader):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = PaperRuntimeService(
            store=InMemoryPendingSignalStore([]), identity=PaperRuntimeServiceTest._identity(None),
            runtime_manager_client=_FakeRuntimeManagerClient([]),
            telemetry_emitter=_FakeTelemetryEmitter(), poll_interval_seconds=3600,
            lifecycle_outbox_path=Path(directory.name) / "lifecycle.json")
        service._member_entry_reader = reader
        return service

    def _observe(self, service, entry_id, checksum, days=(1, 2)):
        for day, price in zip(days, (100, 110)):
            service._member_virtual_books.observe(
                binding_id="b", envelope_checksum="sha256:env", symbol="2330.TW",
                event_time=f"2024-10-0{day}T05:30:00Z", price=price, open_price=price, source_ref="r",
                members={entry_id: {"artifact_id": entry_id, "version": "1", "checksum": checksum,
                                    "virtual_only": True, "view": {"direction": "LONG", "strength": 1}}},
                bar_frequency="1d")

    def _books(self, service):
        return {b["member_id"]: b for b in service.snapshot()["paper_state"]["member_virtual_books"]}

    def test_approved_entry_is_read_once_per_interval_not_per_snapshot(self):
        from services.evolution.test_approved_baselines import genuine_entry
        entry = genuine_entry(tenant="default")
        reads = []
        service = self._service(lambda ref, tenant: reads.append(ref) or entry)
        self._observe(service, entry["registry_id"], entry["checksum"])
        for _ in range(50):
            books = self._books(service)
        self.assertEqual(len(reads), 1)
        self.assertEqual(books[entry["registry_id"]]["baseline"]["status"], "approved")
        self.assertEqual(len(reads), 1)

    def test_refresh_interval_bounds_reads(self):
        from services.evolution.test_approved_baselines import genuine_entry
        entry = genuine_entry(tenant="default")
        reads = []
        service = self._service(lambda ref, tenant: reads.append(ref) or entry)
        self._observe(service, entry["registry_id"], entry["checksum"])
        clock = {"t": 1000.0}
        with patch("services.execution.lean_runtime.paper_runtime.time.monotonic", side_effect=lambda: clock["t"]):
            self._books(service)
            clock["t"] += 299
            self._books(service)
            self.assertEqual(len(reads), 1)
            clock["t"] += 2
            self._books(service)
            self._books(service)
            self.assertEqual(len(reads), 2)

    def test_unreadable_baseline_logs_once_without_traceback_and_says_why(self):
        def boom(ref, tenant):
            raise RuntimeError("registry owner URL and read principal are required")
        service = self._service(boom)
        self._observe(service, "m", "sha256:m")
        with self.assertLogs(LOGGER, level="DEBUG") as captured:
            for _ in range(100):
                books = self._books(service)
        records = [r for r in captured.records if "member baseline" in r.getMessage()]
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0].exc_info)
        self.assertEqual(records[0].levelno, logging.WARNING)
        self.assertEqual(books["m"]["baseline"]["status"], "no_approved_baseline")
        self.assertEqual(books["m"]["baseline"]["reason"], "registry_read_unavailable:RuntimeError")
        self.assertEqual(books["m"]["baseline_comparison"]["status"], "no_approved_baseline")

    def test_missing_foreign_tenant_and_mismatched_entries_are_unavailable(self):
        from services.evolution.test_approved_baselines import genuine_entry
        entry = genuine_entry(tenant="default")
        foreign = copy.deepcopy(entry)
        foreign["owner_tenant"] = "someone-else"
        other = copy.deepcopy(entry)
        other["registry_id"] = "another-artifact"
        for served, reason in ((None, "registry_entry_missing"), ({}, "registry_entry_missing"),
                               (foreign, "wrong_tenant"), (other, "registry_entry_is_not_member_artifact")):
            service = self._service(lambda ref, tenant, served=served: served)
            self._observe(service, entry["registry_id"], entry["checksum"])
            book = self._books(service)[entry["registry_id"]]
            self.assertEqual(book["baseline"]["reason"], reason)
            self.assertEqual(book["baseline_comparison"]["status"], "no_approved_baseline")
        service = self._service(lambda ref, tenant: entry)
        self._observe(service, entry["registry_id"], "sha256:not-the-entry")
        self.assertEqual(self._books(service)[entry["registry_id"]]["baseline"]["reason"], "checksum_mismatch")

    def test_real_approved_entry_yields_comparison_report_only(self):
        from services.evolution.test_approved_baselines import genuine_entry
        entry = genuine_entry(tenant="default")
        service = self._service(lambda ref, tenant: entry)
        for day, price in enumerate((100, 104, 108, 112, 120), start=1):
            service._member_virtual_books.observe(
                binding_id="b", envelope_checksum="sha256:env", symbol="2330.TW",
                event_time=f"2024-10-0{day}T05:30:00Z", price=price, open_price=price, source_ref="r",
                members={entry["registry_id"]: {
                    "artifact_id": entry["registry_id"], "version": "1", "checksum": entry["checksum"],
                    "virtual_only": False, "underlying_count": 1,
                    "view": {"direction": "LONG", "strength": 1}}},
                bar_frequency="1d")
        book = self._books(service)[entry["registry_id"]]
        self.assertEqual(book["baseline_comparison"]["status"], "compared")
        self.assertFalse(book["is_real_order"])


if __name__ == "__main__":
    unittest.main()
