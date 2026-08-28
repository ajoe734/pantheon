#!/usr/bin/env python3
"""Completion guardrail checker for loop-autopilot tasks.

Reads ai-status.json and reports which loop-autopilot tasks have closure
evidence gaps that would cause the 'done' transition to be rejected.

Exit codes:
  0 — all scanned tasks have sufficient evidence (or there are none)
  1 — one or more tasks have evidence gaps

Usage:
    python3 scripts/loop_done_guardrail.py [--task-id TASK_ID] [--status-file PATH]

Examples:
    # Check every loop-autopilot task in ai-status.json
    python3 scripts/loop_done_guardrail.py

    # Check a specific task
    python3 scripts/loop_done_guardrail.py --task-id LOOP-AUTO-002

    # Check against a non-default status file
    python3 scripts/loop_done_guardrail.py --status-file /path/to/ai-status.json
"""
import argparse
import hashlib
import importlib.util
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATUS_FILE = ROOT / "ai-status.json"
DEFAULT_ARCHIVE_EXCLUDED_TASK_IDS = {
    "LOOP-PROD-000",
    "LOOP-PROD-001",
    "LOOP-PROD-002",
    "LOOP-PROD-DONE-GUARDRAIL-FINAL-REVIEW-001",
    "LOOP-PROD-PLANNING-BRIEFS-001",
}

FROZEN_ARCHIVE_REPLAY_TASK_IDS = (
    "LOOP-PROD-AGORA-001",
    "LOOP-PROD-AGORA-002",
    "LOOP-PROD-ALPHA-001",
    "LOOP-PROD-AUTH-001",
    "LOOP-PROD-CAP-001",
    "LOOP-PROD-CONS-001",
    "LOOP-PROD-DEP-001",
    "LOOP-PROD-DIST-001",
    "LOOP-PROD-GAP-ADDENDUM-001",
    "LOOP-PROD-GAP-ADDENDUM-002",
    "LOOP-PROD-IMIT-001",
    "LOOP-PROD-MAI-001",
    "LOOP-PROD-OODA-001",
    "LOOP-PROD-REC-001",
    "LOOP-PROD-RUNTIME-BOOT-001",
    "LOOP-PROD-SRC-001",
    "LOOP-PROD-TEACH-001",
    "LOOP-PROD-TEL-001",
)

# Canonical non-goals that trigger loop guardrail checks.
LOOP_AUTOPILOT_NON_GOALS = {
    "No panel-only closure",
    "No seed fixture as live proof",
    "No approval gate bypass",
}

# Case-insensitive substrings that indicate a panel/fixture/route-only claim.
_FIXTURE_ONLY_SIGNALS = (
    "fixture only",
    "fixture-only",
    "fixture_only",
    "seed only",
    "seed-only",
    "seed_only",
    "seed fixture as proof",
    "seed fixture as live",
    "fixture as live proof",
    "panel only",
    "panel-only",
    "panel_only",
    "panel copy",
    "route only",
    "route-only",
    "route_only",
)

_BLOCKING_ADMISSION_SIGNALS = (
    "blocked",
    "pending",
    "review_required",
    "review required",
    "review-required",
    "change_requested",
    "changes_requested",
    "change-requested",
    "changes-requested",
)

_ACCEPTED_ADMISSION_PREFIXES = (
    "pass",
    "approved",
    "accepted",
    "review_approved",
    "complete",
    "completed",
    "done",
)

_APPROVED_REVIEW_STATUSES = {"approved", "pass", "review_approved"}

_NON_VERDICT_REVIEW_KIND_SIGNALS = (
    "ready_for_review",
    "ready_for_independent_review",
    "evidence_ready",
    "owner_evidence_ready",
    "owner_closeout_gate",
)

_PRODUCT_CLOSEOUT_CATALOG = (
    ROOT
    / "docs"
    / "bff"
    / "execution-tasks"
    / "2026-07-26-twelve-loop-gap"
    / "tasks.json"
)
_PRODUCT_CLOSEOUT_VERDICT_ID_ENV = "PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID"
_PRODUCT_CLOSEOUT_FINAL_TASK_ID = "L12-CLOSE-001"


def _load_product_closeout_module() -> Any:
    module_name = "_pantheon_loop_guard_product_closeout_verdict"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    module_path = (
        ROOT
        / "services"
        / "control-plane"
        / "governance"
        / "product_closeout_verdict.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"cannot load protected closeout verdict module: {module_path}"
        )
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _status_root() -> Path:
    raw = str(os.environ.get("PANTHEON_STATUS_ROOT") or "").strip()
    return Path(raw).expanduser().resolve() if raw else ROOT


