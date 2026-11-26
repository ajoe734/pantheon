"""Supervised Alpha Replication and Revalidation Loop Controller.

Reconciles approved StrategySpecs (desired state) to AlphaReplicationQueue (actual state)
and executes the AlphaRevalidationWorker to produce non-stub ExperimentRun records.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.controller_state import ControllerState, ControllerStateStore
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.revalidation_worker import AlphaRevalidationWorker
from services.research.experiment_orchestrator.authority import ExperimentAuthority
from services.service_token_file import configured_service_token

REPO_ROOT = Path(__file__).resolve().parents[3]


class SeedLineageCounter:
    """Distinct seed ``source_id`` count, read incrementally from the append-only seed store.

    In-memory only (no cache file): keyed by the store's (st_dev, st_ino). An unchanged store costs
    one stat(); a grown store parses only the bytes after the last complete line already counted; a
    replaced or truncated store is re-read from the start. Lines are streamed, never loaded whole.
    """

    def __init__(self) -> None:
        self._identity: tuple[int, int] | None = None
        self._offset = 0  # byte offset just past the last newline-terminated line counted
        self._size = -1
        self._source_ids: set[str] = set()
        self.bytes_parsed = 0  # cumulative, for tests and evidence

    def count(self, path: Path) -> int:
        try:
            st = path.stat()
        except FileNotFoundError:
            self._reset(None)
            return 0
        identity = (st.st_dev, st.st_ino)
        if identity != self._identity or st.st_size < self._offset:
            self._reset(identity)
        if st.st_size == self._size:
            return len(self._source_ids)
        with path.open("rb") as handle:
            handle.seek(self._offset)
            offset = self._offset
            for raw in handle:
                self.bytes_parsed += len(raw)
                self._ingest(raw)
                if raw.endswith(b"\n"):
                    offset += len(raw)
                # an unterminated last line is counted now and re-read once it is completed
        self._offset = offset
        self._size = st.st_size
        return len(self._source_ids)

    def _reset(self, identity: tuple[int, int] | None) -> None:
        self._identity, self._offset, self._size = identity, 0, -1
        self._source_ids = set()

    def _ingest(self, raw: bytes) -> None:
        line = raw.decode("utf-8").strip()
        if not line:
            return
        payload = json.loads(line)
        if isinstance(payload, dict) and "source_id" in payload:
            source_id = str(payload["source_id"] or "").strip()
            if source_id:
                self._source_ids.add(source_id)


class ReplicationControllerConfig:
    def __init__(
        self,
        database_url: str | None = None,
        registry_url: str | None = None,
        interval_seconds: int = 60,
        max_ticks: int = 0,
        state_path: Path | None = None,
        data_dir: Path | None = None,
        seed_store_path: Path | None = None,
        authority: ExperimentAuthority | None = None,
        admission_store: ReplicationAdmissionStore | None = None,
        card_registrar: Any = None,
        card_matcher: Any = None,
        demands_provider: Any = None,
        dataset_resolver: Any = None,
        card_wiring_builder: Any = None,
    ) -> None:
        # Card-derived StrategySpec wiring (all optional; unset = no card path).
        # card_registrar: TemplateCandidateRegistrar; card_matcher(entry, demand)->bool is
        # the existing discovery scorer; demands_provider(tenant_id)->open demands;
        # dataset_resolver(binding)->governed dataset version.
        self.card_registrar = card_registrar
        self.card_matcher = card_matcher
        self.demands_provider = demands_provider
        self.dataset_resolver = dataset_resolver
        # card_wiring_builder() -> CardWiring, evaluated every tick by the production entry point
        # (main) so a mounted credential or dataset pointer that appears later enables the path and
        # one that disappears disables it, with the reason on the health file. Never a fallback.
        self.card_wiring_builder = card_wiring_builder
        self.card_draft_cursor: str | None = None
        self.card_admission_switch: Any = lambda: True  # production entry point installs the env switch
        self.card_path_status: dict[str, Any] = {
            "enabled": all((card_registrar, card_matcher, demands_provider)),
            "reason": None if all((card_registrar, card_matcher, demands_provider)) else "card_path_not_wired",
        }
        self.database_url = database_url or os.getenv("DATABASE_URL") or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"
        self.registry_url = registry_url or os.getenv("PANTHEON_REGISTRY_URL") or "http://registry:8087"
        self.interval_seconds = int(os.getenv("ALPHA_REPLICATION_CONTROLLER_INTERVAL_SECONDS", str(interval_seconds)))
        self.max_ticks = int(os.getenv("ALPHA_REPLICATION_CONTROLLER_MAX_TICKS", str(max_ticks)))
        
        self.data_dir = data_dir or Path(os.getenv("ALPHA_REPLICATION_DATA_DIR") or "data/alpha-replication")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.state_path = state_path or Path(os.getenv("ALPHA_REPLICATION_CONTROLLER_STATE_PATH") or self.data_dir / "controller_state.json")
        self.seed_store_path = seed_store_path or Path(os.getenv("STRATEGY_SPEC_SEED_STORE_PATH") or "data/source-ingest/distill_seeds.jsonl")
        self.authority = authority
        self.admission_store = admission_store or ReplicationAdmissionStore(self.data_dir)
        self.seed_lineage = SeedLineageCounter()


def refresh_card_wiring(config: "ReplicationControllerConfig") -> dict[str, Any]:
    """Rebuild the four card collaborators from the deployed environment (production entry point only)."""
    if config.card_wiring_builder is None:
        return config.card_path_status
    if not config.card_admission_switch():
        # switch off: no wiring is built and nothing downstream reads Registry drafts
        config.card_registrar = config.card_matcher = config.demands_provider = config.dataset_resolver = None
        config.card_path_status = {"enabled": False, "reason": "card_path_disabled", "missing": []}
        return config.card_path_status
    wiring = config.card_wiring_builder()
    config.card_registrar = wiring.registrar
    config.card_matcher = wiring.matcher
    config.demands_provider = wiring.demands_provider
    config.dataset_resolver = wiring.dataset_resolver
    config.card_path_status = {"enabled": wiring.enabled, "reason": wiring.reason, "missing": list(wiring.missing)}
    return config.card_path_status


def _get_approved_specs_for_strategy(registry_url: str, strategy_id: str) -> list[dict]:
    url = f"{registry_url}/api/registry/strategies/{strategy_id}/strategy-specs?artifact_state=approved"
    try:
        headers = {}
        token = configured_service_token("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            views = json.loads(response.read().decode("utf-8"))
            return [view["entry"] for view in views if "entry" in view]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return []
        raise
    except Exception as exc:
        raise RuntimeError(f"Failed to query registry for strategy {strategy_id}: {exc}") from exc


CARD_ADMISSION_PAGE_SIZE = 20
CARD_ADMISSION_MAX_PAGES_PER_TICK = 3  # hard cap: at most 60 admissible drafts read per tick


def card_admission_enabled(env: Mapping[str, str] | None = None) -> bool:
    """One switch, default off, checked before any Registry draft read."""
    raw = (os.environ if env is None else env).get("ALPHA_REPLICATION_CARD_ADMISSION_ENABLED", "0")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _list_card_admission_drafts(config: "ReplicationControllerConfig", tenant_id: str) -> list[dict]:
    """Bounded owner read: only gate-ready card drafts of the tenant, filtered in the Registry store.

    Pages of CARD_ADMISSION_PAGE_SIZE, at most CARD_ADMISSION_MAX_PAGES_PER_TICK per tick; the keyset
    cursor persists on the config so a backlog larger than the cap is walked across ticks.
    """
    headers = {}
    token = configured_service_token("ALPHA_REPLICATION_REGISTRY_SERVICE_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    entries: list[dict] = []
    cursor = getattr(config, "card_draft_cursor", None)
    for _ in range(CARD_ADMISSION_MAX_PAGES_PER_TICK):
        query = {"view": "card_admission_drafts", "tenant_id": tenant_id, "page_size": CARD_ADMISSION_PAGE_SIZE}
        if cursor:
            query["page_token"] = cursor
        url = f"{config.registry_url}/api/registry/strategy-specs?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=5) as response:
            page = json.loads(response.read().decode("utf-8"))
        entries.extend(v["entry"] for v in page.get("items", []) if "entry" in v)
        cursor = page.get("next_page_token")
        if not cursor:
            break
    config.card_draft_cursor = cursor  # None = wrap to the start on the next tick
    return entries


def _admit_card_specs(config: "ReplicationControllerConfig", tenant_id: str) -> list[dict[str, Any]]:
    """Research owner: admit card-derived specs the discovery scorer matched to an open demand."""
    from services.research.alpha_replication.admission import CardSpecAdmissionOwner

    owner = CardSpecAdmissionOwner(
        registrar=config.card_registrar, store=config.admission_store, matcher=config.card_matcher
    )
    demands = list(config.demands_provider(tenant_id))
    results: list[dict[str, Any]] = []
    for entry in _list_card_admission_drafts(config, tenant_id):
        if (entry.get("metadata") or {}).get("tenant_id") != tenant_id:
            continue
        try:
            outcome = owner.admit(entry, demands)
        except Exception as exc:  # noqa: BLE001 - one spec never blocks the tick
            outcome = {"status": "error", "registry_id": entry.get("registry_id"),
                       "reason": f"{type(exc).__name__}:{str(exc)[:160]}"}
        if outcome["status"] != "draft" or outcome.get("reason") == "no_open_demand_match":
            results.append(outcome)
    return results


def _register_card_passes(config: "ReplicationControllerConfig", worker: Any, tenant_id: str,
                          approved: list[dict]) -> list[dict[str, Any]]:
    """Register passing card runs through the template path's registration function."""
    from services.research.alpha_replication.admission import register_card_pass

    demands = {d["demand_id"]: d for d in config.demands_provider(tenant_id)}
    results: list[dict[str, Any]] = []
    for entry in approved:
        spec = (entry.get("metadata") or {}).get("strategy_spec") or {}
        if ((spec.get("metadata") or {}).get("card_wiring") or {}).get("status") != "gate_ready":
            continue
        adm = config.admission_store.get_admission(tenant_id, entry["registry_id"]) or {}
        demand = demands.get(adm.get("demand_ref"))
        if demand is None:
            continue
        for run in worker.list_runs(tenant_id=tenant_id, strategy_spec_id=entry["registry_id"]):
            meta = run.get("metadata") or {}
            if run.get("status") != "completed" or (meta.get("replication_gate") or {}).get("admission_status") != "admitted":
                continue
            try:
                reg = register_card_pass(registrar=config.card_registrar, entry=entry, demand=demand,
                                         run_metadata=meta)
                results.append({"registry_id": entry["registry_id"], "run_id": run.get("run_id"), **reg})
            except Exception as exc:  # noqa: BLE001
                results.append({"registry_id": entry["registry_id"], "run_id": run.get("run_id"),
                                "status": "error", "reason": f"{type(exc).__name__}:{str(exc)[:160]}"})
    return results


