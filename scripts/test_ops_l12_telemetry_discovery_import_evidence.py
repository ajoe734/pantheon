"""
OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 — fail-closed evidence manifest check.

The task's evidence manifest is the artifact bound as `review_file` at closeout,
so a defect in it is a governance defect, not a documentation nit. This module is
the fail-closed gate for the three ways that manifest went wrong during review:

1. it did not validate against `schemas/product-evidence.schema.json`;
2. it carried timestamps in the future relative to the commit that recorded them
   (`task.evidence_cut_at`, `validation.validated_at`, and `record_log[].recorded_at`
   entries stamped ahead of the actual recording time);
3. its companion `evidence.sha256` was not a live integrity anchor.

Fail-closed means no skips. Schema validation runs against `jsonschema` when it is
importable and against a local draft-07 subset validator otherwise, so a bare
interpreter without the dependency still gets the same verdict rather than a pass
by omission. The subset covers exactly the keywords the schema uses: `type`,
`required`, `properties`, `items`, and `additionalProperties` in both its boolean
and its schema form.

Scope: this task's own manifest. Other twelve-loop-gap manifests are not asserted
here — widening the target would make this task's gate fail on another lane's
artifact, which is not a defect this task owns.
"""
from __future__ import annotations

import hashlib
import json
import re
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
    / "OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001"
)
MANIFEST_PATH = EVIDENCE_DIR / "evidence.json"
CHECKSUM_PATH = EVIDENCE_DIR / "evidence.sha256"
PACKAGING_MANIFEST_PATH = (
    REPO_ROOT
    / "docs"
    / "deployment"
    / "evidence"
    / "twelve-loop-gap"
    / "OPS-L12-PYTHON-PACKAGING-PROVISION-001"
    / "evidence.json"
)

TASK_ID = "OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001"
OWNER = "Codex"
REVIEWER = "Codex2"
PACKAGING_TASK_ID = "OPS-L12-PYTHON-PACKAGING-PROVISION-001"
COMMAND_REF = re.compile(r"^validation\.commands\[(\d+)\]$")

