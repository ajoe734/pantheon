"""Execution-time MFA-bound authorization, separated from privileged intake.

OPS-PRIVILEGED-TASK-EXECUTION-AUTH-001. Source of record:
``docs/04/pantheon_first_release_closure_2026-09-06/EXECUTION_AUTHORIZATION_SA_SD.md``.

A correctly signed ``security``/``hosted``/``live`` dev-bridge task may be
materialized without any operator grant (see
``development_bridge/dev_bridge_materialize.py``); it becomes a canonical
non-executable ``pending_authorization`` record instead. This module owns the
one place that later, separately, decides whether that same task may actually
execute: it derives the immutable execution policy, verifies an
independently-issued MFA-bound grant against that policy, and enforces
one-shot consumption so a grant can authorize exactly one dispatch attempt.

This module has no filesystem, subprocess, or supervisor dependency and does
not read wall-clock time itself (callers pass ``now``); it is intentionally as
hermetic as ``rewrite/dispatch_admission.py``, which is the sole consumer of
:func:`is_execution_authorized` on the planner/delivery side. Canonical state
mutation (holding the task-state lock, committing to the authoritative journal)
stays with the imperative callers in ``scripts/ai_status.py`` and
``supervisor.py``, exactly as ``rewrite/dispatch_admission.py``'s own
docstring describes for that module's boundary.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Security/hosted/live retain the one-shot MFA-bound execution-authorization
# requirement; functional/paper/read_only/ci/reconcile_only never did and stay
# unaffected end-to-end (SA/SD 2). Kept as an independent literal from
# ``scripts/ai_status.py``'s ``DEV_BRIDGE_WORK_CLASSES`` family: this module
# must not import the CLI entrypoint, and the two lists are asserted equal by
# ``test_execution_authorization.py`` so they cannot silently diverge.
PRIVILEGED_WORK_CLASSES = frozenset({"security", "hosted", "live"})

# Distinct execution purpose/audience from packet-source signing so a
# packet-source key can never double as an MFA issuer (SA/SD 3).
EXECUTION_GRANT_PURPOSE = "pantheon.execution.mfa"
EXECUTION_GRANT_CAPABILITY = "assistant.canonical.execute"

# Mirrors the existing bridge operator-authorization start-freshness bound
# (dev_bridge_materialize.verify_signed_dev_bridge_packet) so both trust
# ladders share one reviewed freshness policy.
MAX_GRANT_START_FRESHNESS_SECONDS = 300
DEFAULT_RUN_TTL_SECONDS = 3600
MAX_RUN_TTL_SECONDS = 24 * 3600

STATE_PENDING = "pending_authorization"
STATE_GRANTED = "granted"
STATE_RESERVED = "reserved"
STATE_REVOKED = "revoked"

# A runtime that does not declare this capability predates the execution
# barrier entirely and must never be treated as able to safely run a pending
# or granted privileged record (SA/SD 2, "old-runtime-recognized durable
# hold").
RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION = "execution_authorization_v1"

# The current runtime's own declaration of which execution-authorization
# capabilities it has. Any runtime root that has this exact module revision
# on its import path declares this; an older command-runtime root -- one
# that predates this module, or an earlier copy of it without this constant
# -- has no such declaration at all. ``scripts/promote_supervisor_runtime.py``
# discover-only-probes a *candidate* runtime root's own interpreter for this
# constant (not the currently running supervisor's copy) before promoting it,
# so an old-runtime rollback is refused while any task carries a pending or
# granted privileged execution-authorization record (SA/SD 2, 6).
RUNTIME_CAPABILITIES = frozenset({RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION})


class ExecutionAuthorizationError(ValueError):
    """A grant, policy, or authorization state is invalid or insufficient."""


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExecutionAuthorizationError("execution authorization contains invalid JSON") from exc


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalized_resources(resources: Any) -> list[str]:
    if not isinstance(resources, (list, tuple, set, frozenset)):
        return []
    return sorted({str(item).strip() for item in resources if str(item).strip()})


# Alias: artifacts are normalized the same way (sorted, deduplicated,
# stripped strings) so both feed the same canonical-digest shape.
_normalized_artifacts = _normalized_resources


def is_privileged_work_class(work_class: Any) -> bool:
    return str(work_class or "").strip().lower() in PRIVILEGED_WORK_CLASSES


def runtime_supports_execution_authorization(capabilities: Any) -> bool:
    """Return whether a runtime declares the post-barrier capability flag.

    Used to refuse deferred-intake privileged dispatch on an
    old-runtime-recognized rollback target that predates both the intake and
    execution barriers (SA/SD 2 and 6).
    """

    if isinstance(capabilities, Mapping):
        # A mapping declares capability *values*, not just capability
        # *names*: a runtime that reports
        # ``{RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION: False}`` is
        # explicitly declaring the barrier absent, not present. Checking
        # only ``.keys()`` treated that false value as enabled solely
        # because the key existed.
        return bool(capabilities.get(RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION))
    if isinstance(capabilities, (list, tuple, set, frozenset)):
        return RUNTIME_CAPABILITY_EXECUTION_AUTHORIZATION in {
            str(value).strip() for value in capabilities
        }
    return False


def execution_policy_digest(
    *,
    task_id: Any,
    repository: Any,
    environment: Any,
    resources: Any,
    action_scope: Any,
    artifacts: Any = None,
    work_class: Any = None,
    task_spec_hash: Any = None,
) -> str:
    """Bind source classification, full signed specification, and execution scope."""

    payload = {
        "task_id": str(task_id or "").strip(),
        "repository": str(repository or "").strip(),
        "environment": str(environment or "").strip(),
        "resources": _normalized_resources(resources),
        "action_scope": str(action_scope or "").strip(),
        "artifacts": _normalized_artifacts(artifacts),
        "work_class": work_class,
        "task_spec_hash": task_spec_hash,
    }
    return _sha256_hex(_canonical_json(payload))


def derive_execution_policy(
    *,
    task_id: Any,
    work_class: Any,
    repository: Any,
    environment: Any = None,
    resources: Any = None,
    action_scope: Any = None,
    artifacts: Any = None,
    task_spec: Mapping[str, Any] | None = None,
    task_spec_hash: str | None = None,
) -> dict[str, Any]:
    """Derive the immutable execution policy for one task's exact contract.

    Canonical assignment, task metadata, reopen, recovery, and replay must
    never erase or weaken this: callers persist the returned mapping
    byte-for-byte and only ever replace it via a fresh call over the current
    signed task contract, never by editing individual fields (SA/SD 2).
    """

    normalized_class = str(work_class or "").strip().lower()
    environment_value = str(environment or "pantheon-dev").strip()
    action_scope_value = str(action_scope or "execute").strip()
    resources_list = _normalized_resources(resources)
    artifacts_list = _normalized_artifacts(artifacts)
    repository_value = str(repository or "").strip()
    if task_spec is not None:
        if not isinstance(task_spec, Mapping):
            raise ExecutionAuthorizationError("execution policy task_spec must be an object")
        actual_hash = _sha256_hex(_canonical_json(dict(task_spec)))
        if task_spec_hash is not None and task_spec_hash != actual_hash:
            raise ExecutionAuthorizationError("execution policy task_spec_hash mismatch")
        task_spec_hash = actual_hash
    digest = execution_policy_digest(
        task_id=task_id,
        repository=repository_value,
        environment=environment_value,
        resources=resources_list,
        action_scope=action_scope_value,
        artifacts=artifacts_list,
        work_class=normalized_class,
        task_spec_hash=task_spec_hash,
    )
    return {
        "work_class": normalized_class,
        "requires_execution_authorization": is_privileged_work_class(normalized_class),
        "repository": repository_value,
        "environment": environment_value,
        "resources": resources_list,
        "artifacts": artifacts_list,
        "action_scope": action_scope_value,
        "policy_digest": digest,
        "task_spec_hash": task_spec_hash,
        "source_owner": (task_spec or {}).get("owner"),
        "source_reviewer": (task_spec or {}).get("reviewer"),
    }


def pending_authorization_hold(policy: Mapping[str, Any]) -> dict[str, Any]:
    """Return the durable non-executable subrecord attached at intake.

    Dependency completion alone can never launch this: dispatch admission's
    :func:`is_execution_authorized` only returns ``True`` once a verified
    grant has separately reached ``STATE_GRANTED`` (SA/SD 5, positive case 2).
    """

    if not policy.get("requires_execution_authorization"):
        raise ExecutionAuthorizationError(
            "pending_authorization_hold requires a privileged execution policy"
        )
    return {
        "state": STATE_PENDING,
        "policy": deepcopy(dict(policy)),
        "old_runtime_hold": True,
        "grant": None,
        "reserved_run_id": None,
        "reserved_at": None,
    }


def _parse_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _verify_ed25519(
    body: Mapping[str, Any],
    signature: Mapping[str, Any],
    *,
    trusted_issuers: Mapping[str, str],
) -> str:
    key_id = str(signature.get("key_id") or "").strip()
    encoded_key = trusted_issuers.get(key_id)
    if not isinstance(encoded_key, str) or not encoded_key.strip():
        raise ExecutionAuthorizationError("execution grant issuer is not trusted")
    canonical = _canonical_json(body)
    try:
        public_key_bytes = base64.urlsafe_b64decode(
            encoded_key + "=" * (-len(encoded_key) % 4)
        )
        signature_value = str(signature.get("value") or "")
        signature_bytes = base64.urlsafe_b64decode(
            signature_value + "=" * (-len(signature_value) % 4)
        )
        Ed25519PublicKey.from_public_bytes(public_key_bytes).verify(
            signature_bytes, canonical
        )
    except (TypeError, ValueError, binascii.Error, InvalidSignature) as exc:
        raise ExecutionAuthorizationError(
            "execution grant signature verification failed"
        ) from exc
    return _sha256_hex(public_key_bytes)


def verify_execution_grant(
    grant: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    task_id: Any,
    generation: Any,
    trusted_issuers: Mapping[str, str],
    now: datetime,
    task: Mapping[str, Any] | None = None,
) -> str:
    """Verify one signed execution-authorization grant against exact policy.

    ``trusted_issuers`` maps ``key_id`` to a base64url-encoded Ed25519 public
    key. It is a distinct trust root from the dev-bridge packet-source keys
    (``BRIDGE_SIGNING_PUBLIC_KEYS_JSON``): a packet-source key must never be
    accepted here (SA/SD 3). Returns the successfully verified public-key
    fingerprint for durable nonce consumption. Raises
    :class:`ExecutionAuthorizationError` on any failure.
    """

    if not isinstance(grant, Mapping):
        raise ExecutionAuthorizationError("execution grant is missing")
    if not isinstance(policy, Mapping) or policy.get("requires_execution_authorization") is not True:
        raise ExecutionAuthorizationError(
            "execution grant is not applicable to a non-privileged policy"
        )
    signature = grant.get("signature")
    if not isinstance(signature, Mapping) or signature.get("algorithm") != "Ed25519":
        raise ExecutionAuthorizationError(
            "execution grant signature is missing or invalid"
        )
    if not isinstance(trusted_issuers, Mapping) or not trusted_issuers:
        raise ExecutionAuthorizationError(
            "no trusted MFA issuer is configured; grant submission stays closed"
        )
    if not _policy_is_well_formed(policy):
        raise ExecutionAuthorizationError("execution policy is missing or corrupt")
    expected_digest = execution_policy_digest(
        task_id=task_id, repository=policy["repository"], environment=policy["environment"],
        resources=policy["resources"], action_scope=policy["action_scope"],
        artifacts=policy["artifacts"], work_class=policy["work_class"],
        task_spec_hash=policy["task_spec_hash"],
    )
    if expected_digest != policy["policy_digest"]:
        raise ExecutionAuthorizationError("execution policy digest is corrupt")
    if task is not None and not execution_policy_matches_task(task, policy=policy):
        raise ExecutionAuthorizationError("execution policy does not match current signed task contract")
    body = deepcopy(dict(grant))
    body.pop("signature", None)
    issuer_fingerprint = _verify_ed25519(body, signature, trusted_issuers=trusted_issuers)

    if str(grant.get("purpose") or "").strip() != EXECUTION_GRANT_PURPOSE:
        raise ExecutionAuthorizationError("execution grant purpose is invalid")
    if str(grant.get("capability") or "").strip() != EXECUTION_GRANT_CAPABILITY:
        raise ExecutionAuthorizationError("execution grant capability is invalid")
    if str(grant.get("audience") or "").strip() != str(task_id or "").strip():
        raise ExecutionAuthorizationError("execution grant audience does not match task")
    if grant.get("mfa_verified") is not True:
        raise ExecutionAuthorizationError(
            "execution grant requires an independently verified MFA assertion"
        )
    if not isinstance(grant.get("mfa_actor"), str) or not grant["mfa_actor"].strip():
        raise ExecutionAuthorizationError("execution grant MFA actor identity is required")

    if str(grant.get("task_id") or "").strip() != str(task_id or "").strip():
        raise ExecutionAuthorizationError("execution grant task_id mismatch")
    grant_generation = grant.get("generation")
    if type(grant_generation) is not int or grant_generation < 0:
        raise ExecutionAuthorizationError("execution grant generation must be an integer")
    if type(generation) is not int or grant_generation != generation:
        raise ExecutionAuthorizationError("execution grant generation mismatch")
    if str(grant.get("policy_digest") or "").strip() != str(policy.get("policy_digest") or "").strip():
        raise ExecutionAuthorizationError("execution grant policy_digest mismatch")
    if str(grant.get("repository") or "").strip() != str(policy.get("repository") or "").strip():
        raise ExecutionAuthorizationError("execution grant repository mismatch")
    if str(grant.get("environment") or "").strip() != str(policy.get("environment") or "").strip():
        raise ExecutionAuthorizationError("execution grant environment mismatch")
    if str(grant.get("action_scope") or "").strip() != str(policy.get("action_scope") or "").strip():
        raise ExecutionAuthorizationError("execution grant action_scope mismatch")
    if not _is_string_list(grant.get("resources")) or _normalized_resources(grant.get("resources")) != policy["resources"]:
        raise ExecutionAuthorizationError("execution grant resources mismatch")

    nonce = grant.get("nonce")
    if not isinstance(nonce, str) or not nonce.strip():
        raise ExecutionAuthorizationError("execution grant nonce is required")

    issued = _parse_utc(grant.get("issued_at"))
    expires = _parse_utc(grant.get("expires_at"))
    if issued is None or expires is None or expires <= issued:
        raise ExecutionAuthorizationError("execution grant lifetime is invalid")
    if (expires - issued).total_seconds() > MAX_GRANT_START_FRESHNESS_SECONDS:
        raise ExecutionAuthorizationError(
            "execution grant start-freshness window exceeds the maximum"
        )
    if now < issued:
        raise ExecutionAuthorizationError("execution grant is not yet valid")
    if now >= expires:
        raise ExecutionAuthorizationError("execution grant has expired")

    run_ttl_seconds = grant.get("run_ttl_seconds", DEFAULT_RUN_TTL_SECONDS)
    if type(run_ttl_seconds) is not int:
        raise ExecutionAuthorizationError("execution grant run_ttl_seconds is invalid")
    if run_ttl_seconds <= 0 or run_ttl_seconds > MAX_RUN_TTL_SECONDS:
        raise ExecutionAuthorizationError("execution grant run_ttl_seconds is out of bounds")
    return issuer_fingerprint


def consume_grant_nonce(
    ledger: dict[str, Any],
    grant: Mapping[str, Any],
    *,
    task_id: Any,
    now: datetime,
    issuer_fingerprint: str,
) -> None:
    """Atomically spend a grant's one-shot nonce against a durable ledger.

    The caller holds the canonical task-state lock and owns ``ledger``
    in-place mutation and its own commit; this function only decides replay
    eligibility. ``issuer_fingerprint`` comes from verify_execution_grant,
    never from caller-supplied signature routing metadata. A second submission of the same signed grant -- to
    this task or any other -- is rejected (SA/SD 3, "one-shot nonce").
    """

    if not isinstance(ledger, dict) or not isinstance(grant, Mapping):
        raise ExecutionAuthorizationError("execution grant replay ledger or grant is invalid")
    nonce = grant.get("nonce")
    if not isinstance(nonce, str) or not nonce.strip():
        raise ExecutionAuthorizationError("execution grant nonce is required")
    signature = grant.get("signature")
    if not isinstance(signature, Mapping) or not isinstance(signature.get("key_id"), str):
        raise ExecutionAuthorizationError("execution grant issuer signature is invalid")
    if not isinstance(issuer_fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", issuer_fingerprint):
        raise ExecutionAuthorizationError("verified execution issuer fingerprint is required")
    nonce = nonce.strip()
    # key_id is unsigned routing metadata; multiple configured aliases may
    # resolve to the same issuer key. Only the successfully verified key's
    # fingerprint supplies stable replay identity across those aliases.
    assertion_id = _sha256_hex(f"{issuer_fingerprint}:{nonce}".encode("utf-8"))
    if assertion_id in ledger:
        raise ExecutionAuthorizationError("execution grant nonce was already consumed")
    ledger[assertion_id] = {
        "task_id": str(task_id or ""),
        "consumed_at": now.isoformat().replace("+00:00", "Z"),
    }


def build_granted_authorization(
    *,
    policy: Mapping[str, Any],
    grant: Mapping[str, Any],
    task: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the redacted, durable ``STATE_GRANTED`` subrecord to persist.

    Only redacted references are kept -- exact scalar bindings and a
    verified-actor identity, never a bearer token, private key, or credential
    hash (SA/SD 3).
    """

    return {
        "state": STATE_GRANTED,
        "policy": deepcopy(dict(policy)),
        "old_runtime_hold": True,
        "grant": {
            "task_id": str(grant.get("task_id") or ""),
            "generation": int(grant.get("generation")),
            "policy_digest": str(grant.get("policy_digest") or ""),
            "repository": str(grant.get("repository") or ""),
            "environment": str(grant.get("environment") or ""),
            "resources": _normalized_resources(grant.get("resources")),
            "action_scope": str(grant.get("action_scope") or ""),
            "mfa_actor": str(grant.get("mfa_actor") or ""),
            "mfa_issuer_key_id": str((grant.get("signature") or {}).get("key_id") or ""),
            "issued_at": str(grant.get("issued_at") or ""),
            "expires_at": str(grant.get("expires_at") or ""),
            "run_ttl_seconds": int(grant.get("run_ttl_seconds", DEFAULT_RUN_TTL_SECONDS)),
            "owner": task.get("owner") if task is not None else policy.get("source_owner"),
            "reviewer": task.get("reviewer") if task is not None else policy.get("source_reviewer"),
        },
        "reserved_run_id": None,
        "reserved_at": None,
    }


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)


