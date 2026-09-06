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
mutation (holding the task-state lock, committing to ``ai-status.json``)
stays with the imperative callers in ``scripts/ai_status.py`` and
``supervisor.py``, exactly as ``rewrite/dispatch_admission.py``'s own
docstring describes for that module's boundary.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
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
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


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
) -> str:
    """Digest one task's exact execution scope, including its artifact contract.

    ``artifacts`` is included so a canonical ``command_artifact_contract``
    revision changes this digest exactly like a ``command_execution_resource``
    revision does; :func:`is_execution_authorized` recomputes this digest
    against the *current* task on every call and fails closed on a mismatch,
    so either kind of scope revision invalidates an outstanding grant even
    when it does not also bump ``generation`` (SA/SD 3, "reassignment, scope
    or target change invalidates the grant").
    """

    payload = {
        "task_id": str(task_id or "").strip(),
        "repository": str(repository or "").strip(),
        "environment": str(environment or "").strip(),
        "resources": _normalized_resources(resources),
        "action_scope": str(action_scope or "").strip(),
        "artifacts": _normalized_artifacts(artifacts),
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
    digest = execution_policy_digest(
        task_id=task_id,
        repository=repository_value,
        environment=environment_value,
        resources=resources_list,
        action_scope=action_scope_value,
        artifacts=artifacts_list,
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
) -> None:
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
    except (ValueError, binascii.Error, InvalidSignature) as exc:
        raise ExecutionAuthorizationError(
            "execution grant signature verification failed"
        ) from exc


def verify_execution_grant(
    grant: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    task_id: Any,
    generation: Any,
    trusted_issuers: Mapping[str, str],
    now: datetime,
) -> None:
    """Verify one signed execution-authorization grant against exact policy.

    ``trusted_issuers`` maps ``key_id`` to a base64url-encoded Ed25519 public
    key. It is a distinct trust root from the dev-bridge packet-source keys
    (``BRIDGE_SIGNING_PUBLIC_KEYS_JSON``): a packet-source key must never be
    accepted here (SA/SD 3). Raises :class:`ExecutionAuthorizationError` with
    an actionable reason on any failure; never returns a partial verdict.
    """

    if not isinstance(grant, Mapping):
        raise ExecutionAuthorizationError("execution grant is missing")
    if not policy.get("requires_execution_authorization"):
        raise ExecutionAuthorizationError(
            "execution grant is not applicable to a non-privileged policy"
        )
    signature = grant.get("signature")
    if not isinstance(signature, Mapping) or signature.get("algorithm") != "Ed25519":
        raise ExecutionAuthorizationError(
            "execution grant signature is missing or invalid"
        )
    if not trusted_issuers:
        raise ExecutionAuthorizationError(
            "no trusted MFA issuer is configured; grant submission stays closed"
        )
    body = deepcopy(dict(grant))
    body.pop("signature", None)
    _verify_ed25519(body, signature, trusted_issuers=trusted_issuers)

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
    if not str(grant.get("mfa_actor") or "").strip():
        raise ExecutionAuthorizationError("execution grant MFA actor identity is required")

    if str(grant.get("task_id") or "").strip() != str(task_id or "").strip():
        raise ExecutionAuthorizationError("execution grant task_id mismatch")
    try:
        grant_generation = int(grant.get("generation"))
    except (TypeError, ValueError):
        raise ExecutionAuthorizationError("execution grant generation must be an integer")
    if grant_generation != int(generation or 0):
        raise ExecutionAuthorizationError("execution grant generation mismatch")
    if str(grant.get("policy_digest") or "").strip() != str(policy.get("policy_digest") or "").strip():
        raise ExecutionAuthorizationError("execution grant policy_digest mismatch")
    if str(grant.get("repository") or "").strip() != str(policy.get("repository") or "").strip():
        raise ExecutionAuthorizationError("execution grant repository mismatch")
    if str(grant.get("environment") or "").strip() != str(policy.get("environment") or "").strip():
        raise ExecutionAuthorizationError("execution grant environment mismatch")
    if str(grant.get("action_scope") or "").strip() != str(policy.get("action_scope") or "").strip():
        raise ExecutionAuthorizationError("execution grant action_scope mismatch")
    if _normalized_resources(grant.get("resources")) != list(policy.get("resources") or []):
        raise ExecutionAuthorizationError("execution grant resources mismatch")

    nonce = str(grant.get("nonce") or "").strip()
    if not nonce:
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
    if now > expires:
        raise ExecutionAuthorizationError("execution grant has expired")

    try:
        run_ttl_seconds = int(grant.get("run_ttl_seconds", DEFAULT_RUN_TTL_SECONDS))
    except (TypeError, ValueError):
        raise ExecutionAuthorizationError("execution grant run_ttl_seconds is invalid")
    if run_ttl_seconds <= 0 or run_ttl_seconds > MAX_RUN_TTL_SECONDS:
        raise ExecutionAuthorizationError("execution grant run_ttl_seconds is out of bounds")


def consume_grant_nonce(
    ledger: dict[str, Any],
    grant: Mapping[str, Any],
    *,
    task_id: Any,
    now: datetime,
) -> None:
    """Atomically spend a grant's one-shot nonce against a durable ledger.

    The caller holds the canonical task-state lock and owns ``ledger``
    in-place mutation and its own commit; this function only decides replay
    eligibility. A second submission of the exact same signed grant -- to
    this task or any other -- is rejected (SA/SD 3, "one-shot nonce").
    """

    nonce = str(grant.get("nonce") or "").strip()
    if not nonce:
        raise ExecutionAuthorizationError("execution grant nonce is required")
    key_id = str((grant.get("signature") or {}).get("key_id") or "").strip()
    assertion_id = _sha256_hex(f"{key_id}:{nonce}".encode("utf-8"))
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
        },
        "reserved_run_id": None,
        "reserved_at": None,
    }