# Every manifest location that carries a wall-clock timestamp. A future value in
# any of them means the manifest is asserting something that had not happened yet
# when its bytes were committed.
TIMESTAMP_FIELDS = (
    ("task", "evidence_cut_at"),
    ("validation", "validated_at"),
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_utc(text: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting the `Z` suffix the manifests use."""
    normalized = text.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp is not timezone-qualified: {text!r}")
    return parsed.astimezone(timezone.utc)


def _iter_manifest_timestamps(manifest: dict):
    """Yield `(location, raw_text)` for every timestamp the manifest asserts."""
    for section, field in TIMESTAMP_FIELDS:
        value = manifest.get(section, {}).get(field)
        if isinstance(value, str) and value:
            yield f"{section}.{field}", value

    for index, entry in enumerate(manifest.get("record_log") or []):
        value = entry.get("recorded_at")
        if isinstance(value, str) and value:
            yield f"record_log[{index}].recorded_at", value

    delivery = manifest.get("implementation_delivery") or {}
    pull_request = delivery.get("pull_request")
    if isinstance(pull_request, dict) and isinstance(pull_request.get("merged_at"), str):
        yield "implementation_delivery.pull_request.merged_at", pull_request["merged_at"]
    for index, entry in enumerate(delivery.get("pull_requests") or []):
        value = entry.get("merged_at")
        if isinstance(value, str) and value:
            yield f"implementation_delivery.pull_requests[{index}].merged_at", value


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_strings(nested)


def _validate_subset(instance, schema, path: str = "$") -> list[str]:
    """Draft-07 subset validator: `type`, `required`, `properties`, `items`,
    `additionalProperties`. Returns a list of human-readable violations."""
    errors: list[str] = []

    expected_type = schema.get("type")
    type_checks = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "number": (int, float),
        "integer": int,
    }
    if expected_type in type_checks:
        python_type = type_checks[expected_type]
        # bool is a subclass of int in Python; the schema means them separately.
        wrong_bool = expected_type in {"integer", "number"} and isinstance(instance, bool)
        if wrong_bool or not isinstance(instance, python_type):
            errors.append(f"{path}: expected {expected_type}, got {type(instance).__name__}")
            return errors

    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required property {key!r}")

        properties = schema.get("properties", {})
        extra_schema = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors.extend(_validate_subset(value, properties[key], f"{path}.{key}"))
            elif extra_schema is False:
                errors.append(f"{path}: property {key!r} is not allowed")
            elif isinstance(extra_schema, dict):
                errors.extend(_validate_subset(value, extra_schema, f"{path}.{key}"))

    if isinstance(instance, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(instance):
                errors.extend(_validate_subset(item, item_schema, f"{path}[{index}]"))

    return errors


class TestEvidenceManifestSchema(unittest.TestCase):
    """The manifest must validate against the formal product-evidence schema."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = _load(MANIFEST_PATH)
        cls.schema = _load(SCHEMA_PATH)

    def test_manifest_and_schema_are_present(self):
        self.assertTrue(MANIFEST_PATH.is_file(), MANIFEST_PATH)
        self.assertTrue(SCHEMA_PATH.is_file(), SCHEMA_PATH)
        self.assertTrue(CHECKSUM_PATH.is_file(), CHECKSUM_PATH)

    def test_manifest_validates_against_the_local_subset_validator(self):
        errors = _validate_subset(self.manifest, self.schema)
        self.assertEqual(errors, [], "; ".join(errors))

    def test_manifest_validates_against_jsonschema_when_available(self):
        try:
            import jsonschema
        except ImportError:
            # Not a skip: the subset validator above already asserted the same
            # shape unconditionally. This case only adds the reference check.
            return
        jsonschema.validate(instance=self.manifest, schema=self.schema)

    def test_subset_validator_rejects_the_shapes_the_review_rejected(self):
        # A validator that passes everything proves nothing. These are the actual
        # defects found in the earlier cut of this manifest.
        self.assertEqual(
            _validate_subset(self.manifest, self.schema),
            [],
            "the mutation checks below are only meaningful from a valid baseline",
        )
        undeclared_key = json.loads(json.dumps(self.manifest))
        undeclared_key["implementation_delivery"]["delivery_note"] = "extra key"
        self.assertNotEqual(_validate_subset(undeclared_key, self.schema), [])

        missing_required = json.loads(json.dumps(self.manifest))
        del missing_required["integrity"]["algorithm"]
        self.assertNotEqual(_validate_subset(missing_required, self.schema), [])

        wrong_record_log = json.loads(json.dumps(self.manifest))
        wrong_record_log["record_log"] = [{"actor": "Claude", "at": "2026-07-26T21:55:00Z"}]
        self.assertNotEqual(_validate_subset(wrong_record_log, self.schema), [])

    def test_manifest_binds_this_task(self):
        task = self.manifest["task"]
        self.assertEqual(task["id"], TASK_ID)
        self.assertEqual(task["owner"], OWNER)
        self.assertEqual(task["reviewer"], REVIEWER)
        self.assertIn(
            task["overall_admission"],
            {"ready_for_independent_review", "review_approved"},
        )
        self.assertEqual(EVIDENCE_DIR.name, TASK_ID)
        self.assertEqual(
            task["review_file"],
            str(MANIFEST_PATH.relative_to(REPO_ROOT)),
        )
        self.assertNotEqual(task["owner"], task["reviewer"])

        acceptance = {entry["id"]: entry for entry in self.manifest["acceptance"]}
        self.assertEqual(acceptance["AC2"]["status"], "pass")
        self.assertIn(PACKAGING_TASK_ID, acceptance["AC2"]["statement"])

    def test_dependency_manifest_closes_the_packaging_precondition(self):
        dependency = _load(PACKAGING_MANIFEST_PATH)
        task = dependency["task"]
        self.assertEqual(task["id"], PACKAGING_TASK_ID)
        self.assertEqual(task["overall_admission"], "review_approved")
        self.assertEqual(task["reviewer"], REVIEWER)
        acceptance = {entry["id"]: entry for entry in dependency["acceptance"]}
        self.assertEqual(acceptance["AC3"]["status"], "pass")
        self.assertIn("foreign cwd", acceptance["AC3"]["statement"])

    def test_validation_command_references_are_in_range(self):
        command_count = len(self.manifest["validation"]["commands"])
        out_of_range = []
        for value in _iter_strings(self.manifest):
            match = COMMAND_REF.fullmatch(value)
            if match and int(match.group(1)) >= command_count:
                out_of_range.append(value)
        self.assertEqual(
            sorted(set(out_of_range)),
            [],
            f"validation command references must be < {command_count}",
        )


class TestEvidenceManifestTimestamps(unittest.TestCase):
    """No manifest timestamp may claim a moment that has not happened yet."""

    @classmethod
    def setUpClass(cls):
        cls.manifest = _load(MANIFEST_PATH)

    def test_every_timestamp_parses_as_utc(self):
        found = dict(_iter_manifest_timestamps(self.manifest))
        self.assertIn("task.evidence_cut_at", found)
        self.assertIn("validation.validated_at", found)
        self.assertTrue(
            any(key.startswith("record_log[") for key in found),
            "record_log must carry recorded_at timestamps",
        )
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
        self.assertEqual(
            future,
            [],
            "manifest timestamps must record observed times, never future ones: "
            f"{future} (now={now.isoformat()})",
        )

    def test_record_log_is_a_dense_ordered_sequence(self):
        entries = self.manifest["record_log"]
        for index, entry in enumerate(entries):
            missing = sorted({"sequence", "recorded_at", "kind", "status"} - set(entry))
            self.assertEqual(missing, [], f"record_log[{index}] is missing {missing}")
        self.assertEqual(
            [entry["sequence"] for entry in entries],
            list(range(1, len(entries) + 1)),
            "record_log sequence numbers must be dense and 1-based",
        )
        cut_at = _parse_utc(self.manifest["task"]["evidence_cut_at"])
        for entry in entries:
            with self.subTest(sequence=entry["sequence"]):
                self.assertLessEqual(
                    _parse_utc(entry["recorded_at"]),
                    cut_at,
                    "a record_log entry cannot postdate the evidence cut that ships it",
                )


class TestEvidenceChecksumAnchor(unittest.TestCase):
    """`evidence.sha256` must be a live anchor, not a stale copy."""

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
        declared = {
            Path(path).name for path in manifest["integrity"]["checksum_coverage"]
        }
        pinned = {
            line.partition("  ")[2].strip()
            for line in CHECKSUM_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
        self.assertEqual(declared, pinned)


if __name__ == "__main__":
    unittest.main()
