"""Tests for the restricted RFC 6902 patch engine.

Covers: allowed ops, path allowlist, hash verification, patch application,
        schema validation, and error codes.
"""
import copy
import hashlib
import json
import unittest

from .patching import (
    ALLOWED_OPS,
    ALLOWED_TOP_LEVEL_PREFIXES,
    PATCH_FORMAT_VERSION,
    PatchBaseHashMismatchError,
    PatchOperationError,
    PatchPathForbiddenError,
    PatchPolicyInvalidError,
    PatchResultSchemaInvalidError,
    apply_patch,
    apply_patch_validated,
    compute_document_sha256,
    validate_operations,
)


def _sha256(doc: dict) -> str:
    payload = json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Minimal valid StrategySpec payload fixture
# ---------------------------------------------------------------------------

def _base_spec() -> dict:
    return {
        "spec_version": "1.0",
        "strategy_id": "strat-test-v1",
        "title": "Test Strategy",
        "hypothesis": "Markets exhibit momentum.",
        "objective": "Research prototype.",
        "lifecycle_state": "draft",
        "market_scope": {
            "symbols": ["RESEARCH_UNIVERSE"],
            "frequency": "1d",
        },
        "data_dependencies": [
            {"ref": "dataset:tw-equities-v1", "kind": "dataset"},
        ],
        "execution_profile": {
            "signal_schema_version": "1.0",
            "quantity_type": "PERCENT_PORTFOLIO",
            "execution_mode_hint": "research",
        },
        "evaluation_plan": {
            "metrics": ["sharpe_ratio"],
        },
        "governance": {
            "approval_required": True,
            "policy_id": "research-v1",
        },
        "provenance": {
            "source_kind": "manual",
            "created_at": "2026-06-21T00:00:00Z",
        },
        "metadata": {},
    }


class TestComputeDocumentSha256(unittest.TestCase):
    def test_same_doc_same_hash(self):
        doc = _base_spec()
        h1 = compute_document_sha256(doc)
        h2 = compute_document_sha256(dict(doc))
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_different_doc_different_hash(self):
        doc1 = _base_spec()
        doc2 = _base_spec()
        doc2["title"] = "Different Title"
        self.assertNotEqual(compute_document_sha256(doc1), compute_document_sha256(doc2))

    def test_canonical_json_key_order_independent(self):
        doc = {"b": 2, "a": 1}
        doc_reversed = {"a": 1, "b": 2}
        self.assertEqual(compute_document_sha256(doc), compute_document_sha256(doc_reversed))


class TestValidateOperations(unittest.TestCase):
    def test_allowed_ops_pass(self):
        for op in ALLOWED_OPS:
            ops = [{"op": op, "path": "/title", "value": "x"}] if op != "remove" else [{"op": op, "path": "/title"}]
            validate_operations(ops)

    def test_forbidden_op_move(self):
        with self.assertRaises(PatchPathForbiddenError) as ctx:
            validate_operations([{"op": "move", "path": "/title", "from": "/hypothesis"}])
        self.assertEqual(ctx.exception.error_code, "PATCH_PATH_FORBIDDEN")

    def test_forbidden_op_copy(self):
        with self.assertRaises(PatchPathForbiddenError):
            validate_operations([{"op": "copy", "path": "/title", "from": "/hypothesis"}])

    def test_forbidden_path_spec_version(self):
        with self.assertRaises(PatchPathForbiddenError) as ctx:
            validate_operations([{"op": "replace", "path": "/spec_version", "value": "2.0"}])
        self.assertEqual(ctx.exception.error_code, "PATCH_PATH_FORBIDDEN")

    def test_forbidden_path_strategy_id(self):
        with self.assertRaises(PatchPathForbiddenError):
            validate_operations([{"op": "replace", "path": "/strategy_id", "value": "new-id"}])

    def test_forbidden_path_lifecycle_state(self):
        with self.assertRaises(PatchPathForbiddenError):
            validate_operations([{"op": "replace", "path": "/lifecycle_state", "value": "candidate"}])

    def test_forbidden_path_provenance(self):
        with self.assertRaises(PatchPathForbiddenError):
            validate_operations([{"op": "replace", "path": "/provenance/source_kind", "value": "repo"}])

    def test_allowed_subpath(self):
        validate_operations([{"op": "replace", "path": "/market_scope/frequency", "value": "1h"}])

    def test_add_without_value_raises(self):
        # Missing "value" key entirely should raise
        with self.assertRaises(ValueError):
            validate_operations([{"op": "add", "path": "/metadata/tag"}])
        # value=None is still provided (key present); must succeed
        validate_operations([{"op": "add", "path": "/metadata/tag", "value": None}])

    def test_remove_does_not_require_value(self):
        validate_operations([{"op": "remove", "path": "/metadata/tag"}])


