"""Restricted RFC 6902 JSON Patch engine for StrategySpec documents.

Implements the patch grammar defined in design-closure-round2 §A2:
  - Allowed ops: add, remove, replace, test
  - Allowed top-level paths: /title /hypothesis /objective /market_scope
    /data_dependencies /execution_profile /evaluation_plan /governance
    /evidence_refs /code_refs /metadata
  - Forbidden paths: /spec_version /strategy_id /lifecycle_state /provenance

Error codes match §A8:
  PATCH_PATH_FORBIDDEN, PATCH_BASE_HASH_MISMATCH,
  PATCH_RESULT_SCHEMA_INVALID, PATCH_POLICY_INVALID
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PATCH_FORMAT_VERSION = "rfc6902-restricted-v1"

ALLOWED_OPS: frozenset[str] = frozenset({"add", "remove", "replace", "test"})

# Mutable top-level paths (§A2 allowlist)
ALLOWED_TOP_LEVEL_PREFIXES: tuple[str, ...] = (
    "/title",
    "/hypothesis",
    "/objective",
    "/market_scope",
    "/data_dependencies",
    "/execution_profile",
    "/evaluation_plan",
    "/governance",
    "/evidence_refs",
    "/code_refs",
    "/metadata",
)

# Paths that must never appear in patch operations (§A2 forbidden)
FORBIDDEN_PATHS: frozenset[str] = frozenset({
    "/spec_version",
    "/strategy_id",
    "/lifecycle_state",
    "/provenance",
})


# ---------------------------------------------------------------------------
# Error hierarchy
# ---------------------------------------------------------------------------

class PatchError(Exception):
    error_code: str = "PATCH_ERROR"


class PatchPathForbiddenError(PatchError):
    error_code = "PATCH_PATH_FORBIDDEN"


class PatchBaseHashMismatchError(PatchError):
    error_code = "PATCH_BASE_HASH_MISMATCH"


class PatchResultSchemaInvalidError(PatchError):
    error_code = "PATCH_RESULT_SCHEMA_INVALID"


class PatchPolicyInvalidError(PatchError):
    """'test' operation failed or another policy check failed."""
    error_code = "PATCH_POLICY_INVALID"


class PatchOperationError(PatchError):
    """Operation could not be applied (missing key, wrong type, etc.)."""
    error_code = "PATCH_OPERATION_ERROR"


# ---------------------------------------------------------------------------
# SHA-256 helpers
# ---------------------------------------------------------------------------

def _canonical_json(doc: Any) -> bytes:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def compute_document_sha256(doc: Mapping[str, Any]) -> str:
    """Return the lowercase hex SHA-256 of the canonical JSON form of doc."""
    return hashlib.sha256(_canonical_json(dict(doc))).hexdigest()


# ---------------------------------------------------------------------------
# JSON Pointer helpers (RFC 6901)
# ---------------------------------------------------------------------------

def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _parse_pointer(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise PatchOperationError(f"Invalid JSON Pointer: {pointer!r}")
    return [_unescape(tok) for tok in pointer[1:].split("/")]


def _get_at(node: Any, tokens: list[str]) -> Any:
    current = node
    for tok in tokens:
        if isinstance(current, list):
            if tok == "-":
                raise PatchOperationError("Cannot read array end pointer '-'")
            try:
                idx = int(tok)
            except ValueError:
                raise PatchOperationError(f"Expected array index, got {tok!r}")
            if idx < 0 or idx >= len(current):
                raise PatchOperationError(f"Array index {idx} out of bounds (len={len(current)})")
            current = current[idx]
        elif isinstance(current, dict):
            if tok not in current:
                raise PatchOperationError(f"Key {tok!r} not found")
            current = current[tok]
        else:
            raise PatchOperationError(f"Cannot index {type(current).__name__!r} with {tok!r}")
    return current


def _parent_and_tail(doc: Any, tokens: list[str]) -> tuple[Any, str]:
    if not tokens:
        raise PatchOperationError("Cannot decompose empty token list")
    parent = _get_at(doc, tokens[:-1])
    return parent, tokens[-1]


# ---------------------------------------------------------------------------
# Primitive RFC 6902 operations
# ---------------------------------------------------------------------------

def _op_add(doc: Any, tokens: list[str], value: Any) -> Any:
    """Implements RFC 6902 'add'. Returns the (possibly new) root."""
    if not tokens:
        return copy.deepcopy(value)
    parent, tail = _parent_and_tail(doc, tokens)
    v = copy.deepcopy(value)
    if isinstance(parent, list):
        if tail == "-":
            parent.append(v)
        else:
            try:
                idx = int(tail)
            except ValueError:
                raise PatchOperationError(f"Expected array index, got {tail!r}")
            if idx < 0 or idx > len(parent):
                raise PatchOperationError(f"Array index {idx} out of bounds (len={len(parent)})")
            parent.insert(idx, v)
    elif isinstance(parent, dict):
        parent[tail] = v
    else:
        raise PatchOperationError(f"Cannot add into {type(parent).__name__!r}")
    return doc


def _op_remove(doc: Any, tokens: list[str]) -> Any:
    """Implements RFC 6902 'remove'. Target must exist."""
    if not tokens:
        raise PatchOperationError("Cannot remove root document")
    parent, tail = _parent_and_tail(doc, tokens)
    if isinstance(parent, list):
        if tail == "-":
            raise PatchOperationError("Cannot remove array end pointer '-'")
        try:
            idx = int(tail)
        except ValueError:
            raise PatchOperationError(f"Expected array index, got {tail!r}")
        if idx < 0 or idx >= len(parent):
            raise PatchOperationError(f"Array index {idx} out of bounds (len={len(parent)})")
        del parent[idx]
    elif isinstance(parent, dict):
        if tail not in parent:
            raise PatchOperationError(f"Key {tail!r} not found; cannot remove")
        del parent[tail]
    else:
        raise PatchOperationError(f"Cannot remove from {type(parent).__name__!r}")
    return doc


def _op_replace(doc: Any, tokens: list[str], value: Any) -> Any:
    """Implements RFC 6902 'replace'. Target must exist."""
    _get_at(doc, tokens)  # verify existence
    parent, tail = _parent_and_tail(doc, tokens)
    v = copy.deepcopy(value)
    if isinstance(parent, list):
        try:
            idx = int(tail)
        except ValueError:
            raise PatchOperationError(f"Expected array index, got {tail!r}")
        if idx < 0 or idx >= len(parent):
            raise PatchOperationError(f"Array index {idx} out of bounds")
        parent[idx] = v
    elif isinstance(parent, dict):
        parent[tail] = v
    else:
        raise PatchOperationError(f"Cannot replace in {type(parent).__name__!r}")
    return doc


def _op_test(doc: Any, tokens: list[str], expected: Any) -> None:
    """Implements RFC 6902 'test'. Raises PatchPolicyInvalidError on mismatch."""
    actual = _get_at(doc, tokens)
    if actual != expected:
        path_str = "/" + "/".join(tokens) if tokens else ""
        raise PatchPolicyInvalidError(
            f"test failed at {path_str!r}: expected {expected!r}, got {actual!r}"
        )


# ---------------------------------------------------------------------------
# Path allowlist validation
# ---------------------------------------------------------------------------

def _is_path_allowed(path: str) -> bool:
    if path in FORBIDDEN_PATHS:
        return False
    for prefix in ALLOWED_TOP_LEVEL_PREFIXES:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def validate_operations(operations: Sequence[Mapping[str, Any]]) -> None:
    """Validate all operations against the restricted grammar.

    Raises PatchPathForbiddenError for disallowed ops or paths.
    Raises ValueError for structurally invalid operations.
    """
    for i, op in enumerate(operations):
        op_kind = op.get("op")
        if op_kind not in ALLOWED_OPS:
            raise PatchPathForbiddenError(
                f"op[{i}]: {op_kind!r} is not allowed. Allowed: {sorted(ALLOWED_OPS)}"
            )
        path = op.get("path", "")
        if not _is_path_allowed(path):
            raise PatchPathForbiddenError(
                f"op[{i}]: path {path!r} is not in the allowed StrategySpec patch allowlist"
            )
        if op_kind in ("add", "replace", "test") and "value" not in op:
            raise ValueError(f"op[{i}]: op={op_kind!r} requires 'value'")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_patch(
    base_doc: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
    *,
    expected_base_sha256: str | None = None,
) -> dict[str, Any]:
    """Apply restricted RFC 6902 operations to a StrategySpec document.

    Args:
        base_doc: The base StrategySpec dict (read-only; a deep copy is made).
        operations: Patch operations conforming to the restricted grammar.
        expected_base_sha256: When supplied, the base doc hash must match.

    Returns:
        A new dict representing the patched document.

    Raises:
        PatchBaseHashMismatchError: Hash mismatch.
        PatchPathForbiddenError: Operation targets a forbidden or unlisted path.
        PatchPolicyInvalidError: A 'test' operation fails.
        PatchOperationError: Operation cannot be applied (missing key, bad index, etc.).
    """
    if expected_base_sha256 is not None:
        actual_sha256 = compute_document_sha256(base_doc)
        if actual_sha256 != expected_base_sha256:
            raise PatchBaseHashMismatchError(
                f"base_document_sha256 mismatch: expected {expected_base_sha256!r}, "
                f"got {actual_sha256!r}"
            )

    validate_operations(operations)

    doc: Any = copy.deepcopy(dict(base_doc))

    for i, op in enumerate(operations):
        op_kind: str = op["op"]
        path: str = op["path"]
        tokens = _parse_pointer(path)
        try:
            if op_kind == "add":
                doc = _op_add(doc, tokens, op["value"])
            elif op_kind == "remove":
                doc = _op_remove(doc, tokens)
            elif op_kind == "replace":
                doc = _op_replace(doc, tokens, op["value"])
            elif op_kind == "test":
                _op_test(doc, tokens, op["value"])
        except (PatchPolicyInvalidError, PatchPathForbiddenError, PatchBaseHashMismatchError):
            raise
        except PatchError:
            raise
        except Exception as exc:
            raise PatchOperationError(
                f"op[{i}] op={op_kind!r} path={path!r}: {exc}"
            ) from exc

    return doc  # type: ignore[return-value]


def apply_patch_validated(
    base_doc: Mapping[str, Any],
    operations: Sequence[Mapping[str, Any]],
    *,
    expected_base_sha256: str | None = None,
    schema_path: Any = None,
) -> tuple[dict[str, Any], str]:
    """Apply patch, validate the result against StrategySpec schema, return (result, new_sha256).

    Raises PatchResultSchemaInvalidError when the patched document fails schema validation.
    """
    from .models import StrategySpecValidationError, validate_strategy_spec_payload

    result = apply_patch(base_doc, operations, expected_base_sha256=expected_base_sha256)

    try:
        validate_strategy_spec_payload(result, schema_path)
    except StrategySpecValidationError as exc:
        raise PatchResultSchemaInvalidError(
            f"Patched document fails StrategySpec schema: {exc}"
        ) from exc

    return result, compute_document_sha256(result)
