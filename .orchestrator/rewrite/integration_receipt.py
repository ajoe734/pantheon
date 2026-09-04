"""DTG-INT-01: canonical integration-receipt schema, pure consumption
predicate, and the narrow internal mutation authority that records a merge
outcome onto its own task row so the auto-integrator cron stops
re-evaluating an already-landed delivery.

This module is deliberately narrow and self-contained (stdlib plus
``.orchestrator/common`` and its ``rewrite`` sibling ``task_state_store``,
matching the existing import shape of ``rewrite/worker_recovery.py`` and
``rewrite/verify_activity_integrity.py``) so importing it from
``scripts/git/auto_integrator.py`` cannot create a circular import with
``scripts/ai_status.py`` (which already imports several ``rewrite`` modules
at its own module top level).

``record_integration_receipt`` intentionally does not call into
``scripts/ai_status.py``: doing so would either duplicate the canonical V2
commit this function itself performs, or require trusting the same
``ORCH_RUN_ID``/``AI_NAME`` environment convention the design explicitly
forbids for this write path. The receipt is invisible, machine-only
bookkeeping -- it never changes task status -- so the human-facing
current-work/dashboard projection is refreshed the same way it already is
for any other canonical field: by the next regular supervisor cycle, through
the existing projection contract, not a new one.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from common import canonical_task_state_lock_file, validate_status_command_runtime

from rewrite.task_state_store import append_state_commit, snapshot_transaction

RECEIPT_KEY = "integration_receipt"
RECEIPT_VERSION = 1
RECEIPT_RESULT_LANDED = "landed"
RECEIPT_OBSERVATION_PERFORMED_MERGE = "performed_merge"
RECEIPT_OBSERVATION_RECONCILED = "reconciled_existing_merge"
RECEIPT_OBSERVATIONS = frozenset(
    {RECEIPT_OBSERVATION_PERFORMED_MERGE, RECEIPT_OBSERVATION_RECONCILED}
)
RECEIPT_SOURCE = "canonical_auto_integrator"

# The pure predicate (see module docstring on ``integration_receipt_consumes_candidate``)
# must not read live repository config, so it can only recognize the single
# repository this design is in scope for. This literal mirrors the compiled-in
# default in ``.orchestrator/multi_repo_registry.py``'s ``DEFAULT_REPOSITORIES``
# (also duplicated as a literal fallback in ``.orchestrator/common.py``); a task
# bound to any other repository id safely never matches (falls back to normal
# re-evaluation, never a false match).
_DEFAULT_REPOSITORY_ID = "pantheon"
_DEFAULT_REPOSITORY_SLUG = "ajoe734/pantheon"
_DEFAULT_REPOSITORY_ALIASES = frozenset(
    {_DEFAULT_REPOSITORY_ID.casefold(), _DEFAULT_REPOSITORY_SLUG.casefold()}
)
_DEFAULT_TARGET_BRANCH = "dev"

_OID_RE = re.compile(r"^[0-9a-f]{40}$")
_RFC3339_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")

_MERGE_ADMISSIBLE_STATUSES = frozenset({"review_approved", "in_progress", "review"})


class IntegrationReceiptError(RuntimeError):
    """Base error for the receipt authority/mutation path."""


class IntegrationReceiptAuthorityError(IntegrationReceiptError):
    """One of the required authority proofs (SD.md DTG-INT-01 6.4) failed."""


class IntegrationReceiptConflictError(IntegrationReceiptError):
    """A different, non-matching receipt already exists on this row."""


class IntegrationReceiptBindingError(IntegrationReceiptError):
    """The row no longer matches the caller's revalidated delivery snapshot."""


@dataclass(frozen=True)
class IntegrationBinding:
    """The frozen delivery identity a receipt write must still match."""

    repository: str
    target_branch: str
    pr: int
    head_sha: str


@dataclass(frozen=True)
class IntegrationReceipt:
    """A validated ``integration_receipt`` payload, ready to attach to a row."""

    observation: str
    task_generation: int
    repository: str
    target_branch: str
    pr: int
    head_sha: str
    merge_commit_sha: str
    observed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": RECEIPT_VERSION,
            "result": RECEIPT_RESULT_LANDED,
            "observation": self.observation,
            "task_generation": self.task_generation,
            "repository": self.repository,
            "target_branch": self.target_branch,
            "pr": self.pr,
            "head_sha": self.head_sha,
            "merge_commit_sha": self.merge_commit_sha,
            "observed_at": self.observed_at,
            "source": RECEIPT_SOURCE,
        }


