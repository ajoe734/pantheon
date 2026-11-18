"""Focused tests for scripts/measure_hosted_storage.py (RC3 WP3.3): replay of the 2026-11-17 audit."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_hosted_storage as mhs  # noqa: E402

GB, MB = 1000 ** 3, 1000 ** 2
NOW = 1_794_000_000
OLD = NOW - 30 * 86400


def audit_raw(extra_files=()):
    files = [
        ("bff", 1030 * MB, NOW - 60, "/data/bff/downstream_health.sqlite3"),
        ("source-ingest", 502 * MB, NOW - 60, "/data/source-ingest/latest_market_snapshots.jsonl"),
        ("source-ingest", 614 * MB, OLD, "/data/source-ingest/latest_market_snapshots.jsonl.pre-compact.bak"),
        ("source-ingest", 374 * MB, OLD, "/data/source-ingest/latest_market_snapshots.jsonl.pre-compact2.bak"),
    ]
    # 563 orphan temp files, 4.85 GB, in runtime-data paper-performance (older than a day)
    files += [("runtime", 4_850 * MB // 563, OLD, f"/data/runtime/paper-performance/run{i}.json.tmp") for i in range(563)]
    files += list(extra_files)
    out = ["@@time", str(NOW), "@@disk", "Filesystem 1B-blocks Used Available Mounted on",
           f"/dev/sda1 {193 * GB} {140 * GB} {53 * GB} /", "@@free",
           f"Mem: {31 * GB} {20 * GB} 0 0 0 {9 * GB}", "@@docker_df",
           json.dumps({"Type": "Build Cache", "Size": "49.8GB", "Reclaimable": "40GB"}),
           "@@docker_stats", json.dumps({"Name": "search-svc", "MemUsage": "10.4GiB / 31GiB"}),
           json.dumps({"Name": "postgres", "MemUsage": "1.2GiB / 31GiB"}), "@@files"]
    out += [f"{c}\t{s}\t{m}.0\t{p}" for c, s, m, p in files]
    out += ["@@lines", "/data/source-ingest/latest_market_snapshots.jsonl\t1200", "@@tables",
            f"public.telemetry_events|{29 * GB}|13700000",
            f"trade_journey_projection.event_receipts|{4400 * MB}|13700000",
            f"reconciliation_drift.work_claims|{198 * MB}|600000", "@@end"]
    return "\n".join(out)


class AuditReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        manifest = json.loads(mhs.MANIFEST_PATH.read_text())
        cls.report = mhs.build_report(audit_raw(), manifest)

    def ids(self, kind):
        return {f.get("entry_id") for f in self.report["findings"] if f["finding"] == kind}

    def test_flags_audit_entries(self):
        sized = self.ids("exceeded_size")
        self.assertIn("schema_table:public.telemetry_events", sized)
        self.assertIn("table:trade_journey_projection.event_receipts", sized)
        self.assertIn("sqlite:downstream_health.sqlite3", sized)
        self.assertEqual(self.report["status"], "red")

    def test_orphan_temp_files_flagged(self):
        orphans = [f for f in self.report["findings"] if f["finding"] == "orphan_temp_files"]
        self.assertEqual(orphans[0]["count"], 563)
        self.assertEqual(orphans[0]["directory"], "/data/runtime/paper-performance")

    def test_unknown_backups_reported_with_owner_fields_on_entries(self):
        unknown = [f for f in self.report["findings"] if f["finding"] == "unknown_state_file"]
        self.assertTrue(any(f["count"] == 2 and f["bytes"] == 988 * MB for f in unknown))
        e = next(f for f in self.report["findings"] if f.get("entry_id") == "sqlite:downstream_health.sqlite3")
        self.assertEqual((e["owner"], e["register_class"]), ("control-plane", "RC3"))

    def test_headline_numbers(self):
        h = self.report["headline"]
        self.assertEqual(h["disk"][0]["free"], 53 * GB)
        self.assertEqual(h["top_containers_by_memory"][0]["name"], "search-svc")
        self.assertEqual(h["docker_df"][0]["type"], "Build Cache")
        self.assertEqual(h["memory"]["available"], 9 * GB)

    def test_line_counts(self):
        m = self.report["measurements"]["compose_path:/data/source-ingest/latest_market_snapshots.jsonl"]
        self.assertEqual(m["lines"], 1200)


class GrowthAndGreen(unittest.TestCase):
    def test_growth_vs_previous_run(self):
        manifest = json.loads(mhs.MANIFEST_PATH.read_text())
        first = mhs.build_report(audit_raw(), manifest)
        second_raw = audit_raw().replace(f"public.telemetry_events|{29 * GB}", f"public.telemetry_events|{29 * GB + 750 * MB}")
        second_raw = second_raw.replace(f"@@time\n{NOW}", f"@@time\n{NOW + 86400}")
        second = mhs.build_report(second_raw, manifest, first)
        m = second["measurements"]["schema_table:public.telemetry_events"]
        self.assertAlmostEqual(m["growth_per_day"], 750 * MB, delta=1000)
        self.assertIn("exceeded_growth", {f["finding"] for f in second["findings"]})

    def test_green_when_within_bounds(self):
        manifest = {"entries": [{"kind": "sqlite", "name": "x.sqlite3", "owner": "o", "bound_status": "bounded"}]}
        raw = "@@time\n%d\n@@files\nc\t10\t%d.0\t/data/x.sqlite3\n@@end" % (NOW, NOW)
        report = mhs.build_report(raw, manifest)
        self.assertEqual((report["status"], report["findings"]), ("green", []))

    def test_unbounded_without_budget_is_warning_only(self):
        manifest = {"entries": [{"kind": "sqlite", "name": "x.sqlite3", "owner": "o", "bound_status": "unbounded",
                                 "register_class": "RC3", "task": "T"}]}
        raw = "@@time\n%d\n@@files\nc\t10\t%d.0\t/data/x.sqlite3\n@@end" % (NOW, NOW)
        report = mhs.build_report(raw, manifest)
        self.assertEqual(report["status"], "green")
        self.assertEqual(report["findings"][0]["finding"], "unbounded")

    def test_remote_script_is_read_only(self):
        s = mhs.REMOTE_SCRIPT
        for forbidden in ("rm ", "mv ", "cp ", "INSERT", "UPDATE", "DELETE", "DROP", "docker run", "docker rm",
                          "docker restart", "cat ", ">>", "printenv", " env"):
            self.assertNotIn(forbidden, s)
        self.assertIn("default_transaction_read_only=on", s)


MIB = 1024 ** 2


def mem_raw(*lines):
    return "\n".join(["@@time", str(NOW), "@@memory", *lines, "@@end"])


def mem_report(lines, previous_lines=None):
    manifest = {"entries": []}
    prev = mhs.build_report(mem_raw(*previous_lines), manifest) if previous_lines else None
    return mhs.build_report(mem_raw(*lines), manifest, prev)


def kinds(report):
    return {(f["finding"], f.get("service")): f for f in report["findings"] if "service" in f}


class MemoryBudget(unittest.TestCase):
    def test_oom_kill_is_red(self):
        r = mem_report([f"c1|adapter|0|{3072 * MIB}|{3000 * MIB}|54|54"])
        f = kinds(r)[("container_oom_killed", "adapter")]
        self.assertEqual((r["status"], f["oom_kill"], f["register_class"]), ("red", 54, "RC3"))

    def test_restart_growth_is_red(self):
        r = mem_report(["c1|svc|5|268435456|1000|0|0"], ["c1|svc|3|268435456|1000|0|0"])
        f = kinds(r)[("container_restarts_grew", "svc")]
        self.assertEqual((r["status"], f["restarts"], f["previous_restarts"]), ("red", 5, 3))

    def test_cache_bound_container_at_limit_is_warning_only(self):
        lim = 8 * 1024 ** 3
        r = mem_report([f"c1|postgres|0|{lim}|{lim}|1000000|0"], [f"c1|postgres|0|{lim}|{lim}|10|0"])
        k = kinds(r)
        self.assertEqual(r["status"], "green")
        self.assertEqual(k[("memory_limit_reached", "postgres")]["severity"], "warning")
        self.assertEqual(k[("memory_peak_near_limit", "postgres")]["percent"], 100.0)

    def test_counter_reset_is_not_a_finding(self):
        r = mem_report(["c1|svc|0|268435456|1000|0|0"], ["c1|svc|4|268435456|1000|7|0"])
        self.assertEqual(r["findings"], [])
        self.assertEqual(r["status"], "green")

    def test_container_without_limit(self):
        r = mem_report(["c1|minio|0|max|5000|-|-"])
        row = r["memory_table"][0]
        self.assertIsNone(row["limit"])
        self.assertIsNone(row["percent"])
        self.assertEqual(r["status"], "green")
        self.assertIn(("memory_limit_missing", "minio"), kinds(r))

    def test_table_present(self):
        r = mem_report([f"c1|a|0|{100 * MIB}|{50 * MIB}|0|0"])
        self.assertEqual(r["memory_table"][0]["percent"], 50.0)
        self.assertEqual(r["findings"], [])

    def test_restarted_service_reported_without_previous_report(self):
        # replay of 2026-11-18 05:40Z: incidents restarts 1, oom_kill 0, no previous report
        r = mem_report([f"c1|incidents|1|1073741824|541327360|0|0|2026-11-18T04:46:20Z"])
        f = kinds(r)[("container_restarted_since_creation", "incidents")]
        self.assertEqual((f["restarts"], f["created"], f["oom_kill"]), (1, "2026-11-18T04:46:20Z", 0))
        self.assertIn("not evidence", f["note"])
        self.assertEqual(r["status"], "green")

    def test_restarted_service_reported_when_count_did_not_grow(self):
        line = "c1|incidents|1|1073741824|541327360|0|0|2026-11-18T04:46:20Z"
        r = mem_report([line], [line])
        self.assertIn(("container_restarted_since_creation", "incidents"), kinds(r))
        self.assertNotIn(("container_restarts_grew", "incidents"), kinds(r))

    def test_no_restart_finding_at_zero_and_one_per_service(self):
        r = mem_report(["c1|a|0|268435456|1000|0|0|2026-11-18T04:46:20Z", "c2|b|2|268435456|1000|0|0"])
        found = [f for f in r["findings"] if f["finding"] == "container_restarted_since_creation"]
        self.assertEqual([f["service"] for f in found], ["b"])
        self.assertIsNone(found[0]["created"])

    def test_remote_script_is_read_only(self):
        self.assertIn("memory.peak", mhs.REMOTE_SCRIPT)
        for bad in (" > ", " rm ", "docker restart", "docker exec -u root"):
            self.assertNotIn(bad, mhs.REMOTE_SCRIPT.split("@@memory")[1].split("@@files")[0])


if __name__ == "__main__":
    unittest.main()