def _task_privileged_by_source(task: Mapping[str, Any]) -> bool:
    """Return the task's ground-truth privileged classification.

    Derived from the durable, verified dev-bridge packet provenance
    (``task.dev_bridge.work_class``), never from whether an
    ``execution_authorization`` subrecord happens to be present. Canonical
    assignment, metadata, reopen, recovery, and replay can drop or corrupt
    the subrecord; they cannot change what packet the task was actually
    materialized from (SA/SD 2).
    """

    dev_bridge = task.get("dev_bridge")
    if not isinstance(dev_bridge, Mapping):
        return False
    return is_privileged_work_class(dev_bridge.get("work_class"))


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

    privileged_by_source = _task_privileged_by_source(task)
    record = task.get("execution_authorization")
    if record is None:
        return not privileged_by_source
    if not isinstance(record, Mapping):
        return False
    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        return False
    if not policy.get("requires_execution_authorization"):
        return not privileged_by_source
    if record.get("state") != STATE_GRANTED:
        return False
    grant = record.get("grant")
    if not isinstance(grant, Mapping):
        return False
    try:
        task_generation = int(task.get("generation", 0) or 0)
    except (TypeError, ValueError):
        return False
    if grant.get("generation") != task_generation:
        return False
    if str(grant.get("policy_digest") or "") != str(policy.get("policy_digest") or ""):
        return False
    # Recompute the digest against the task's *current* target/resources/
    # artifact contract rather than trusting the two frozen, previously
    # agreeing digests above. ``command_execution_resource`` and
    # ``command_artifact_contract`` revise a pre-dispatch task's scope
    # without bumping ``generation``; without this recomputation neither
    # frozen digest would ever change, so a scope revision made after grant
    # issuance would silently keep an outstanding grant valid.
    current_digest = execution_policy_digest(
        task_id=task.get("id"),
        repository=task.get("target_repo") or policy.get("repository"),
        environment=policy.get("environment"),
        resources=task.get("execution_resources"),
        action_scope=policy.get("action_scope"),
        artifacts=task.get("artifacts"),
    )
    if current_digest != str(policy.get("policy_digest") or ""):
        return False
    expires = _parse_utc(grant.get("expires_at"))
    if expires is None or now > expires:
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
    """

    privileged_by_source = _task_privileged_by_source(task)
    record = task.get("execution_authorization")
    if record is None:
        return not privileged_by_source
    if not isinstance(record, Mapping):
        return False
    policy = record.get("policy")
    if not isinstance(policy, Mapping):
        return False
    if not policy.get("requires_execution_authorization"):
        return not privileged_by_source
    if record.get("state") != STATE_RESERVED:
        return False
    if str(record.get("reserved_run_id") or "").strip() != str(run_id or "").strip():
        return False
    grant = record.get("grant")
    if not isinstance(grant, Mapping):
        return False
    reserved_at = _parse_utc(record.get("reserved_at"))
    if reserved_at is None:
        return False
    try:
        run_ttl_seconds = int(grant.get("run_ttl_seconds", DEFAULT_RUN_TTL_SECONDS))
    except (TypeError, ValueError):
        return False
    if run_ttl_seconds <= 0:
        return False
    if now < reserved_at:
        return False
    if (now - reserved_at).total_seconds() > run_ttl_seconds:
        return False
    return True


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

    if not is_execution_authorized(task, now=now):
        raise ExecutionAuthorizationError(
            "task is not currently execution-authorized; refusing to reserve"
        )
    record = task.get("execution_authorization")
    assert isinstance(record, Mapping)
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