def _queue_payload_from_registry_entry(
    entry: Mapping[str, Any],
    *,
    tenant_id: str,
) -> dict[str, Any]:
    """Normalize one approved immutable registry view into the queue contract."""

    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError("approved StrategySpec registry metadata is required")
    spec = metadata.get("strategy_spec")
    if not isinstance(spec, Mapping):
        raise ValueError("approved StrategySpec inline payload is required")
    bound_tenant = str(metadata.get("tenant_id") or spec.get("tenant_id") or "").strip()
    if not bound_tenant:
        raise ValueError("approved StrategySpec tenant_id is required")
    if bound_tenant != tenant_id:
        raise ValueError(
            f"approved StrategySpec tenant_id={bound_tenant!r} does not match "
            f"controller tenant_id={tenant_id!r}"
        )
    if entry.get("artifact_type") != "strategy_spec":
        raise ValueError("registry entry is not a StrategySpec")
    if entry.get("artifact_state") != "approved":
        raise ValueError("only approved StrategySpec registry entries may enqueue")
    strategy_id = str(entry.get("strategy_id") or "").strip()
    version = str(entry.get("version") or "").strip()
    if str(spec.get("strategy_id") or "").strip() != strategy_id:
        raise ValueError("embedded StrategySpec strategy_id does not match registry entry")
    return {
        "tenant_id": bound_tenant,
        "strategy_spec_id": entry.get("registry_id"),
        "strategy_id": strategy_id,
        "spec_version": version,
        "artifact_state": entry.get("artifact_state"),
        "checksum": entry.get("checksum"),
        "approval_decision_id": entry.get("approval_decision_id"),
        "approver": entry.get("approver"),
        "approved_at": entry.get("approved_at"),
    }


