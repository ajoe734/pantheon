"""Strategies domain application service and business logic.

Part of BFF-ROUTER-USECASE-CORRECTIVE-001.
Encapsulates read store access, persistence ports, and strategy seed store operations
away from route handlers.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple, Set, Union
import uuid

from fastapi import HTTPException

from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from models import ErrorCode

try:
    from services.control_plane.bff.ports.strategy_write_owner import StrategyWriteOwnerPort
except (ImportError, ValueError):
    from ..ports.strategy_write_owner import StrategyWriteOwnerPort  # type: ignore

try:
    from services.source_ingestion.replication_bridge import (
        StrategySeedReplicationBridge,
        StrategySeedReplicationBridgeError,
    )
except (ImportError, ValueError):
    StrategySeedReplicationBridge = None  # type: ignore
    StrategySeedReplicationBridgeError = Exception  # type: ignore

try:
    from services.source_ingestion.strategy_seed_store import (
        SeedReviewDecision,
        StrategySpecSeedReviewError,
        StrategySpecSeedStore,
        StrategySpecSeedStoreError,
    )
except (ImportError, ValueError):
    SeedReviewDecision = None  # type: ignore
    StrategySpecSeedReviewError = Exception  # type: ignore
    StrategySpecSeedStore = None  # type: ignore
    StrategySpecSeedStoreError = Exception  # type: ignore

log = logging.getLogger(__name__)


def list_strategy_summaries(read_store: Any) -> List[Dict[str, Any]]:
    """Return canonical strategy specs from the strategy read owner."""
    return list(read_store.list_strategy_specs() or [])


class StrategiesService:
    def __init__(
        self,
        *,
        read_surface: Optional[Union[ReadSurfacePorts, Callable[[], ReadSurfacePorts]]] = None,
        get_read_store: Optional[Callable[[], ReadSurfacePorts]] = None,
        strategy_write_owner: Optional[Union[StrategyWriteOwnerPort, Callable[[], StrategyWriteOwnerPort]]] = None,
        get_strategy_write_owner: Optional[Callable[[], StrategyWriteOwnerPort]] = None,
        list_strategy_summaries: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        bff_error: Optional[Callable[..., HTTPException]] = None,
        seed_store: Optional[StrategySpecSeedStore] = None,
        replication_bridge: Optional[StrategySeedReplicationBridge] = None,
        utc_now: Optional[Callable[[], str]] = None,
        normalize_lifecycle_state: Optional[Callable[[Any], str]] = None,
        normalize_risk_level: Optional[Callable[[Any], str]] = None,
        stable_json_hash: Optional[Callable[[Dict[str, Any]], str]] = None,
        idempotency_store: Optional[Dict[str, Dict[str, Any]]] = None,
        idempotency_check: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
        dry_run_success_response: Optional[Callable[..., Any]] = None,
        seed_replication_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
        seed_review_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._read_surface = read_surface
        self._get_read_store = get_read_store
        self._strategy_write_owner = strategy_write_owner
        self._get_strategy_write_owner = get_strategy_write_owner
        self._list_strategy_summaries = list_strategy_summaries
        self._bff_error = bff_error
        self._seed_store = seed_store
        self._replication_bridge = replication_bridge
        self._utc_now = utc_now
        self._normalize_lifecycle_state = normalize_lifecycle_state
        self._normalize_risk_level = normalize_risk_level
        self._stable_json_hash = stable_json_hash
        self._idempotency_store = idempotency_store
        self._idempotency_check = idempotency_check
        self._dry_run_success_response = dry_run_success_response
        self._seed_replication_idempotency = seed_replication_idempotency
        self._seed_review_idempotency = seed_review_idempotency

    def _get_read_store_port(self) -> ReadSurfacePorts:
        if self._read_surface is not None:
            return self._read_surface() if callable(self._read_surface) else self._read_surface
        if self._get_read_store is not None:
            return self._get_read_store()
        raise NotImplementedError("Neither read_surface nor get_read_store dependency was supplied")

    def _get_write_owner_port(self) -> Optional[StrategyWriteOwnerPort]:
        if self._strategy_write_owner is not None:
            return self._strategy_write_owner() if callable(self._strategy_write_owner) else self._strategy_write_owner
        if self._get_strategy_write_owner is not None:
            return self._get_strategy_write_owner()
        try:
            rs = self._get_read_store_port()
            if hasattr(rs, "upsert_strategy") or hasattr(rs, "create_strategy_spec"):
                return rs
        except Exception:
            pass
        return None

    @property
    def seed_store(self) -> StrategySpecSeedStore:
        if self._seed_store is not None:
            return self._seed_store
        if StrategySpecSeedStore is not None:
            return StrategySpecSeedStore()
        raise RuntimeError("StrategySpecSeedStore is unavailable")

    def get_seed_store_path(self) -> str:
        return str(self.seed_store.path)

    def list_strategy_summaries(self) -> List[Dict[str, Any]]:
        if self._list_strategy_summaries is not None:
            return self._list_strategy_summaries()
        raise NotImplementedError("list_strategy_summaries dependency was not supplied")

    def get_strategy_spec_detail(self, strategy_id: str, version_selector: str = "current") -> Optional[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_strategy_spec_detail", None)
        if callable(getter):
            try:
                return getter(strategy_id, version_selector=version_selector)
            except Exception:
                pass
        return None

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_strategy_spec", None)
        if callable(getter):
            try:
                res = getter(strategy_id)
                if res:
                    return res
            except Exception:
                pass
        getter = getattr(read_store, "get_strategy", None)
        if callable(getter):
            try:
                res = getter(strategy_id)
                if res:
                    return res
            except Exception:
                pass
        return None

    def ensure_strategy_exists(self, strategy_id: str) -> None:
        strategy = self.get_strategy(strategy_id)
        if strategy:
            return
        if self._bff_error:
            raise self._bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Strategy not found",
                f"Strategy {strategy_id} does not exist",
            )
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} does not exist")

    def list_strategy_spec_versions(self, strategy_id: str) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "list_strategy_spec_versions", None)
        if callable(getter):
            return getter(strategy_id) or []
        return []

    def list_research_experiments(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        raw = read_store.list_research_experiments() or []
        if strategy_id:
            return [e for e in raw if (e.get("linked_strategy_id") or e.get("strategy_id")) == strategy_id]
        return raw

    def list_research_artifacts(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        raw = read_store.list_research_artifacts() or []
        if strategy_id:
            return [a for a in raw if (a.get("linked_strategy_id") or a.get("strategy_id")) == strategy_id]
        return raw

    def get_strategy_lineage(self, strategy_id: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        read_store = self._get_read_store_port()
        edges = read_store.list_lineage_edges() or []
        nodes_seen: Set[str] = set()
        related: List[Dict[str, Any]] = []
        for edge in edges:
            node_keys = (
                str(edge.get("from_artifact_id") or edge.get("source_id") or ""),
                str(edge.get("to_artifact_id") or edge.get("target_id") or ""),
                str(edge.get("strategy_id") or ""),
            )
            if strategy_id in node_keys:
                related.append(edge)
                for key in node_keys:
                    if key:
                        nodes_seen.add(key)
        nodes_seen.add(strategy_id)
        return related, sorted(nodes_seen)

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "list_ooda_packets_for_strategy", None)
        if callable(getter):
            return getter(strategy_id) or []
        return []

    def persist_strategy(
        self,
        record: Dict[str, Any],
        *,
        actor: Dict[str, Any],
        command_key: str,
    ) -> bool:
        writer = self._get_write_owner_port()
        if writer is None:
            if self._bff_error:
                raise self._bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Canonical strategy writer unavailable",
                    "Cannot persist strategy without an authoritative domain store",
                )
            raise HTTPException(status_code=503, detail="Canonical strategy writer unavailable")
        written = False
        try:
            res = None
            if hasattr(writer, "upsert_strategy"):
                res = writer.upsert_strategy({**record, "actor": actor, "command_key": command_key})
            elif hasattr(writer, "create_strategy_spec"):
                res = writer.create_strategy_spec({**record, "actor": actor, "command_key": command_key})
            if res:
                written = True
        except HTTPException:
            raise
        except Exception as exc:
            if self._bff_error:
                raise self._bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Canonical strategy persistence failed",
                    str(exc),
                ) from exc
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if not written:
            if self._bff_error:
                raise self._bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Canonical strategy writer unavailable",
                    "Cannot persist strategy without an authoritative domain store",
                )
            raise HTTPException(status_code=503, detail="Canonical strategy writer unavailable")
        return True

    def get_persona_route_policy(self, persona_id: str) -> Dict[str, Any]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_route_policy_for_persona", None)
        if callable(getter):
            return getter(persona_id) or {}
        return {}

    def get_persona_capability_snapshot(self, persona_id: str) -> Dict[str, Any]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_capability_snapshot_for_persona", None)
        if callable(getter):
            return getter(persona_id) or {}
        return {}

    # --- Seed store use cases ---
    def list_seeds(self) -> List[Any]:
        return self.seed_store.list_all()

    def get_seed(self, seed_id: str) -> Optional[Any]:
        return self.seed_store.get(seed_id)

    def record_seed_review_decision(self, seed_id: str, **kwargs: Any) -> Tuple[Any, Any]:
        return self.seed_store.record_review_decision(seed_id, **kwargs)

    def submit_seed_replication(
        self,
        *,
        seed_id: str,
        payload: Dict[str, Any],
        operator_id: str,
        resolved_key: str,
    ) -> Dict[str, Any]:
        """Execute replication command objective: idempotency check, bridge submit, replay decision, cache write."""
        request_hash = (
            self._stable_json_hash(
                {
                    "route": "POST /bff/management/strategy-seeds/{seed_id}/submit-replication",
                    "seed_id": seed_id,
                    "payload": payload,
                }
            )
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if self._seed_replication_idempotency is not None:
            existing = self._seed_replication_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    if self._bff_error:
                        raise self._bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different request hash",
                            precondition_failed="idempotency_conflict",
                            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                        )
                    raise HTTPException(status_code=409, detail="Idempotency key conflict")
                cached = json.loads(json.dumps(existing.get("result") or {}))
                cached.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return cached

        bridge = self._replication_bridge or (StrategySeedReplicationBridge() if StrategySeedReplicationBridge is not None else None)
        if bridge is None:
            raise NotImplementedError("StrategySeedReplicationBridge is unavailable")

        submission = bridge.submit_seed_to_replication(
            seed_id,
            requested_by=operator_id,
            idempotency_key=resolved_key,
            created_at=payload.get("created_at") or None,
            strategy_spec_version=str(payload.get("strategy_spec_version") or "1.0.0"),
        )

        snapshot_at = submission.created_at or (self._utc_now() if self._utc_now else "2026-09-27T00:00:00Z")
        result = {
            "data": {
                "seed_id": submission.seed_id,
                "replication_ref": submission.replication_ref,
                "experiment_task_id": submission.experiment_task_id,
                "strategy_id": submission.strategy_id,
                "strategy_spec_version": submission.strategy_spec_version,
                "research_task_id": submission.research_task.get("task_id"),
                "status": submission.research_task.get("status") or "queued",
                "experiment_task": dict(submission.experiment_task),
                "registry_write_performed": False,
                "execution_route": "none",
                "deployment_authority": "none",
                "approved_artifact_created": False,
                "deployment_plan_created": False,
                "runtime_binding_created": False,
                "idempotent_replay": submission.idempotent_replay,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
                "idempotency": {
                    "idempotencyKey": resolved_key,
                    "replayed": False,
                },
            },
        }
        if self._seed_replication_idempotency is not None:
            self._seed_replication_idempotency[resolved_key] = {
                "request_hash": request_hash,
                "result": result,
            }
        return result

    def review_seed(
        self,
        *,
        seed_id: str,
        payload: Dict[str, Any],
        action: str,
        operator_id: str,
        target_refs: List[Dict[str, Any]],
        resolved_key: str,
        result_builder: Callable[[Any, Any, str, str, bool], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute review command objective: idempotency check, store decision, replay handling, cache write."""
        request_hash = (
            self._stable_json_hash(
                {
                    "route": "POST /bff/management/strategy-seeds/{seed_id}/review",
                    "seed_id": seed_id,
                    "action": action,
                    "payload": payload,
                }
            )
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if self._seed_review_idempotency is not None:
            existing = self._seed_review_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    if self._bff_error:
                        raise self._bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different request hash",
                            precondition_failed="idempotency_conflict",
                            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                        )
                    raise HTTPException(status_code=409, detail="Idempotency key conflict")
                cached = json.loads(json.dumps(existing.get("result") or {}))
                cached.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return cached

        snapshot_at = self._utc_now() if self._utc_now else "2026-09-27T00:00:00Z"
        updated, decision = self.seed_store.record_review_decision(
            seed_id,
            decision=action,
            reviewer_id=operator_id,
            reason=str(payload.get("reason") or ""),
            target_refs=target_refs,
            created_at=payload.get("created_at") or snapshot_at,
            idempotency_key=resolved_key,
            request_hash=request_hash,
        )
        replayed = bool(getattr(decision, "idempotent_replay", False))
        result = result_builder(updated, decision, snapshot_at, resolved_key, replayed)
        if self._seed_review_idempotency is not None:
            self._seed_review_idempotency[resolved_key] = {
                "request_hash": request_hash,
                "result": result,
            }
        return result

    def merge_seed(
        self,
        *,
        seed_id: str,
        payload: Dict[str, Any],
        target_seed_id: str,
        operator_id: str,
        target_refs: List[Dict[str, Any]],
        resolved_key: str,
        result_builder: Callable[[Any, Any, str, str, bool], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute merge command objective: idempotency check, store merge, replay handling, cache write."""
        request_hash = (
            self._stable_json_hash(
                {
                    "route": "POST /bff/management/strategy-seeds/{seed_id}/merge",
                    "seed_id": seed_id,
                    "payload": payload,
                }
            )
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if self._seed_review_idempotency is not None:
            existing = self._seed_review_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    if self._bff_error:
                        raise self._bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different request hash",
                            precondition_failed="idempotency_conflict",
                            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                        )
                    raise HTTPException(status_code=409, detail="Idempotency key conflict")
                cached = json.loads(json.dumps(existing.get("result") or {}))
                cached.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return cached

        snapshot_at = self._utc_now() if self._utc_now else "2026-09-27T00:00:00Z"
        updated, decision = self.seed_store.merge_seed(
            seed_id,
            target_seed_id=target_seed_id,
            reviewer_id=operator_id,
            reason=str(payload.get("reason") or ""),
            target_refs=target_refs,
            created_at=payload.get("created_at") or snapshot_at,
            idempotency_key=resolved_key,
            request_hash=request_hash,
        )
        replayed = bool(getattr(decision, "idempotent_replay", False))
        result = result_builder(updated, decision, snapshot_at, resolved_key, replayed)
        if self._seed_review_idempotency is not None:
            self._seed_review_idempotency[resolved_key] = {
                "request_hash": request_hash,
                "result": result,
            }
        return result

    def create_strategy(
        self,
        *,
        payload: Dict[str, Any],
        identity: Any,
        principal: Dict[str, Any],
        resolved_key: str,
        dry_run: bool,
    ) -> Dict[str, Any]:
        """Execute the strategy creation command objective, including replay check, dry-run, and persistence."""
        request_hash = (
            self._stable_json_hash({"route": "POST /bff/strategies", "payload": payload, "principal": principal})
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if not dry_run and self._idempotency_check is not None:
            cached = self._idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached

        name = str(payload.get("name") or "").strip()
        snapshot_at = self._utc_now() if self._utc_now is not None else "2026-09-27T00:00:00Z"
        strategy_id = f"strategy-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        state = (
            self._normalize_lifecycle_state(payload.get("state") or "draft")
            if self._normalize_lifecycle_state is not None
            else str(payload.get("state") or "draft")
        )
        risk = (
            self._normalize_risk_level(payload.get("risk"))
            if self._normalize_risk_level is not None
            else str(payload.get("risk") or "medium")
        )
        record = {
            "id": strategy_id,
            "strategy_id": strategy_id,
            "name": name,
            "owner": str(payload.get("owner") or getattr(identity, "operator_id", "operator")),
            "updatedAt": snapshot_at,
            "state": state,
            "risk": risk,
            "alpha": str(payload.get("alpha") or ""),
            "capitalPoolId": str(payload.get("capitalPoolId") or payload.get("capital_pool_id") or ""),
            "personaIds": list(payload.get("personaIds") or payload.get("persona_ids") or []),
            "pnl30d": float(payload.get("pnl30d") or 0.0),
            "sharpe": float(payload.get("sharpe") or 0.0),
            "drawdown": float(payload.get("drawdown") or 0.0),
            "availableActions": ["edit", "submit", "retire"],
            "labelKey": f"strategy.{strategy_id}",
        }
        if dry_run:
            if self._dry_run_success_response is not None:
                return self._dry_run_success_response(
                    record,
                    snapshot_at=snapshot_at,
                    idempotency_key=resolved_key,
                    evidence_kind="strategy.create",
                )
            return {"data": record, "meta": {"snapshot_at": snapshot_at, "dry_run": True}}

        self.persist_strategy(
            record,
            actor=principal,
            command_key=resolved_key,
        )
        result = {
            "data": record,
            "meta": {"snapshot_at": snapshot_at},
        }
        if self._idempotency_store is not None:
            self._idempotency_store[resolved_key] = {"request_hash": request_hash, "result": result}
        return result