def _policy_is_well_formed(policy: Mapping[str, Any]) -> bool:
    if policy.get("requires_execution_authorization") is not True:
        return False
    if not isinstance(policy.get("work_class"), str) or policy["work_class"] not in PRIVILEGED_WORK_CLASSES:
        return False
    for field in ("repository", "environment", "action_scope", "source_owner", "source_reviewer"):
        if not isinstance(policy.get(field), str) or not policy[field].strip():
            return False
    for field in ("policy_digest", "task_spec_hash"):
        if not isinstance(policy.get(field), str) or not re.fullmatch(r"[0-9a-f]{64}", policy[field]):
            return False
    for field in ("resources", "artifacts"):
        if not _is_string_list(policy.get(field)) or policy[field] != _normalized_resources(policy[field]):
            return False
    return True


def execution_policy_matches_task(task: Mapping[str, Any], *, policy: Mapping[str, Any]) -> bool:
    """Validate the frozen signed specification and its current canonical projection.

    Assignment is allowed to change through its generation-bound transition;
    all other signed fields retain the exact intake contract. The grant also
    snapshots the current assignment, preventing a silent owner swap.
    """
    if not isinstance(policy, Mapping) or not _policy_is_well_formed(policy):
        return False
    bridge = task.get("dev_bridge")
    if not isinstance(bridge, Mapping) or bridge.get("work_class") != policy["work_class"]:
        return False
    if bridge.get("operator_authorization_required", True) is not True:
        return False
    spec = bridge.get("task_spec")
    if not isinstance(spec, Mapping):
        return False
    if bridge.get("task_spec_hash") != policy["task_spec_hash"]:
        return False
    try:
        if _sha256_hex(_canonical_json(dict(spec))) != policy["task_spec_hash"]:
            return False
        for field in ("id", "title", "target_repo", "owner", "reviewer"):
            if not isinstance(spec.get(field), str) or not spec[field].strip():
                return False
        for field in ("depends_on", "artifacts", "acceptance"):
            if not _is_string_list(spec.get(field)):
                return False
        for field, source_field in (("source_owner", "owner"), ("source_reviewer", "reviewer")):
            if policy[field] != spec[source_field]:
                return False
        for field in ("owner", "reviewer"):
            if not isinstance(task.get(field), str) or not task[field].strip():
                return False
        for field, value in spec.items():
            if field in {"owner", "reviewer"}:
                continue
            if field == "summary":
                current = task.get("summary_zh")
            elif field == "phase":
                current = task.get("phase")
                value = value or "Unassigned"
            else:
                current = task.get(field)
            if current != value:
                return False
        for field, default in (("dependency_tracks", {}), ("execution_resources", [])):
            if field not in spec and task.get(field, default) != default:
                return False
        if policy["repository"] != task.get("target_repo"):
            return False
        if policy["resources"] != _normalized_resources(task.get("execution_resources")):
            return False
        if policy["artifacts"] != _normalized_artifacts(task.get("artifacts")):
            return False
        current_digest = execution_policy_digest(
            task_id=task.get("id"), repository=task.get("target_repo"),
            environment=policy["environment"], resources=task.get("execution_resources"),
            action_scope=policy["action_scope"], artifacts=task.get("artifacts"),
            work_class=bridge["work_class"], task_spec_hash=bridge["task_spec_hash"],
        )
    except (ExecutionAuthorizationError, TypeError, ValueError):
        return False
    return current_digest == policy["policy_digest"]


