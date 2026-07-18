import gzip
import json
import os
import stat
import sqlite3
import tempfile
import unittest
import hashlib
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock
import sys

# Ensure orchestrator directory is in sys.path
ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_DIR = ROOT / ".orchestrator"
if str(ORCHESTRATOR_DIR) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_DIR))

# Import the script
import scripts.activity_audit_logical_inventory as inventory
import common


class TestActivityAuditLogicalInventory(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.status_root = Path(self.temp_dir.name)
        self.log_path = self.status_root / "ai-activity-log.jsonl"
        self.archive_dir = self.status_root / "archive" / "logs"
        self.archive_dir.mkdir(parents=True, exist_ok=True)

        # Monkeypatch the default paths to point to our test temp_dir
        self.original_status_root = inventory.STATUS_ROOT_DEFAULT
        self.original_log_file = inventory.LOG_FILE_DEFAULT
        self.original_evidence_dir = inventory.EVIDENCE_DIR_DEFAULT

        inventory.STATUS_ROOT_DEFAULT = self.status_root
        inventory.LOG_FILE_DEFAULT = self.log_path
        inventory.EVIDENCE_DIR_DEFAULT = self.status_root / "evidence"

        self.production_pinned_registry = (
            common.HISTORICAL_ACTIVITY_OVERLAP_EXCEPTIONS
        )
        overlap = [
            {
                "event_id": f"redacted-pinned-overlap-{index:04d}",
                "message": "hermetic fixture",
            }
            for index in range(999)
        ]
        predecessor_entries = [
            {"event_id": "redacted-pinned-predecessor-0"},
            {"event_id": "redacted-pinned-predecessor-1"},
            *overlap,
        ]
        successor_entries = [
            *overlap,
            {"event_id": "redacted-pinned-successor-0"},
            {"event_id": "redacted-pinned-successor-1"},
        ]
        self.pinned_predecessor_payload = self._jsonl(predecessor_entries)
        self.pinned_successor_payload = self._jsonl(successor_entries)
        predecessor_gzip = self._gzip_bytes(self.pinned_predecessor_payload)
        successor_gzip = self._gzip_bytes(self.pinned_successor_payload)
        overlap_payload = self._jsonl(overlap)
        self.pinned_exception = common.HistoricalActivityOverlapException(
            predecessor_name="ai-activity-log.jsonl-2026-05-24T1237Z.gz",
            predecessor_gzip_sha256=hashlib.sha256(predecessor_gzip).hexdigest(),
            predecessor_gzip_byte_count=len(predecessor_gzip),
            predecessor_payload_sha256=hashlib.sha256(
                self.pinned_predecessor_payload
            ).hexdigest(),
            predecessor_payload_byte_count=len(self.pinned_predecessor_payload),
            predecessor_line_count=1001,
            successor_name="ai-activity-log.jsonl-2026-05-24T1239Z.gz",
            successor_gzip_sha256=hashlib.sha256(successor_gzip).hexdigest(),
            successor_gzip_byte_count=len(successor_gzip),
            successor_payload_sha256=hashlib.sha256(
                self.pinned_successor_payload
            ).hexdigest(),
            successor_payload_byte_count=len(self.pinned_successor_payload),
            successor_line_count=1001,
            overlap_sha256=hashlib.sha256(overlap_payload).hexdigest(),
            overlap_byte_count=len(overlap_payload),
            overlap_line_count=999,
        )
        self.pinned_registry_patch = mock.patch.object(
            common,
            "HISTORICAL_ACTIVITY_OVERLAP_EXCEPTIONS",
            (self.pinned_exception,),
        )
        self.pinned_registry_patch.start()

    def tearDown(self):
        self.pinned_registry_patch.stop()
        inventory.STATUS_ROOT_DEFAULT = self.original_status_root
        inventory.LOG_FILE_DEFAULT = self.original_log_file
        inventory.EVIDENCE_DIR_DEFAULT = self.original_evidence_dir
        self.temp_dir.cleanup()

    def _write_gz(self, path: Path, entries: list[dict]):
        payload = "\n".join(json.dumps(e) for e in entries) + "\n"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(payload)

    @staticmethod
    def _jsonl(entries: list[dict]) -> bytes:
        return b"".join(
            json.dumps(
                entry,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
            for entry in entries
        )

    @staticmethod
    def _gzip_bytes(payload: bytes) -> bytes:
        return gzip.compress(payload, mtime=0)

    def _write_gz_raw(self, path: Path, content: bytes):
        path.write_bytes(self._gzip_bytes(content))

    def _get_hermetic_pinned_bytes(self):
        return self.pinned_predecessor_payload, self.pinned_successor_payload

    def _setup_all_four_incident_pairs(self):
        """Helper to create files that trigger the four expected incident folds
        in stream_logical_activity.
        """
        # Create non-overlapping event IDs for each fold to avoid duplicates across sources
        overlap_A = [{"event_id": f"overlap_A_{i}", "val": i} for i in range(1000)]
        overlap_B = [{"event_id": f"overlap_B_{i}", "val": i} for i in range(1000)]
        overlap_C = [{"event_id": f"overlap_C_{i}", "val": i} for i in range(1000)]
        overlap_D = [{"event_id": f"overlap_D_{i}", "val": i} for i in range(1000)]

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, [{"event_id": "f1_1"}] + overlap_A)
        self._write_gz(f2, overlap_A + [{"event_id": "f2_1"}])

        f3 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1301Z.gz"
        f4 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1404Z.gz"
        self._write_gz(f3, [{"event_id": "f3_1"}] + overlap_B)
        self._write_gz(f4, overlap_B + overlap_C)

        f5 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        self._write_gz(f5, overlap_C + overlap_D)

        # Active log overlap
        self.log_path.write_text(
            "\n".join(json.dumps(e) for e in (overlap_D + [{"event_id": "active_1"}])) + "\n",
            encoding="utf-8"
        )

    def test_successful_inventory_generation(self):
        self._setup_all_four_incident_pairs()

        # Explicit status-root/evidence-dir args check
        inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

        # Check evidence files exist
        self.assertTrue((self.status_root / "evidence" / "manifest.json").exists())
        self.assertTrue((self.status_root / "evidence" / "summary.json").exists())
        self.assertTrue((self.status_root / "evidence" / "evidence.md").exists())

        # Validate summary content
        summary = json.loads((self.status_root / "evidence" / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["total_sources"], 6)  # f1, f2, f3, f4, f5, log_path
        self.assertEqual(summary["total_byte_identical_folds"], 4)

        folds = summary["folds_details"]
        for f in folds:
            self.assertEqual(f["status"], "byte_identical")
            self.assertEqual(f["fold_type"], "incident_lineage")

        # Validate manifest sources ten identity fields
        manifest = json.loads((self.status_root / "evidence" / "manifest.json").read_text(encoding="utf-8"))
        for src in manifest["sources"]:
            self.assertIn("sha256_before", src)
            self.assertIn("sha256_after", src)
            self.assertIn("st_dev_before", src)
            self.assertIn("st_dev_after", src)
            self.assertIn("st_ino_before", src)
            self.assertIn("st_ino_after", src)
            self.assertIn("st_size_before", src)
            self.assertIn("st_size_after", src)
            self.assertIn("st_mtime_ns_before", src)
            self.assertIn("st_mtime_ns_after", src)

        # Validate exact class-count schemas
        self.assertIn("source_class_counts", summary)
        self.assertIn("fold_class_counts", summary)
        self.assertIn("line_count_classes", summary)
        self.assertEqual(summary["source_class_counts"], {"legacy_ts_std": 5, "active": 1})
        self.assertEqual(summary["fold_class_counts"], {"incident_lineage": 4})
        self.assertEqual(summary["line_count_classes"], {"1000": 4})

    def test_fails_on_missing_incident_fold(self):
        # Setup files but WITHOUT the overlap so that folding fails
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, [{"event_id": "f1_1"}])
        self._write_gz(f2, [{"event_id": "f2_1"}])

        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")
        self.assertIn("Required fold not observed", str(ctx.exception))

    def test_fails_on_symlink(self):
        self._setup_all_four_incident_pairs()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"

        # Replace f1 with a symlink to f2
        f1.unlink()
        f1.symlink_to(f2)

        with self.assertRaises(RuntimeError) as ctx:
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")
        self.assertIn("symlink", str(ctx.exception).lower())

    def test_fails_on_bad_utf8(self):
        self._setup_all_four_incident_pairs()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f1.unlink()
        with gzip.open(f1, "wb") as f:
            f.write(b"\xff\xfe\x00\x00")

        with self.assertRaises(RuntimeError):
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

    def test_fails_on_bad_json(self):
        self._setup_all_four_incident_pairs()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f1.unlink()
        with gzip.open(f1, "wt", encoding="utf-8") as f:
            f.write("invalid json\n")

        with self.assertRaises(RuntimeError):
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

    def test_physical_inventory_rejects_duplicate_json_keys(self):
        ambiguous = (
            '{"event_id":"inventory-a","event_id":"inventory-b",'
            '"metadata":{"role":"a","role":"b"}}\n'
        )
        for source_kind in ("active", "archive"):
            with self.subTest(source=source_kind):
                self.log_path.unlink(missing_ok=True)
                for archive in self.archive_dir.glob("*.gz"):
                    archive.unlink()
                if source_kind == "active":
                    source = self.log_path
                    source.write_text(ambiguous, encoding="utf-8")
                else:
                    source = (
                        self.archive_dir
                        / "ai-activity-log.jsonl-2026-07-15T0000Z.gz"
                    )
                    with gzip.open(source, "wt", encoding="utf-8") as handle:
                        handle.write(ambiguous)
                before = source.read_bytes()
                with self.assertRaisesRegex(
                    RuntimeError,
                    "duplicate JSON key.*" + str(source),
                ):
                    inventory.generate_inventory(
                        status_root=self.status_root,
                        evidence_dir=self.status_root / "evidence",
                    )
                self.assertEqual(source.read_bytes(), before)

    def test_fails_on_bad_gzip(self):
        self._setup_all_four_incident_pairs()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f1.unlink()
        f1.write_bytes(b"not a gzip file")

        with self.assertRaises(RuntimeError):
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

    def test_replacement_detection(self):
        self._setup_all_four_incident_pairs()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"

        real_lstat = Path.lstat
        call_count = 0

        def fake_lstat(self_obj):
            nonlocal call_count
            stat_res = real_lstat(self_obj)
            if self_obj == f1:
                call_count += 1
                if call_count > 1:
                    class FakeStat:
                        st_mode = stat_res.st_mode
                        st_dev = stat_res.st_dev + 1
                        st_ino = stat_res.st_ino + 1
                        st_size = stat_res.st_size
                        st_mtime_ns = stat_res.st_mtime_ns
                    return FakeStat()
            return stat_res

        with mock.patch.object(Path, "lstat", fake_lstat):
            with self.assertRaises(RuntimeError) as ctx:
                inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")
            err_msg = str(ctx.exception)
            self.assertTrue("swapped" in err_msg or "replaced" in err_msg)

    def test_same_size_same_mtime_mutation(self):
        self._setup_all_four_incident_pairs()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"

        calls = []
        original_compute_hash = inventory.compute_hash_from_fd

        def fake_compute_hash(fd):
            h = original_compute_hash(fd)
            calls.append(h)
            if len(calls) == 2:
                return h + "_mutated"
            return h

        with mock.patch("scripts.activity_audit_logical_inventory.compute_hash_from_fd", fake_compute_hash):
            with self.assertRaises(RuntimeError) as ctx:
                inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")
            self.assertIn("unstable", str(ctx.exception))

    def test_scan_physical_sources_directly(self):
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"

        self._write_gz(f1, [{"event_id": "evt1", "val": 1}, {"event_id": "evt1", "val": 1}])
        self._write_gz(f2, [{"event_id": "evt1", "val": 1}, {"event_id": "evt1", "val": 2}])

        db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = db_file.name
        db_file.close()

        try:
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE raw_scanned_events ("
                "  source_path TEXT,"
                "  line_number INTEGER,"
                "  event_id TEXT,"
                "  digest TEXT,"
                "  is_dup_within INTEGER,"
                "  is_dup_overall INTEGER,"
                "  is_payload_mismatch INTEGER"
                ")"
            )
            conn.execute("CREATE INDEX idx_raw_events_id ON raw_scanned_events (event_id)")
            conn.execute("CREATE INDEX idx_raw_events_path ON raw_scanned_events (source_path, event_id)")

            source_infos, total_lines = inventory.scan_physical_sources([f1, f2], conn, self.status_root)
            conn.commit()

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events")
            self.assertEqual(cursor.fetchone()[0], 4)

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_overall = 1")
            self.assertEqual(cursor.fetchone()[0], 3)

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_overall = 1 AND is_payload_mismatch = 0")
            self.assertEqual(cursor.fetchone()[0], 2)

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_overall = 1 AND is_payload_mismatch = 1")
            self.assertEqual(cursor.fetchone()[0], 1)

            cursor.execute("SELECT COUNT(*) FROM raw_scanned_events WHERE is_dup_within = 1")
            self.assertEqual(cursor.fetchone()[0], 2)

            # Verify stable=True and before hashes are recorded
            self.assertEqual(len(source_infos), 2)
            self.assertTrue(source_infos[0]["stable"])
            self.assertIn("sha256_before", source_infos[0])
            self.assertNotIn("sha256_after", source_infos[0])

        finally:
            conn.close()
            if os.path.exists(db_path):
                os.unlink(db_path)

    def test_fd_and_temp_db_cleanup(self):
        self._setup_all_four_incident_pairs()

        # Count open FDs before
        fds_before = len(os.listdir("/proc/self/fd"))

        # Setup mock tempfile creation to inspect unlinking
        temp_paths = []
        original_tempfile = tempfile.NamedTemporaryFile

        def fake_tempfile(*args, **kwargs):
            tf = original_tempfile(*args, **kwargs)
            temp_paths.append(tf.name)
            return tf

        with mock.patch("tempfile.NamedTemporaryFile", fake_tempfile):
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

        # Count open FDs after
        fds_after = len(os.listdir("/proc/self/fd"))

        # Verify no file descriptors were leaked
        self.assertLessEqual(abs(fds_before - fds_after), 1)

        # Verify temp DB path was unlinked
        for path in temp_paths:
            self.assertFalse(os.path.exists(path))

    def test_inserted_1609_lineage(self):
        """Tests that the dynamic lineage is correctly traced when a 1609 file is inserted
        between 1450 and active.
        """
        overlap_A = [{"event_id": f"overlap_A_{i}", "val": i} for i in range(1000)]
        overlap_B = [{"event_id": f"overlap_B_{i}", "val": i} for i in range(1000)]
        overlap_C = [{"event_id": f"overlap_C_{i}", "val": i} for i in range(1000)]
        overlap_D = [{"event_id": f"overlap_D_{i}", "val": i} for i in range(1000)]
        overlap_E = [{"event_id": f"overlap_E_{i}", "val": i} for i in range(1000)]

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz(f1, [{"event_id": "f1_1"}] + overlap_A)
        self._write_gz(f2, overlap_A + [{"event_id": "f2_1"}])

        f3 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1301Z.gz"
        f4 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1404Z.gz"
        self._write_gz(f3, [{"event_id": "f3_1"}] + overlap_B)
        self._write_gz(f4, overlap_B + overlap_C)

        f5 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1450Z.gz"
        f6 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1609Z.gz"
        self._write_gz(f5, overlap_C + overlap_D)

        # 1609 has event_id_count = 0 (byte_identical no event IDs) to verify post_incident_rotation classification
        no_id_overlap_E = [{"val": i} for i in range(1000)]
        self._write_gz(f6, overlap_D + no_id_overlap_E)

        # Active log overlap
        self.log_path.write_text(
            "\n".join(json.dumps(e) for e in (no_id_overlap_E + [{"event_id": "active_1"}])) + "\n",
            encoding="utf-8"
        )

        inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

        summary = json.loads((self.status_root / "evidence" / "summary.json").read_text(encoding="utf-8"))
        folds = summary["folds_details"]

        # Find 1450 -> 1609 and 1609 -> active folds
        fold_1450_1609 = next(f for f in folds if f["prev_source"] == "ai-activity-log.jsonl-2026-07-16T1450Z.gz")
        fold_1609_active = next(f for f in folds if f["prev_source"] == "ai-activity-log.jsonl-2026-07-16T1609Z.gz")

        # 1450 -> 1609 contains event IDs from overlap_D
        self.assertTrue(fold_1450_1609["event_id_count"] > 0)
        self.assertEqual(fold_1450_1609["fold_type"], "incident_lineage")

        # 1609 -> active has 0 event IDs (from no_id_overlap_E)
        self.assertEqual(fold_1609_active["event_id_count"], 0)
        self.assertEqual(fold_1609_active["fold_type"], "post_incident_rotation")

    def test_failure_leaves_evidence_unchanged(self):
        self._setup_all_four_incident_pairs()

        # Run successfully first to generate files
        inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

        manifest_before = (self.status_root / "evidence" / "manifest.json").read_bytes()
        summary_before = (self.status_root / "evidence" / "summary.json").read_bytes()
        evidence_before = (self.status_root / "evidence" / "evidence.md").read_bytes()

        # Now corrupt f1 to trigger failure
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f1.unlink()
        f1.write_bytes(b"corrupt")

        with self.assertRaises(Exception):
            inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

        # Verify files are completely unchanged
        self.assertEqual((self.status_root / "evidence" / "manifest.json").read_bytes(), manifest_before)
        self.assertEqual((self.status_root / "evidence" / "summary.json").read_bytes(), summary_before)
        self.assertEqual((self.status_root / "evidence" / "evidence.md").read_bytes(), evidence_before)

    def test_replacement_between_passes(self):
        self._setup_all_four_incident_pairs()

        # Run successfully first to generate files
        inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")

        manifest_before = (self.status_root / "evidence" / "manifest.json").read_bytes()
        summary_before = (self.status_root / "evidence" / "summary.json").read_bytes()
        evidence_before = (self.status_root / "evidence" / "evidence.md").read_bytes()

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"

        real_lstat = Path.lstat
        real_stream = common.stream_logical_activity
        in_recheck = False

        def fake_stream(*args, **kwargs):
            nonlocal in_recheck
            gen = real_stream(*args, **kwargs)
            for val in gen:
                yield val
            in_recheck = True

        def fake_lstat(self_obj):
            stat_res = real_lstat(self_obj)
            if self_obj == f1 and in_recheck:
                class FakeStat:
                    st_mode = stat_res.st_mode
                    st_dev = stat_res.st_dev
                    st_ino = stat_res.st_ino + 999
                    st_size = stat_res.st_size
                    st_mtime_ns = stat_res.st_mtime_ns
                return FakeStat()
            return stat_res

        with mock.patch("scripts.activity_audit_logical_inventory.stream_logical_activity", fake_stream):
            with mock.patch.object(Path, "lstat", fake_lstat):
                with self.assertRaises(RuntimeError) as ctx:
                    inventory.generate_inventory(status_root=self.status_root, evidence_dir=self.status_root / "evidence")
                self.assertIn("replaced between passes", str(ctx.exception))

        # Verify files are completely unchanged, failure before final verification leaving prior evidence untouched
        self.assertEqual((self.status_root / "evidence" / "manifest.json").read_bytes(), manifest_before)
        self.assertEqual((self.status_root / "evidence" / "summary.json").read_bytes(), summary_before)
        self.assertEqual((self.status_root / "evidence" / "evidence.md").read_bytes(), evidence_before)

    def test_source_insertion_between_physical_and_logical_passes_fails(self):
        self.log_path.write_text(
            json.dumps({"event_id": "initial-active"}) + "\n",
            encoding="utf-8",
        )
        inserted = (
            self.archive_dir / "ai-activity-log.jsonl-2026-07-17T1700Z.gz"
        )
        real_stream = common.stream_logical_activity

        def stream_after_insertion(*args, **kwargs):
            self._write_gz(inserted, [{"event_id": "inserted-archive"}])
            return real_stream(*args, **kwargs)

        with mock.patch(
            "scripts.activity_audit_logical_inventory.stream_logical_activity",
            side_effect=stream_after_insertion,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "source set changed between physical and logical passes",
            ):
                inventory.generate_inventory(
                    status_root=self.status_root,
                    evidence_dir=self.status_root / "evidence",
                )

    def test_pinned_pair_success(self):
        b1, b2 = self._get_hermetic_pinned_bytes()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b1)
        self._write_gz_raw(f2, b2)

        # Write active log with some dummy content
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        res = list(common.stream_logical_activity(self.log_path))
        # Total logic entries: 1001 (f1) + 1001 (f2) - 999 (collapsed) + 1 (active) = 1004 entries!
        self.assertEqual(len(res), 1004)
        self.assertEqual(res[0][0]["event_id"], "redacted-pinned-predecessor-0")
        self.assertEqual(res[1][0]["event_id"], "redacted-pinned-predecessor-1")
        self.assertEqual(res[-3][0]["event_id"], "redacted-pinned-successor-0")
        self.assertEqual(res[-2][0]["event_id"], "redacted-pinned-successor-1")
        self.assertEqual(res[-1][0]["event_id"], "evt_active")

    def test_production_pinned_registry_metadata_is_exact(self):
        self.assertEqual(len(self.production_pinned_registry), 1)
        exception = self.production_pinned_registry[0]
        self.assertEqual(
            exception,
            common.HistoricalActivityOverlapException(
                predecessor_name=(
                    "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
                ),
                predecessor_gzip_sha256=(
                    "ad7dd174e0278a3c21b10024cd227f0d138052dd0945bc3b24159538d87ed6c5"
                ),
                predecessor_gzip_byte_count=772038,
                predecessor_payload_sha256=(
                    "8435543b845639383471bd3a3d1b1d1642bb0944649b5e2a4ffe1ad5ad9a4e57"
                ),
                predecessor_payload_byte_count=5326818,
                predecessor_line_count=1001,
                successor_name=(
                    "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
                ),
                successor_gzip_sha256=(
                    "d211e27bc5337c8eff200e14d48800f949658e6c8b43d9fd22e54ea8c77061da"
                ),
                successor_gzip_byte_count=771941,
                successor_payload_sha256=(
                    "da6a102178c82fb4eca8d0794ed5b419f0c97770e0ad63542dde0033e7efa3ff"
                ),
                successor_payload_byte_count=5326326,
                successor_line_count=1001,
                overlap_sha256=(
                    "0a3b56f720a5aa493d8968edfff8e32e0df98e410f6334d6790f10a06019f247"
                ),
                overlap_byte_count=5325808,
                overlap_line_count=999,
            ),
        )

    def test_pinned_pair_same_payload_with_different_gzip_fails(self):
        predecessor_payload, successor_payload = self._get_hermetic_pinned_bytes()
        predecessor = (
            self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        )
        successor = (
            self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        )
        predecessor.write_bytes(gzip.compress(predecessor_payload, mtime=1))
        self._write_gz_raw(successor, successor_payload)
        self.log_path.write_text(
            json.dumps({"event_id": "active"}) + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeError, "Invalid predecessor gzip hash"):
            list(common.stream_logical_activity(self.log_path))

    def test_pinned_registry_count_mismatches_fail_closed(self):
        predecessor_payload, successor_payload = self._get_hermetic_pinned_bytes()
        predecessor = (
            self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        )
        successor = (
            self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        )
        self._write_gz_raw(predecessor, predecessor_payload)
        self._write_gz_raw(successor, successor_payload)
        self.log_path.write_text(
            json.dumps({"event_id": "active"}) + "\n",
            encoding="utf-8",
        )

        cases = (
            (
                "predecessor_payload_byte_count",
                self.pinned_exception.predecessor_payload_byte_count + 1,
                "Invalid predecessor byte count",
            ),
            (
                "predecessor_line_count",
                self.pinned_exception.predecessor_line_count + 1,
                "Invalid predecessor line count",
            ),
            (
                "predecessor_gzip_byte_count",
                self.pinned_exception.predecessor_gzip_byte_count + 1,
                "Invalid predecessor gzip byte count",
            ),
            (
                "successor_payload_byte_count",
                self.pinned_exception.successor_payload_byte_count + 1,
                "Invalid successor byte count",
            ),
            (
                "successor_line_count",
                self.pinned_exception.successor_line_count + 1,
                "Invalid successor line count",
            ),
            (
                "successor_gzip_byte_count",
                self.pinned_exception.successor_gzip_byte_count + 1,
                "Invalid successor gzip byte count",
            ),
            (
                "overlap_byte_count",
                self.pinned_exception.overlap_byte_count + 1,
                "Invalid overlap bytes length",
            ),
            (
                "overlap_line_count",
                self.pinned_exception.overlap_line_count + 1,
                "Invalid overlap length",
            ),
        )
        for field, value, expected in cases:
            with self.subTest(field=field), mock.patch.object(
                common,
                "HISTORICAL_ACTIVITY_OVERLAP_EXCEPTIONS",
                (replace(self.pinned_exception, **{field: value}),),
            ):
                with self.assertRaisesRegex(RuntimeError, expected):
                    list(common.stream_logical_activity(self.log_path))

    @unittest.skipUnless(
        os.environ.get("PANTHEON_RUN_CENTRAL_ACTIVITY_INTEGRATION") == "1",
        "optional central read-only pinned-pair integration is disabled",
    )
    def test_optional_central_pinned_pair_read_only_integration(self):
        central = Path("/home/lupin/code/pantheon/archive/logs")
        predecessor_source = (
            central / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        )
        successor_source = (
            central / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        )
        if not predecessor_source.is_file() or not successor_source.is_file():
            self.fail("opt-in central pinned archives are unavailable")
        predecessor = self.archive_dir / predecessor_source.name
        successor = self.archive_dir / successor_source.name
        predecessor.write_bytes(predecessor_source.read_bytes())
        successor.write_bytes(successor_source.read_bytes())
        self.log_path.write_text(
            json.dumps({"event_id": "optional-central-active"}) + "\n",
            encoding="utf-8",
        )
        with mock.patch.object(
            common,
            "HISTORICAL_ACTIVITY_OVERLAP_EXCEPTIONS",
            self.production_pinned_registry,
        ):
            result = list(common.stream_logical_activity(self.log_path))
        self.assertEqual(len(result), 1004)

    def test_pinned_pair_bad_predecessor_hash_fails(self):
        b1, b2 = self._get_hermetic_pinned_bytes()
        # Mutate predecessor first line (keep it valid JSON but change hash)
        lines1 = b1.splitlines()
        obj = json.loads(lines1[0].decode("utf-8"))
        obj["event_id"] = "mutated_event_id_value"
        lines1[0] = json.dumps(obj).encode("utf-8")
        b1_mutated = b"\n".join(lines1) + b"\n"

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b1_mutated)
        self._write_gz_raw(f2, b2)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            list(common.stream_logical_activity(self.log_path))
        self.assertIn("Invalid predecessor hash", str(ctx.exception))

    def test_pinned_pair_bad_successor_hash_fails(self):
        b1, b2 = self._get_hermetic_pinned_bytes()
        # Mutate successor last line (outside the prefix overlap, keep valid JSON)
        lines = b2.splitlines()
        obj = json.loads(lines[-1].decode("utf-8"))
        obj["event_id"] = "mutated_successor_event"
        lines[-1] = json.dumps(obj).encode("utf-8")
        b2_mutated = b"\n".join(lines) + b"\n"

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b1)
        self._write_gz_raw(f2, b2_mutated)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        with self.assertRaises(RuntimeError) as ctx:
            list(common.stream_logical_activity(self.log_path))
        self.assertIn("Invalid successor hash", str(ctx.exception))

    def test_pinned_pair_bad_overlap_hash_fails(self):
        b1, b2 = self._get_hermetic_pinned_bytes()
        # Mutate a line in the overlap region of both files (so overlap matches but has different hash)
        lines1 = b1.splitlines()
        lines2 = b2.splitlines()

        # Change a digit character to preserve byte length exactly
        line_bytes = lines1[-500]
        mutated_line = bytearray(line_bytes)
        for idx_char, b_val in enumerate(mutated_line):
            if b'0' <= bytes([b_val]) <= b'9':
                mutated_line[idx_char] = ord('2') if chr(b_val) != '2' else ord('3')
                break
        mutated_line = bytes(mutated_line)

        lines1[-500] = mutated_line
        lines2[499] = mutated_line

        b1_mut = b"\n".join(lines1) + b"\n"
        b2_mut = b"\n".join(lines2) + b"\n"

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b1_mut)
        self._write_gz_raw(f2, b2_mut)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        mutated_exception = replace(
            self.pinned_exception,
            predecessor_gzip_sha256=hashlib.sha256(f1.read_bytes()).hexdigest(),
            predecessor_gzip_byte_count=f1.stat().st_size,
            predecessor_payload_sha256=hashlib.sha256(b1_mut).hexdigest(),
            predecessor_payload_byte_count=len(b1_mut),
            successor_gzip_sha256=hashlib.sha256(f2.read_bytes()).hexdigest(),
            successor_gzip_byte_count=f2.stat().st_size,
            successor_payload_sha256=hashlib.sha256(b2_mut).hexdigest(),
            successor_payload_byte_count=len(b2_mut),
        )
        with mock.patch.object(
            common,
            "HISTORICAL_ACTIVITY_OVERLAP_EXCEPTIONS",
            (mutated_exception,),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                list(common.stream_logical_activity(self.log_path))
            self.assertIn("Invalid overlap SHA-256", str(ctx.exception))

    def test_pinned_pair_bad_filename_fails(self):
        b1, b2 = self._get_hermetic_pinned_bytes()
        # Write pinned content to regular timestamp files
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz_raw(f1, b1)
        self._write_gz_raw(f2, b2)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Invalid overlap length 999"):
            list(common.stream_logical_activity(self.log_path))

    def test_pinned_pair_reversed_order_fails(self):
        b1, b2 = self._get_hermetic_pinned_bytes()
        # Reverse filenames for content
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b2)
        self._write_gz_raw(f2, b1)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        # Reversed order will either not match overlap or fail verification
        with self.assertRaises(RuntimeError):
            list(common.stream_logical_activity(self.log_path))
