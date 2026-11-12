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

from .persona_provisioning_coordinator import (
    PersonaProvisioningCoordinationError,
    PersonaProvisioningCoordinator,
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

    def _guarded(self, tenant_id, persona_id, number, fn):
        """Run an authoritative pre-dispatch owner read; fail closed on error."""
        try:
            return fn()
        except PoolPublishRefused as exc:
            if exc.step != "owner_unavailable":
                raise
            self._refuse(tenant_id, persona_id, number, exc.reason,
                         status_code=exc.status_code, step=exc.step)
        except Exception as exc:
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
            self._refuse_pool_read(
                tenant_id, persona_id, pool_version, exc.reason, exc.status_code, exc.step
            )
        except Exception as exc:  # owner HTTP/transport failure: nothing moved
            self._refuse_pool_read(
                tenant_id, persona_id, pool_version,
                f"persona owner pool read unavailable: {type(exc).__name__}: {exc}",
                503, "owner_unavailable",
            )
        number = int(pool["pool_version"])
        digest = canonical_digest(pool)
        history = self._load(tenant_id, persona_id)
        done = history["published"].get(str(number))
        record = ProvisioningRecord(
            tenant_id=tenant_id, idempotency_key="pool-publish", request_hash="",
            normalized_name="", persona_id=persona_id, request_payload={},
        )
        ids = deterministic_provisioning_ids(record)
        artifact_id = f"artifact-persona-pool-{ids.token}-v{number}"
        version = f"1.{number}.0"
        # Authoritative owner state, never the local record: the live binding
        # says what runs now and the Registry says what each envelope is.
        reader = self.coordinator.runtime_binding_reader
        bindings = self._guarded(
            tenant_id, persona_id, number,
            lambda: reader(ids.deployment_plan_id, ids.capital_pool_id) if reader else [],
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
            tenant_id, persona_id, number, lambda: self._registry_view(artifact_id)
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
            self._refuse(tenant_id, persona_id, number, exc.reason,
                         status_code=exc.status_code, step=exc.step)
        request_hash = canonical_digest(
            {"pool_digest": digest, "envelope": artifact_id, "previous": previous}
        )
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
        except PersonaProvisioningCoordinationError as exc:
            self._refuse(tenant_id, persona_id, number, str(exc), status_code=409, step="owner_chain")
        except Exception as exc:  # owner unavailable: nothing moved, previous keeps running
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
                "idempotent_replay": done is not None and done["status"] == "published"}

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