def task_privileged_by_source(task: Mapping[str, Any]) -> bool:
    """Return the task's ground-truth privileged classification.

    Derived from the durable, verified dev-bridge packet provenance
    (``task.dev_bridge.work_class``), never from whether an
    ``execution_authorization`` subrecord happens to be present. Canonical
    assignment, metadata, reopen, recovery, and replay can drop or corrupt
    the subrecord; they cannot change what packet the task was actually
    materialized from (SA/SD 2). Public so callers outside this module
    (``supervisor.py``'s authoritative reserve/launch boundary) can apply the
    same source-derived fail-closed verdict instead of trusting a possibly
    stale or corrupt ``execution_authorization`` snapshot.
    """

    dev_bridge = task.get("dev_bridge")
    if dev_bridge is None:
        return False
    if not isinstance(dev_bridge, Mapping):
        return True
    work_class = dev_bridge.get("work_class")
    return (
        not isinstance(work_class, str)
        or work_class not in {"functional", "paper", "read_only", "ci", "reconcile_only"}
        or dev_bridge.get("operator_authorization_required") is True
    )


# Retained as a private alias: this module's own callers below predate the
# public rename and an external audit trail may still reference the old name.
_task_privileged_by_source = task_privileged_by_source


def _grant_matches_current_scope(
    task: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    grant: Mapping[str, Any],
) -> bool:
    """Shared exact-binding check used by both the grant and the reservation gate.

    A grant only ever authorizes the exact task generation, policy digest,
    and current target/resources/artifact scope it was verified against
    (SA/SD 3, "reassignment, scope or target change invalidates it"). Used
    identically by :func:`is_execution_authorized` (does the outstanding
    grant still apply) and :func:`reservation_is_current` (does the reserved
    grant still apply at actual worker entry) so neither can drift out of
    sync with the other.
    """

    task_generation = task.get("generation", 0)
    if type(task_generation) is not int or task_generation < 0:
        return False
    if type(grant.get("generation")) is not int or grant.get("generation") != task_generation:
        return False
    if grant.get("task_id") != task.get("id"):
        return False
    for field in ("repository", "environment", "resources", "action_scope"):
        if grant.get(field) != policy.get(field):
            return False
    for field in ("owner", "reviewer"):
        if grant.get(field) != task.get(field):
            return False
    if str(grant.get("policy_digest") or "") != str(policy.get("policy_digest") or ""):
        return False
    # Frozen grant/policy agreement alone does not prove that the signed
    # specification still matches the canonical task about to execute.
    return execution_policy_matches_task(task, policy=policy)


