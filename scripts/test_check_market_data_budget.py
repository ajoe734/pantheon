"""Tests of scripts/check_market_data_budget.py: limits are read from the design tables and a byte
overrun fails (assertions on bytes, not on log lines)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import check_market_data_budget as cmb

try:
    importlib.util.find_spec("pyarrow")
    HAVE_PYARROW = importlib.util.find_spec("pyarrow") is not None
except ValueError:
    HAVE_PYARROW = False


class DesignTableTests(unittest.TestCase):
    def test_rows_read_from_design_with_both_sections(self) -> None:
        rows = cmb.load_design_rows()
        self.assertEqual({r["section"] for r in rows.values()}, {"6", "6A"})
        for key in ("daily_bars", "lineage", "market_snapshot_current", "listing_allowlist", "tw_index_daily",
                    "collector_news", "features/returns"):
            self.assertIn(key, rows)

    def test_every_design_row_has_a_checker_or_waiver(self) -> None:
        rows = cmb.load_design_rows()
        covered = set(cmb.CHECKERS) | set(cmb.WAIVERS) | {"lineage"}
        self.assertEqual(set(rows) - covered, set(), "a design row without checker or waiver")
        self.assertEqual(covered - set(rows), set(), "a checker for a row the design no longer has")

    def test_limits_are_parsed_in_bytes(self) -> None:
        cells = cmb.load_design_rows()["daily_bars"]["cells"]
        self.assertEqual(cmb.limit(cells, 1, cmb._le(), "x"), 4_000_000)
        self.assertEqual(cmb.limit(cells, 2, r"ceiling\s+" + cmb._QTY, "x"), 2_500_000_000)
        self.assertEqual(cmb.limit(cells, 3, r"day\s*>\s*" + cmb._QTY, "x"), 6_000_000)

    def test_unreadable_limit_fails_closed_with_reason(self) -> None:
        with self.assertRaises(cmb.BudgetError) as ctx:
            cmb.limit(["a", "no number here"], 1, cmb._le(), "demo")
        self.assertIn("demo", str(ctx.exception))

    def test_row_without_checker_or_waiver_fails_closed(self) -> None:
        rows = cmb.load_design_rows()
        rows["brand_new_store"] = {"section": "6", "cells": ["`brand_new_store`", "<= 1 MB", "<= 1 GB", "x"]}
        results = cmb.evaluate(rows, only={"brand_new_store"})
        self.assertEqual([r["status"] for r in results], ["fail"])
        self.assertIn("neither a checker nor a waiver", results[0]["reason"])

    def test_removed_design_row_fails_closed(self) -> None:
        rows = cmb.load_design_rows()
        del rows["tw_macro_series"]
        results = cmb.evaluate(rows, only={"nothing"})
        self.assertTrue(any(r["status"] == "fail" and "no such row" in r["reason"] for r in results))

    def test_missing_design_file_fails_closed_in_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.json"
            rc = cmb.main(["--design", str(Path(tmp) / "absent.md"), "--output", str(out)])
            self.assertEqual(rc, 1)
            self.assertIn("design file missing", json.loads(out.read_text())["failures"][0])


class ByteLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = cmb.load_design_rows()

    def test_daily_bars_day_over_target_and_alarm_fails(self) -> None:
        cells = self.rows["daily_bars"]["cells"]
        ok = cmb.check_daily_bars(cells, {"day_bytes": 4_000_000})
        self.assertTrue(all(r["status"] == "pass" for r in ok))
        over = cmb.check_daily_bars(cells, {"day_bytes": 4_000_001})
        self.assertEqual([r["metric"] for r in over if r["status"] == "fail"], ["day_bytes"])
        alarm = cmb.check_daily_bars(cells, {"day_bytes": 6_000_001})
        self.assertIn("day_bytes_alarm", [r["metric"] for r in alarm if r["status"] == "fail"])

    def test_daily_bars_total_over_one_gb_in_500_days_fails(self) -> None:
        cells = self.rows["daily_bars"]["cells"]
        res = cmb.check_daily_bars(cells, {"day_bytes": 5_000_001})
        self.assertIn("total_bytes_500_days", [r["metric"] for r in res if r["status"] == "fail"])

    def test_lineage_over_limit_fails(self) -> None:
        cells = self.rows["lineage"]["cells"]
        self.assertTrue(all(r["status"] == "pass" for r in cmb.check_lineage(cells, {"lineage_day_bytes": 400_000})))
        res = cmb.check_lineage(cells, {"lineage_day_bytes": 400_001})
        self.assertEqual([r["metric"] for r in res if r["status"] == "fail"], ["day_bytes", "total_bytes_500_days"])

    def test_snapshot_each_limit_fails_independently(self) -> None:
        cells = self.rows["market_snapshot_current"]["cells"]
        good = {"keys": 53_000, "max_record_bytes": 16_000, "live_bytes": 640_000_000, "file_bytes": 832_000_000,
                "wal_bytes": 64_000_000, "writer_txn_ms_max": 500, "reader_call_ms_max": 100}
        self.assertTrue(all(r["status"] == "pass" for r in cmb.check_snapshot(cells, good)))
        for field, bad, metric in (("max_record_bytes", 16_001, "max_record_bytes"),
                                   ("live_bytes", 640_000_001, "live_bytes_53000_keys"),
                                   ("file_bytes", 832_001_000, "file_over_live_ratio"),
                                   ("wal_bytes", 64_000_001, "wal_bytes"),
                                   ("writer_txn_ms_max", 501, "writer_txn_ms_max"),
                                   ("reader_call_ms_max", 101, "reader_call_ms_max")):
            res = cmb.check_snapshot(cells, {**good, field: bad})
            self.assertIn(metric, [r["metric"] for r in res if r["status"] == "fail"], field)

    def test_listing_and_6a_dataset_over_limit_fail(self) -> None:
        listing = cmb.check_listing(self.rows["listing_allowlist"]["cells"], {"day_bytes": 4_000_001})
        self.assertEqual([r["status"] for r in listing], ["fail", "pass"])
        flow = cmb.check_dataset("tw_institutional_flow")(self.rows["tw_institutional_flow"]["cells"],
                                                         {"day_bytes": 400_001})
        self.assertEqual(flow[0]["status"], "fail")
        self.assertIn("exceeds", flow[0]["reason"])

    def test_oversized_generated_dataset_fails_through_evaluate(self) -> None:
        big = lambda: [[f"{i}", "x" * 400, str(hash((i, "n")))] for i in range(40_000)]  # noqa: E731
        with mock.patch.dict(cmb.DATASET_ROWS, {"tw_index_daily": big}):
            res = cmb.evaluate(self.rows, only={"tw_index_daily"})
        self.assertEqual(res[0]["status"], "fail")

    def test_keyed_dataset_measured_through_real_store_and_declaration_checked(self) -> None:
        from services.source_ingestion.daily_bars_store import KEYED_DATASETS

        for name in ("tw_margin_short_balance", "collector_news"):
            m = cmb.measure_dataset(name)
            self.assertEqual(m["keyed"], 1.0)
            res = cmb.check_dataset(name)(self.rows[name]["cells"], m)
            self.assertEqual({r["metric"] for r in res}, {"day_bytes", "declared_budget_bytes"})
            self.assertTrue(all(r["status"] == "pass" for r in res), res)
        loose = {"day_bytes": 1.0, "keyed": 1.0, "declared_budget_bytes": 10**12}
        res = cmb.check_dataset("tw_margin_short_balance")(self.rows["tw_margin_short_balance"]["cells"], loose)
        self.assertEqual([r["status"] for r in res], ["pass", "fail"])
        self.assertTrue(set(cmb.DATASET_ROWS) <= set(KEYED_DATASETS))

    def test_live_stats_over_limit_fails_and_unknown_store_fails(self) -> None:
        res = cmb.check_live(self.rows, {"daily_bars": {"day_bytes": 6_000_001}, "ghost_store": {"day_bytes": 1}})
        failed = {(r["store"], r["metric"]) for r in res if r["status"] == "fail"}
        self.assertIn(("daily_bars", "day_bytes"), failed)
        self.assertIn(("ghost_store", "live_row_present"), failed)
        self.assertTrue(all(r["status"] == "pass" for r in cmb.check_live(self.rows, {"daily_bars": {"day_bytes": 1_000_000}})))

    def test_snapshot_live_stats_missing_fields_fail(self) -> None:
        res = cmb.check_live(self.rows, {"market_snapshot_current": {"live_bytes": 1}})
        self.assertEqual(res[0]["status"], "fail")
        self.assertIn("lack", res[0]["reason"])


@unittest.skipUnless(HAVE_PYARROW, "pyarrow is provisioned by the source ingestion requirements in Branch CI")
class HostedShapedTests(unittest.TestCase):
    def test_full_hosted_shaped_run_passes_within_time_budget(self) -> None:
        report = cmb.build_report()
        self.assertTrue(report["ok"], report["failures"])
        self.assertEqual(report["failed"], 0)
        self.assertLess(report["elapsed_seconds"], 120)
        stores = {r["store"] for r in report["results"] if r["status"] == "pass"}
        self.assertTrue({"daily_bars", "lineage", "market_snapshot_current", "listing_allowlist"} <= stores)
        daily = cmb.measure_daily_bars()
        self.assertEqual(daily["rows"], 52_958)
        self.assertLessEqual(daily["day_bytes"], 4_000_000)
        self.assertLessEqual(daily["lineage_day_bytes"], 400_000)
        self.assertLessEqual(daily["bytes_per_row"] * 53_000, 4_000_000)

    def test_a_store_row_shrunk_in_the_design_fails_the_run(self) -> None:
        text = cmb.DESIGN_PATH.read_text(encoding="utf-8").replace("<= 4.0 MB (per day: 53,000", "<= 0.5 MB (per day: 53,000", 1)
        self.assertNotEqual(text, cmb.DESIGN_PATH.read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp) / "design.md"
            design.write_text(text, encoding="utf-8")
            report = cmb.build_report(only={"daily_bars"}, design_path=design)
        self.assertFalse(report["ok"])
        self.assertTrue(any("day_bytes" in f and "exceeds" in f for f in report["failures"]))


if __name__ == "__main__":
    unittest.main()
