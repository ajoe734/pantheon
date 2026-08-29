"""Typed, explicit test doubles for Management/PPL mutable fixtures and SEM dataset reads.

Migration context: ACG-RS-TYPED-TEST-SEAM-20260829, continuing the disposition
work started by `read_store_fixtures.py` (ACG-RS-FOUNDATION-20260828 /
disposition item ACG-02-002).

Three test-owned surfaces still force tests to construct or reach into
`ReadSurfaceStore` (services/control-plane/bff/read_store.py) directly instead
of using `ReadSurfacePorts`:

1. "Management" (the OODA/Management domain: `ooda_packets`,
   `approval_decisions`, `deployment_diffs`, `v5_interventions`,
   `synthesis_conflict_logs`) -- tests build this data by mutating
   `ReadSurfaceStore` in place (see `test_ppl_alloc_012_ranking_projection.py`
   and friends).
2. "PPL" (the Persona/Capital/Runtime domain: `personas`, `capital_pools`,
   `persona_bindings`, `runtime_bindings`, `rankings`, `persona_league`,
   `rebalances`, `capital_allocations`, `containments`) -- same pattern.
3. "SEM" (the generic dataset-source/dataset-records reader helpers
   `_sem_read_records` / `_sem_local_records` / `_sem_inbox_records` in
   `services/control-plane/bff/main.py`) -- these dynamically `getattr` a
   `dataset_source` and `_read_dataset_records` method off whatever object is
   bound to `read_store`, which is exactly the kind of generic cross-domain
   reflection this migration is retiring.

This module gives tests an explicit, typed way to build Management/PPL
mutable fixture data and to satisfy the SEM reader contract, all without
importing or constructing `ReadSurfaceStore`. It intentionally does not
provide a generic `setattr`/`getattr` forwarding facade: every mutation and
every read is a named, typed method for a named, typed dataset.
"""
from __future__ import annotations

from typing import Any, Dict, List

from read_store_fixtures import make_fixture_record