def is_execution_authorized(
    task: Mapping[str, Any],
    *,
    now: datetime,
) -> bool:
    """Pure query: may ``task`` launch a privileged execution attempt now.

    Fed into the same normalized verdict consumed by both the planner and
    late delivery through ``rewrite/dispatch_admission.py`` (SA/SD 4). A task
    that is not privileged by its durable source provenance, and carries no
    execution-authorization subrecord, is always authorized -- ordinary
    functional/paper/read_only/ci/reconcile_only dispatch is unaffected.

    A task that *is* privileged by source provenance fails closed on a
    missing, malformed, or downgraded subrecord/policy instead of falling
    back to "authorized": a dropped subrecord, a corrupt policy shape, or an
    erased/downgraded ``requires_execution_authorization`` flag can never
    silently relabel privileged work as ordinary functional work.
    """

    privileged_by_source = task_privileged_by_source(task)
    record = task.get("execution_authorization")
    if record is None:
        return not privileged_by_source
    if not isinstance(record, Mapping):
        return False
    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        return False
    if policy.get("requires_execution_authorization") is not True:
        return False
    if record.get("state") != STATE_GRANTED:
        return False
    grant = record.get("grant")
    if not isinstance(grant, Mapping):
        return False
    if not _grant_matches_current_scope(task, policy=policy, grant=grant):
        return False
    expires = _parse_utc(grant.get("expires_at"))
    issued = _parse_utc(grant.get("issued_at"))
    ttl = grant.get("run_ttl_seconds")
    if (issued is None or expires is None or not issued <= now < expires
            or (expires - issued).total_seconds() > MAX_GRANT_START_FRESHNESS_SECONDS
            or type(ttl) is not int or not 0 < ttl <= MAX_RUN_TTL_SECONDS):
        return False
    return True