def build_loop_writer(*, dsn: str, state: ControllerState) -> Any:
    if not dsn:
        return None
    # A configured durable writer must not silently disappear when an image
    # omits its driver. Unconfigured local use remains explicitly optional.
    module = importlib.import_module("services.loop-control")
    return module.LoopControllerWriter(
        dsn,
        tenant_id=state.tenant_id,
        environment=state.environment,
        controller_id=state.controller_id,
        controller_name=state.controller_name,
        deployment_sha=str(state.deployment.get("git_sha") or "unknown"),
    )


_TICK_TIMEOUT_SECONDS = 30


def _controller_lease_seconds(config: ReplicationControllerConfig) -> int:
    """Lease must outlive one steady-state interval plus one tick budget."""
    return int(config.interval_seconds) + _TICK_TIMEOUT_SECONDS


def _tick_evidence_ref(state: ControllerState) -> str:
    return f"alpha-replication-controller:{state.controller_id}:tick:{state.sequence_no}"


def run_controller_tick(
    *,
    config: ReplicationControllerConfig,
    state: ControllerState,
    store: ControllerStateStore,
    writer: Any,
) -> dict[str, Any]:
    state.record_tick_started()
    store.save(state)
    
    loop_id = os.getenv("PANTHEON_LOOP_ID") or "alpha_replication"
    
    desired_meta: dict[str, Any] = {}
    reconcile_meta: dict[str, Any] = {}
    actual_meta: dict[str, Any] = {}
    
    try:
        refresh_card_wiring(config)
        card_enabled = all((config.card_registrar, config.card_matcher, config.demands_provider))
        card_error: str | None = None
        card_admissions: list[dict[str, Any]] = []
        if card_enabled:
            try:
                card_admissions = _admit_card_specs(config, state.tenant_id)
            except Exception as exc:  # noqa: BLE001 - a card owner outage never stops the queue
                card_error = f"card_admission_unavailable:{type(exc).__name__}:{str(exc)[:200]}"
        # 1. Read admissions from ReplicationAdmissionStore for current tenant
        admissions = config.admission_store.list_admissions(tenant_id=state.tenant_id)

        # Legacy seed file read: seed store is retained for lineage tracking (seed_lineage_count on the
        # controller health file), but seed source_ids do NOT directly grant admission without an
        # approved ReplicationAdmission. Counted incrementally: an unchanged store costs one stat().
        try:
            seed_lineage_count = config.seed_lineage.count(config.seed_store_path)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to read StrategySpec discovery trigger store for lineage: {exc}"
            ) from exc

        # Look up registry entries ONLY for admitted strategy specs
        approved_specs = []
        for adm in admissions:
            strategy_id = adm["strategy_id"]
            strategy_spec_id = adm["strategy_spec_id"]
            try:
                entries = _get_approved_specs_for_strategy(config.registry_url, strategy_id)
                # Filter registry entries to the exact admitted strategy_spec_id
                matched_entries = [e for e in entries if e.get("registry_id") == strategy_spec_id]
                approved_specs.extend(matched_entries)
            except Exception as exc:
                raise RuntimeError(f"Registry lookup failed for strategy {strategy_id}: {exc}") from exc
        
        desired_meta = {
            "approved_spec_count": len(approved_specs),
            "strategy_spec_ids": [entry.get("registry_id") for entry in approved_specs],
            "tenant_id": state.tenant_id,
            "seed_lineage_count": seed_lineage_count,
        }
        
        # 2. Enqueue into AlphaReplicationQueue
        queue = AlphaReplicationQueue(config.data_dir)
        enqueued_count = 0
        for entry in approved_specs:
            try:
                spec_payload = _queue_payload_from_registry_entry(
                    entry,
                    tenant_id=state.tenant_id,
                )
                result = queue.enqueue(
                    spec_payload,
                    enqueued_by="alpha-replication-controller",
                )
                if result is not None:
                    enqueued_count += 1
            except Exception as exc:
                raise RuntimeError(
                    f"Failed to enqueue StrategySpec {entry.get('registry_id')}: {exc}"
                ) from exc
        
        # 3. Process the queue using AlphaRevalidationWorker
        worker = AlphaRevalidationWorker(
            queue,
            config.data_dir,
            worker_id="alpha-revalidation-worker",
            authority=config.authority,
            registry_url=config.registry_url,
            dataset_resolver=config.dataset_resolver,
        )
        tick_result = worker.run_once(tenant_id=state.tenant_id)
        card_registrations: list[dict[str, Any]] = []
        if card_enabled:
            try:
                card_registrations = _register_card_passes(config, worker, state.tenant_id, approved_specs)
            except Exception as exc:  # noqa: BLE001
                card_error = card_error or f"card_registration_unavailable:{type(exc).__name__}:{str(exc)[:200]}"
        
        reconcile_meta = {
            "enqueued_new": enqueued_count,
            "processed": tick_result.get("processed", 0),
            "created_run_ids": tick_result.get("created_run_ids", []),
            "created_authority_task_ids": tick_result.get(
                "created_authority_task_ids",
                [],
            ),
            "created_authority_run_ids": tick_result.get(
                "created_authority_run_ids",
                [],
            ),
            "created_experiment_task_ids": tick_result.get(
                "created_experiment_task_ids",
                [],
            ),
            "created_experiment_run_ids": tick_result.get(
                "created_experiment_run_ids",
                [],
            ),
            "authority_receipts": tick_result.get("authority_receipts", []),
            "errors": tick_result.get("errors", []),
            "dispatch_mode": tick_result.get("dispatch_mode"),
            "card_admissions": card_admissions,
            "card_registrations": card_registrations,
            # Readable on the worker health file: why the card path is or is not running.
            "card_path": {**config.card_path_status, "error": card_error},
        }
        
        # 4. Observe actual state: get metrics
        q_metrics = queue.get_metrics()
        w_metrics = worker.get_metrics()
        actual_meta = {
            "queue_total": q_metrics.get("total", 0),
            "queue_pending": q_metrics.get("pending", 0),
            "queue_revalidated": q_metrics.get("revalidated", 0),
            "queue_claimed": q_metrics.get("claimed", 0),
            "queue_dlq": q_metrics.get("dlq", 0),
            "worker_runs": w_metrics.get("run_count", 0),
            "worker_errors": w_metrics.get("error_count", 0),
        }
        
        state.record_success(
            desired_state=desired_meta,
            reconcile=reconcile_meta,
            schedule={},
            actual_readback=actual_meta,
        )
        store.save(state)
        
        # Write to loop control db
        if writer:
            try:
                import asyncio
                observed_at = datetime.now(timezone.utc).isoformat()
                evidence_refs: list[str] = [_tick_evidence_ref(state)]
                for receipt in tick_result.get("authority_receipts", []):
                    task_ref = (
                        "research-authority://experiment-tasks/"
                        f"{receipt['authority_task_id']}"
                    )
                    run_ref = (
                        "research-authority://experiment-runs/"
                        f"{receipt['authority_run_id']}"
                    )
                    for evidence_ref in (task_ref, run_ref):
                        if evidence_ref not in evidence_refs:
                            evidence_refs.append(evidence_ref)
                
                asyncio.run(writer.record_success(
                    loop_id=loop_id,
                    summary=f"Processed {tick_result.get('processed')} queue entries",
                    backlog=q_metrics.get("pending", 0),
                    desired_state={
                        "present": len(approved_specs) > 0,
                        "source": "registry.approved_strategy_specs",
                        "checked_at": observed_at,
                        "summary": f"{len(approved_specs)} approved StrategySpecs admitted",
                        "sources": ["registry.approved_strategy_specs"],
                    },
                    downstream_actual_state={
                        "status": "ready",
                        "source": "alpha-replication.queue_metrics",
                        "checked_at": observed_at,
                        "summary": (
                            f"{actual_meta['queue_pending']} pending, "
                            f"{actual_meta['queue_revalidated']} revalidated, "
                            f"{actual_meta['queue_dlq']} dlq"
                        ),
                        "sources": ["alpha-replication.queue_metrics"],
                    },
                    lease_duration_seconds=_controller_lease_seconds(config),
                    evidence_refs=evidence_refs,
                    payload={
                        "queue": q_metrics,
                        "worker": w_metrics,
                    }
                ))
            except Exception as exc:
                print(f"Warning: failed to write to LoopControllerWriter: {exc}", file=sys.stderr)
                
    except Exception as exc:
        state.record_failure(stage="tick", reason=str(exc))
        store.save(state)
        if writer:
            try:
                import asyncio
                asyncio.run(writer.record_failure(
                    loop_id=loop_id,
                    reason=str(exc),
                    evidence_refs=[_tick_evidence_ref(state)],
                    lease_duration_seconds=_controller_lease_seconds(config),
                ))
            except Exception:
                pass
        raise
        
    return state.to_dict()


