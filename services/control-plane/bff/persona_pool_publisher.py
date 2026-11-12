"""Publish a persona strategy-pool version as a new immutable envelope (R6).

Each pool version becomes one registry execution artifact that pins every
member's version, checksum and weight plus the pool version/digest, with
lineage to the previous envelope.  Approval, DeploymentPlan and the runtime
binding move all go through the existing owner paths via
``PersonaProvisioningCoordinator.publish_pool_envelope``; this module owns
only pool -> envelope validation, idempotency and the recorded outcome.

Fail closed: any mismatch records the reason and leaves the previous envelope
running (the binding is only ever moved by Deployment after the owner chain
verified).  Only paper scope is accepted.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol

from services.control_plane.persona.persona_strategy_pool import (
    PoolError,
    PoolNotFound,
    canonical_digest,
)
from services.registry.portfolio_envelope import build_portfolio_envelope
from services.registry.strategy_artifact import strategy_artifact_checksum

from .persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
    PoolDispatchUncertain,
    TERMINAL_RUNTIME_BINDING_STATUSES,
    _path_id,
    _registry_entry,
    deterministic_provisioning_ids,
)
from .persona_provisioning import ProvisioningRecord


class PoolPublishRefused(RuntimeError):
    """A publish was refused; the previous envelope keeps running."""

    def __init__(self, reason: str, *, status_code: int = 409, step: str = "validate") -> None:
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code
        self.step = step


class _PendingUncertain(Exception):
    """An owner read failed while an earlier dispatch may still be queued."""


class OwnerPoolReader:
    """Read pool versions from the persona owner API (never from BFF state).

    ``fetch(path, tenant_id)`` returns the owner JSON or ``None`` for 404.
    """

    def __init__(self, fetch: Callable[[str, str], Any]) -> None:
        self._fetch = fetch

    def _read(self, persona_id: str, tenant_id: str, suffix: str) -> dict[str, Any]:
        payload = self._fetch(f"/api/personas/{persona_id}/strategy-pool{suffix}", tenant_id)
        if not isinstance(payload, Mapping) or payload.get("members") is None:
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        return dict(payload)

    def current(self, persona_id: str, tenant_id: str) -> dict[str, Any]:
        return self._read(persona_id, tenant_id, "")

    def version(self, persona_id: str, tenant_id: str, number: int) -> dict[str, Any]:
        return self._read(persona_id, tenant_id, f"/versions/{int(number)}")


class PublishRecordStore(Protocol):
    def get(self, key: str) -> "dict[str, Any] | None": ...

    def compare_and_set(
        self, key: str, expected: "dict[str, Any] | None", new: "dict[str, Any]"
    ) -> "tuple[bool, Any]": ...


class InMemoryPublishRecords:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> "dict[str, Any] | None":
        value = self._data.get(key)
        return copy.deepcopy(value) if value is not None else None

    def compare_and_set(self, key, expected, new):
        if self._data.get(key) != expected:
            return False, copy.deepcopy(self._data.get(key))
        self._data[key] = copy.deepcopy(new)
        return True, None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PersonaPoolPublisher:
    def __init__(
        self,
        *,
        coordinator: PersonaProvisioningCoordinator,
        pool_store: Any,
        records: PublishRecordStore | None = None,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self.coordinator = coordinator
        self.pool_store = pool_store
        self.records = records or InMemoryPublishRecords()
        self.now = now

    # -- records -----------------------------------------------------------
    @staticmethod
    def _key(tenant_id: str, persona_id: str) -> str:
        return f"{tenant_id}:{persona_id}"

    def _load(self, tenant_id: str, persona_id: str) -> dict[str, Any]:
        return self.records.get(self._key(tenant_id, persona_id)) or {
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "published": {},
            "refusals": [],
            "current_envelope": None,
        }

    def _save(self, tenant_id: str, persona_id: str, mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        key = self._key(tenant_id, persona_id)
        for _ in range(8):
            prior = self.records.get(key)
            record = copy.deepcopy(prior) if prior is not None else self._load(tenant_id, persona_id)
            mutate(record)
            ok, _ = self.records.compare_and_set(key, prior, record)
            if ok:
                return record
        raise PoolPublishRefused("publish record contention", status_code=503, step="record")

    def history(self, tenant_id: str, persona_id: str) -> dict[str, Any]:
        return self._load(tenant_id, persona_id)

    def _refuse(self, tenant_id, persona_id, pool_version, reason, *, status_code, step):
        def mutate(record):
            record["refusals"].append(
                {
                    "at": self.now(),
                    "pool_version": pool_version,
                    "step": step,
                    "reason": reason,
                    "previous_envelope_kept": record.get("current_envelope"),
                }
            )

        self._save(tenant_id, persona_id, mutate)
        raise PoolPublishRefused(reason, status_code=status_code, step=step)

    def _refuse_pool_read(self, tenant_id, persona_id, pool_version, reason, status_code, step):
        try:
            self._refuse(
                tenant_id, persona_id, int(pool_version or 0), reason,
                status_code=status_code, step=step,
            )
        except PoolPublishRefused as exc:
            if exc.step != step:  # record contention must not mask the pool read cause
                raise PoolPublishRefused(reason, status_code=status_code, step=step) from exc
            raise

    # -- member snapshot ---------------------------------------------------
    def _member_entry(self, tenant_id: str, ref: str, checksum: str) -> Mapping[str, Any]:
        try:
            receipt = self.coordinator.transport.get(
                "registry", f"/api/registry/strategy-artifacts/{_path_id(ref)}"
            )
        except Exception as exc:  # owner HTTP/transport failure while reading a member
            raise PoolPublishRefused(
                f"member registry read unavailable: {type(exc).__name__}: {exc}",
                status_code=503, step="owner_unavailable",
            ) from exc
        if receipt is None:
            raise PoolPublishRefused(f"member {ref!r} is not a registry entry", step="member")
        entry = _registry_entry(receipt)
        if entry.get("owner_tenant") != tenant_id:
            # Never reveal another tenant's artifact.
            raise PoolPublishRefused(f"member {ref!r} is not a registry entry", step="member")
        if str(entry.get("artifact_state")) != "approved":
            raise PoolPublishRefused(
                f"member {ref!r} is not approved (state={entry.get('artifact_state')})",
                step="member",
            )
        if str(entry.get("checksum") or "") != checksum:
            raise PoolPublishRefused(
                f"member {ref!r} checksum does not match the registry entry", step="member"
            )
        body = (entry.get("metadata") or {}).get("strategy_artifact")
        if not isinstance(body, Mapping):
            raise PoolPublishRefused(f"member {ref!r} has no strategy artifact", step="member")
        # Independently validate the embedded body: the entry checksum alone
        # does not prove the artifact we are about to pin is the approved one.
        try:
            body_checksum = strategy_artifact_checksum(body)
        except Exception as exc:
            raise PoolPublishRefused(
                f"member {ref!r} embedded strategy artifact is invalid: {exc}", step="member"
            ) from exc
        if (
            body_checksum != checksum
            or body.get("artifact_id") != ref
            or str(body.get("version")) != str(entry.get("version"))
        ):
            raise PoolPublishRefused(
                f"member {ref!r} embedded strategy artifact does not match its "
                "registry checksum, identity or version",
                step="member",
            )
        return entry

    def _registry_view(self, artifact_id: str) -> "Mapping[str, Any] | None":
        if not artifact_id:
            return None
        try:
            receipt = self.coordinator.transport.get(
                "registry", f"/api/registry/strategy-artifacts/{_path_id(artifact_id)}"
            )
        except Exception as exc:  # owner HTTP/transport failure: nothing moved
            raise PoolPublishRefused(
                f"envelope registry read unavailable: {type(exc).__name__}: {exc}",
                status_code=503, step="owner_unavailable",
            ) from exc
        return _registry_entry(receipt) if receipt is not None else None

    def _guarded(self, tenant_id, persona_id, number, fn, pending=None):
        """Run an authoritative pre-dispatch owner read; fail closed on error.

        With ``pending`` (an earlier accepted dispatch that may still execute)
        an owner read failure cannot prove anything: keep the uncertainty.
        """
        try:
            return fn()
        except PoolPublishRefused as exc:
            if exc.step != "owner_unavailable":
                raise
            if pending is not None:
                raise _PendingUncertain(exc.reason) from exc
            self._refuse(tenant_id, persona_id, number, exc.reason,
                         status_code=exc.status_code, step=exc.step)
        except Exception as exc:
            if pending is not None:
                raise _PendingUncertain(
                    f"owner read unavailable: {type(exc).__name__}: {exc}"
                ) from exc
            self._refuse(
                tenant_id, persona_id, number,
                f"owner read unavailable: {type(exc).__name__}: {exc}",
                status_code=503, step="owner_unavailable",
            )

    # -- publish -----------------------------------------------------------
    def publish(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        requested_by: str,
        pool_version: int | None = None,
        stage: str = "paper",
    ) -> dict[str, Any]:
        if stage != "paper":
            raise PoolPublishRefused(
                "pool envelope publication is limited to paper scope", status_code=403, step="scope"
            )
        record = ProvisioningRecord(
            tenant_id=tenant_id, idempotency_key="pool-publish", request_hash="",
            normalized_name="", persona_id=persona_id, request_payload={},
        )
        ids = deterministic_provisioning_ids(record)
        try:
            pool = (
                self.pool_store.current(persona_id, tenant_id)
                if pool_version is None
                else self.pool_store.version(persona_id, tenant_id, pool_version)
            )
        except PoolError as exc:
            self._refuse_pool_read(
                tenant_id, persona_id, pool_version, str(exc), exc.status_code, "pool"
            )
        except PoolPublishRefused as exc:
            if exc.step == "owner_unavailable":
                return self._pool_read_unavailable(tenant_id, persona_id, pool_version, ids, exc.reason)
            self._refuse_pool_read(
                tenant_id, persona_id, pool_version, exc.reason, exc.status_code, exc.step
            )
        except Exception as exc:  # owner HTTP/transport failure: nothing moved
            return self._pool_read_unavailable(
                tenant_id, persona_id, pool_version, ids,
                f"persona owner pool read unavailable: {type(exc).__name__}: {exc}",
            )
        number = int(pool["pool_version"])
        digest = canonical_digest(pool)
        history = self._load(tenant_id, persona_id)
        done = history["published"].get(str(number))
        artifact_id = f"artifact-persona-pool-{ids.token}-v{number}"
        version = f"1.{number}.0"
        pending = done if (done or {}).get("status") == "dispatched" else None
        live_binding: Mapping[str, Any] = {}
        try:
            # Authoritative owner state, never the local record: the live binding
            # says what runs now and the Registry says what each envelope is.
            reader = self.coordinator.runtime_binding_reader
            bindings = self._guarded(
                tenant_id, persona_id, number,
                lambda: reader(ids.deployment_plan_id, ids.capital_pool_id) if reader else [],
                pending,
            )
            live = [
                b for b in bindings
                if b.get("capital_pool_id") == ids.capital_pool_id
                and b.get("status") not in TERMINAL_RUNTIME_BINDING_STATUSES
            ]
            if len(live) != 1:
                self._refuse(
                    tenant_id, persona_id, number,
                    f"expected exactly one live RuntimeBinding for the persona pool, got {len(live)}",
                    status_code=409, step="binding",
                )
            live_binding = live[0]
            existing = self._guarded(
                tenant_id, persona_id, number, lambda: self._registry_view(artifact_id), pending
            )
            if existing is not None:
                meta = existing.get("metadata") or {}
                if meta.get("pool_digest") != digest:
                    self._refuse(
                        tenant_id, persona_id, number,
                        "pool version content differs from the already published snapshot",
                        status_code=409, step="digest",
                    )
                previous = {
                    "artifact_id": meta.get("previous_envelope_id"),
                    "version": meta.get("previous_envelope_version"),
                }
            else:
                live_entry = self._guarded(
                    tenant_id, persona_id, number,
                    lambda: self._registry_view(live_binding.get("artifact_id") or ""),
                    pending,
                )
                live_pool = int(((live_entry or {}).get("metadata") or {}).get("pool_version") or 0)
                if number <= live_pool:
                    self._refuse(
                        tenant_id, persona_id, number,
                        f"pool version {number} is not newer than the running envelope (pool version {live_pool})",
                        status_code=409, step="order",
                    )
                previous = {
                    "artifact_id": live_binding.get("artifact_id"),
                    "version": live_binding.get("artifact_version"),
                }
            if not previous["artifact_id"] or not previous["version"]:
                self._refuse(
                    tenant_id, persona_id, number, "previous envelope cannot be identified",
                    status_code=409, step="previous",
                )

            try:
                envelope, member_pins = self._build_envelope(
                    tenant_id, ids, pool, artifact_id, version, number, digest, previous
                )
            except PoolPublishRefused as exc:
                if exc.step == "owner_unavailable" and pending is not None:
                    raise _PendingUncertain(exc.reason) from exc
                self._refuse(tenant_id, persona_id, number, exc.reason,
                             status_code=exc.status_code, step=exc.step)
        except _PendingUncertain as exc:
            return self._uncertain_from_history(
                tenant_id, persona_id, number, ids, exc.args[0], done=done
            )

        request_hash = canonical_digest(
            {"pool_digest": digest, "envelope": artifact_id, "previous": previous}
        )
        dispatch_note = None
        try:
            receipt = self.coordinator.publish_pool_envelope(
                {
                    "tenant_id": tenant_id,
                    "persona_id": persona_id,
                    "pool_version": number,
                    "pool_digest": digest,
                    "envelope": envelope,
                    "previous": previous,
                    "requested_by": requested_by,
                    "request_hash": request_hash,
                    "stage": stage,
                    "market": envelope["parameters"].get("market"),
                }
            )
        except PoolDispatchUncertain as exc:
            # Dispatch may have been admitted and still be queued. Refuse only
            # with authoritative terminal rejection evidence plus readback that
            # the previous envelope still runs; otherwise keep a dispatched
            # receipt regardless of the binding being active right now.
            receipt = exc.receipt
            try:
                kept = exc.terminal_rejection and self._previous_still_live(
                    ids, receipt, previous
                )
            except Exception:
                kept = False
            if kept:
                self._refuse(tenant_id, persona_id, number, exc.reason,
                             status_code=409, step="owner_chain")
            dispatch_note = (
                f"dispatch outcome unconfirmed ({exc.reason}); reconcile via owners"
            )
        except PersonaProvisioningCoordinationError as exc:
            self._refuse(tenant_id, persona_id, number, str(exc), status_code=409, step="owner_chain")
        except Exception as exc:  # owner unavailable
            if done is not None and done.get("status") == "dispatched":
                # An earlier request already (possibly) had its dispatch
                # accepted; an unavailable owner now cannot prove otherwise.
                # Preserve that uncertainty instead of claiming nothing moved.
                receipt = {
                    "persona_id": persona_id,
                    "pool_version": number,
                    "envelope_artifact_id": artifact_id,
                    "envelope_version": version,
                    "approval_decision_id": done["approval_decision_id"],
                    "deployment_plan_id": done["deployment_plan_id"],
                    "previous_envelope_id": previous["artifact_id"],
                    "source_binding_id": live_binding.get("binding_id"),
                    "runtime_id": live_binding.get("runtime_id"),
                    "capital_pool_id": ids.capital_pool_id,
                    "persona_capital_binding_id": ids.persona_capital_binding_id,
                    "saga_status": None,
                }
                dispatch_note = (
                    f"dispatch outcome unconfirmed (earlier accepted dispatch; "
                    f"owner now unavailable: {exc}); reconcile via owners"
                )
            else:
                self._refuse(
                    tenant_id, persona_id, number,
                    f"owner path unavailable: {exc}", status_code=503, step="owner_unavailable",
                )

        try:
            moved = self._binding_moved(ids, receipt)
        except Exception:  # readback failed after dispatch: report truthfully as unconfirmed
            moved = False
        entry = {
            "pool_version": number,
            "pool_digest": digest,
            "envelope_artifact_id": artifact_id,
            "envelope_version": version,
            "members": member_pins,
            "previous": previous,
            "deployment_plan_id": receipt["deployment_plan_id"],
            "approval_decision_id": receipt["approval_decision_id"],
            "status": "published" if moved else "dispatched",
            "published_at": (done or {}).get("published_at") or self.now(),
            **({"note": dispatch_note} if dispatch_note else {}),
        }

        def mutate(record):
            record["published"][str(number)] = entry
            if moved:
                record["current_envelope"] = {
                    "artifact_id": artifact_id,
                    "version": version,
                    "plan_id": receipt["deployment_plan_id"],
                    "pool_version": number,
                }

        self._save(tenant_id, persona_id, mutate)
        return {**receipt, **{k: entry[k] for k in ("status", "members", "pool_digest")},
                **({"note": dispatch_note} if dispatch_note else {}),
                "idempotent_replay": done is not None and done["status"] == "published"}

    def _pending_dispatch(self, tenant_id, persona_id, pool_version):
        """The recorded dispatched entry an unreadable pool read might refer to."""
        published = self._load(tenant_id, persona_id)["published"]
        if pool_version is not None:
            entry = published.get(str(int(pool_version)))
            return entry if entry and entry.get("status") == "dispatched" else None
        pending = [e for e in published.values() if e.get("status") == "dispatched"]
        return max(pending, key=lambda e: e["pool_version"]) if pending else None

    def _pool_read_unavailable(self, tenant_id, persona_id, pool_version, ids, reason):
        if self._pending_dispatch(tenant_id, persona_id, pool_version) is not None:
            return self._uncertain_from_history(
                tenant_id, persona_id, pool_version, ids, reason
            )
        self._refuse_pool_read(tenant_id, persona_id, pool_version, reason, 503, "owner_unavailable")

    def _uncertain_from_history(self, tenant_id, persona_id, pool_version, ids, reason, done=None):
        """Report an earlier accepted dispatch as unconfirmed; assert nothing else."""
        done = done or self._pending_dispatch(tenant_id, persona_id, pool_version)
        number = int(done["pool_version"])
        previous = done.get("previous") or {}
        receipt = {
            "persona_id": persona_id,
            "pool_version": number,
            "envelope_artifact_id": done["envelope_artifact_id"],
            "envelope_version": done["envelope_version"],
            "approval_decision_id": done["approval_decision_id"],
            "deployment_plan_id": done["deployment_plan_id"],
            "previous_envelope_id": previous.get("artifact_id"),
            "capital_pool_id": ids.capital_pool_id,
            "persona_capital_binding_id": ids.persona_capital_binding_id,
            "saga_status": None,
        }
        return {
            **receipt,
            "status": "dispatched",
            "members": done.get("members"),
            "pool_digest": done.get("pool_digest"),
            "note": f"dispatch outcome unconfirmed (earlier accepted dispatch; "
                    f"owner read unavailable: {reason}); reconcile via owners",
            "idempotent_replay": False,
        }

    def _previous_still_live(self, ids, receipt, previous) -> bool:
        """True only when owner readback proves the previous envelope still runs."""
        reader = self.coordinator.runtime_binding_reader
        if not reader:
            return False
        bindings = reader(receipt["deployment_plan_id"], ids.capital_pool_id)
        live = [b for b in bindings if b.get("capital_pool_id") == ids.capital_pool_id
                and b.get("status") not in TERMINAL_RUNTIME_BINDING_STATUSES]
        return bool(
            len(live) == 1
            and live[0].get("artifact_id") == previous["artifact_id"]
            and live[0].get("artifact_version") == previous["version"]
            and live[0].get("status") == "active"
        )

    def _binding_moved(self, ids, receipt) -> bool:
        reader = self.coordinator.runtime_binding_reader
        bindings = reader(receipt["deployment_plan_id"], ids.capital_pool_id) if reader else []
        live = [b for b in bindings if b.get("status") == "active"
                and b.get("capital_pool_id") == ids.capital_pool_id]
        return bool(
            len(live) == 1
            and live[0].get("artifact_id") == receipt["envelope_artifact_id"]
            and live[0].get("artifact_version") == receipt["envelope_version"]
            and live[0].get("runtime_id") == receipt.get("runtime_id")
            and live[0].get("persona_capital_binding_id") == ids.persona_capital_binding_id
        )

    def _build_envelope(self, tenant_id, ids, pool, artifact_id, version, number, digest, previous):
        members, pins = [], []
        for member in pool["members"]:
            if member["stage"] == "removed":
                continue
            entry = self._member_entry(tenant_id, member["artifact_ref"], member["checksum"])
            virtual = member["stage"] == "trial"
            weight = 0 if virtual else member["risk_weight"]
            members.append(
                {
                    "member_id": member["artifact_ref"],
                    "artifact": copy.deepcopy(entry["metadata"]["strategy_artifact"]),
                    "risk_weight": weight,
                    "virtual_only": virtual,
                }
            )
            pins.append(
                {"artifact_ref": member["artifact_ref"], "version": entry["version"],
                 "checksum": member["checksum"], "risk_weight": weight, "virtual_only": virtual}
            )
        if not members:
            raise PoolPublishRefused("pool has no members to publish", step="members")
        try:
            envelope = build_portfolio_envelope(
                artifact_id=artifact_id,
                strategy_id=ids.strategy_id,
                version=version,
                members=members,
                pool_snapshot={"pool_version": number, "pool_digest": digest},
                parent_envelope_id=previous["artifact_id"],
            )
        except Exception as exc:  # StrategyArtifactValidationError / ValueError
            raise PoolPublishRefused(f"envelope build refused: {exc}", step="envelope") from exc
        return envelope, pins