def reservation_is_current(
    task: Mapping[str, Any],
    *,
    run_id: Any,
    now: datetime,
) -> bool:
    """Pure query: may this exact ``run_id`` actually launch privileged work now.

    This is the direct worker-entry counterpart to :func:`is_execution_authorized`
    (SA/SD 4, "revalidate at actual worker entry before privileged execution,
    not only when a queue row was originally planned"). It requires the
    canonical claim/lease boundary
    (``supervisor.reserve_execution_authorization_for_launch``) to have
    already committed a ``STATE_RESERVED`` record bound to this exact
    ``run_id``, within its grant's bounded run lifetime -- a direct
    ``worker_runner`` invocation that bypasses that boundary, a replayed or
    stale run id, or an attempt outside the reserved run's TTL is rejected
    before any process launch. A non-privileged task is always current, same
    as :func:`is_execution_authorized`.

    Also re-applies :func:`_grant_matches_current_scope`: a reservation
    committed against one task generation/policy/scope must not still be
    treated as current once a reassignment, scope, or target revision lands
    after the reservation was made but before the worker actually enters
    (SA/SD 3, 4) -- the reservation boundary is not exempt from the same
    exact-binding rule the original grant is held to.
    """

    privileged_by_source = task_privileged_by_source(task)
    record = task.get("execution_authorization")
    if record is None:
        return not privileged_by_source
    if not isinstance(record, Mapping):
        return False
    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        return False
    if policy.get("requires_execution_authorization") is not True:
        return False
    if record.get("state") != STATE_RESERVED:
        return False
    if not isinstance(run_id, str) or not run_id.strip() or record.get("reserved_run_id") != run_id:
        return False
    grant = record.get("grant")
    if not isinstance(grant, Mapping):
        return False
    if not _grant_matches_current_scope(task, policy=policy, grant=grant):
        return False
    reserved_at = _parse_utc(record.get("reserved_at"))
    if reserved_at is None:
        return False
    run_ttl_seconds = grant.get("run_ttl_seconds")
    if type(run_ttl_seconds) is not int or not 0 < run_ttl_seconds <= MAX_RUN_TTL_SECONDS:
        return False
    issued = _parse_utc(grant.get("issued_at"))
    expires = _parse_utc(grant.get("expires_at"))
    if (issued is None or expires is None or not issued <= reserved_at < expires
            or (expires - issued).total_seconds() > MAX_GRANT_START_FRESHNESS_SECONDS):
        return False
    if now < reserved_at:
        return False
    if (now - reserved_at).total_seconds() >= run_ttl_seconds:
        return False
    return True