@dataclass(frozen=True)
class IntegrationAuthority:
    """Concrete proofs the caller already holds, re-verified before writing.

    ``lock_path``/``lock_schema``/``lock_pid`` re-check the canonical
    auto-integrator flock this process acquired in
    ``scripts/git/auto_integrator.py:lock_file`` -- the metadata file it
    publishes under that flock is re-read here to prove *this* process still
    owns it (SD.md 6.4 point 3), without this module importing
    ``auto_integrator`` itself.
    """

    command_root: Path
    command_sha: str
    command_remote: str
    command_base_ref: str
    status_root: Path
    lock_path: Path
    lock_schema: str
    lock_pid: int


@dataclass(frozen=True)
class ReceiptWriteResult:
    task_id: str
    written: bool
    replay: bool
    receipt: dict[str, Any]


def _oid(value: Any) -> str:
    """Normalize-then-validate: for values this module already trusts (the
    row's own ``review_binding``, or a caller-supplied merge commit it is
    about to write) where case is incidental, not part of the stored
    contract."""

    text = str(value or "").strip().lower()
    return text if _OID_RE.match(text) else ""


def _strict_oid(value: Any) -> str:
    """Exact-match only: a *stored* receipt's SHAs must already be lowercase
    40-hex (SD.md 6.2 "SHAs are lowercase 40-character Git OIDs") -- a
    mixed-case value is malformed evidence, not silently coerced."""

    text = str(value or "")
    return text if _OID_RE.match(text) else ""


def parse_integration_receipt(raw: Any) -> dict[str, Any] | None:
    """Validate a stored ``integration_receipt`` payload (SD.md 6.2/6.3).

    Returns ``None`` for anything missing, malformed, or an unknown version --
    never raises. An unrecognized receipt is non-consuming evidence, not a
    projection failure.
    """

    if not isinstance(raw, Mapping):
        return None
    try:
        if raw.get("version") != RECEIPT_VERSION:
            return None
        if raw.get("result") != RECEIPT_RESULT_LANDED:
            return None
        observation = raw.get("observation")
        if observation not in RECEIPT_OBSERVATIONS:
            return None
        task_generation = raw.get("task_generation")
        if (
            isinstance(task_generation, bool)
            or not isinstance(task_generation, int)
            or task_generation < 1
        ):
            return None
        repository = str(raw.get("repository") or "").strip()
        if not repository:
            return None
        target_branch = str(raw.get("target_branch") or "").strip()
        if not target_branch:
            return None
        pr = raw.get("pr")
        if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
            return None
        head_sha = _strict_oid(raw.get("head_sha"))
        if not head_sha:
            return None
        merge_commit_sha = _strict_oid(raw.get("merge_commit_sha"))
        if not merge_commit_sha:
            return None
        observed_at = str(raw.get("observed_at") or "").strip()
        if not _RFC3339_UTC_RE.match(observed_at):
            return None
        if raw.get("source") != RECEIPT_SOURCE:
            return None
    except Exception:
        return None
    return {
        "version": RECEIPT_VERSION,
        "result": RECEIPT_RESULT_LANDED,
        "observation": observation,
        "task_generation": task_generation,
        "repository": repository,
        "target_branch": target_branch,
        "pr": pr,
        "head_sha": head_sha,
        "merge_commit_sha": merge_commit_sha,
        "observed_at": observed_at,
        "source": RECEIPT_SOURCE,
    }


def frozen_delivery_binding(task: Mapping[str, Any]) -> dict[str, Any] | None:
    """Derive the row's own frozen (repository, branch, pr, head) identity.

    Pure and read-only: only fields already embedded on the canonical row
    (``target_repo``, ``review_binding``) are consulted, matching
    ``integration_receipt_consumes_candidate``'s "no filesystem operation"
    contract (SD.md 6.6). A non-default repository id cannot be resolved to
    a GitHub slug without reading live config, so it safely returns ``None``
    (never a false match) rather than guessing.
    """

    if not isinstance(task, Mapping):
        return None
    repo_id = str(task.get("target_repo") or _DEFAULT_REPOSITORY_ID).strip() or _DEFAULT_REPOSITORY_ID
    if repo_id.casefold() not in _DEFAULT_REPOSITORY_ALIASES:
        return None
    binding = task.get("review_binding")
    if not isinstance(binding, Mapping):
        return None
    pr = binding.get("pr")
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        return None
    head_sha = _oid(binding.get("head_sha"))
    if not head_sha:
        return None
    target_branch = str(binding.get("base") or "").strip() or _DEFAULT_TARGET_BRANCH
    return {
        "repository": _DEFAULT_REPOSITORY_SLUG,
        "target_branch": target_branch,
        "pr": pr,
        "head_sha": head_sha,
    }