def initialize_controller_state(store: ControllerStateStore) -> ControllerState:
    """Resume a checkpoint only within its configured tenant and deployment.

    Admissions and queue entries are separate stores and are never moved or
    rewritten here. An old health checkpoint must not choose the new worker's
    tenant or continue publishing an obsolete source identity after rollout.
    """
    state = store.load()
    tenant_id = os.getenv("PANTHEON_TENANT_ID") or "default"
    environment = os.getenv("PANTHEON_ENV") or "dev"
    git_sha = os.getenv("GIT_SHA") or "unknown"
    if state is None or (
        state.tenant_id != tenant_id
        or state.environment != environment
        or state.deployment.get("git_sha") != git_sha
    ):
        state = ControllerState(
            controller_id=os.getenv("PANTHEON_CONTROLLER_ID") or f"alpha-replication-controller-{os.getpid()}",
            controller_name=os.getenv("PANTHEON_CONTROLLER_NAME") or "alpha-replication-controller",
            environment=environment,
            tenant_id=tenant_id,
            deployment={"git_sha": git_sha},
        )
        store.save(state)
    return state


def build_production_config() -> ReplicationControllerConfig:
    """Controller configuration of the deployed worker: the card collaborators come from the environment."""
    from services.research.alpha_replication.card_production_wiring import build_card_wiring

    config = ReplicationControllerConfig()
    config.card_wiring_builder = lambda: build_card_wiring(
        data_dir=config.data_dir, admission_store=config.admission_store)
    config.card_admission_switch = card_admission_enabled
    return config


def start_template_evaluation(config: ReplicationControllerConfig) -> Any:
    """Run the scheduled AlphaTemplate evaluation beside the controller loop (own thread, never blocks a tick).

    A failure to start is reported and never stops the controller."""
    try:
        from services.research.alpha_templates.schedule import start_background

        return start_background(config.data_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"alpha template evaluation not started: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def main() -> int:
    config = build_production_config()
    store = ControllerStateStore(config.state_path)
    state = initialize_controller_state(store)

    writer = None
    if config.database_url:
        writer = build_loop_writer(dsn=config.database_url, state=state)
        
    print(f"Starting Alpha Replication Controller loop, interval={config.interval_seconds}s")
    start_template_evaluation(config)
    
    tick_count = 0
    while True:
        try:
            run_controller_tick(config=config, state=state, store=store, writer=writer)
        except Exception as exc:
            print(f"Error in controller tick: {exc}", file=sys.stderr)
            
        tick_count += 1
        if config.max_ticks > 0 and tick_count >= config.max_ticks:
            print("Max ticks reached. Exiting.")
            break
            
        time.sleep(config.interval_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