def is_execution_authorization_hold(task: Mapping[str, Any]) -> bool:
    """Identify only the old-runtime fence owned by this authorization record.

    An explicit blocker transfers ownership away from this fence. Normalizing
    it for the current dispatch predicate does not remove its persisted value.
    """
    record = task.get("execution_authorization")
    return bool(
        task.get("waiting_for") == "Human/Ops"
        and isinstance(task.get("status"), str)
        and task["status"] in {"todo", "in_progress", "review", "review_approved"}
        and isinstance(record, Mapping)
        and record.get("old_runtime_hold") is True
        and isinstance(record.get("state"), str)
        and record["state"] in {STATE_PENDING, STATE_GRANTED, STATE_RESERVED, STATE_REVOKED}
        and execution_policy_matches_task(task, policy=record.get("policy"))
    )


def execution_authorization_status(task: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    """Read-only authorization readback; never infer runnable or running state."""
    result = {
        "status": "invalid",
        "reason": "authorization_record_invalid",
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "authorizes_new_attempt": False,
        "reservation_current": False,
    }
    record = task.get("execution_authorization")
    if record is None:
        if not task_privileged_by_source(task):
            result.update(status="not_required", reason="ordinary_work", authorizes_new_attempt=True)
        else:
            result["reason"] = "authorization_record_missing"
        return result
    if not isinstance(record, Mapping):
        return result
    policy = record.get("policy")
    if not execution_policy_matches_task(task, policy=policy):
        result["reason"] = "signed_policy_or_current_task_contract_invalid"
        return result
    state = record.get("state")
    if not isinstance(state, str):
        return result
    if state == STATE_PENDING:
        result.update(status="admitted_pending_authorization", reason="genuine_execution_grant_required")
    elif state == STATE_REVOKED:
        result.update(status="revoked", reason="execution_grant_revoked")
    elif state == STATE_GRANTED and is_execution_authorized(task, now=now):
        result.update(status="authorization_ready", reason="fresh_grant_for_one_attempt", authorizes_new_attempt=True)
    elif state == STATE_RESERVED and reservation_is_current(task, run_id=record.get("reserved_run_id"), now=now):
        result.update(status="reserved_attempt", reason="reservation_current_without_process_evidence", reservation_current=True)
    elif state in {STATE_GRANTED, STATE_RESERVED}:
        grant = record.get("grant")
        if not isinstance(grant, Mapping) or not _grant_matches_current_scope(task, policy=policy, grant=grant):
            result["reason"] = "grant_binding_invalid"
        elif state == STATE_GRANTED:
            issued = _parse_utc(grant.get("issued_at"))
            expires = _parse_utc(grant.get("expires_at"))
            if issued is not None and expires is not None and issued < expires and now >= expires:
                result.update(status="expired", reason="grant_start_window_expired")
            elif issued is not None and now < issued:
                result["reason"] = "grant_not_yet_valid"
            else:
                result["reason"] = "grant_lifetime_invalid"
        else:
            reserved_at = _parse_utc(record.get("reserved_at"))
            ttl = grant.get("run_ttl_seconds")
            if reserved_at is not None and type(ttl) is int and 0 < ttl <= MAX_RUN_TTL_SECONDS and (now - reserved_at).total_seconds() >= ttl:
                result.update(status="expired", reason="reserved_run_lifetime_expired")
            else:
                result["reason"] = "reservation_binding_or_lifetime_invalid"
    return result


def reserve_execution_authorization(
    task: Mapping[str, Any],
    *,
    run_id: Any,
    now: datetime,
) -> dict[str, Any]:
    """Return the updated ``execution_authorization`` record for one launch.

    Callers persist the returned mapping under the existing exclusive
    task-state lock with a compare-and-swap against the record this function
    was given, so two concurrent launches racing the same ``STATE_GRANTED``
    snapshot cannot both win (SA/SD 4). Never mutates ``task`` in place.
    """

    if not isinstance(run_id, str) or not run_id.strip():
        raise ExecutionAuthorizationError("execution reservation run_id is required")
    if not is_execution_authorized(task, now=now):
        raise ExecutionAuthorizationError(
            "task is not currently execution-authorized; refusing to reserve"
        )
    record = task.get("execution_authorization")
    if not isinstance(record, Mapping):
        raise ExecutionAuthorizationError("task has no execution-authorization record to reserve")
    updated = deepcopy(dict(record))
    updated["state"] = STATE_RESERVED
    updated["reserved_run_id"] = str(run_id or "")
    updated["reserved_at"] = now.isoformat().replace("+00:00", "Z")
    return updated


def revoked_execution_authorization(
    task: Mapping[str, Any],
    *,
    actor: Any,
    now: datetime,
    reason: Any = None,
) -> dict[str, Any]:
    """Return the updated record after revocation.

    Revocation only prevents *new* unauthorized effects; it never declares an
    already-authorized, already-running attempt's compensation to be a
    confirmed rollback (SA/SD 4). Callers must still perform bounded,
    protocol-specific already-authorized compensation separately -- this
    function only stops future spend.
    """

    record = task.get("execution_authorization")
    if not isinstance(record, Mapping):
        raise ExecutionAuthorizationError("task has no execution-authorization record to revoke")
    updated = deepcopy(dict(record))
    updated["state"] = STATE_REVOKED
    updated["revoked_by"] = str(actor or "").strip()
    updated["revoked_at"] = now.isoformat().replace("+00:00", "Z")
    if reason is not None:
        updated["revoked_reason"] = str(reason).strip()
    return updated