def integration_receipt_consumes_candidate(task: Mapping[str, Any]) -> bool:
    """True only when ``task`` already carries a receipt for its current
    identity -- the auto-integrator must skip it without any GitHub call,
    fetch, filesystem operation, or ancestry query (SD.md 6.6)."""

    if not isinstance(task, Mapping):
        return False
    receipt = parse_integration_receipt(task.get(RECEIPT_KEY))
    if receipt is None:
        return False
    try:
        current_generation = task.get("generation", 1)
        if isinstance(current_generation, bool) or not isinstance(current_generation, int):
            return False
    except Exception:
        return False
    if receipt["task_generation"] != current_generation:
        return False
    binding = frozen_delivery_binding(task)
    if binding is None:
        return False
    return (
        receipt["repository"] == binding["repository"]
        and receipt["target_branch"] == binding["target_branch"]
        and receipt["pr"] == binding["pr"]
        and receipt["head_sha"] == binding["head_sha"]
        and bool(receipt["merge_commit_sha"])
    )


def _read_lock_owner_pid(lock_path: Path, *, expected_schema: str) -> int | None:
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    if payload.get("schema") != expected_schema or payload.get("state") != "held":
        return None
    pid = payload.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) else None


def _verify_authority(
    *,
    config: Mapping[str, Any],
    authority: IntegrationAuthority,
) -> None:
    try:
        validate_status_command_runtime(
            authority.command_root,
            expected_sha=authority.command_sha,
            expected_remote=authority.command_remote,
            base_ref=authority.command_base_ref,
        )
    except RuntimeError as exc:
        raise IntegrationReceiptAuthorityError(
            f"promoted command runtime authority failed: {exc}"
        ) from exc

    paths = config.get("paths") if isinstance(config, Mapping) else None
    raw_status_file = (paths or {}).get("status_file") if isinstance(paths, Mapping) else None
    if not raw_status_file:
        raise IntegrationReceiptAuthorityError("config carries no paths.status_file")
    expected_status_root = Path(str(raw_status_file)).expanduser().resolve().parent
    if authority.status_root.expanduser().resolve() != expected_status_root:
        raise IntegrationReceiptAuthorityError(
            "status root is not the canonical absolute status root: "
            f"{authority.status_root} != {expected_status_root}"
        )

    owner_pid = _read_lock_owner_pid(authority.lock_path, expected_schema=authority.lock_schema)
    if owner_pid is None or owner_pid != authority.lock_pid:
        raise IntegrationReceiptAuthorityError(
            "canonical auto-integrator flock is not held by this process generation"
        )


def _find_task(state: Mapping[str, Any], task_id: str) -> dict[str, Any] | None:
    tasks = state.get("tasks") if isinstance(state, Mapping) else None
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if isinstance(task, dict) and str(task.get("id") or "") == task_id:
            return task
    return None


def _atomic_write_status_file(status_file: Path, state: Mapping[str, Any]) -> None:
    serialized = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=status_file.parent, delete=False
    ) as handle:
        handle.write(serialized)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, status_file)


