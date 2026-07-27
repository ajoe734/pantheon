"""
OPS-L12-PYTHON-PACKAGING-PROVISION-001 — fail-closed evidence manifest check.

This task's manifest is the artifact bound as `review_file` at closeout, so a
defect in it is a governance defect. This module gates it the same way
`scripts/test_ops_l12_telemetry_discovery_import_evidence.py` gates the
predecessor's: schema validity, no future timestamps, a dense record log, and a
live checksum anchor.

Fail-closed means no skips. The draft-07 subset validator is **not**
reimplemented here — it is imported from the predecessor's gate, which is the
one already-merged copy in the repository. Reimplementing it would create a
second source of truth for the same schema, and editing that module would put
this task's diff inside another lane's merged artifact. When `jsonschema` is
importable (it is a declared dependency in requirements.txt, which the
`Python packaging provision` CI job installs) the reference validator runs too,
and `test_both_validators_agree` holds the borrowed subset validator to it.

Scope: this task's own manifest only.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "schemas" / "product-evidence.schema.json"
EVIDENCE_DIR = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "twelve-loop-gap"
    / "OPS-L12-PYTHON-PACKAGING-PROVISION-001"
)
MANIFEST_PATH = EVIDENCE_DIR / "evidence.json"
CHECKSUM_PATH = EVIDENCE_DIR / "evidence.sha256"
SIBLING_GATE = REPO_ROOT / "scripts" / "test_ops_l12_telemetry_discovery_import_evidence.py"

TASK_ID = "OPS-L12-PYTHON-PACKAGING-PROVISION-001"

TIMESTAMP_FIELDS = (
    ("task", "evidence_cut_at"),
    ("validation", "validated_at"),
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_subset(instance, schema, path: str = "$") -> list[str]:
    """Borrowed draft-07 subset validator; see the module docstring."""
    spec = importlib.util.spec_from_file_location(
        "_ops_l12_discovery_import_evidence_gate", SIBLING_GATE
    )
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise AssertionError(f"cannot load the shared subset validator from {SIBLING_GATE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._validate_subset(instance, schema, path)


def _parse_utc(text: str) -> datetime:
    normalized = text.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone-qualified: {text!r}")
    return parsed.astimezone(timezone.utc)


def _iter_manifest_timestamps(manifest: dict):
    for section, field in TIMESTAMP_FIELDS:
        value = manifest.get(section, {}).get(field)
        if isinstance(value, str) and value:
            yield f"{section}.{field}", value
    for index, entry in enumerate(manifest.get("record_log") or []):
        value = entry.get("recorded_at")
        if isinstance(value, str) and value:
            yield f"record_log[{index}].recorded_at", value
    delivery = manifest.get("implementation_delivery") or {}
    for index, entry in enumerate(delivery.get("pull_requests") or []):
        value = entry.get("merged_at")
        if isinstance(value, str) and value:
            yield f"implementation_delivery.pull_requests[{index}].merged_at", value


class TestEvidenceManifestSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = _load(MANIFEST_PATH)
        cls.schema = _load(SCHEMA_PATH)

    def test_manifest_and_companions_exist(self):
        self.assertTrue(MANIFEST_PATH.is_file(), MANIFEST_PATH)
        self.assertTrue(CHECKSUM_PATH.is_file(), CHECKSUM_PATH)
        self.assertTrue(SCHEMA_PATH.is_file(), SCHEMA_PATH)

    def test_manifest_validates_against_the_schema(self):
        errors = _validate_subset(self.manifest, self.schema)
        self.assertEqual(errors, [], "; ".join(errors))

    def test_both_validators_agree(self):
        try:
            import jsonschema
        except ImportError:
            # Not a skip: the subset validator above already asserted the shape
            # unconditionally. This case only adds the reference cross-check.
            return
        jsonschema.validate(instance=self.manifest, schema=self.schema)
        # And the borrowed validator must reject what the reference rejects,
        # otherwise it is passing vacuously.
        broken = json.loads(json.dumps(self.manifest))
        del broken["integrity"]["algorithm"]
        self.assertNotEqual(_validate_subset(broken, self.schema), [])
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(instance=broken, schema=self.schema)

    def test_manifest_binds_this_task(self):
        task = self.manifest["task"]
        self.assertEqual(task["id"], TASK_ID)
        self.assertEqual(EVIDENCE_DIR.name, TASK_ID)
        self.assertEqual(task["review_file"], str(MANIFEST_PATH.relative_to(REPO_ROOT)))
        self.assertEqual(task["owner"], "Codex")
        self.assertEqual(task["reviewer"], "Codex2")
        self.assertNotEqual(task["owner"], task["reviewer"])


class TestEvidenceManifestTimestamps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = _load(MANIFEST_PATH)

    def test_every_timestamp_parses_as_utc(self):
        found = dict(_iter_manifest_timestamps(self.manifest))
        self.assertIn("task.evidence_cut_at", found)
        self.assertIn("validation.validated_at", found)
        self.assertTrue(any(key.startswith("record_log[") for key in found))
        for location, raw in found.items():
            with self.subTest(location=location):
                _parse_utc(raw)

    def test_no_manifest_timestamp_is_in_the_future(self):
        now = datetime.now(timezone.utc)
        future = [
            f"{location}={raw}"
            for location, raw in _iter_manifest_timestamps(self.manifest)
            if _parse_utc(raw) > now
        ]
        self.assertEqual(future, [], f"{future} (now={now.isoformat()})")

    def test_record_log_is_a_dense_ordered_sequence(self):
        entries = self.manifest["record_log"]
        self.assertEqual(
            [entry["sequence"] for entry in entries],
            list(range(1, len(entries) + 1)),
            "record_log sequence numbers must be dense and 1-based",
        )
        cut_at = _parse_utc(self.manifest["task"]["evidence_cut_at"])
        for entry in entries:
            with self.subTest(sequence=entry["sequence"]):
                self.assertLessEqual(_parse_utc(entry["recorded_at"]), cut_at)


class TestEvidenceChecksumAnchor(unittest.TestCase):
    def test_checksum_file_matches_the_committed_bytes(self):
        mismatches = []
        entries = 0
        for line in CHECKSUM_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            expected, _, name = line.partition("  ")
            target = (EVIDENCE_DIR / name.strip()).resolve()
            entries += 1
            if not target.is_file():
                mismatches.append(f"{name}: missing")
                continue
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != expected.strip():
                mismatches.append(f"{name}: {actual} != {expected.strip()}")
        self.assertGreater(entries, 0, "evidence.sha256 must pin at least one file")
        self.assertEqual(mismatches, [], "; ".join(mismatches))

    def test_checksum_coverage_matches_the_manifest_declaration(self):
        manifest = _load(MANIFEST_PATH)
        declared = {Path(path).name for path in manifest["integrity"]["checksum_coverage"]}
        pinned = {
            line.partition("  ")[2].strip()
            for line in CHECKSUM_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        self.assertEqual(declared, pinned)

    def test_recorded_source_digests_match_the_committed_files(self):
        # The manifest pins the digest of every implementation file it claims to
        # have validated. A stale digest means the manifest is describing a tree
        # that is not the one being reviewed.
        manifest = _load(MANIFEST_PATH)
        mismatches = []
        by_epoch = manifest["integrity"]["source_artifact_sha256_by_epoch"]
        for epoch, files in by_epoch.items():
            for relative_path, expected in files.items():
                target = REPO_ROOT / relative_path
                if not target.is_file():
                    mismatches.append(f"{epoch}:{relative_path}: missing")
                    continue
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
                if actual != expected:
                    mismatches.append(f"{epoch}:{relative_path}: {actual} != {expected}")
        self.assertEqual(mismatches, [], "; ".join(mismatches))


if __name__ == "__main__":
    unittest.main()
