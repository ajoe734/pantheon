import gzip
import json
import os
import stat
import sqlite3
import tempfile
import unittest
import hashlib
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

    def tearDown(self):
        inventory.STATUS_ROOT_DEFAULT = self.original_status_root
        inventory.LOG_FILE_DEFAULT = self.original_log_file
        inventory.EVIDENCE_DIR_DEFAULT = self.original_evidence_dir
        self.temp_dir.cleanup()

    def _write_gz(self, path: Path, entries: list[dict]):
        payload = "\n".join(json.dumps(e) for e in entries) + "\n"
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(payload)

    def _write_gz_raw(self, path: Path, content: bytes):
        with gzip.open(path, "wb") as f:
            f.write(content)

    def _get_real_pinned_bytes(self):
        t1237_src = Path("/home/lupin/code/pantheon/archive/logs/ai-activity-log.jsonl-2026-05-24T1237Z.gz")
        t1239_src = Path("/home/lupin/code/pantheon/archive/logs/ai-activity-log.jsonl-2026-05-24T1239Z.gz")
        if not t1237_src.exists() or not t1239_src.exists():
            raise unittest.SkipTest("Real central logs not available for pinned exception test")
        
        with gzip.open(t1237_src, "rb") as f:
            b1 = f.read()
        with gzip.open(t1239_src, "rb") as f:
            b2 = f.read()
        return b1, b2

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
            
            # Verify stable=True and before/after hashes are recorded
            self.assertEqual(len(source_infos), 2)
            self.assertTrue(source_infos[0]["stable"])
            self.assertEqual(source_infos[0]["sha256_before"], source_infos[0]["sha256_after"])
            
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

    def test_pinned_pair_success(self):
        b1, b2 = self._get_real_pinned_bytes()
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b1)
        self._write_gz_raw(f2, b2)
        
        # Write active log with some dummy content
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        res = list(common.stream_logical_activity(self.log_path))
        # Total logic entries: 1001 (f1) + 1001 (f2) - 999 (collapsed) + 1 (active) = 1004 entries!
        self.assertEqual(len(res), 1004)

    def test_pinned_pair_bad_predecessor_hash_fails(self):
        b1, b2 = self._get_real_pinned_bytes()
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
        b1, b2 = self._get_real_pinned_bytes()
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
        b1, b2 = self._get_real_pinned_bytes()
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

        h1_mut = hashlib.sha256(b1_mut).hexdigest()
        h2_mut = hashlib.sha256(b2_mut).hexdigest()

        original_sha256 = hashlib.sha256

        # Custom Hasher to return valid predecessor/successor hashes but mutated overlap hash
        class FakeHasher:
            def __init__(self, data=b""):
                self.hasher = original_sha256(data)
            def update(self, data):
                self.hasher.update(data)
            def hexdigest(self):
                h = self.hasher.hexdigest()
                if h == h1_mut:
                    return "8435543b845639383471bd3a3d1b1d1642bb0944649b5e2a4ffe1ad5ad9a4e57"
                if h == h2_mut:
                    return "da6a102178c82fb4eca8d0794ed5b419f0c97770e0ad63542dde0033e7efa3ff"
                return h

        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b1_mut)
        self._write_gz_raw(f2, b2_mut)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        with mock.patch("hashlib.sha256", FakeHasher):
            with self.assertRaises(RuntimeError) as ctx:
                list(common.stream_logical_activity(self.log_path))
            self.assertIn("Invalid overlap SHA-256", str(ctx.exception))

    def test_pinned_pair_bad_filename_fails(self):
        b1, b2 = self._get_real_pinned_bytes()
        # Write pinned content to regular timestamp files
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T0358Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T1130Z.gz"
        self._write_gz_raw(f1, b1)
        self._write_gz_raw(f2, b2)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "Invalid overlap length 999"):
            list(common.stream_logical_activity(self.log_path))

    def test_pinned_pair_reversed_order_fails(self):
        b1, b2 = self._get_real_pinned_bytes()
        # Reverse filenames for content
        f1 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1237Z.gz"
        f2 = self.archive_dir / "ai-activity-log.jsonl-2026-05-24T1239Z.gz"
        self._write_gz_raw(f1, b2)
        self._write_gz_raw(f2, b1)
        self.log_path.write_text(json.dumps({"event_id": "evt_active"}) + "\n", encoding="utf-8")

        # Reversed order will either not match overlap or fail verification
        with self.assertRaises(RuntimeError):
            list(common.stream_logical_activity(self.log_path))