def _closeout_relative_artifact(relative_path: str) -> Path:
    relative = Path(relative_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(
            "closeout review_file must be a repository-relative path "
            "without parent traversal"
        )
    return relative


def _safe_rooted_artifact(root: Path, relative: Path) -> Path | None:
    """Resolve ``relative`` under ``root`` or return ``None`` when absent.

    A candidate that exists but breaks containment or crosses a symlink is a
    hard failure: the caller must not silently fall through to another trusted
    root once an untrustworthy artifact has been observed.
    """

    root = root.absolute()
    candidate = (root / relative).absolute()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(
            f"closeout review_file escapes its trusted root: {root}"
        ) from exc
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise RuntimeError(
                f"closeout review_file cannot contain a symlink: {cursor}"
            )
        if not cursor.exists():
            return None
    if not candidate.is_file():
        return None
    return candidate


def _protected_closeout_roots() -> tuple[list[Path], str | None]:
    """Return the ordered trusted roots that may hold a protected manifest.

    A governed reviewer command executes from the immutable command root while
    the manifest it is approving is authored in the supervisor-bound task
    worktree, and only reaches the central status root once the delivery PR has
    merged.  Searching the status root alone therefore fails a valid signed
    verdict in exactly the split-root dispatch it is meant to guard.  The order
    is the bound task worktree (it holds the artifact under review), then the
    command root, then the status root; the latter two are merged replay/audit
    copies.  Search order is not a trust decision: the manifest bytes are
    sha256-bound into the Human/Ops signature, so a substituted copy in any
    root fails the binding check instead of being accepted.
    """

    workspace_roots, error = _review_workspace_roots()
    if error:
        return [], error

    roots: list[Path] = []
    for root in [*workspace_roots, _status_root()]:
        resolved = root.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots, None


def _safe_protected_closeout_artifact(relative_path: str) -> Path:
    """Resolve a protected closeout manifest across validated roots."""

    relative = _closeout_relative_artifact(relative_path)
    roots, root_error = _protected_closeout_roots()
    if root_error:
        raise RuntimeError(f"closeout review_file root binding is invalid: {root_error}")

    for root in roots:
        resolved = _safe_rooted_artifact(root, relative)
        if resolved is not None:
            return resolved

    searched = ", ".join(str(root) for root in roots)
    raise RuntimeError(
        f"closeout review_file is missing or not regular: {relative_path} "
        f"(searched: {searched})"
    )


def _protected_forbidden_roots() -> tuple[Path, ...]:
    """Derive the candidate-controlled roots that must not hold verifier state.

    The verifier policy and its ledger are the whole authority behind a
    protected verdict, so they have to live outside anything a candidate can
    write.  In a split-root dispatch that is three roots, not one: the
    supervisor-bound task worktree (the worker's own checkout), the immutable
    command root, and the central status root.  Omitting the bound worktree
    leaves the ledger writable by the same lane the verdict is gating, which
    lets a candidate truncate the JSONL tail back to a signed issue record and
    resurrect a revoked or already consumed approval.

    These roots come from the same validated bindings used to resolve the
    manifest, so the authority boundary cannot drift away from the search path.
    A relative or conflicting binding raises instead of degrading to a narrower
    boundary, and the bound worktree is taken from the supervisor's lease
    rather than the candidate's environment so it cannot be erased by unsetting
    both workspace variables.
    """

    bound_roots, error = _bound_workspace_roots()
    if error:
        raise RuntimeError(f"protected closeout root binding is invalid: {error}")

    forbidden: list[Path] = []
    for root in [*bound_roots, ROOT, _status_root()]:
        # Keep both the literal and the symlink-resolved form: containment is
        # checked without resolving the protected path, so a root reachable
        # under either spelling must be forbidden under both.
        for form in (root.absolute(), root.resolve()):
            if form not in forbidden:
                forbidden.append(form)
    return tuple(forbidden)


def _protected_policy_path(
    module: Any,
    explicit_policy_path: Path | str | None = None,
) -> Path:
    """Resolve verifier policy without accepting caller-controlled env overrides."""

    if explicit_policy_path is not None:
        return Path(explicit_policy_path)
    return Path(module.DEFAULT_PROTECTED_POLICY_PATH)


def _load_product_closeout_binding(task: dict[str, Any], module: Any) -> Any:
    if not _PRODUCT_CLOSEOUT_CATALOG.is_file():
        raise RuntimeError(
            f"protected closeout catalog is missing: {_PRODUCT_CLOSEOUT_CATALOG}"
        )
    try:
        catalog = json.loads(_PRODUCT_CLOSEOUT_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot read protected closeout catalog") from exc
    if not isinstance(catalog, dict):
        raise RuntimeError("protected closeout catalog root must be an object")
    authority = catalog.get("completion_authority")
    if not isinstance(authority, dict):
        raise RuntimeError("catalog completion_authority is missing")
    expected_fields = list(module.VERDICT_BINDING_FIELDS)
    if authority.get("verdict_binding_fields") != expected_fields:
        raise RuntimeError(
            "catalog verdict binding fields do not match the installed verifier"
        )
    if authority.get("requires_protected_human_ops_verdict") is not True:
        raise RuntimeError("catalog does not require protected Human/Ops verdicts")

    task_id = str(task.get("id") or "").strip()
    protected_task_ids = authority.get("required_human_ops_signoff_task_ids")
    if not isinstance(protected_task_ids, list) or task_id not in protected_task_ids:
        raise RuntimeError(
            f"task {task_id or '?'} is not a catalog-authorized Human/Ops sink"
        )
    catalog_task = next(
        (
            item
            for item in catalog.get("tasks") or []
            if isinstance(item, dict) and item.get("id") == task_id
        ),
        None,
    )
    if (
        not isinstance(catalog_task, dict)
        or catalog_task.get("requires_human_ops_signoff") is not True
    ):
        raise RuntimeError(
            f"catalog task {task_id or '?'} does not require Human/Ops signoff"
        )

    review_file = str(task.get("review_file") or "").strip()
    if not review_file:
        raise RuntimeError(
            "protected closeout requires a review_file evidence manifest"
        )
    if Path(review_file).is_absolute():
        raise RuntimeError("protected closeout review_file must be repository-relative")
    manifest_path = _safe_protected_closeout_artifact(review_file)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("cannot read protected closeout evidence manifest") from exc
    if not isinstance(manifest, dict):
        raise RuntimeError("protected closeout evidence manifest must be an object")
    deployment = manifest.get("deployment")
    if not isinstance(deployment, dict):
        raise RuntimeError("closeout manifest deployment identity is missing")
    identity = deployment.get("identity_admission")
    if not isinstance(identity, dict):
        raise RuntimeError("closeout manifest deployment.identity_admission is missing")
    target_environment = str(
        identity.get("target_environment") or deployment.get("environment") or ""
    ).strip()
    deployment_environment = str(deployment.get("environment") or "").strip()
    if (
        identity.get("target_environment")
        and deployment_environment
        and target_environment != deployment_environment
    ):
        raise RuntimeError("closeout manifest target environment is internally inconsistent")

    return module.CloseoutBinding(
        program_id=str(catalog.get("program_id") or ""),
        catalog_sha256=module.canonical_json_sha256(catalog),
        task_id=task_id,
        closeout_manifest_sha256=_sha256_file(manifest_path),
        target_environment=target_environment,
        frontend_sha=str(identity.get("frontend_sha") or ""),
        bff_sha=str(identity.get("bff_sha") or ""),
    )


def requires_protected_closeout_verdict(task: dict[str, Any]) -> bool:
    """Return canonical protection truth; mutable task metadata cannot disable it."""

    task_id = str(task.get("id") or "").strip()
    if task.get("requires_human_ops_signoff") is True:
        return True
    if task_id == _PRODUCT_CLOSEOUT_FINAL_TASK_ID:
        return True
    try:
        catalog = json.loads(_PRODUCT_CLOSEOUT_CATALOG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(catalog, dict):
        return False
    authority = catalog.get("completion_authority")
    if not isinstance(authority, dict):
        return False
    protected_ids = authority.get("required_human_ops_signoff_task_ids")
    return isinstance(protected_ids, list) and task_id in protected_ids


def validate_protected_closeout_transition(
    task: dict[str, Any],
    *,
    transition: str,
    consume: bool = False,
    transition_actor: str = "",
    policy_path: Path | str | None = None,
) -> dict[str, Any] | None:
    """Verify the protected verdict for a guarded task transition.

    ``review_approved`` and the pre-commit ``done`` check require an
    unconsumed verdict.  A completed ``done`` record must have exactly one
    matching consumption.  Only ``done`` may atomically append that
    consumption.
    """

    if not requires_protected_closeout_verdict(task):
        return None
    if transition not in {"review_approved", "done"}:
        raise RuntimeError(f"unsupported protected closeout transition: {transition}")
    if consume and transition != "done":
        raise RuntimeError("protected verdict consumption is only valid for done")

    module = _load_product_closeout_module()
    # Resolve the authority boundary before any manifest, policy, or ledger
    # access so an untrustworthy workspace binding fails closed rather than
    # silently narrowing what counts as candidate-controlled.
    forbidden_roots = _protected_forbidden_roots()
    binding = _load_product_closeout_binding(task, module)
    task_ref = task.get("protected_closeout_verdict")
    if task_ref is None:
        task_ref = {}
    if not isinstance(task_ref, dict):
        raise RuntimeError("protected_closeout_verdict task reference must be an object")
    task_verdict_id = str(task_ref.get("verdict_id") or "").strip()
    environment_verdict_id = str(
        os.environ.get(_PRODUCT_CLOSEOUT_VERDICT_ID_ENV) or ""
    ).strip()
    verdict_id = environment_verdict_id or task_verdict_id
    if not verdict_id:
        raise RuntimeError(
            f"protected closeout requires {_PRODUCT_CLOSEOUT_VERDICT_ID_ENV} "
            "or a prior governed verdict reference"
        )

    service = module.load_verifier_service(
        policy_path=_protected_policy_path(module, policy_path),
        forbidden_roots=forbidden_roots,
    )
    consumption_idempotency_key = module.canonical_json_sha256(
        {
            "schema_version": 1,
            "verdict_id": verdict_id,
            "binding": binding.to_dict(),
            "transition": "done",
        }
    )
    consumption_state = (
        "consumed"
        if consume
        or (transition == "done" and task.get("status") == "done")
        else "unconsumed"
    )
    if consume:
        consumption = service.consume(
            verdict_id,
            expected_binding=binding,
            transition="done",
            transition_actor=transition_actor,
            idempotency_key=consumption_idempotency_key,
        )
        verdict = service.verify(
            verdict_id,
            expected_binding=binding,
            consumption_state="consumed",
        )
    else:
        consumption = None
        verdict = service.verify(
            verdict_id,
            expected_binding=binding,
            consumption_state=consumption_state,
        )

    reference_matches_verdict = (
        not task_verdict_id or task_verdict_id == verdict_id
    )
    if reference_matches_verdict:
        if task_ref.get("ledger_entry_id") and (
            task_ref.get("ledger_entry_id") != verdict.get("ledger_entry_id")
        ):
            raise RuntimeError("protected verdict ledger entry reference mismatch")
        if task_ref.get("verifier_capability_sha256") and (
            task_ref.get("verifier_capability_sha256")
            != verdict.get("verifier_capability_sha256")
        ):
            raise RuntimeError("protected verifier capability reference mismatch")

    actual_consumption = consumption
    if consumption_state == "consumed" and actual_consumption is None:
        actual_consumption = service.consumption_record(verdict_id)
    if consumption_state == "unconsumed" and (
        task_ref.get("consumption_record_id")
        or task_ref.get("consumption_record_hash")
        or task_ref.get("consumption_idempotency_key")
    ):
        raise RuntimeError(
            "unconsumed protected verdict cannot carry consumption references"
        )
    if actual_consumption is not None and reference_matches_verdict:
        expected_consumption_refs = {
            "consumption_record_id": actual_consumption["record_id"],
            "consumption_record_hash": actual_consumption["record_hash"],
            "consumption_idempotency_key": actual_consumption["idempotency_key"],
        }
        for field, expected_value in expected_consumption_refs.items():
            if task_ref.get(field) and task_ref[field] != expected_value:
                raise RuntimeError(
                    f"protected verdict {field} reference mismatch"
                )
    result = {
        "verdict_id": verdict["verdict_id"],
        "ledger_entry_id": verdict["ledger_entry_id"],
        "verifier_capability_sha256": verdict["verifier_capability_sha256"],
        "policy_version": verdict["policy_version"],
        "key_id": verdict["key_id"],
        "decision": verdict["decision"],
    }
    if task_verdict_id and task_verdict_id != verdict_id:
        result["superseded_verdict_id"] = task_verdict_id
    if actual_consumption is not None:
        result.update(
            {
                "consumption_record_id": actual_consumption["record_id"],
                "consumption_record_hash": actual_consumption["record_hash"],
                "consumption_idempotency_key": actual_consumption[
                    "idempotency_key"
                ],
            }
        )
    return result


def _canonical_workspace_roots() -> tuple[list[Path], str | None]:
    """Return the worktree roots the supervisor leased to this run.

    The environment bindings below are candidate-controlled.  A worker holding
    a valid ``ORCH_RUN_ID`` could unset both of them and thereby erase its own
    worktree from the authority boundary, which is enough to make a ledger it
    can write count as trusted.  Central runtime state is written by the
    supervisor outside every task worktree, so the leased path recorded there
    survives that erasure and is the authoritative source.

    An active run whose lease cannot be resolved is an error, not an absent
    binding: degrading to a narrower boundary is exactly the bypass.
    """

    if not str(os.environ.get("ORCH_RUN_ID") or "").strip():
        return [], None
    try:
        import ai_status

        roots = [Path(root).resolve() for root in ai_status.active_lease_workspace_roots()]
    except Exception as exc:  # noqa: BLE001 - any failure must fail closed
        return [], (
            "cannot resolve the supervisor-leased workspace root: "
            f"{type(exc).__name__}: {exc}"
        )
    return roots, None


def _bound_workspace_roots() -> tuple[list[Path], str | None]:
    """Return every worker workspace root bound to this run.

    Both environment bindings name the same task worktree.  A relative or
    conflicting binding is not an ambiguity to resolve by preference order: it
    means the caller's view of its own workspace is untrustworthy, so every
    consumer must fail closed rather than guess which root is authoritative.

    The supervisor-leased root is merged in on top of the environment so that
    stripping the environment cannot shrink the set.  The union is the safe
    direction for both consumers: a wider forbidden set only rejects more, and
    a wider manifest search path is still sha256-bound to the signed verdict.
    """

    bound_roots: list[Path] = []
    for env_name in ("PANTHEON_WORKTREE_ROOT", "ORCH_WORKSPACE_PATH"):
        raw = str(os.environ.get(env_name) or "").strip()
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            return [], f"{env_name} must be an absolute path"
        resolved = path.resolve()
        if resolved not in bound_roots:
            bound_roots.append(resolved)

    if len(bound_roots) > 1:
        return [], "conflicting PANTHEON_WORKTREE_ROOT and ORCH_WORKSPACE_PATH"

    canonical_roots, canonical_error = _canonical_workspace_roots()
    if canonical_error:
        return [], canonical_error
    for root in canonical_roots:
        if root not in bound_roots:
            bound_roots.append(root)
    return bound_roots, None


def _review_workspace_roots() -> tuple[list[Path], str | None]:
    """Return trusted roots that may contain a repo-relative review manifest.

    Status commands execute from the immutable command root while delivery
    evidence is authored in a supervisor-bound task worktree.  The worker
    runner already validates both workspace environment variables against its
    lease metadata; this helper additionally rejects conflicting bindings and
    keeps the command root as the replay/audit fallback.
    """

    bound_roots, error = _bound_workspace_roots()
    if error:
        return [], error

    roots = list(bound_roots)
    command_root = ROOT.resolve()
    if command_root not in roots:
        roots.append(command_root)
    return roots, None


def _resolve_review_file(review_file_path: str) -> tuple[Path | None, str | None]:
    """Resolve a portable repo-relative review manifest without path escape."""

    relative = Path(review_file_path)
    if relative.is_absolute():
        return None, (
            "product-level closeout review_file must be repo-relative and "
            f"portable, got absolute path: {review_file_path}"
        )
    if not review_file_path or ".." in relative.parts:
        return None, f"review_file path escapes repository scope: {review_file_path}"

    roots, root_error = _review_workspace_roots()
    if root_error:
        return None, root_error

    checked: list[Path] = []
    for root in roots:
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            return None, f"review_file path escapes repository scope: {review_file_path}"
        checked.append(candidate)
        if candidate.is_file():
            return candidate, None

    searched = ", ".join(str(path) for path in checked)
    return None, f"review_file does not exist: {review_file_path} (searched: {searched})"


def _as_lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_blocking_residual(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "blocking"}
    return False


def _blocking_residual_risk_ids(residual_risks: Any) -> list[str]:
    if not isinstance(residual_risks, dict):
        return []

    blocking: list[str] = []
    for risk_id, risk in residual_risks.items():
        if isinstance(risk, dict) and _is_blocking_residual(
            risk.get("blocking_for_this_task")
        ):
            blocking.append(str(risk_id))
    return blocking


def _has_positive_overall_admission(overall_admission: str) -> bool:
    normalized = overall_admission.strip().lower()
    return any(normalized.startswith(prefix) for prefix in _ACCEPTED_ADMISSION_PREFIXES)


def _is_formal_reviewer_verdict(
    log_item: Any,
    *,
    expected_reviewer: str,
    owner: str,
) -> bool:
    if not isinstance(log_item, dict):
        return False

    kind = _as_lower(log_item.get("kind"))
    status = _as_lower(log_item.get("status"))
    actor = str(log_item.get("actor") or "").strip()

    if status not in _APPROVED_REVIEW_STATUSES:
        return False
    if any(signal in kind for signal in _NON_VERDICT_REVIEW_KIND_SIGNALS):
        return False

    verdict_like = (
        "verdict" in kind
        or "approval" in kind
        or "approved" in kind
        or kind == "review"
        or kind == "implementation_review"
        or kind.startswith("independent_review")
        or kind.startswith("review_")
        or kind.endswith("_review")
    )
    if not verdict_like:
        return False

    if expected_reviewer:
        return actor == expected_reviewer
    if owner and actor:
        return actor != owner
    return bool(actor)


def is_loop_autopilot_task(task: dict[str, Any]) -> bool:
    """Return True when the task carries loop-autopilot guardrail requirements."""
    if task.get("loop_ids"):
        return True
    non_goals: list[str] = task.get("non_goals") or []
    return bool(set(non_goals) & LOOP_AUTOPILOT_NON_GOALS)


def check_task(task: dict[str, Any]) -> list[str]:
    """Return a list of evidence gap descriptions for the task.

    An empty list means the task passes all guardrail checks.
    """
    gaps: list[str] = []
    if requires_protected_closeout_verdict(task):
        try:
            validate_protected_closeout_transition(
                task,
                transition=(
                    "done" if task.get("status") == "done" else "review_approved"
                ),
            )
        except Exception as exc:
            gaps.append(
                "protected Human/Ops closeout verdict rejected: "
                f"{type(exc).__name__}: {exc}"
            )

    # Protection truth comes from the canonical sink/catalog and must still be
    # audited if mutable task metadata such as loop_ids or non_goals is removed.
    if not is_loop_autopilot_task(task):
        return gaps

    non_goals: set[str] = set(task.get("non_goals") or [])
    proof_required: list[str] = task.get("proof_required") or []
    review_file_path = str(task.get("review_file") or "").strip()
    product_level_required = bool(task.get("product_level_required"))

    # Gap 1: panel-only closure prohibited but no review_file.
    if "No panel-only closure" in non_goals and not review_file_path:
        gaps.append(
            "non_goal 'No panel-only closure' requires a review_file with controller "
            "liveness evidence (freeze REVIEW_FILE=<evidence-path> during PR handoff; "
            "artifact-only tasks may bind it during approve)"
        )

    # Gap 2: fixture/seed signals in review notes.
    if "No seed fixture as live proof" in non_goals:
        review_notes: list[str] = task.get("review_notes_zh") or []
        if isinstance(review_notes, str):
            review_notes = [review_notes]
        combined = " ".join(str(n) for n in review_notes).lower()
        flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined), None)
        if flagged:
            gaps.append(
                f"review notes contain '{flagged}' which violates "
                "'No seed fixture as live proof'"
            )

    # Gap 3: proof_required listed but no review_file to link evidence.
    if proof_required and not review_file_path:
        sample = ", ".join(f'"{p}"' for p in proof_required[:2])
        suffix = " ..." if len(proof_required) > 2 else ""
        gaps.append(
            f"proof_required ({sample}{suffix}) but no review_file was recorded — "
            "reopen and freeze REVIEW_FILE=<evidence-path> during PR handoff "
            "(artifact-only tasks may bind it during approve)"
        )

    if product_level_required and not review_file_path:
        gaps.append("product-level closeout requires a review_file evidence manifest")
    elif product_level_required and not review_file_path.endswith("evidence.json"):
        gaps.append(
            "product-level closeout review_file must be an evidence.json manifest: "
            f"{review_file_path}"
        )

    # Deep product evidence manifest checks if review_file is provided and is evidence.json.
    if review_file_path and review_file_path.endswith("evidence.json"):
        evidence_file, resolve_error = _resolve_review_file(review_file_path)
        if resolve_error:
            gaps.append(resolve_error)
            return gaps
        assert evidence_file is not None

        try:
            with open(evidence_file, encoding="utf-8") as fh:
                evidence_data = json.load(fh)
        except Exception as e:
            gaps.append(f"failed to parse review_file JSON: {e}")
            return gaps

        # 1. JSON Schema validation
        schema_path = ROOT / "schemas/product-evidence.schema.json"
        if schema_path.exists():
            try:
                import jsonschema
            except ImportError:
                gaps.append("jsonschema library is not installed on this host (ImportError)")
                return gaps

            try:
                with open(schema_path, encoding="utf-8") as sfh:
                    schema_data = json.load(sfh)
                jsonschema.validate(instance=evidence_data, schema=schema_data)
            except Exception as e:
                message = getattr(e, "message", str(e).splitlines()[0])
                gaps.append(f"product evidence schema validation failed: {message}")
        else:
            gaps.append("missing product evidence schema file at schemas/product-evidence.schema.json")

        # 2. Task consistency checks
        ev_task = evidence_data.get("task", {})
        if ev_task.get("id") != task.get("id"):
            gaps.append(
                f"evidence manifest task ID mismatch: expected {task.get('id')}, "
                f"got {ev_task.get('id')}"
            )
        if task.get("owner") and ev_task.get("owner") != task.get("owner"):
            gaps.append(
                f"evidence manifest owner mismatch: expected {task.get('owner')}, "
                f"got {ev_task.get('owner')}"
            )
        if task.get("reviewer") and ev_task.get("reviewer") != task.get("reviewer"):
            gaps.append(
                f"evidence manifest reviewer mismatch: expected {task.get('reviewer')}, "
                f"got {ev_task.get('reviewer')}"
            )
        if ev_task.get("review_file") != review_file_path:
            gaps.append(
                "evidence manifest review_file path mismatch: "
                f"expected {review_file_path}, got {ev_task.get('review_file')}"
            )

        # 3. Overall admission and acceptance checks
        overall_adm = str(ev_task.get("overall_admission") or "").lower()
        if not overall_adm:
            gaps.append("missing evidence overall admission")
        elif (
            any(signal in overall_adm for signal in _BLOCKING_ADMISSION_SIGNALS)
            or "reject" in overall_adm
            or "fail" in overall_adm
        ):
            gaps.append(
                "evidence overall admission is not done-eligible: "
                f"{ev_task.get('overall_admission')}"
            )
        elif not _has_positive_overall_admission(overall_adm):
            gaps.append(
                "evidence overall admission is not an accepted closeout state: "
                f"{ev_task.get('overall_admission')}"
            )

        target_maturity = str(ev_task.get("target_maturity") or "").lower()
        if target_maturity == "live" and "evidence_only" in overall_adm:
            gaps.append(
                "unsupported maturity: 'live' target maturity cannot be accepted "
                "with 'evidence_only' admission constraint"
            )

        acceptance_items = evidence_data.get("acceptance") or []
        for ac in acceptance_items:
            ac_id = ac.get("id", "?")
            ac_status = str(ac.get("status") or "").lower()
            if ac_status not in ("pass", "not_applicable"):
                gaps.append(
                    f"blocking acceptance requirement ID '{ac_id}': "
                    f"status is '{ac.get('status')}'"
                )

        blocking_risk_ids = _blocking_residual_risk_ids(evidence_data.get("residual_risks"))
        for risk_id in blocking_risk_ids:
            gaps.append(f"blocking residual risk '{risk_id}' remains open")

        # 4. Mock-only live claim check
        if target_maturity == "live":
            bp = evidence_data.get("behavioral_proof") or {}
            bp_texts = []
            for proof_item in bp.values():
                if isinstance(proof_item, dict) and "proof" in proof_item:
                    bp_texts.extend(proof_item["proof"])

            combined_bp = " ".join(str(t) for t in bp_texts).lower()
            combined_notes = " ".join(str(n) for n in (task.get("review_notes_zh") or [])).lower()

            bp_flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined_bp), None)
            notes_flagged = next((sig for sig in _FIXTURE_ONLY_SIGNALS if sig in combined_notes), None)

            if bp_flagged or notes_flagged:
                gaps.append(
                    "mock-only live claim detected "
                    f"(flagged: '{bp_flagged or notes_flagged}') "
                    "violating live maturity target"
                )

        # 5. Missing core properties checks
        # 5.1 Terminal readback check
        hosted_rb = evidence_data.get("hosted_readback") or {}
        ct_rb = hosted_rb.get("capture_time_hosted_readback")
        pre_dep = hosted_rb.get("pre_deploy")

        if not ct_rb and not pre_dep:
            gaps.append("missing terminal readback evidence in hosted_readback")
        else:
            for rb_sec in (ct_rb, pre_dep):
                if rb_sec and isinstance(rb_sec, dict):
                    for k, v in rb_sec.items():
                        if k.endswith("_http") and isinstance(v, int) and v >= 500:
                            gaps.append(f"terminal readback failure observed: {k} returned status {v}")

        # 5.2 Restart check
        bp = evidence_data.get("behavioral_proof") or {}
        restart_rec = bp.get("restart_and_recovery") or {}
        restart_proof = restart_rec.get("proof") or []
        if not restart_proof:
            gaps.append("missing restart and recovery proof in behavioral_proof")
        else:
            combined_restart = " ".join(str(p) for p in restart_proof).lower()
            if "mock" in combined_restart or "stub" in combined_restart:
                gaps.append(
                    "restart proof contains mock or stub claims violating restart guardrail"
                )

        # 5.3 Hosted check for frontend tasks
        is_frontend = (
            task.get("repository") == "execute-plans" or
            any(
                "execute-plans/" in str(f) or str(f).startswith("src/")
                for f in (task.get("artifacts") or [])
            ) or
            any(
                "execute-plans/" in str(f) or str(f).startswith("src/")
                for f in (
                    evidence_data.get("scope", {}).get("implementation_changed_files")
                    or []
                )
            )
        )
        if is_frontend:
            sec_safety = evidence_data.get("security_and_safety") or {}
            hosted_fe = sec_safety.get("hosted_frontend") or {}
            fe_status = str(hosted_fe.get("status") or "").lower()
            if fe_status == "not_applicable":
                gaps.append(
                    "missing hosted evidence: frontend tasks require explicit "
                    "hosted desktop/mobile proof"
                )

        # 5.4 Security checks
        sec_safety = evidence_data.get("security_and_safety") or {}
        for sec_req in (
            "rbac",
            "tenant_isolation",
            "mfa",
            "no_live_capital",
            "two_person_approval",
        ):
            req_sec = sec_safety.get(sec_req) or {}
            req_status = str(req_sec.get("status") or "").lower()
            if req_status not in ("pass", "not_applicable"):
                gaps.append(
                    f"missing security evidence: {sec_req} "
                    "status is not pass/not_applicable"
                )

        # 5.5 Reviewer checks
        if not task.get("reviewer"):
            gaps.append("missing reviewer: task must have a reviewer assigned")
        elif task.get("reviewer") == task.get("owner"):
            gaps.append("invalid reviewer: reviewer cannot be the owner")

        record_log = evidence_data.get("record_log") or []
        reviewer_approved = any(
            _is_formal_reviewer_verdict(
                log_item,
                expected_reviewer=str(
                    ev_task.get("reviewer") or task.get("reviewer") or ""
                ).strip(),
                owner=str(ev_task.get("owner") or task.get("owner") or "").strip(),
            )
            for log_item in record_log
        )
        if not reviewer_approved:
            gaps.append(
                "missing reviewer verdict: no approved formal reviewer verdict "
                "recorded in record_log"
            )

        # 6. Phantom cross-repo delivery & merge-target ancestry
        impl_delivery = evidence_data.get("implementation_delivery") or {}
        pr = impl_delivery.get("pull_request") or {}
        prs = impl_delivery.get("pull_requests") or []

        has_pr = bool(pr.get("number") or any(item.get("number") for item in prs))
        if task.get("repository") == "execute-plans" and not has_pr:
            gaps.append("phantom cross-repo delivery rejected: missing PR records in implementation_delivery")

        # Ancestry check
        merge_sha = pr.get("merge_sha") or (prs[0].get("merge_sha") if prs else None)
        if merge_sha:
            if task.get("repository") == "pantheon" or task.get("repository") == "ajoe734/pantheon":
                try:
                    import subprocess
                    res = subprocess.run(
                        ["git", "merge-base", "--is-ancestor", merge_sha, "HEAD"],
                        cwd=str(ROOT),
                        capture_output=True
                    )
                    if res.returncode != 0:
                        gaps.append(
                            "merge-target ancestry validation failed: "
                            f"{merge_sha} is not an ancestor of HEAD"
                        )
                except Exception:
                    pass

    return gaps


