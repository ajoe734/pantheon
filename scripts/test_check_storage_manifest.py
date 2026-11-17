"""Focused tests for scripts/check_storage_manifest.py (RC3 WP3.2)."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_storage_manifest as csm  # noqa: E402


def entry(kind, name, **kw):
    base = dict(kind=kind, name=name, owner="svc", growth_driver="d", growth_per_day="unmeasured",
                cap="none", cleanup="none", readers=["services/svc/a.py"], bound_status="unreviewed",
                register_class="RC3", task="to_be_opened")
    base.update(kw)
    return base


class FakeRepo:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "services" / "svc").mkdir(parents=True)
        (self.root / "services" / "svc" / "a.py").write_text(
            'P = "audit.jsonl"\nDB = "state.sqlite3"\nimport sqlite3\nsqlite3.connect(DB)\n'
            'SQL = "CREATE TABLE IF NOT EXISTS events (id int)"\nT = "svc.work_items"  # table\n'
            'N = f"{day}.jsonl"\nSQL2 = f"CREATE TABLE {self._table} (id int)"\n'
        )
        (self.root / "services" / "svc" / "test_a.py").write_text('X = "ignored_by_tests.jsonl"\n')
        (self.root / "docker-compose.yml").write_text("volumes:\n  - /data/svc/state.json:/x\n")

    def manifest(self):
        return {"entries": [
            entry("jsonl", "audit.jsonl"), entry("jsonl", "{day}.jsonl"), entry("sqlite", "state.sqlite3"),
            entry("sqlite_connect", "services/svc/a.py"), entry("compose_path", "/data/svc/state.json"),
            entry("table", "events"), entry("table", "services/svc/a.py::{self._table}"),
            entry("schema_table", "svc.work_items"),
        ], "waivers": []}


class StorageManifestTests(unittest.TestCase):
    def setUp(self):
        self.repo = FakeRepo()
        self.addCleanup(self.repo.tmp.cleanup)

    def run_check(self, manifest):
        return csm.check(manifest, self.repo.root)

    def test_green_when_declared_and_tests_are_excluded(self):
        report = self.run_check(self.repo.manifest())
        self.assertEqual(report["failures"], [], report)
        self.assertNotIn("ignored_by_tests.jsonl", json.dumps(report))

    def test_undeclared_writer_fails_then_declaring_it_passes(self):
        (self.repo.root / "services" / "svc" / "b.py").write_text('Q = "new_queue.jsonl"\n')
        red = self.run_check(self.repo.manifest())
        self.assertFalse(red["passed"])
        self.assertIn("undeclared writer (add a manifest entry or waiver): jsonl:new_queue.jsonl", red["failures"])
        manifest = self.repo.manifest()
        manifest["entries"].append(entry("jsonl", "new_queue.jsonl"))
        self.assertTrue(self.run_check(manifest)["passed"])

    def test_waiver_covers_a_discovered_name(self):
        (self.repo.root / "services" / "svc" / "b.py").write_text('Q = "frag.jsonl"\n')
        manifest = self.repo.manifest()
        manifest["waivers"].append({"kind": "jsonl", "name": "frag.jsonl", "reason": "fragment"})
        self.assertTrue(self.run_check(manifest)["passed"])

    def test_stale_entry_and_stale_waiver_fail(self):
        manifest = self.repo.manifest()
        manifest["entries"].append(entry("jsonl", "gone.jsonl"))
        manifest["waivers"].append({"kind": "jsonl", "name": "gone2.jsonl", "reason": "x"})
        failures = self.run_check(manifest)["failures"]
        self.assertTrue(any("no longer found" in f and "gone.jsonl" in f for f in failures))
        self.assertTrue(any("waiver jsonl:gone2.jsonl" in f for f in failures))

    def test_pattern_entry_matches_templated_names(self):
        manifest = self.repo.manifest()
        manifest["entries"] = [e for e in manifest["entries"] if e["name"] != "{day}.jsonl"]
        manifest["entries"].append({**entry("jsonl", "x"), "pattern": r"\{[a-z]+\}\.jsonl"})
        manifest["entries"][-1].pop("name")
        self.assertTrue(self.run_check(manifest)["passed"])

    def test_unbounded_needs_register_class_and_task(self):
        manifest = self.repo.manifest()
        manifest["entries"][0] = entry("jsonl", "audit.jsonl", bound_status="unbounded", task="")
        failures = self.run_check(manifest)["failures"]
        self.assertTrue(any("unbounded entry needs task" in f for f in failures))

    def test_bounded_requires_proof_found_in_code(self):
        manifest = self.repo.manifest()
        good = {"file": "services/svc/a.py", "contains": "audit.jsonl"}
        manifest["entries"][0] = entry("jsonl", "audit.jsonl", bound_status="bounded", cap="1 MB", bound_proof=good)
        self.assertTrue(self.run_check(manifest)["passed"])
        manifest["entries"][0]["bound_proof"] = {"file": "services/svc/a.py", "contains": "def prune"}
        self.assertTrue(any("bound_proof" in f for f in self.run_check(manifest)["failures"]))

    def test_missing_field_fails(self):
        manifest = self.repo.manifest()
        del manifest["entries"][0]["cleanup"]
        self.assertTrue(any("missing cleanup" in f for f in self.run_check(manifest)["failures"]))


class RepoManifestTests(unittest.TestCase):
    def test_repository_manifest_matches_code(self):
        manifest = json.loads(csm.MANIFEST_PATH.read_text())
        report = csm.check(manifest, csm.REPO_ROOT)
        self.assertEqual(report["failures"], [], report["failures"][:10])

    def test_audit_measured_entries_are_recorded(self):
        manifest = json.loads(csm.MANIFEST_PATH.read_text())
        measured = {e["name"]: e["measured"]["size"] for e in manifest["entries"] if "measured" in e}
        for name, needle in (("public.telemetry_events", "29 GB"), ("trade_journey_projection.event_receipts", "4.4 GB"),
                             ("downstream_health.sqlite3", "1.03 GB"), ("reconciliation_drift.work_claims", "198 MB"),
                             ("latest_market_snapshots.jsonl", "502 MB"), ("/data/source-ingest/distill_job_queue.jsonl", "158 MB")):
            self.assertIn(needle, measured.get(name, ""), name)


if __name__ == "__main__":
    unittest.main()