def record_integration_receipt(
    *,
    config: Mapping[str, Any],
    task_id: str,
    expected_generation: int,
    expected_delivery_binding: IntegrationBinding,
    observation: str,
    merge_commit_sha: str,
    observed_at: str,
    status_file: Path,
    event_path: Path | None,
    authority: IntegrationAuthority,
) -> ReceiptWriteResult:
    """Attach a validated ``integration_receipt`` to exactly one task row.

    Narrow and purpose-specific (SD.md 6.4): no worker lease is faked, no
    ``PANTHEON_LOCAL_HUMAN_OPS``/``ORCH_RUN_ID`` is set or trusted, and this
    is not a general task-amendment API -- it writes exactly one field,
    behind its own authority checks, and only when the row still matches the
    caller's revalidated delivery snapshot.
    """

    if observation not in RECEIPT_OBSERVATIONS:
        raise IntegrationReceiptError(f"unsupported observation: {observation!r}")
    merge_commit_sha = _oid(merge_commit_sha)
    if not merge_commit_sha:
        raise IntegrationReceiptError("merge_commit_sha must be a 40-hex oid")
    if not _RFC3339_UTC_RE.match(observed_at):
        raise IntegrationReceiptError("observed_at must be UTC RFC3339 (...Z)")

    _verify_authority(config=config, authority=authority)

    with canonical_task_state_lock_file(status_file, shared=False):
        if event_path is not None:
            with snapshot_transaction(event_path) as transaction:
                snapshot = transaction.load_snapshot()
                state = snapshot["state"]
                if not isinstance(state, dict):
                    raise IntegrationReceiptBindingError(
                        "authoritative task-state journal projection is not an object"
                    )
                result = _apply_receipt_to_state(
                    state,
                    task_id=task_id,
                    expected_generation=expected_generation,
                    expected_delivery_binding=expected_delivery_binding,
                    observation=observation,
                    merge_commit_sha=merge_commit_sha,
                    observed_at=observed_at,
                )
                if result.written:
                    transaction.append_state_commit(state, source=RECEIPT_SOURCE)
                    _atomic_write_status_file(status_file, state)
                return result
        state = json.loads(status_file.read_text(encoding="utf-8"))
        result = _apply_receipt_to_state(
            state,
            task_id=task_id,
            expected_generation=expected_generation,
            expected_delivery_binding=expected_delivery_binding,
            observation=observation,
            merge_commit_sha=merge_commit_sha,
            observed_at=observed_at,
        )
        if result.written:
            if event_path is not None:  # pragma: no cover - defensive, unreachable
                append_state_commit(event_path, state, source=RECEIPT_SOURCE)
            _atomic_write_status_file(status_file, state)
        return result


def _apply_receipt_to_state(
    state: dict[str, Any],
    *,
    task_id: str,
    expected_generation: int,
    expected_delivery_binding: IntegrationBinding,
    observation: str,
    merge_commit_sha: str,
    observed_at: str,
) -> ReceiptWriteResult:
    task = _find_task(state, task_id)
    if task is None:
        raise IntegrationReceiptBindingError(f"task {task_id} not found in canonical state")

    current_generation = task.get("generation", 1)
    if (
        isinstance(current_generation, bool)
        or not isinstance(current_generation, int)
        or current_generation != expected_generation
    ):
        raise IntegrationReceiptBindingError(
            f"task {task_id} generation changed: expected {expected_generation}, "
            f"found {current_generation!r}"
        )

    status = str(task.get("status") or "").strip().lower()
    if status not in _MERGE_ADMISSIBLE_STATUSES:
        raise IntegrationReceiptBindingError(
            f"task {task_id} status {status!r} is not review_approved or an active "
            "merge-then-review state"
        )

    current_binding = frozen_delivery_binding(task)
    if current_binding is None or (
        current_binding["repository"] != expected_delivery_binding.repository
        or current_binding["target_branch"] != expected_delivery_binding.target_branch
        or current_binding["pr"] != expected_delivery_binding.pr
        or current_binding["head_sha"] != expected_delivery_binding.head_sha
    ):
        raise IntegrationReceiptBindingError(
            f"task {task_id} delivery binding no longer matches the revalidated snapshot"
        )

    candidate_receipt = IntegrationReceipt(
        observation=observation,
        task_generation=current_generation,
        repository=expected_delivery_binding.repository,
        target_branch=expected_delivery_binding.target_branch,
        pr=expected_delivery_binding.pr,
        head_sha=expected_delivery_binding.head_sha,
        merge_commit_sha=merge_commit_sha,
        observed_at=observed_at,
    ).as_dict()

    existing = parse_integration_receipt(task.get(RECEIPT_KEY))
    if existing is not None:
        identity_fields = (
            "task_generation",
            "repository",
            "target_branch",
            "pr",
            "head_sha",
            "merge_commit_sha",
        )
        if all(existing[field] == candidate_receipt[field] for field in identity_fields):
            return ReceiptWriteResult(
                task_id=task_id, written=False, replay=True, receipt=existing
            )
        raise IntegrationReceiptConflictError(
            f"task {task_id} already carries a conflicting integration_receipt"
        )

    task[RECEIPT_KEY] = candidate_receipt
    return ReceiptWriteResult(
        task_id=task_id, written=True, replay=False, receipt=candidate_receipt
    )