def _task_from_evidence_manifest(
    manifest_path: Path,
    evidence_data: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    ev_task = evidence_data.get("task") if isinstance(evidence_data.get("task"), dict) else {}
    task_id = str(ev_task.get("id") or "").strip()
    if not task_id:
        return None, "manifest task.id is missing"

    review_file = _display_path(manifest_path)
    task = {
        "id": task_id,
        "status": "done",
        "loop_ids": ["loop_product_level_replay"],
        "owner": ev_task.get("owner"),
        "reviewer": ev_task.get("reviewer"),
        "repository": ev_task.get("repository"),
        "review_file": review_file,
        "artifacts": (
            evidence_data.get("scope", {}).get("implementation_changed_files")
            if isinstance(evidence_data.get("scope"), dict)
            else []
        ),
    }
    return task, None


def audit_evidence_root(evidence_root: Path) -> dict[str, Any]:
    manifest_paths = sorted(evidence_root.rglob("evidence.json"))
    results: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []

    for manifest_path in manifest_paths:
        display_path = _display_path(manifest_path)
        try:
            with open(manifest_path, encoding="utf-8") as fh:
                evidence_data = json.load(fh)
        except Exception as exc:
            excluded.append({"manifest": display_path, "reason": f"failed to parse JSON: {exc}"})
            continue

        task, excluded_reason = _task_from_evidence_manifest(manifest_path, evidence_data)
        if task is None:
            excluded.append(
                {
                    "manifest": display_path,
                    "reason": excluded_reason or "not a task evidence manifest",
                }
            )
            continue

        gaps = check_task(task)
        ev_task = evidence_data.get("task") if isinstance(evidence_data.get("task"), dict) else {}
        results.append(
            {
                "task_id": task["id"],
                "manifest": display_path,
                "owner": ev_task.get("owner"),
                "reviewer": ev_task.get("reviewer"),
                "overall_admission": ev_task.get("overall_admission"),
                "result": "pass" if not gaps else "fail",
                "gap_count": len(gaps),
                "gaps": gaps,
            }
        )

    failed = [result for result in results if result["result"] == "fail"]
    return {
        "audit_id": "closeout-truth-audit-2026-07-16",
        "generated_at": (
            datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "mode": "read_only_evidence_replay",
        "source_root": _display_path(evidence_root),
        "selection": {
            "included_manifests": len(results),
            "excluded_manifests": excluded,
            "archive_mutation": "none",
        },
        "summary": {
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "scanned": len(results),
        },
        "results": results,
    }


def _task_from_archive_snapshot(
    snapshot_path: Path,
    snapshot_data: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    task = snapshot_data.get("task")
    if not isinstance(task, dict):
        return None, "archive snapshot task object is missing"
    task_id = str(task.get("id") or "").strip()
    if not task_id:
        return None, "archive snapshot task.id is missing"

    replay_task = dict(task)
    replay_task["id"] = task_id
    replay_task["status"] = task.get("status") or snapshot_data.get("terminal_status") or "done"
    replay_task["loop_ids"] = task.get("loop_ids") or ["loop_product_archive_replay"]
    replay_task["product_level_required"] = True
    return replay_task, None


def _classify_archive_replay_result(gaps: list[str]) -> str:
    if not gaps:
        return "valid_closure"

    false_closure_signals = (
        "overall admission is not done-eligible",
        "overall admission is not an accepted closeout state",
        "blocking acceptance requirement",
        "blocking residual risk",
        "missing reviewer verdict",
        "missing reviewer:",
        "invalid reviewer:",
    )
    if any(any(signal in gap for signal in false_closure_signals) for gap in gaps):
        return "false_closure"
    return "stale_evidence"


def _follow_up_task_id(task_id: str, classification: str) -> str | None:
    if classification == "valid_closure":
        return None
    suffix = "FALSE-CLOSEOUT" if classification == "false_closure" else "STALE-EVIDENCE"
    return f"{task_id}-{suffix}-REPAIR"


def audit_archive_root(
    archive_root: Path,
    excluded_task_ids: set[str],
    frozen_task_ids: tuple[str, ...] = FROZEN_ARCHIVE_REPLAY_TASK_IDS,
) -> dict[str, Any]:
    frozen_task_id_set = set(frozen_task_ids)
    frozen_snapshot_paths_by_id = {
        task_id: archive_root / f"{task_id}.json" for task_id in frozen_task_ids
    }
    archive_task_ids_present: set[str] = set()
    if archive_root.exists():
        for path in sorted(archive_root.iterdir()):
            if (
                path.is_file()
                and path.suffix == ".json"
                and path.name.startswith("LOOP-PROD")
            ):
                archive_task_ids_present.add(path.stem)

    results: list[dict[str, Any]] = []
    excluded: list[dict[str, str]] = []
    source_set_errors: list[str] = []

    duplicate_frozen_task_ids = sorted(
        {
            task_id
            for index, task_id in enumerate(frozen_task_ids)
            if task_id in frozen_task_ids[:index]
        }
    )
    for task_id in duplicate_frozen_task_ids:
        source_set_errors.append(f"duplicate frozen archive replay task id: {task_id}")

    missing_task_ids = [
        task_id
        for task_id, snapshot_path in frozen_snapshot_paths_by_id.items()
        if not snapshot_path.is_file()
    ]
    for task_id in missing_task_ids:
        source_set_errors.append(f"missing frozen archive snapshot: {task_id}.json")

    unexpected_task_ids = sorted(
        archive_task_ids_present - frozen_task_id_set - excluded_task_ids
    )
    for task_id in unexpected_task_ids:
        source_set_errors.append(f"unexpected LOOP-PROD archive snapshot: {task_id}.json")

    for task_id in sorted(excluded_task_ids):
        snapshot_path = archive_root / f"{task_id}.json"
        if snapshot_path.is_file():
            excluded.append(
                {
                    "task_id": task_id,
                    "snapshot": _display_path(snapshot_path),
                    "reason": "governance/meta task excluded from frozen product-closure replay set",
                }
            )

    seen_replay_task_ids: dict[str, str] = {}
    seen_snapshot_task_ids: dict[str, str] = {}
    for frozen_task_id in frozen_task_ids:
        snapshot_path = frozen_snapshot_paths_by_id[frozen_task_id]
        if not snapshot_path.is_file():
            continue

        task_id_from_path = snapshot_path.stem
        display_path = _display_path(snapshot_path)

        before_hash = _sha256_file(snapshot_path)
        try:
            with open(snapshot_path, encoding="utf-8") as fh:
                snapshot_data = json.load(fh)
        except Exception as exc:
            after_hash = _sha256_file(snapshot_path)
            classification = "stale_evidence"
            results.append(
                {
                    "task_id": task_id_from_path,
                    "snapshot": display_path,
                    "snapshot_sha256_before": before_hash,
                    "snapshot_sha256_after": after_hash,
                    "snapshot_hash_unchanged": before_hash == after_hash,
                    "review_file": None,
                    "result": "fail",
                    "classification": classification,
                    "gap_count": 1,
                    "gaps": [f"failed to parse archive snapshot JSON: {exc}"],
                    "required_follow_up_task_id": _follow_up_task_id(
                        task_id_from_path,
                        classification,
                    ),
                }
            )
            continue

        task, excluded_reason = _task_from_archive_snapshot(snapshot_path, snapshot_data)
        after_hash = _sha256_file(snapshot_path)
        if task is None:
            classification = "stale_evidence"
            results.append(
                {
                    "task_id": task_id_from_path,
                    "snapshot": display_path,
                    "snapshot_sha256_before": before_hash,
                    "snapshot_sha256_after": after_hash,
                    "snapshot_hash_unchanged": before_hash == after_hash,
                    "review_file": None,
                    "result": "fail",
                    "classification": classification,
                    "gap_count": 1,
                    "gaps": [excluded_reason or "archive snapshot is not replayable"],
                    "required_follow_up_task_id": _follow_up_task_id(
                        task_id_from_path,
                        classification,
                    ),
                }
            )
            continue

        precheck_gaps: list[str] = []
        replay_task_id = str(task["id"])
        snapshot_task_id = str(snapshot_data.get("task_id") or "").strip()
        if not snapshot_task_id:
            precheck_gaps.append("archive snapshot top-level task_id is missing")
        elif snapshot_task_id != task_id_from_path:
            precheck_gaps.append(
                "archive filename/task_id mismatch: "
                f"file {task_id_from_path}.json contains task_id {snapshot_task_id}"
            )
        if snapshot_task_id:
            if snapshot_task_id in seen_snapshot_task_ids:
                precheck_gaps.append(
                    "duplicate archive snapshot task_id: "
                    f"{snapshot_task_id} also appears in "
                    f"{seen_snapshot_task_ids[snapshot_task_id]}"
                )
            else:
                seen_snapshot_task_ids[snapshot_task_id] = display_path

        if replay_task_id != task_id_from_path:
            precheck_gaps.append(
                "archive filename/task ID mismatch: "
                f"file {task_id_from_path}.json contains task.id {replay_task_id}"
            )
        if replay_task_id in seen_replay_task_ids:
            precheck_gaps.append(
                "duplicate archive task id: "
                f"{replay_task_id} also appears in {seen_replay_task_ids[replay_task_id]}"
            )
        else:
            seen_replay_task_ids[replay_task_id] = display_path

        gaps = precheck_gaps + check_task(task)
        classification = _classify_archive_replay_result(gaps)
        results.append(
            {
                "task_id": task_id_from_path,
                "snapshot_task_id": snapshot_task_id,
                "task_object_id": replay_task_id,
                "snapshot": display_path,
                "snapshot_sha256_before": before_hash,
                "snapshot_sha256_after": after_hash,
                "snapshot_hash_unchanged": before_hash == after_hash,
                "owner": task.get("owner"),
                "reviewer": task.get("reviewer"),
                "status": task.get("status"),
                "terminal_status": snapshot_data.get("terminal_status"),
                "terminal_outcome": snapshot_data.get("terminal_outcome"),
                "review_file": task.get("review_file"),
                "result": "pass" if not gaps else "fail",
                "classification": classification,
                "gap_count": len(gaps),
                "gaps": gaps,
                "required_follow_up_task_id": _follow_up_task_id(
                    task_id_from_path,
                    classification,
                ),
            }
        )

    failed = [result for result in results if result["result"] == "fail"]
    classification_counts: dict[str, int] = {}
    for result in results:
        classification = str(result["classification"])
        classification_counts[classification] = classification_counts.get(classification, 0) + 1

    failed_count = len(failed) + len(source_set_errors)
    return {
        "audit_id": "closeout-truth-audit-2026-07-16",
        "generated_at": (
            datetime.now(UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        ),
        "mode": "read_only_archive_snapshot_replay",
        "source_root": str(archive_root),
        "selection": {
            "frozen_rule": (
                "fixed 18 task IDs from FROZEN_ARCHIVE_REPLAY_TASK_IDS; "
                "governance/meta snapshots are excluded from unexpected-source "
                "errors but never used to derive the replay set"
            ),
            "frozen_task_ids": list(frozen_task_ids),
            "included_task_ids": [result["task_id"] for result in results],
            "included_snapshots": len(results),
            "excluded_task_ids": sorted(excluded_task_ids),
            "excluded_snapshots": excluded,
            "source_set_errors": source_set_errors,
            "archive_mutation": "none",
        },
        "summary": {
            "passed": len(results) - len(failed),
            "failed": failed_count,
            "failed_results": len(failed),
            "source_set_error_count": len(source_set_errors),
            "scanned": len(results),
            "classification_counts": classification_counts,
        },
        "results": results,
    }


def write_audit_json(audit: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def write_audit_markdown(audit: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Loop Product Closeout Truth Audit",
        "",
        f"- Audit ID: `{audit['audit_id']}`",
        f"- Generated at: `{audit['generated_at']}`",
        f"- Mode: `{audit['mode']}`",
        f"- Source root: `{audit['source_root']}`",
        f"- Archive mutation: `{audit['selection']['archive_mutation']}`",
        f"- Scanned: {audit['summary']['scanned']}",
        f"- Passed: {audit['summary']['passed']}",
        f"- Failed: {audit['summary']['failed']}",
        "",
        "## Results",
        "",
        "| Task | Verdict | Classification | Follow-up | Gaps |",
        "|---|---:|---|---|---|",
    ]

    for result in audit["results"]:
        gaps = "<br>".join(result["gaps"]) if result["gaps"] else "none"
        lines.append(
            "| `{task_id}` | `{verdict}` | `{classification}` | `{follow_up}` | {gaps} |".format(
                task_id=result["task_id"],
                verdict=result["result"],
                classification=result.get("classification") or "",
                follow_up=result.get("required_follow_up_task_id") or "",
                gaps=gaps,
            )
        )

    excluded_items = (
        audit["selection"].get("excluded_manifests")
        or audit["selection"].get("excluded_snapshots")
        or []
    )
    if excluded_items:
        lines.extend(["", "## Excluded Sources", ""])
        for excluded in excluded_items:
            label = excluded.get("manifest") or excluded.get("snapshot") or excluded.get("task_id")
            lines.append(f"- `{label}`: {excluded['reason']}")

    source_set_errors = audit["selection"].get("source_set_errors") or []
    if source_set_errors:
        lines.extend(["", "## Source Set Errors", ""])
        for error in source_set_errors:
            lines.append(f"- {error}")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def load_status(status_file: Path) -> dict[str, Any]:
    try:
        with open(status_file, encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        print(f"ERROR: status file not found: {status_file}", file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(f"ERROR: cannot parse status file: {exc}", file=sys.stderr)
        sys.exit(2)


def run(status_file: Path, task_id: str | None) -> int:
    state = load_status(status_file)
    tasks: list[dict[str, Any]] = state.get("tasks") or []

    if task_id:
        matched = [t for t in tasks if t.get("id") == task_id]
        if not matched:
            print(f"ERROR: task '{task_id}' not found in {status_file}", file=sys.stderr)
            return 2
        tasks = matched

    loop_tasks = [t for t in tasks if is_loop_autopilot_task(t)]

    if not loop_tasks:
        target = f"'{task_id}'" if task_id else "any task"
        print(f"No loop-autopilot tasks matched ({target}). Nothing to check.")
        return 0

    fail_count = 0
    for task in loop_tasks:
        tid = task.get("id", "?")
        status = task.get("status", "?")
        gaps = check_task(task)
        if gaps:
            fail_count += 1
            print(f"[FAIL] {tid} (status={status})")
            for gap in gaps:
                print(f"       ✗ {gap}")
        else:
            print(f"[OK]   {tid} (status={status})")

    total = len(loop_tasks)
    ok_count = total - fail_count
    print(f"\n{ok_count}/{total} loop task(s) passed guardrail checks.")
    return 1 if fail_count else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--task-id", metavar="ID", help="Check only this task ID")
    parser.add_argument(
        "--evidence-root",
        metavar="PATH",
        help="Replay guardrail against evidence.json manifests under PATH",
    )
    parser.add_argument(
        "--archive-root",
        metavar="PATH",
        help="Replay guardrail against archived task snapshots under PATH",
    )
    parser.add_argument(
        "--exclude-task-id",
        action="append",
        default=[],
        help="Task ID to exclude from --archive-root replay; may be passed multiple times",
    )
    parser.add_argument(
        "--audit-json",
        metavar="PATH",
        help="Write evidence replay audit JSON to PATH",
    )
    parser.add_argument(
        "--audit-md",
        metavar="PATH",
        help="Write evidence replay audit Markdown to PATH",
    )
    parser.add_argument(
        "--status-file",
        metavar="PATH",
        default=str(DEFAULT_STATUS_FILE),
        help=f"Path to ai-status.json (default: {DEFAULT_STATUS_FILE})",
    )
    args = parser.parse_args()

    if args.evidence_root and args.archive_root:
        raise SystemExit("Use only one of --evidence-root or --archive-root")

    if args.evidence_root or args.archive_root:
        if args.archive_root:
            excluded_task_ids = set(DEFAULT_ARCHIVE_EXCLUDED_TASK_IDS)
            excluded_task_ids.update(args.exclude_task_id or [])
            audit = audit_archive_root(Path(args.archive_root), excluded_task_ids)
        else:
            audit = audit_evidence_root(Path(args.evidence_root))
        if args.audit_json:
            write_audit_json(audit, Path(args.audit_json))
        if args.audit_md:
            write_audit_markdown(audit, Path(args.audit_md))
        for result in audit["results"]:
            label = "OK" if result["result"] == "pass" else "FAIL"
            detail = result.get("classification") or result.get("overall_admission") or ""
            print(f"[{label}] {result['task_id']} ({detail})")
            for gap in result["gaps"]:
                print(f"       ✗ {gap}")
        summary = audit["summary"]
        print(
            f"\n{summary['passed']}/{summary['scanned']} replay source(s) "
            "passed closeout truth replay."
        )
        sys.exit(1 if summary["failed"] or summary.get("source_set_error_count") else 0)

    status_path = Path(args.status_file)
    rc = run(status_path, args.task_id)
    sys.exit(rc)


if __name__ == "__main__":
    main()
