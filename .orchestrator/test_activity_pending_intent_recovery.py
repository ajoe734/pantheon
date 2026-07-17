"""Tests for the stranded schema-v1 pending-intent recovery transaction.

Every test runs against a repo-external isolated status root under a
tempfile directory. No test opens, locks, rotates, or rewrites the central
status root; test_isolated_lock_paths proves the lock paths in use resolve
inside the fixture root.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import common  # noqa: E402
import activity_pending_intent_recovery as recovery  # noqa: E402

MODULE_PATH = Path(recovery.__file__).resolve()

EXEC_ENV = {
    recovery.EXECUTE_ENV: recovery.EXECUTE_ENV_VALUE,
}


def _entries(start: int, count: int, prefix: str = "evt") -> list[dict]:
    return [
        {
            "event_id": f"{prefix}-{index:05d}",
            "ts": "2026-07-16T23:00:00Z",
            "agent": "Fixture",
            "message": f"m{index}",
        }
        for index in range(start, start + count)
    ]


def _jsonl(entries: list[dict]) -> bytes:
    return "".join(
        json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries
    ).encode("utf-8")


class StrictRecoveryParserTests(unittest.TestCase):
    def test_active_and_gzip_payload_event_ids_reject_duplicate_keys(self):
        ambiguous = (
            b'{"event_id":"first","event_id":"second",'
            b'"metadata":{"role":"a","role":"b"}}\n'
        )
        for source in ("active activity log", "superseding gzip payload"):
            with self.subTest(source=source), self.assertRaisesRegex(
                recovery.RecoveryProofError,
                "duplicate JSON key",
            ):
                recovery._event_ids(ambiguous, source=source)


class PendingIntentIncidentFixture(unittest.TestCase):
    """Builds the exact stranded-intent incident inside an isolated root."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name).resolve()
        self.log_path = self.root / "ai-activity-log.jsonl"
        self.archive_dir = self.root / "archive" / "logs"
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        self.legacy_dir = self.root / ".orchestrator" / "logs" / "activity-log-archive"
        self.legacy_dir.mkdir(parents=True, exist_ok=True)
        self.rotation_dir = (
            self.root / ".orchestrator" / "logs" / "activity-rotation"
        )
        self.rotation_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _publish_v1_intent(
        self,
        source: bytes,
        *,
        keep_lines: int,
        install_archive: bool = True,
    ) -> dict:
        lines = source.splitlines(keepends=True)
        self.assertGreater(len(lines), keep_lines)
        archive_payload = b"".join(lines[:-keep_lines]) if keep_lines else source
        tail = b"".join(lines[-keep_lines:]) if keep_lines else b""
        digest = hashlib.sha256(archive_payload).hexdigest()
        archive_relative = f"archive/logs/{self.log_path.name}-{digest}.gz"
        seed = {
            "schema_version": 1,
            "log_name": self.log_path.name,
            "source_sha256": hashlib.sha256(source).hexdigest(),
            "archive_sha256": digest,
            "tail_sha256": hashlib.sha256(tail).hexdigest(),
            "archive_relative_path": archive_relative,
        }
        transaction_id = "activity-rotation-" + common._canonical_json_sha256(
            {key: seed[key] for key in sorted(seed)}
        )
        intent = {**seed, "transaction_id": transaction_id}
        stage_archive, stage_tail = common._activity_rotation_stage_paths(
            self.log_path, transaction_id
        )
        common._durable_write_gzip(stage_archive, archive_payload)
        common.durable_write_bytes(stage_tail, tail)
        common.write_json(
            common.activity_rotation_intent_path(self.log_path), intent
        )
        if install_archive:
            common._durable_write_gzip(self.root / archive_relative, archive_payload)
        return {
            "transaction_id": transaction_id,
            "intent": intent,
            "archive_payload": archive_payload,
            "tail": tail,
            "archive_relative": archive_relative,
            "stage_archive": stage_archive,
            "stage_tail": stage_tail,
        }

    def build_incident(
        self,
        *,
        source_events: int = 60,
        v1_keep: int = 20,
        post_intent: int = 5,
        legacy_keep: int = 10,
        post_rotation: int = 8,
        prior_legacy: bool = True,
        legacy_name: str = "ai-activity-log.jsonl-2026-07-16T2337Z.gz",
    ) -> dict:
        events = _entries(0, source_events + post_intent + post_rotation)
        source = _jsonl(events[:source_events])
        facts = self._publish_v1_intent(source, keep_lines=v1_keep)
        facts["source"] = source
        facts["all_events"] = events

        if prior_legacy:
            prior = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T2037Z.gz"
            common._durable_write_gzip(prior, _jsonl(_entries(0, 7, prefix="old")))
            facts["prior_legacy"] = prior

        superseding_payload = _jsonl(events[: source_events + post_intent])
        superseding_path = self.archive_dir / legacy_name
        common._durable_write_gzip(superseding_path, superseding_payload)
        facts["superseding_path"] = superseding_path
        facts["superseding_payload"] = superseding_payload

        superseding_lines = superseding_payload.splitlines(keepends=True)
        retained = (
            b"".join(superseding_lines[-legacy_keep:]) if legacy_keep else b""
        )
        active = retained + _jsonl(events[source_events + post_intent :])
        self.log_path.write_bytes(active)
        facts["active"] = active
        facts["retained"] = retained
        return facts

    def _tree_state(self) -> dict[str, str]:
        state: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_symlink():
                state[str(path)] = "symlink:" + os.readlink(path)
            elif path.is_file():
                state[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                state[str(path)] = "dir"
        return state

    def _pin(self) -> tuple[dict, str]:
        manifest, _raw = recovery.capture_inventory(self.root)
        return manifest, recovery.manifest_digest(manifest)

    def _execute(self, manifest: dict, digest: str, **kwargs):
        with mock.patch.dict(os.environ, EXEC_ENV):
            return recovery.execute(
                self.root,
                manifest,
                expected_inventory_sha256=digest,
                writer_guard_attestation=kwargs.pop(
                    "attestation", "test-guard: all fixture writers stopped"
                ),
                **kwargs,
            )


class InventoryAndDryRunTests(PendingIntentIncidentFixture):
    def test_inventory_is_read_only_and_complete(self):
        facts = self.build_incident()
        before = self._tree_state()
        manifest, raw = recovery.capture_inventory(self.root)
        self.assertEqual(self._tree_state(), before)

        self.assertEqual(
            manifest["intent_payload"]["transaction_id"], facts["transaction_id"]
        )
        proof = manifest["proof"]
        self.assertTrue(proof["intent_present"])
        self.assertFalse(proof["already_resolved"])
        self.assertEqual(proof["missing_event_count"], 0)
        self.assertEqual(proof["duplicate_event_count"], 0)
        self.assertEqual(
            proof["superseding_relative_path"],
            str(facts["superseding_path"].relative_to(self.root)),
        )
        self.assertEqual(proof["retained_overlap_line_count"], 10)
        self.assertEqual(proof["post_intent_suffix_line_count"], 5)
        self.assertEqual(proof["post_rotation_suffix_line_count"], 8)
        self.assertEqual(
            proof["logical_event_total"], proof["logical_event_distinct"]
        )
        self.assertIn("intent", raw)
        # Locks are inventoried by lstat only.
        self.assertIn("note", manifest["locks"])

    def test_dry_run_reports_resolvable_without_mutation(self):
        self.build_incident()
        manifest, digest = self._pin()
        before = self._tree_state()
        report = recovery.dry_run(self.root, manifest)
        self.assertEqual(self._tree_state(), before)
        self.assertEqual(report["status"], "resolvable")
        self.assertFalse(report["mutation_performed"])
        self.assertEqual(report["pinned_inventory_sha256"], digest)
        self.assertEqual(report["active_appended_bytes_since_pin"], 0)
        proposed = report["proposed_resolution_row"]
        self.assertEqual(
            set(proposed), set(common.ACTIVITY_ROTATION_RESOLUTION_ROW_KEYS)
        )
        self.assertEqual(len(report["proposed_mutations"]), 3)

    def test_dry_run_allows_append_since_pin(self):
        self.build_incident()
        manifest, _digest = self._pin()
        with self.log_path.open("ab") as handle:
            handle.write(_jsonl(_entries(9000, 2, prefix="late")))
        report = recovery.dry_run(self.root, manifest)
        self.assertEqual(report["status"], "resolvable")
        self.assertGreater(report["active_appended_bytes_since_pin"], 0)

    def test_dry_run_rejects_non_append_active_drift(self):
        self.build_incident()
        manifest, _digest = self._pin()
        # Removing the last appended line still proves standalone relations,
        # but the active log is no longer the pinned bytes plus a suffix.
        lines = self.log_path.read_bytes().splitlines(keepends=True)
        self.log_path.write_bytes(b"".join(lines[:-1]))
        with self.assertRaisesRegex(RuntimeError, "appended suffix"):
            recovery.dry_run(self.root, manifest)


class ExecuteGateTests(PendingIntentIncidentFixture):
    def test_execute_requires_environment_opt_in(self):
        self.build_incident()
        manifest, digest = self._pin()
        before = self._tree_state()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(recovery.EXECUTE_ENV, None)
            with self.assertRaisesRegex(RuntimeError, recovery.EXECUTE_ENV):
                recovery.execute(
                    self.root,
                    manifest,
                    expected_inventory_sha256=digest,
                    writer_guard_attestation="guard",
                )
        self.assertEqual(self._tree_state(), before)

    def test_execute_requires_exact_inventory_digest(self):
        self.build_incident()
        manifest, digest = self._pin()
        before = self._tree_state()
        with mock.patch.dict(os.environ, EXEC_ENV):
            with self.assertRaisesRegex(RuntimeError, "digest does not match"):
                recovery.execute(
                    self.root,
                    manifest,
                    expected_inventory_sha256="0" * 64,
                    writer_guard_attestation="guard",
                )
        del digest
        self.assertEqual(self._tree_state(), before)

    def test_execute_requires_guard_attestation(self):
        self.build_incident()
        manifest, digest = self._pin()
        before = self._tree_state()
        with mock.patch.dict(os.environ, EXEC_ENV):
            with self.assertRaisesRegex(RuntimeError, "guard attestation"):
                recovery.execute(
                    self.root,
                    manifest,
                    expected_inventory_sha256=digest,
                    writer_guard_attestation="   ",
                )
        self.assertEqual(self._tree_state(), before)

    def test_execute_stops_on_append_since_pin(self):
        self.build_incident()
        manifest, digest = self._pin()
        with self.log_path.open("ab") as handle:
            handle.write(_jsonl(_entries(9100, 1, prefix="late")))
        with self.assertRaisesRegex(RuntimeError, "stable-input recheck failed"):
            self._execute(manifest, digest)
        self.assertTrue(
            common.activity_rotation_intent_path(self.log_path).exists()
        )

    def test_execute_stops_on_changed_active_inode(self):
        self.build_incident()
        manifest, digest = self._pin()
        payload = self.log_path.read_bytes()
        replacement = self.log_path.with_suffix(".swap")
        replacement.write_bytes(payload)
        os.replace(replacement, self.log_path)
        with self.assertRaisesRegex(RuntimeError, "active.inode"):
            self._execute(manifest, digest)


class ExecuteTransactionTests(PendingIntentIncidentFixture):
    def test_execute_resolves_exact_incident(self):
        facts = self.build_incident()
        manifest, digest = self._pin()
        report = self._execute(manifest, digest)
        self.assertEqual(report["status"], "resolved")
        self.assertTrue(report["mutation_performed"])
        self.assertEqual(
            report["resolved_transaction_id"], facts["transaction_id"]
        )

        intent_path = common.activity_rotation_intent_path(self.log_path)
        self.assertFalse(intent_path.exists())
        self.assertFalse(facts["stage_archive"].exists())
        self.assertFalse(facts["stage_tail"].exists())

        preserved_dir = common.activity_rotation_preserved_dir(
            self.log_path, facts["transaction_id"]
        )
        preserved_intent = json.loads(
            (preserved_dir / recovery.PRESERVED_INTENT_NAME).read_text()
        )
        self.assertEqual(preserved_intent, facts["intent"])
        self.assertEqual(
            gzip.decompress(
                (preserved_dir / recovery.PRESERVED_STAGE_ARCHIVE_NAME).read_bytes()
            ),
            facts["archive_payload"],
        )
        self.assertEqual(
            (preserved_dir / recovery.PRESERVED_STAGE_TAIL_NAME).read_bytes(),
            facts["tail"],
        )

        # Original incident artifacts remain untouched.
        self.assertEqual(self.log_path.read_bytes(), facts["active"])
        self.assertEqual(
            gzip.decompress((self.root / facts["archive_relative"]).read_bytes()),
            facts["archive_payload"],
        )
        self.assertEqual(
            gzip.decompress(facts["superseding_path"].read_bytes()),
            facts["superseding_payload"],
        )

        # Readers accept the layout and never enumerate the superseded archive.
        common.assert_activity_audit_stable_unlocked(self.log_path)
        sources = common.activity_audit_source_paths_unlocked(self.log_path)
        self.assertNotIn(
            (self.root / facts["archive_relative"]).resolve(),
            [source.resolve() for source in sources],
        )

        _bytes, rows, paths = common._load_activity_rotation_resolutions_unlocked(
            self.log_path
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["resolved_transaction_id"], facts["transaction_id"]
        )
        self.assertEqual(rows[0]["inventory_sha256"], digest)
        self.assertEqual(
            paths[0].resolve(), (self.root / facts["archive_relative"]).resolve()
        )

        # Idempotent re-run against the ORIGINAL pin performs no mutation.
        resolutions_path = common.activity_rotation_resolutions_path(self.log_path)
        resolved_bytes = resolutions_path.read_bytes()
        report2 = self._execute(manifest, digest)
        self.assertEqual(report2["status"], "already-resolved")
        self.assertFalse(report2["mutation_performed"])
        self.assertEqual(resolutions_path.read_bytes(), resolved_bytes)

        # A fresh post-resolution pin has no pending intent left to execute
        # against; execute fails closed instead of guessing.
        manifest2, digest2 = self._pin()
        with self.assertRaisesRegex(RuntimeError, "no pending schema-v1 intent"):
            self._execute(manifest2, digest2)
        self.assertEqual(resolutions_path.read_bytes(), resolved_bytes)

    def test_execute_supports_zero_one_many_appends_and_zero_overlap(self):
        for post_rotation, legacy_keep in ((0, 10), (1, 10), (12, 10), (4, 0)):
            with self.subTest(post_rotation=post_rotation, legacy_keep=legacy_keep):
                self.tearDown()
                self.setUp()
                self.build_incident(
                    post_rotation=post_rotation, legacy_keep=legacy_keep
                )
                manifest, digest = self._pin()
                proof = manifest["proof"]
                self.assertEqual(
                    proof["post_rotation_suffix_line_count"], post_rotation
                )
                self.assertEqual(proof["retained_overlap_line_count"], legacy_keep)
                report = self._execute(manifest, digest)
                self.assertEqual(report["status"], "resolved")

    def test_exact_duplicate_superseding_archive_is_safe(self):
        self.build_incident(post_intent=0, post_rotation=3)
        manifest, digest = self._pin()
        self.assertEqual(manifest["proof"]["post_intent_suffix_line_count"], 0)
        report = self._execute(manifest, digest)
        self.assertEqual(report["status"], "resolved")

    def test_competing_recovery_is_serialized_by_exclusive_lock(self):
        self.build_incident()
        manifest, digest = self._pin()
        holder_ready = threading.Event()
        release = threading.Event()

        def hold_lock():
            with common.activity_audit_lock_file(self.log_path, shared=False):
                holder_ready.set()
                release.wait(timeout=30)

        thread = threading.Thread(target=hold_lock, daemon=True)
        thread.start()
        self.assertTrue(holder_ready.wait(timeout=10))
        try:
            with self.assertRaises(Exception):
                self._execute(manifest, digest)
            self.assertTrue(
                common.activity_rotation_intent_path(self.log_path).exists()
            )
        finally:
            release.set()
            thread.join(timeout=10)


class RelationVariantTests(PendingIntentIncidentFixture):
    def _expect_fail(self, pattern: str):
        with self.assertRaisesRegex(RuntimeError, pattern):
            recovery.capture_inventory(self.root)

    def test_superseding_archive_relationship_variants_fail_closed(self):
        source_events, post_intent = 60, 5
        variants = (
            "prefix_of_source",
            "one_byte_differs",
            "overlap_without_newline",
            "independent",
            "two_candidates",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                self.tearDown()
                self.setUp()
                facts = self.build_incident(
                    source_events=source_events, post_intent=post_intent
                )
                superseding = facts["superseding_path"]
                payload = facts["superseding_payload"]
                if variant == "prefix_of_source":
                    truncated = b"".join(
                        facts["source"].splitlines(keepends=True)[:-2]
                    )
                    common._durable_write_gzip(superseding, truncated)
                    self._expect_fail("exactly one legacy archive")
                elif variant == "one_byte_differs":
                    mutated = bytearray(payload)
                    mutated[len(payload) // 2] ^= 0x01
                    common._durable_write_gzip(superseding, bytes(mutated))
                    self._expect_fail(
                        "exactly one legacy archive|not valid JSON|duplicate"
                    )
                elif variant == "overlap_without_newline":
                    common._durable_write_gzip(
                        superseding, facts["source"] + b'{"event_id": "torn"'
                    )
                    self._expect_fail("not newline-terminated")
                elif variant == "independent":
                    common._durable_write_gzip(
                        superseding, _jsonl(_entries(5000, 30, prefix="other"))
                    )
                    self._expect_fail("exactly one legacy archive")
                elif variant == "two_candidates":
                    second = (
                        self.archive_dir
                        / "ai-activity-log.jsonl-2026-07-16T2350Z.gz"
                    )
                    common._durable_write_gzip(
                        second, payload + _jsonl(_entries(8000, 1, prefix="x"))
                    )
                    self._expect_fail("exactly one legacy archive")

    def test_partial_final_active_line_fails_closed(self):
        self.build_incident()
        with self.log_path.open("ab") as handle:
            handle.write(b'{"event_id": "torn-tail"')
        self._expect_fail("partial trailing line")

    def test_post_rotation_suffix_repeating_superseded_events_fails(self):
        facts = self.build_incident()
        repeated = facts["superseding_payload"].splitlines(keepends=True)[2]
        with self.log_path.open("ab") as handle:
            handle.write(repeated)
        self._expect_fail("duplicate logical event ids|repeats events")


class TamperMatrixTests(PendingIntentIncidentFixture):
    def test_incident_artifact_tampers_fail_closed(self):
        cases = (
            "intent_field",
            "intent_transaction_id",
            "stage_archive_payload",
            "stage_tail_bytes",
            "stage_tail_truncated",
            "installed_gzip_conflict",
            "installed_missing",
            "stage_archive_missing",
            "stage_tail_missing",
            "stage_archive_partial_gzip",
            "active_overlap_tamper",
            "extra_content_archive",
            "unknown_archive_name",
            "lineage_present",
        )
        for case in cases:
            with self.subTest(case=case):
                self.tearDown()
                self.setUp()
                facts = self.build_incident()
                intent_path = common.activity_rotation_intent_path(self.log_path)
                if case == "intent_field":
                    payload = json.loads(intent_path.read_text())
                    payload["source_sha256"] = "0" * 64
                    intent_path.write_text(json.dumps(payload))
                    pattern = "contract is invalid"
                elif case == "intent_transaction_id":
                    payload = json.loads(intent_path.read_text())
                    payload["transaction_id"] = "activity-rotation-" + "0" * 64
                    intent_path.write_text(json.dumps(payload))
                    pattern = "contract is invalid"
                elif case == "stage_archive_payload":
                    common._durable_write_gzip(
                        facts["stage_archive"],
                        facts["archive_payload"] + b'{"event_id": "x"}\n',
                    )
                    pattern = "digest mismatch"
                elif case == "stage_tail_bytes":
                    mutated = bytearray(facts["tail"])
                    mutated[0] ^= 0x01
                    facts["stage_tail"].write_bytes(bytes(mutated))
                    pattern = "digest mismatch"
                elif case == "stage_tail_truncated":
                    facts["stage_tail"].write_bytes(facts["tail"][:-7])
                    pattern = "digest mismatch"
                elif case == "installed_gzip_conflict":
                    common._durable_write_gzip(
                        self.root / facts["archive_relative"],
                        b'{"event_id": "conflict"}\n',
                    )
                    pattern = "differs from the staged archive payload"
                elif case == "installed_missing":
                    (self.root / facts["archive_relative"]).unlink()
                    pattern = "installed content archive is missing"
                elif case == "stage_archive_missing":
                    facts["stage_archive"].unlink()
                    pattern = "staged rotation archive is missing"
                elif case == "stage_tail_missing":
                    facts["stage_tail"].unlink()
                    pattern = "staged rotation tail is missing"
                elif case == "stage_archive_partial_gzip":
                    raw = facts["stage_archive"].read_bytes()
                    facts["stage_archive"].write_bytes(raw[: len(raw) // 2])
                    pattern = "gzip stream is invalid"
                elif case == "active_overlap_tamper":
                    data = bytearray(self.log_path.read_bytes())
                    data[10] ^= 0x01
                    self.log_path.write_bytes(bytes(data))
                    pattern = (
                        "repeats events|duplicate logical event ids|not valid JSON"
                    )
                elif case == "extra_content_archive":
                    common._durable_write_gzip(
                        self.archive_dir
                        / f"{self.log_path.name}-{'a' * 64}.gz",
                        b'{"event_id": "orphan"}\n',
                    )
                    pattern = "unexplained content-addressed archive"
                elif case == "unknown_archive_name":
                    common._durable_write_gzip(
                        self.archive_dir / f"{self.log_path.name}-badname.gz",
                        b'{"event_id": "weird"}\n',
                    )
                    pattern = "unknown activity archive name"
                elif case == "lineage_present":
                    common.activity_rotation_lineage_path(
                        self.log_path
                    ).write_text("{}\n")
                    pattern = "lineage file exists"
                with self.assertRaisesRegex(RuntimeError, pattern):
                    recovery.capture_inventory(self.root)

    def test_symlinked_incident_leaves_fail_closed(self):
        for leaf in ("intent", "stage_tail", "installed", "superseding", "active"):
            with self.subTest(leaf=leaf):
                self.tearDown()
                self.setUp()
                facts = self.build_incident()
                if leaf == "intent":
                    target = common.activity_rotation_intent_path(self.log_path)
                elif leaf == "stage_tail":
                    target = facts["stage_tail"]
                elif leaf == "installed":
                    target = self.root / facts["archive_relative"]
                elif leaf == "superseding":
                    target = facts["superseding_path"]
                else:
                    target = self.log_path
                shadow = target.with_name(target.name + ".shadow")
                shutil.move(target, shadow)
                target.symlink_to(shadow)
                with self.assertRaisesRegex(
                    RuntimeError, "regular file|symlink"
                ):
                    recovery.capture_inventory(self.root)

    def test_stale_pin_against_mutated_manifest_fails(self):
        self.build_incident()
        manifest, digest = self._pin()
        manifest["captured_utc"] = "1970-01-01T00:00:00Z"
        with mock.patch.dict(os.environ, EXEC_ENV):
            with self.assertRaisesRegex(RuntimeError, "digest does not match"):
                recovery.execute(
                    self.root,
                    manifest,
                    expected_inventory_sha256=digest,
                    writer_guard_attestation="guard",
                )


class CrashRetryTests(PendingIntentIncidentFixture):
    def _run_cli_execute(self, manifest_path: Path, digest: str, fault: str | None):
        env = dict(os.environ)
        env.update(EXEC_ENV)
        if fault:
            env[recovery.FAULT_ENV] = fault
        else:
            env.pop(recovery.FAULT_ENV, None)
        return subprocess.run(
            [
                sys.executable,
                str(MODULE_PATH),
                "execute",
                "--status-root",
                str(self.root),
                "--inventory",
                str(manifest_path),
                "--expected-inventory-sha256",
                digest,
                "--writer-guard-attestation",
                "subprocess-guard: fixture writers stopped",
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=120,
        )

    def test_sigkill_at_each_publish_step_converges_on_retry(self):
        for fault in (
            "pin-recheck",
            "preserve",
            "resolution",
            "resolution-readback",
            "unlink-intent",
            "unlink-stage",
        ):
            with self.subTest(fault=fault):
                self.tearDown()
                self.setUp()
                facts = self.build_incident()
                manifest, digest = self._pin()
                manifest_path = Path(self.temp_dir.name) / "pin.json"
                manifest_path.write_text(json.dumps(manifest))

                crashed = self._run_cli_execute(manifest_path, digest, fault)
                self.assertEqual(crashed.returncode, -9, crashed.stderr)

                # The interrupted state never exposes a partial logical
                # history to shared readers: either the pending intent still
                # fails closed, or the resolution is complete and valid.
                intent_path = common.activity_rotation_intent_path(self.log_path)
                if intent_path.exists():
                    with self.assertRaises(RuntimeError):
                        common.assert_activity_audit_stable_unlocked(self.log_path)
                else:
                    common.assert_activity_audit_stable_unlocked(self.log_path)
                    common.activity_audit_source_paths_unlocked(self.log_path)

                retried = self._run_cli_execute(manifest_path, digest, None)
                self.assertEqual(retried.returncode, 0, retried.stderr)
                report = json.loads(retried.stdout)
                self.assertIn(report["status"], ("resolved", "already-resolved"))

                _bytes, rows, _paths = (
                    common._load_activity_rotation_resolutions_unlocked(
                        self.log_path
                    )
                )
                self.assertEqual(len(rows), 1)
                self.assertFalse(intent_path.exists())
                self.assertFalse(facts["stage_archive"].exists())
                self.assertEqual(self.log_path.read_bytes(), facts["active"])

                third = self._run_cli_execute(manifest_path, digest, None)
                self.assertEqual(third.returncode, 0, third.stderr)
                self.assertEqual(
                    json.loads(third.stdout)["status"], "already-resolved"
                )


class ResolutionReaderContractTests(PendingIntentIncidentFixture):
    def _resolve(self) -> dict:
        facts = self.build_incident()
        manifest, digest = self._pin()
        self._execute(manifest, digest)
        facts["digest"] = digest
        return facts

    def _resolution_rows(self) -> list[dict]:
        path = common.activity_rotation_resolutions_path(self.log_path)
        return [
            json.loads(line)
            for line in path.read_text().splitlines()
            if line.strip()
        ]

    def _write_rows(self, rows: list[dict]) -> None:
        common.activity_rotation_resolutions_path(self.log_path).write_bytes(
            b"".join(common._canonical_json_line(row) for row in rows)
        )

    def test_resolution_row_field_tampers_fail_closed(self):
        mutations = (
            ("sequence", 7, "identity is invalid"),
            ("resolved_transaction_id", "activity-rotation-" + "0" * 64, "id mismatch|binding mismatch"),
            ("archive_gzip_sha256", "0" * 64, "id mismatch|digest mismatch"),
            ("archive_payload_sha256", "0" * 64, "id mismatch|binding mismatch"),
            ("superseding_gzip_sha256", "0" * 64, "id mismatch|digest mismatch"),
            ("superseding_payload_sha256", "0" * 64, "id mismatch|digest mismatch"),
            ("source_byte_count", 1, "id mismatch|conservation counts"),
            ("active_line_count", 999999, "id mismatch|conservation counts"),
            ("previous_resolutions_sha256", "0" * 64, "previous digest mismatch|id mismatch"),
            ("writer_guard_attestation", " ", "attestation is missing|id mismatch"),
            ("resolution_id", "activity-intent-resolution-" + "0" * 64, "id mismatch"),
            ("inventory_sha256", "0" * 64, "id mismatch"),
        )
        for key, value, pattern in mutations:
            with self.subTest(key=key):
                self.tearDown()
                self.setUp()
                self._resolve()
                rows = self._resolution_rows()
                rows[0][key] = value
                self._write_rows(rows)
                with self.assertRaisesRegex(RuntimeError, pattern):
                    common.activity_audit_source_paths_unlocked(self.log_path)

    def test_resolutions_file_truncation_and_blank_rows_fail_closed(self):
        self._resolve()
        path = common.activity_rotation_resolutions_path(self.log_path)
        payload = path.read_bytes()
        for case, mutated in (
            ("truncated", payload[:-3]),
            ("blank_row", b"\n" + payload),
            ("empty", b""),
        ):
            with self.subTest(case=case):
                path.write_bytes(mutated)
                with self.assertRaisesRegex(
                    RuntimeError, "truncated|blank|empty|unreadable"
                ):
                    common.activity_audit_source_paths_unlocked(self.log_path)
                path.write_bytes(payload)

    def test_exact_superseded_archive_backup_is_accepted(self):
        facts = self._resolve()
        archive = self.root / facts["archive_relative"]
        backup = archive.with_name(f"{archive.name}.bak")
        archive.replace(backup)

        sources = common.activity_audit_source_paths_unlocked(self.log_path)
        self.assertNotIn(backup.resolve(), [source.resolve() for source in sources])

        common._durable_write_gzip(backup, b'{"event_id": "swap"}\n')
        with self.assertRaisesRegex(RuntimeError, "gzip digest mismatch"):
            common.activity_audit_source_paths_unlocked(self.log_path)

    def test_missing_superseded_archive_and_backup_fails_closed(self):
        facts = self._resolve()
        archive = self.root / facts["archive_relative"]
        archive.unlink()
        with self.assertRaisesRegex(RuntimeError, "superseded archive is missing"):
            common.activity_audit_source_paths_unlocked(self.log_path)

    def test_symlinked_superseded_archive_backup_fails_closed(self):
        facts = self._resolve()
        archive = self.root / facts["archive_relative"]
        shadow = archive.with_name(f"{archive.name}.shadow")
        backup = archive.with_name(f"{archive.name}.bak")
        archive.replace(shadow)
        backup.symlink_to(shadow)
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            common.activity_audit_source_paths_unlocked(self.log_path)

    def test_superseding_archive_tamper_fails_closed(self):
        facts = self._resolve()
        common._durable_write_gzip(
            facts["superseding_path"], b'{"event_id": "swapped"}\n'
        )
        with self.assertRaisesRegex(
            RuntimeError, "superseding archive gzip digest mismatch"
        ):
            common.activity_audit_source_paths_unlocked(self.log_path)

    def test_symlinked_resolutions_file_fails_closed(self):
        self._resolve()
        path = common.activity_rotation_resolutions_path(self.log_path)
        shadow = path.with_name(path.name + ".shadow")
        shutil.move(path, shadow)
        path.symlink_to(shadow)
        with self.assertRaisesRegex(RuntimeError, "regular file"):
            common.activity_audit_source_paths_unlocked(self.log_path)

    def test_rotation_rejects_publishing_onto_superseded_archive_path(self):
        facts = self._resolve()
        self.log_path.write_bytes(facts["archive_payload"])
        with common.activity_audit_lock_file(self.log_path, shared=False):
            with self.assertRaisesRegex(RuntimeError, "already superseded"):
                common.rotate_activity_log_unlocked(
                    self.log_path, max_bytes=1, keep_lines=0
                )

    def test_superseded_archive_registered_in_lineage_fails_closed(self):
        facts = self._resolve()
        # Force-write a lineage row registering the superseded archive; the
        # combined lineage/resolution accounting must reject the overlap
        # before any logical row is exposed.
        archive = self.root / facts["archive_relative"]
        payload = facts["archive_payload"]
        with self.assertRaisesRegex(RuntimeError, "lineage|conflict|superseded"):
            row = {
                "record_type": common.ACTIVITY_ROTATION_LINEAGE_RECORD_TYPE,
                "schema_version": common.ACTIVITY_LOG_ROTATION_SCHEMA_VERSION,
                "log_name": self.log_path.name,
                "sequence": 1,
                "transaction_id": "activity-rotation-" + "1" * 64,
                "archive_relative_path": facts["archive_relative"],
                "archive_payload_sha256": hashlib.sha256(payload).hexdigest(),
                "archive_gzip_sha256": hashlib.sha256(
                    archive.read_bytes()
                ).hexdigest(),
                "archive_byte_count": len(payload),
                "archive_line_count": len(payload.splitlines()),
                "source_sha256": "0" * 64,
                "source_payload_sha256": "0" * 64,
                "source_byte_count": 0,
                "source_line_count": 0,
                "tail_sha256": common.ACTIVITY_ROTATION_EMPTY_SHA256,
                "tail_byte_count": 0,
                "tail_line_count": 0,
                "previous_sequence": 0,
                "previous_transaction_id": None,
                "previous_lineage_sha256": common.ACTIVITY_ROTATION_EMPTY_SHA256,
                "boundary_normalization": None,
            }
            common.activity_rotation_lineage_path(self.log_path).write_bytes(
                common._canonical_json_line(row)
            )
            common.activity_audit_source_paths_unlocked(self.log_path)


class StrandedIntentFailClosedTests(PendingIntentIncidentFixture):
    def test_v2_reader_and_writers_fail_closed_on_stranded_v1_intent(self):
        self.build_incident()
        with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
            list(common.stream_logical_activity(self.log_path))
        with self.assertRaisesRegex(
            RuntimeError, "PENDING-INTENT-RECOVERY"
        ):
            common._load_activity_rotation_intent(self.log_path)
        with common.activity_audit_lock_file(self.log_path, shared=False):
            with self.assertRaisesRegex(RuntimeError, "PENDING-INTENT-RECOVERY"):
                common.rotate_activity_log_unlocked(
                    self.log_path, max_bytes=1, keep_lines=0
                )
            with self.assertRaisesRegex(RuntimeError, "PENDING-INTENT-RECOVERY"):
                common.append_activity_log_entries_unlocked(
                    self.log_path,
                    [{"event_id": "blocked"}],
                    rotate_bytes=10**9,
                )

    def test_writer_guard_pauses_rotation_and_recovery(self):
        self.build_incident()
        guard_env = {common.ACTIVITY_ROTATION_WRITER_GUARD_ENV: "1"}
        with mock.patch.dict(os.environ, guard_env):
            self.assertTrue(common.activity_rotation_writer_guard_active())
            with common.activity_audit_lock_file(self.log_path, shared=False):
                # Neither mechanism may start or recover a rotation while
                # the guard is active, even over threshold.
                self.assertIsNone(
                    common.rotate_activity_log_unlocked(
                        self.log_path, max_bytes=1, keep_lines=0
                    )
                )
                common.prepare_activity_audit_unlocked(self.log_path)
            self.assertTrue(
                common.activity_rotation_intent_path(self.log_path).exists()
            )

    def test_writer_guard_pauses_fresh_rotation_without_incident(self):
        self.log_path.write_bytes(_jsonl(_entries(0, 30)))
        guard_env = {common.ACTIVITY_ROTATION_WRITER_GUARD_ENV: "1"}
        with mock.patch.dict(os.environ, guard_env):
            with common.activity_audit_lock_file(self.log_path, shared=False):
                self.assertIsNone(
                    common.rotate_activity_log_unlocked(
                        self.log_path, max_bytes=1, keep_lines=0
                    )
                )
        self.assertFalse(
            common.activity_rotation_intent_path(self.log_path).exists()
        )
        with common.activity_audit_lock_file(self.log_path, shared=False):
            archive = common.rotate_activity_log_unlocked(
                self.log_path, max_bytes=1, keep_lines=0
            )
        self.assertIsNotNone(archive)


class LiveScaleEndToEndTests(PendingIntentIncidentFixture):
    def build_live_scale(self) -> dict:
        events = _entries(0, 1550)
        prior = self.archive_dir / "ai-activity-log.jsonl-2026-07-16T2037Z.gz"
        common._durable_write_gzip(prior, _jsonl(events[0:1200]))
        source = _jsonl(events[200:1500])
        facts = self._publish_v1_intent(source, keep_lines=1000)
        superseding_payload = _jsonl(events[200:1520])
        superseding_path = (
            self.archive_dir / "ai-activity-log.jsonl-2026-07-16T2337Z.gz"
        )
        common._durable_write_gzip(superseding_path, superseding_payload)
        active = _jsonl(events[520:1550])
        self.log_path.write_bytes(active)
        facts.update(
            {
                "events": events,
                "superseding_path": superseding_path,
                "active": active,
            }
        )
        return facts

    def test_live_scale_recovery_stream_and_next_rotation(self):
        facts = self.build_live_scale()
        manifest, digest = self._pin()
        proof = manifest["proof"]
        self.assertEqual(proof["retained_overlap_line_count"], 1000)
        self.assertEqual(proof["post_intent_suffix_line_count"], 20)
        self.assertEqual(proof["post_rotation_suffix_line_count"], 30)
        self.assertEqual(proof["logical_event_total"], 1350)

        report = self._execute(manifest, digest)
        self.assertEqual(report["status"], "resolved")

        expected_ids = [f"evt-{index:05d}" for index in range(1550)]
        logical_ids = [
            entry["event_id"]
            for entry, _, _ in common.stream_logical_activity(self.log_path)
        ]
        self.assertEqual(logical_ids, expected_ids)

        # The first schema-v2 content rotation after recovery normalizes the
        # 1000-line legacy boundary and keeps the logical stream identical.
        with common.activity_audit_lock_file(self.log_path, shared=False):
            archive = common.rotate_activity_log_unlocked(
                self.log_path, max_bytes=1, keep_lines=0
            )
        self.assertIsNotNone(archive)
        rows = [
            json.loads(line)
            for line in common.activity_rotation_lineage_path(self.log_path)
            .read_text()
            .splitlines()
        ]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["boundary_normalization"])
        logical_ids = [
            entry["event_id"]
            for entry, _, _ in common.stream_logical_activity(self.log_path)
        ]
        self.assertEqual(logical_ids, expected_ids)
        self.assertEqual(
            gzip.decompress(facts["superseding_path"].read_bytes()),
            _jsonl(facts["events"][200:1520]),
        )


class IsolationTests(PendingIntentIncidentFixture):
    def test_isolated_lock_paths_and_no_central_references(self):
        self.build_incident()
        lock_path = common.activity_audit_lock_path(self.log_path)
        self.assertTrue(str(lock_path).startswith(str(self.root)))
        manifest, digest = self._pin()
        self.assertEqual(manifest["status_root"], str(self.root))
        for entry in manifest["archive_listing"]:
            self.assertFalse(Path(entry["relative_path"]).is_absolute())
        report = self._execute(manifest, digest)
        self.assertEqual(report["status_root"], str(self.root))
        self.assertTrue(
            str(common.activity_rotation_resolutions_path(self.log_path)).startswith(
                str(self.root)
            )
        )


if __name__ == "__main__":
    unittest.main()
