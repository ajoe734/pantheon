"""DTG-CLEAN-M2: signed dev-bridge packet verification, batch loading,
dependency-closure validation, materialization planning, and readback.

Per SD.md 7.5: ai_status.py retains CLI routing, public authority admission,
and the canonical transaction boundary. This module returns validated
mutations/results and never exposes a product BFF route.

Module-boundary note: a handful of symbols this closure touches
(``get_task``, ``canonical_agent_name``, ``pending_review_decision_intent``,
``_bridge_assignment_from_metadata``, ``_clear_status_command_lease_binding``,
``_parse_utc_timestamp``, the ``DEV_BRIDGE_*`` constants, ``TERMINAL_FACTS_KEY``)
are used far beyond this batch-materialize flow, so they stay owned by
ai_status.py and are reached through a lazy (function-body) import -- the
same established pattern already used for ``_github_review_bridge_module()``
and (as of DTG-CLEAN-M1) ``rewrite/status_projection.py``.

``_DEV_BRIDGE_MATERIALIZATION_LOCAL`` is the one exception: it is a
thread-local re-entrancy guard that ``scripts/ai_status.py``'s own
``command_assign`` reads directly (rejecting any caller that tries to attach
bridge provenance without this module having set it first). A lazy import
resolves the bare module name ``ai_status`` from ``sys.modules``, which is
*not* guaranteed to be the same ai_status instance the caller is using --
some tests deliberately load an independent copy of ai_status.py via
``importlib.util.spec_from_file_location`` for isolation, which would give
this module and that copy of ``command_assign`` two different
``threading.local()`` objects and silently break the guard. Since this
function is what sets the flag, it owns the canonical ``local()`` instance
directly (a plain top-level import, not lazy); ai_status.py imports it from
here instead of the other way around.

Deliberately out of scope for this wave: ``verify_signed_dev_bridge_packet``'s
inline Ed25519 signature check duplicates the primitive in
``dev_bridge_signer.verify_packet`` (which operates on a ``DevTaskPacket``
Pydantic model rather than a raw dict). Merging them safely requires proving
byte-for-byte canonical-JSON equivalence between the two paths for
security-critical verification code; that is real de-duplication work SD.md
7.5 gestures at ("remove duplicate protocol bodies") but is deliberately not
attempted in this move, which only relocates the function verbatim to its
correct owner package.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import local
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from common import utc_now as iso_now
import execution_authorization

# Canonical home of the materialization re-entrancy guard (see module
# docstring) -- ai_status.py imports this exact instance rather than owning
# its own copy.
_DEV_BRIDGE_MATERIALIZATION_LOCAL = local()


def _ai_status_module():
    """Lazy import back to the entrypoint module for the handful of symbols
    that are genuinely shared infrastructure, including the shared
    threading.local() re-entrancy guard (see module docstring)."""
    scripts_dir = Path(__file__).resolve().parents[2] / "scripts"
    import sys

    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import ai_status

    return ai_status


def load_dev_bridge_materialize_batch(path_value: str) -> dict[str, Any]:
    """Load one bounded, exact packet-materialization payload from a file.

    The payload names the packet id/digest once and every task row's already
    signed ``TASK_METADATA_JSON``-shaped envelope, so the caller never
    re-derives bridge provenance -- it only carries what the dispatcher
    already verified.
    """
    ai_status = _ai_status_module()

    if not path_value:
        raise SystemExit(
            f"Usage: {ai_status.DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND} <absolute-payload-path>"
        )
    path = Path(os.path.expanduser(path_value))
    if not path.is_absolute():
        raise SystemExit("Dev bridge materialize batch payload path must be absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise SystemExit(f"Unable to inspect dev bridge materialize batch payload: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode) or path.is_symlink():
        raise SystemExit("Dev bridge materialize batch payload must be a non-symlink regular file")
    if metadata.st_size > 4 * 1024 * 1024:
        raise SystemExit("Dev bridge materialize batch payload exceeds 4 MiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid dev bridge materialize batch payload: {exc}") from exc

    exact_keys = {
        "schema_version", "packet_id", "packet_digest", "actor",
        "signed_packet", "tasks",
    }
    if not isinstance(payload, dict) or set(payload) != exact_keys:
        raise SystemExit("Dev bridge materialize batch payload schema is not exact")
    if payload.get("schema_version") != ai_status.DEV_BRIDGE_BATCH_SCHEMA_VERSION:
        raise SystemExit("Unsupported dev bridge materialize batch schema version")

    packet_id = payload.get("packet_id")
    packet_digest = payload.get("packet_digest")
    actor = payload.get("actor")
    if not isinstance(packet_id, str) or not packet_id.strip() or len(packet_id) > 256:
        raise SystemExit("Dev bridge materialize batch packet_id is invalid")
    if not isinstance(packet_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", packet_digest):
        raise SystemExit("Dev bridge materialize batch packet_digest must be a SHA-256 hex digest")
    if not isinstance(actor, str) or not actor.strip() or len(actor) > 80:
        raise SystemExit("Dev bridge materialize batch actor is invalid")
    if actor.strip() != ai_status.DEV_BRIDGE_BATCH_ACTOR:
        raise SystemExit(
            "Dev bridge materialize batch actor must be the trusted bridge actor"
        )

    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or not tasks or len(tasks) > ai_status.DEV_BRIDGE_BATCH_MAX_TASKS:
        raise SystemExit(
            "Dev bridge materialize batch tasks must contain between 1 and "
            f"{ai_status.DEV_BRIDGE_BATCH_MAX_TASKS} rows"
        )

    row_keys = {"task_id", "owner", "reviewer", "title", "assignment_next", "task_metadata"}
    normalized_tasks: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()
    for index, row in enumerate(tasks):
        if not isinstance(row, dict) or set(row) != row_keys:
            raise SystemExit(f"Dev bridge materialize batch row {index} schema is not exact")
        normalized: dict[str, Any] = {}
        for field, limit in (
            ("task_id", 256),
            ("owner", 80),
            ("reviewer", 80),
            ("title", 240),
        ):
            value = row[field]
            if not isinstance(value, str) or not value.strip() or len(value) > limit:
                raise SystemExit(f"Dev bridge materialize batch row {index} has invalid {field}")
            normalized[field] = value.strip()
        assignment_next = row["assignment_next"]
        if assignment_next is None:
            normalized["assignment_next"] = ""
        elif isinstance(assignment_next, str) and len(assignment_next) <= 4096:
            normalized["assignment_next"] = assignment_next.strip()
        else:
            raise SystemExit(
                f"Dev bridge materialize batch row {index} has invalid assignment_next"
            )
        task_metadata = row["task_metadata"]
        if not isinstance(task_metadata, dict):
            raise SystemExit(
                f"Dev bridge materialize batch row {index} task_metadata must be an object"
            )
        bridge = task_metadata.get("dev_bridge")
        if not isinstance(bridge, dict):
            raise SystemExit(
                f"Dev bridge materialize batch row {index} task_metadata.dev_bridge is required"
            )
        if str(bridge.get("packet_id") or "") != packet_id:
            raise SystemExit(
                f"Dev bridge materialize batch row {index} packet_id does not match the batch"
            )
        if str(bridge.get("packet_digest") or "") != packet_digest:
            raise SystemExit(
                f"Dev bridge materialize batch row {index} packet_digest does not match the batch"
            )
        normalized["task_metadata"] = deepcopy(task_metadata)
        task_id = normalized["task_id"]
        if task_id in seen_task_ids:
            raise SystemExit(f"Dev bridge materialize batch repeats task id: {task_id}")
        seen_task_ids.add(task_id)
        normalized_tasks.append(normalized)

    signed_packet = payload.get("signed_packet")
    if not isinstance(signed_packet, dict):
        raise SystemExit("Dev bridge materialize batch signed_packet is required")

    return {
        "schema_version": ai_status.DEV_BRIDGE_BATCH_SCHEMA_VERSION,
        "packet_id": packet_id.strip(),
        "packet_digest": packet_digest,
        "actor": actor.strip(),
        "signed_packet": deepcopy(signed_packet),
        "tasks": normalized_tasks,
    }


def dev_bridge_replay_ledger(state: dict[str, Any]) -> dict[str, Any]:
    """Return the sole dev-bridge replay ledger and retire operator ledgers."""
    ai_status = _ai_status_module()

    current = state.get(ai_status.DEV_BRIDGE_CONSUMED_KEY)
    if current is None:
        current = {}
        state[ai_status.DEV_BRIDGE_CONSUMED_KEY] = current
    if not isinstance(current, dict):
        raise ValueError("Dev bridge replay ledger must be a JSON object")

    legacy = state.pop(ai_status.LEGACY_OPERATOR_ASSERTION_KEYS[0], None)
    if legacy is not None:
        if not isinstance(legacy, Mapping):
            raise ValueError("Legacy operator replay ledger must be a JSON object")
        for receipt_id, receipt in legacy.items():
            if str(receipt_id).startswith("bridge:"):
                current.setdefault(str(receipt_id), deepcopy(receipt))
    state.pop(ai_status.LEGACY_OPERATOR_ASSERTION_KEYS[1], None)
    return current


def verify_signed_dev_bridge_packet(
    batch: Mapping[str, Any], *, state: dict[str, Any] | None = None
) -> None:
    """Verify BFF packet authority and optionally consume it atomically."""
    ai_status = _ai_status_module()

    packet = batch.get("signed_packet")
    if not isinstance(packet, Mapping):
        raise SystemExit("Dev bridge signed packet is missing")
    signature = packet.get("signature")
    if not isinstance(signature, Mapping):
        raise SystemExit("Dev bridge signed packet signature is missing")
    if signature.get("algorithm") != "Ed25519":
        raise SystemExit("Dev bridge signed packet signature algorithm is invalid")
    raw_keys = str(os.environ.get("BRIDGE_SIGNING_PUBLIC_KEYS_JSON") or "").strip()
    if not raw_keys:
        raise SystemExit(
            "BRIDGE_SIGNING_PUBLIC_KEYS_JSON is required; no dev fallback may authorize canonical mutation"
        )
    try:
        public_keys = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        raise SystemExit("Dev bridge public key policy is invalid JSON") from exc
    if not isinstance(public_keys, Mapping) or not public_keys:
        raise SystemExit("Dev bridge public key policy must contain at least one key")
    key_id = str(signature.get("key_id") or signature.get("keyId") or "").strip()
    encoded_public_key = public_keys.get(key_id)
    if not isinstance(encoded_public_key, str):
        raise SystemExit("Dev bridge signed packet key is not trusted")
    body = deepcopy(dict(packet))
    body.pop("signature", None)
    canonical = json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    try:
        public_key = base64.urlsafe_b64decode(
            encoded_public_key + "=" * (-len(encoded_public_key) % 4)
        )
        signature_value = str(signature.get("value") or "")
        signature_bytes = base64.urlsafe_b64decode(
            signature_value + "=" * (-len(signature_value) % 4)
        )
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature_bytes,
            canonical,
        )
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise SystemExit("Dev bridge signed packet verification failed")
    digest = hashlib.sha256(canonical).hexdigest()
    if packet.get("packet_id") != batch.get("packet_id") or digest != batch.get("packet_digest"):
        raise SystemExit("Dev bridge signed packet identity binding failed")
    now = datetime.now(timezone.utc)
    source = packet.get("actor")
    if not isinstance(source, Mapping):
        raise SystemExit("Dev bridge packet actor is required")
    work_class = str(
        packet.get("work_class") or packet.get("workClass") or "security"
    ).strip().lower()
    if work_class not in ai_status.DEV_BRIDGE_WORK_CLASSES:
        raise SystemExit(f"Dev bridge work class is invalid: {work_class!r}")
    # OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001 retired the former MFA-at-intake
    # rule: a correctly signed security/hosted/live packet may be
    # materialized without any operator grant.  It becomes a canonical
    # non-executable pending-authorization record instead
    # (execution_authorization.pending_authorization_hold, applied by
    # scripts/ai_status.py's command_assign when it sees a privileged
    # dev_bridge work_class).  Genuine, independently verified MFA is
    # enforced later, separately, at actual execution -- never here at
    # intake.  ``operator_authorization`` on the packet is no longer
    # consulted for admission; a legacy packet embedding it is accepted the
    # same way, and that embedded assertion is never treated as an implicit
    # or perpetual execution grant.  See
    # docs/04/pantheon_first_release_closure_2026-09-06/EXECUTION_AUTHORIZATION_SA_SD.md
    # section 2.
    #
    # Expiry bounds admission at the authenticated BFF boundary.  The signed
    # packet is the durable receipt; a queued packet may drain later without
    # turning supervisor wall-clock latency into an authorization failure.
    packet_tasks = packet.get("tasks")
    if not isinstance(packet_tasks, list) or len(packet_tasks) != len(batch["tasks"]):
        raise SystemExit("Dev bridge signed packet task count does not match batch")
    for index, (packet_task, row) in enumerate(zip(packet_tasks, batch["tasks"])):
        if not isinstance(packet_task, Mapping):
            raise SystemExit(f"Dev bridge signed packet task {index} is invalid")
        for field in ("id", "owner", "reviewer", "title"):
            row_field = "task_id" if field == "id" else field
            if packet_task.get(field) != row.get(row_field):
                raise SystemExit(
                    f"Dev bridge signed packet task {index} {field} binding failed"
                )
        if "target_repo" in packet_task and "targetRepo" in packet_task:
            if str(packet_task.get("target_repo") or "").strip() != str(packet_task.get("targetRepo") or "").strip():
                raise SystemExit(
                    f"Dev bridge signed packet task {index} has conflicting target_repo and targetRepo"
                )
        packet_task_repo = str(packet_task.get("target_repo") or packet_task.get("targetRepo") or "").strip()
        spec_repo = str(
            ((row.get("task_metadata") or {}).get("dev_bridge") or {})
            .get("task_spec", {})
            .get("target_repo")
            or ""
        ).strip()
        if packet_task_repo != spec_repo:
            raise SystemExit(
                f"Dev bridge signed packet task {index} target_repo binding failed"
            )
    privileged_work_class = work_class not in ai_status.DEV_BRIDGE_FUNCTIONAL_WORK_CLASSES
    if state is not None and privileged_work_class:
        try:
            consumed = dev_bridge_replay_ledger(state)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        if state.get(ai_status.DEV_BRIDGE_CONSUMED_KEY) is not consumed:
            raise SystemExit("Dev bridge replay ledger is invalid")
        cutoff = now - timedelta(days=7)
        for consumed_id, record in list(consumed.items()):
            consumed_at = (
                ai_status._parse_utc_timestamp(record.get("consumed_at"))
                if isinstance(record, Mapping)
                else None
            )
            if consumed_at is None or consumed_at < cutoff:
                consumed.pop(consumed_id, None)
        if len(consumed) >= 2048:
            ordered = sorted(
                consumed,
                key=lambda item: str(consumed[item].get("consumed_at") or ""),
            )
            for consumed_id in ordered[: len(consumed) - 2047]:
                consumed.pop(consumed_id, None)
        # No genuine operator/MFA nonce exists at intake any more (SA/SD 2):
        # this is now plain packet-identity replay protection for privileged
        # classes, keyed by the source's own idempotent packet_id (not the
        # recomputed signature digest, which legitimately varies with
        # emitted_at on a resubmitted packet that otherwise reuses the same
        # packet_id).
        assertion_id = f"bridge:{batch['packet_id']}"
        if assertion_id in consumed:
            raise SystemExit("Dev bridge signed privileged packet was already consumed")
        consumed[assertion_id] = {
            "packet_digest": batch["packet_digest"],
            "task_id": batch["packet_id"],
            "action": ai_status.DEV_BRIDGE_BATCH_MATERIALIZE_COMMAND,
            "consumed_at": iso_now(),
        }


def validate_dev_bridge_batch_dependency_closure(
    state: Mapping[str, Any], batch: Mapping[str, Any]
) -> None:
    """Require every new dependency to be canonical at the batch boundary.

    Scheduler reads never fall back to the human archive.  The bridge must
    therefore reject a packet whose dependency is neither an active task, a
    durable terminal fact, nor another row in this same atomic packet.
    """
    ai_status = _ai_status_module()

    active_ids = {
        str(task.get("id") or "").strip()
        for task in (state.get("tasks") or [])
        if isinstance(task, Mapping) and str(task.get("id") or "").strip()
    }
    terminal_ids = {
        str(task_id).strip()
        for task_id in (state.get(ai_status.TERMINAL_FACTS_KEY) or {})
        if str(task_id).strip()
    }
    batch_ids = {
        str(row.get("task_id") or "").strip()
        for row in (batch.get("tasks") or [])
        if isinstance(row, Mapping) and str(row.get("task_id") or "").strip()
    }
    for row in batch.get("tasks") or []:
        if not isinstance(row, Mapping):
            raise SystemExit("Dev bridge materialize batch row is invalid")
        task_id = str(row.get("task_id") or "").strip()
        if (
            task_id in terminal_ids
            or ai_status.load_archived_snapshot(task_id) is not None
        ):
            raise SystemExit(
                f"Cannot materialize task {task_id}: task is already terminal/archived"
            )
        bridge = ((row.get("task_metadata") or {}).get("dev_bridge") or {})
        task_spec = bridge.get("task_spec") if isinstance(bridge, Mapping) else None
        dependencies = task_spec.get("depends_on") if isinstance(task_spec, Mapping) else None
        if not isinstance(dependencies, list):
            raise SystemExit(
                f"Dev bridge task {task_id or '(unknown)'} has invalid dependency declaration"
            )
        missing = sorted(
            {
                dependency
                for dependency in (str(item or "").strip() for item in dependencies)
                if dependency
                and dependency != task_id
                and dependency not in active_ids
                and dependency not in terminal_ids
                and dependency not in batch_ids
            }
        )
        if missing:
            raise SystemExit(
                f"Dev bridge task {task_id} has unresolved canonical dependencies: "
                + ", ".join(missing)
            )


@contextmanager
def dev_bridge_materialize_mutation_environment(row: Mapping[str, Any], actor: str):
    """Bind one packet task row's signed metadata for exactly one assign call."""
    ai_status = _ai_status_module()

    tracked = ("AI_NAME", "TASK_METADATA_JSON", "TASK_TITLE", "TASK_NEXT")
    previous = {name: os.environ.get(name) for name in tracked}
    previous_active = getattr(_DEV_BRIDGE_MATERIALIZATION_LOCAL, "active", None)
    try:
        for name in tracked:
            os.environ.pop(name, None)
        os.environ["AI_NAME"] = actor
        os.environ["TASK_METADATA_JSON"] = json.dumps(
            row["task_metadata"],
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        os.environ["TASK_TITLE"] = str(row["title"])
        if row.get("assignment_next"):
            os.environ["TASK_NEXT"] = str(row["assignment_next"])
        _DEV_BRIDGE_MATERIALIZATION_LOCAL.active = True
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        if previous_active is None:
            try:
                delattr(_DEV_BRIDGE_MATERIALIZATION_LOCAL, "active")
            except AttributeError:
                pass
        else:
            _DEV_BRIDGE_MATERIALIZATION_LOCAL.active = previous_active
        ai_status._clear_status_command_lease_binding()


def run_dev_bridge_materialize_batch(
    state: dict[str, Any],
    batch: Mapping[str, Any],
    *,
    commands: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Materialize every packet task against one in-memory canonical snapshot.

    A row that raises (owner==reviewer, wave-guard rejection, artifact-scope
    conflict, forged/mismatched bridge provenance, ...) propagates immediately.
    The caller's transaction never reaches the single commit at the end of the
    batch, so a second-row failure commits nothing -- not even the first row.
    """
    ai_status = _ai_status_module()

    actor = str(batch["actor"])
    results: list[dict[str, Any]] = []
    for row in batch["tasks"]:
        task_id = str(row["task_id"])
        existing = ai_status.get_task(state, task_id)
        if existing is not None and ai_status.pending_review_decision_intent(existing) is not None:
            raise SystemExit(
                f"Dev bridge materialization is fenced by pending review decision "
                f"intent for {task_id}"
            )
        with dev_bridge_materialize_mutation_environment(row, actor):
            outcome = commands["assign"](
                state,
                [task_id, row["owner"], row["reviewer"], row["title"]],
            )
        results.append({"task_id": task_id, "changed": outcome is not False})
    changed_count = sum(bool(item["changed"]) for item in results)
    if changed_count not in {0, len(results)}:
        raise SystemExit(
            "Dev bridge materialize batch found a partial pre-existing packet; "
            "refusing to commit a missing-task suffix"
        )
    return results


def read_dev_bridge_materialized_batch(
    state: dict[str, Any],
    batch: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Validate one whole packet directly from authoritative task state.

    This deliberately ignores live top-level owner/reviewer routing. The
    originally signed assignment and every other immutable packet field remain
    frozen inside ``dev_bridge`` and must match the batch payload byte-for-byte.
    """
    ai_status = _ai_status_module()

    results: list[dict[str, Any]] = []
    immutable_fields = {
        "id": "id",
        "title": "title",
        "target_repo": "target_repo",
        "phase": "phase",
        "depends_on": "depends_on",
        "dependency_tracks": "dependency_tracks",
        "execution_resources": "execution_resources",
        "artifacts": "artifacts",
        "acceptance": "acceptance",
        "summary": "summary_zh",
    }
    for row in batch["tasks"]:
        task_id = str(row["task_id"])
        task = ai_status.get_task(state, task_id)
        if task is None:
            raise SystemExit(
                f"Dev bridge materialize readback task is missing: {task_id}"
            )
        metadata = deepcopy(row["task_metadata"])
        expected_bridge = ai_status._bridge_assignment_from_metadata(
            metadata,
            task_id=task_id,
            owner=ai_status.canonical_agent_name(str(row["owner"])),
            reviewer=ai_status.canonical_agent_name(str(row["reviewer"])),
            title=str(row["title"]),
        )
        if expected_bridge is None or task.get("dev_bridge") != expected_bridge:
            raise SystemExit(
                f"Dev bridge materialize readback provenance mismatch: {task_id}"
            )
        signed_spec = expected_bridge["task_spec"]
        for spec_field, task_field in immutable_fields.items():
            expected = signed_spec.get(spec_field)
            observed = task.get(task_field)
            # ``dependency_tracks`` and ``execution_resources`` were added after
            # the bridge packet schema shipped.  Old signed packets legitimately
            # omit them while materialization stores the canonical empty map /
            # list; normalize those compatibility cases without weakening explicit
            # values.
            if spec_field == "dependency_tracks" and spec_field not in signed_spec:
                expected = {}
            if spec_field == "execution_resources" and spec_field not in signed_spec:
                expected = []
            if spec_field == "phase":
                expected = expected or "Unassigned"
            if spec_field == "target_repo" and spec_field not in signed_spec:
                expected = task.get("target_repo")
            if spec_field in {"depends_on", "artifacts", "acceptance", "execution_resources"}:
                expected = list(expected or [])
                observed = list(observed or []) if isinstance(observed, list) else observed
            if observed != expected:
                raise SystemExit(
                    "Dev bridge materialize readback immutable task-spec mismatch: "
                    f"{task_id}.{spec_field}"
                )
        if execution_authorization.is_privileged_work_class(expected_bridge.get("work_class")):
            authorization = task.get("execution_authorization")
            policy = authorization.get("policy") if isinstance(authorization, Mapping) else None
            if not execution_authorization.execution_policy_matches_task(task, policy=policy):
                raise SystemExit(
                    f"Dev bridge materialize readback execution-policy mismatch: {task_id}"
                )
        results.append(
            {
                "taskId": task_id,
                "source": "active",
                "taskSpecHash": expected_bridge["task_spec_hash"],
            }
        )
    return results