class ManagementFixtureBuilder:
    """Explicit, typed mutable fixture builder for the OODA/Management domain.

    Mirrors the `ooda_management_kwargs` shape accepted by
    `ports.create_in_memory_read_surface_ports` so a built fixture can be
    handed straight to `ReadSurfacePorts` via `to_kwargs()`.
    """

    def __init__(self) -> None:
        self._ooda_packets: List[Dict[str, Any]] = []
        self._interventions: List[Dict[str, Any]] = []
        self._synthesis_conflict_logs: List[Dict[str, Any]] = []
        self._approval_decisions: List[Dict[str, Any]] = []
        self._deployment_diffs: Dict[str, Dict[str, Any]] = {}

    def add_ooda_packet(self, packet_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("ooda_packets", record_id=packet_id, **overrides)
        self._ooda_packets.append(record)
        return record

    def add_intervention(self, intervention_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("v5_interventions", record_id=intervention_id, **overrides)
        self._interventions.append(record)
        return record

    def add_synthesis_conflict_log(self, log_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("synthesis_conflict_logs", record_id=log_id, **overrides)
        self._synthesis_conflict_logs.append(record)
        return record

    def add_approval_decision(self, decision_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("approval_decisions", record_id=decision_id, **overrides)
        self._approval_decisions.append(record)
        return record

    def add_deployment_diff(self, plan_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("deployment_diffs", record_id=plan_id, **overrides)
        self._deployment_diffs[plan_id] = record
        return record

    def to_kwargs(self) -> Dict[str, Any]:
        """Return the `ooda_management_kwargs`-shaped snapshot of everything added so far."""
        return {
            "ooda_packets": list(self._ooda_packets),
            "interventions": list(self._interventions),
            "synthesis_conflict_logs": list(self._synthesis_conflict_logs),
            "approval_decisions": list(self._approval_decisions),
            "deployment_diffs": dict(self._deployment_diffs),
        }


class PplFixtureBuilder:
    """Explicit, typed mutable fixture builder for the Persona/Capital/Runtime ("PPL") domain.

    Mirrors the `persona_capital_runtime_kwargs` shape accepted by
    `ports.create_in_memory_read_surface_ports`.
    """

    def __init__(self) -> None:
        self._personas: List[Dict[str, Any]] = []
        self._capital_pools: List[Dict[str, Any]] = []
        self._bindings: List[Dict[str, Any]] = []
        self._runtime_bindings: List[Dict[str, Any]] = []
        self._rankings: List[Dict[str, Any]] = []
        self._persona_league: List[Dict[str, Any]] = []
        self._rebalances: List[Dict[str, Any]] = []
        self._capital_allocations: List[Dict[str, Any]] = []
        self._containments: List[Dict[str, Any]] = []

    def add_persona(self, persona_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("personas", record_id=persona_id, **overrides)
        record.setdefault("persona_id", persona_id)
        self._personas.append(record)
        return record

    def add_capital_pool(self, pool_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("capital_pools", record_id=pool_id, **overrides)
        record.setdefault("pool_id", pool_id)
        self._capital_pools.append(record)
        return record

    def add_binding(self, binding_id: str, persona_id: str, pool_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record(
            "persona_bindings",
            record_id=binding_id,
            binding_id=binding_id,
            persona_id=persona_id,
            pool_id=pool_id,
            **overrides,
        )
        self._bindings.append(record)
        return record

    def add_runtime_binding(self, runtime_id: str, binding_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record(
            "runtime_bindings",
            record_id=runtime_id,
            runtime_id=runtime_id,
            binding_id=binding_id,
            **overrides,
        )
        self._runtime_bindings.append(record)
        return record

    def add_ranking(self, ranking_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("rankings", record_id=ranking_id, **overrides)
        self._rankings.append(record)
        return record

    def add_persona_league_entry(self, persona_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("persona_league", record_id=persona_id, persona_id=persona_id, **overrides)
        self._persona_league.append(record)
        return record

    def add_rebalance(self, rebalance_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("rebalances", record_id=rebalance_id, **overrides)
        self._rebalances.append(record)
        return record

    def add_capital_allocation(self, allocation_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("capital_allocations", record_id=allocation_id, **overrides)
        self._capital_allocations.append(record)
        return record

    def add_containment(self, containment_id: str, **overrides: Any) -> Dict[str, Any]:
        record = make_fixture_record("containments", record_id=containment_id, **overrides)
        self._containments.append(record)
        return record

    def to_kwargs(self) -> Dict[str, Any]:
        """Return the `persona_capital_runtime_kwargs`-shaped snapshot of everything added so far."""
        return {
            "personas": list(self._personas),
            "capital_pools": list(self._capital_pools),
            "bindings": list(self._bindings),
            "runtime_bindings": list(self._runtime_bindings),
            "rankings": list(self._rankings),
            "persona_league": list(self._persona_league),
            "rebalances": list(self._rebalances),
            "capital_allocations": list(self._capital_allocations),
            "containments": list(self._containments),
        }


# The exact dataset names read through `_sem_read_records` / `_sem_local_records`
# / `_sem_inbox_records` in services/control-plane/bff/main.py. A dataset name
# outside this set has no named caller in product code for the SEM reader
# contract, so `SemDatasetReaderTestDouble` refuses to serve it rather than
# silently accepting an arbitrary string the way generic reflection would.
KNOWN_SEM_DATASETS = frozenset(
    {
        "agora_sessions",
        "agora_skill_coaching_sessions",
        "agora_persona_lab_runs",
        "postmortems",
        "agora_evaluation_suites",
        "agora_evaluation_runs",
        "insight_cards",
        "agora_signals",
        "research_tickets",
    }
)


class SemDatasetReaderTestDouble:
    """Explicit, typed double for the SEM dataset-source/dataset-records read contract.

    `services/control-plane/bff/main.py`'s `_sem_read_records` dynamically
    resolves `dataset_source(dataset)` and `_read_dataset_records(dataset)` off
    whatever object `read_store` is bound to. This double implements the same
    two named methods explicitly and typed, restricted to `KNOWN_SEM_DATASETS`,
    so a test can satisfy that contract without constructing `ReadSurfaceStore`
    or relying on `getattr`-based generic dispatch.
    """

    def __init__(self) -> None:
        self._datasets: Dict[str, List[Dict[str, Any]]] = {}

    def set_dataset_records(self, dataset: str, records: List[Dict[str, Any]]) -> None:
        if dataset not in KNOWN_SEM_DATASETS:
            raise KeyError(f"{dataset!r} is not a named SEM dataset caller in main.py")
        self._datasets[dataset] = [dict(record) for record in records]

    def dataset_source(self, dataset: str) -> str:
        if dataset not in KNOWN_SEM_DATASETS:
            raise KeyError(f"{dataset!r} is not a named SEM dataset caller in main.py")
        records = self._datasets.get(dataset)
        return "local_snapshot" if records else "missing"

    def read_dataset_records(self, dataset: str) -> List[Dict[str, Any]]:
        if dataset not in KNOWN_SEM_DATASETS:
            raise KeyError(f"{dataset!r} is not a named SEM dataset caller in main.py")
        return [dict(record) for record in self._datasets.get(dataset, [])]