class TestApplyPatch(unittest.TestCase):
    def test_replace_title(self):
        doc = _base_spec()
        result = apply_patch(doc, [{"op": "replace", "path": "/title", "value": "New Title"}])
        self.assertEqual(result["title"], "New Title")
        # original unchanged
        self.assertEqual(doc["title"], "Test Strategy")

    def test_add_metadata_key(self):
        doc = _base_spec()
        result = apply_patch(doc, [{"op": "add", "path": "/metadata/source", "value": "manual"}])
        self.assertEqual(result["metadata"]["source"], "manual")

    def test_remove_metadata_key(self):
        doc = _base_spec()
        doc["metadata"]["to_remove"] = "gone"
        result = apply_patch(doc, [{"op": "remove", "path": "/metadata/to_remove"}])
        self.assertNotIn("to_remove", result["metadata"])

    def test_test_passes(self):
        doc = _base_spec()
        result = apply_patch(
            doc,
            [
                {"op": "test", "path": "/title", "value": "Test Strategy"},
                {"op": "replace", "path": "/title", "value": "Confirmed"},
            ],
        )
        self.assertEqual(result["title"], "Confirmed")

    def test_test_fails_raises_policy_error(self):
        doc = _base_spec()
        with self.assertRaises(PatchPolicyInvalidError) as ctx:
            apply_patch(doc, [{"op": "test", "path": "/title", "value": "Wrong Title"}])
        self.assertEqual(ctx.exception.error_code, "PATCH_POLICY_INVALID")

    def test_add_to_array(self):
        doc = _base_spec()
        new_dep = {"ref": "dataset:another-v1", "kind": "dataset"}
        result = apply_patch(doc, [{"op": "add", "path": "/data_dependencies/-", "value": new_dep}])
        self.assertEqual(len(result["data_dependencies"]), 2)
        self.assertEqual(result["data_dependencies"][1]["ref"], "dataset:another-v1")

    def test_replace_array_element(self):
        doc = _base_spec()
        result = apply_patch(doc, [{"op": "replace", "path": "/data_dependencies/0/ref", "value": "dataset:updated-v2"}])
        self.assertEqual(result["data_dependencies"][0]["ref"], "dataset:updated-v2")

    def test_hash_verification_pass(self):
        doc = _base_spec()
        sha = compute_document_sha256(doc)
        result = apply_patch(
            doc,
            [{"op": "replace", "path": "/title", "value": "Verified"}],
            expected_base_sha256=sha,
        )
        self.assertEqual(result["title"], "Verified")

    def test_hash_verification_fail(self):
        doc = _base_spec()
        with self.assertRaises(PatchBaseHashMismatchError) as ctx:
            apply_patch(doc, [{"op": "replace", "path": "/title", "value": "X"}], expected_base_sha256="a" * 64)
        self.assertEqual(ctx.exception.error_code, "PATCH_BASE_HASH_MISMATCH")

    def test_immutable_base_doc(self):
        doc = _base_spec()
        original_title = doc["title"]
        apply_patch(doc, [{"op": "replace", "path": "/title", "value": "Changed"}])
        self.assertEqual(doc["title"], original_title)

    def test_forbidden_path_rejected(self):
        doc = _base_spec()
        with self.assertRaises(PatchPathForbiddenError):
            apply_patch(doc, [{"op": "replace", "path": "/strategy_id", "value": "hacked"}])

    def test_missing_key_remove_raises(self):
        doc = _base_spec()
        with self.assertRaises(PatchOperationError):
            apply_patch(doc, [{"op": "remove", "path": "/metadata/nonexistent"}])

    def test_array_index_out_of_bounds(self):
        doc = _base_spec()
        with self.assertRaises(PatchOperationError):
            apply_patch(doc, [{"op": "replace", "path": "/data_dependencies/99/ref", "value": "x"}])

    def test_multiple_operations_sequential(self):
        doc = _base_spec()
        result = apply_patch(doc, [
            {"op": "replace", "path": "/title", "value": "Step1"},
            {"op": "replace", "path": "/hypothesis", "value": "Step2"},
            {"op": "add", "path": "/metadata/tag", "value": "step3"},
        ])
        self.assertEqual(result["title"], "Step1")
        self.assertEqual(result["hypothesis"], "Step2")
        self.assertEqual(result["metadata"]["tag"], "step3")

    def test_patch_format_version_constant(self):
        self.assertEqual(PATCH_FORMAT_VERSION, "rfc6902-restricted-v1")


class TestApplyPatchValidated(unittest.TestCase):
    def test_valid_patch_returns_doc_and_sha256(self):
        doc = _base_spec()
        result_doc, sha256 = apply_patch_validated(
            doc,
            [{"op": "replace", "path": "/title", "value": "Patched Title"}],
        )
        self.assertEqual(result_doc["title"], "Patched Title")
        self.assertEqual(sha256, compute_document_sha256(result_doc))
        self.assertEqual(len(sha256), 64)

    def test_invalid_result_raises_schema_error(self):
        doc = _base_spec()
        # Remove a required field to make the result invalid
        with self.assertRaises(PatchResultSchemaInvalidError) as ctx:
            apply_patch_validated(
                doc,
                [{"op": "remove", "path": "/data_dependencies/0"}],
            )
        self.assertEqual(ctx.exception.error_code, "PATCH_RESULT_SCHEMA_INVALID")


if __name__ == "__main__":
    unittest.main()
