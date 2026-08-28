"""Explicit test-only fixture factories for ReadSurfaceStore-shaped data.

Migration context: ACG-RS-FOUNDATION-20260828 / disposition item ACG-02-002.

`ReadSurfaceStore` (services/control-plane/bff/read_store.py) currently seeds
its in-memory dataset from `_default_read_data()` plus three product-bundled
fixture packs (`_FIXTURE_PACK_A_PATH`/`B`/`C`). Those functions live in
product source and are not test-owned, which is exactly the boundary this
task inventories without yet moving.

This module gives tests an independent, typed way to build the same shaped
records without importing anything from `read_store.py`. It intentionally
does not read the product fixture packs or `_default_read_data()`: copying
their contents here would just recreate the coupling this task exists to
name. Callers that need the *current* seeded behavior should keep
constructing `ReadSurfaceStore` directly; callers that only need a few typed
records for a domain should use `make_fixture_record` /
`build_fixture_dataset` instead.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, TypedDict


class FixtureRecord(TypedDict, total=False):
    """Minimal common shape shared by every domain fixture record."""

    id: str
    status: str
    created_at: str
    updated_at: str


# Every key `ReadSurfaceStore._LOCAL_DATA_KEYS` recognizes, mapped to the
# functional area that owns its meaning. This is the "named typed owner"
# required by the ACG-02-004 gate for retained generic snapshot datasets:
# a domain with no owner here has no business being seeded generically.
DOMAIN_OWNERS: Dict[str, str] = {
    "deployment_plans": "deployment",
    "approval_decisions": "governance",
    "capital_pools": "capital-allocation",
    "persona_bindings": "capital-allocation",
    "runtime_bindings": "runtime",
    "registry_entries": "runtime",
    "personas": "persona-registry",
    "persona_route_policies": "persona-registry",
    "sessions": "persona-runtime",
    "capability_snapshots": "persona-runtime",
    "teaching_sessions": "training",
    "trainer_previews": "training",
    "consultation_sessions": "consultation",
    "consult_transcripts": "consultation",
    "consult_policies": "consultation",
    "route_policies": "routing",
    "incidents": "incident-response",
    "postmortems": "incident-response",
    "evolution_decisions": "evolution",
    "evolution_programs": "evolution",
    "evolution_program_runs": "evolution",
    "evolution_program_candidates": "evolution",
    "telemetry_summaries": "telemetry",
    "telemetry_performance": "telemetry",
    "paper_live_drift_reports": "paper-canary-live",
    "paper_runtime_monitoring_sessions": "paper-canary-live",
    "lineage_edges": "lineage",
    "inspiration_graphs": "lineage",
    "kill_switch": "safety",
    "rollbacks": "safety",
    "rollbacks_by_incident": "safety",
    "all_rollbacks": "safety",
    "latest_runs": "runtime",
    "review_summaries": "governance",
    "rollback_reviews": "governance",
    "governance_audit_events": "governance",
    "governance_review_queue_items": "governance",
    "approval_queue_items": "governance",
    "deployment_diffs": "deployment",
    "research_tickets": "research",
    "research_experiments": "research",
    "research_artifacts": "research",
    "research_notes": "research",
    "institutional_memory_entries": "research",
    "research_analyses": "research",
    "evidence_refs": "research",
    "insight_cards": "research",
    "strategy_specs": "research",
    "research_search_documents": "research",
    "research_search_index": "research",
    "consult_requests": "consultation",
    "consult_memos": "consultation",
    "trainer_replays": "training",
    "trainer_controls": "training",
    "workflow_templates": "operations-console",
    "hook_registry": "operations-console",
    "jobs": "operations-console",
    "bff_jobs": "operations-console",
    "decision_journal_entries": "decision-journal",
    "decision_journal_idempotency": "decision-journal",
    "agora_journal_audit_events": "agora",
    "agora_signals": "agora",
    "agora_feedback": "agora",
    "agora_signal_feedback": "agora",
    "agora_watchlist": "agora",
    "agora_sessions": "agora",
    "agora_skill_coaching_sessions": "agora",
    "agora_persona_lab_runs": "agora",
    "agora_evaluation_suites": "agora",
    "agora_evaluation_runs": "agora",
    "agora_committee_evidence_packs": "agora",
    "agora_handoffs": "agora",
    "agora_training_examples": "agora",
    "agora_audit_events": "agora",
    "v5_interventions": "evolution",
    "ooda_packets": "ooda-loop",
    "synthesis_conflict_logs": "ooda-loop",
    "ranking_formulas": "capital-allocation",
    "rebalances": "capital-allocation",
    "capital_allocations": "capital-allocation",
    "containments": "safety",
    "rankings": "capital-allocation",
    "persona_league": "capital-allocation",
    "ranking_snapshots": "capital-allocation",
    "allocation_evaluations": "capital-allocation",
}


def make_fixture_record(domain: str, record_id: str = "fixture-1", **overrides: Any) -> Dict[str, Any]:
    """Build one minimal typed record for `domain`.

    Raises `KeyError` for any domain not registered in `DOMAIN_OWNERS` -- an
    unnamed domain must not silently get a generic fixture.
    """
    if domain not in DOMAIN_OWNERS:
        raise KeyError(f"no named typed owner registered for fixture domain {domain!r}")
    record: Dict[str, Any] = {
        "id": record_id,
        "status": "active",
        "owner_area": DOMAIN_OWNERS[domain],
    }
    record.update(overrides)
    return record


def build_fixture_dataset(
    domains: Optional[Iterable[str]] = None,
    records_per_domain: int = 1,
) -> Dict[str, List[Dict[str, Any]]]:
    """Build a `{domain: [records]}` dataset entirely from this module.

    `domains` defaults to every registered domain. This never reads the
    product fixture packs or `_default_read_data()`.
    """
    selected = list(domains) if domains is not None else list(DOMAIN_OWNERS)
    dataset: Dict[str, List[Dict[str, Any]]] = {}
    for domain in selected:
        dataset[domain] = [
            make_fixture_record(domain, record_id=f"{domain}-fixture-{i + 1}")
            for i in range(records_per_domain)
        ]
    return dataset
