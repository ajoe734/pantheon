"""Explicit, local dataset for the human-management OODA E2E flows.

Migration context: ACG-RS-RETIRE-E2E-SEED-FIXTURES-V2-20260829.

`tests/e2e/test_human_management_frontend_persona_ooda_100.py`,
`tests/e2e/test_human_management_frontend_browser_ooda_100.py`, and
`tests/e2e/test_persona_ooda_15_cycle_autonomy.py` each ran a local
`ReadSurfacePorts` test double (`OodaE2ETestStore`) seeded by calling
`services/control-plane/bff/read_store.py`'s `_default_read_data()` +
`_load_default_fixture_pack_datasets()` (which reads product-bundled JSON
fixture pack files off disk) + `_merge_market_persona_fleet()`. That coupled
these test/E2E flows to product-owned fixture-bootstrap internals and to an
on-disk product fixture-pack read at test time.

This module replaces that bootstrap with a single checked-in, test-owned
JSON snapshot (`tests/e2e/fixtures/ooda_e2e_default_dataset.json`) covering
only the record domains these three E2E flows' `OodaE2ETestStore` classes
actually read (personas, capital/runtime bindings, sessions, governance and
telemetry lists, etc. -- see `DATASET_DOMAINS`). OODA packets themselves are
NOT part of this dataset: all three flows generate their 135 packets
dynamically via `run_management_persona_ooda_cycles` /
`run_human_management_ooda_100`, so no packet data needs to be seeded here.

Loading is a plain local JSON read -- no network, no live capital or
continuous source ingestion, and no dependency on `read_store.py`.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict

_FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "ooda_e2e_default_dataset.json"

# The persona ids the three E2E flows assert on by name, in the fixed
# management order the flows expect `list_personas()` to return them in.
PERSONA_ORDER = (
    "persona-alpha",
    "persona-pack-a-momentum",
    "p-compliance-sponsor",
    "p-execution-lead",
    "p-macro-observer",
    "p-risk-analyst",
    "persona-us-equity",
    "persona-tw-equity",
    "persona-crypto",
)

# Record domains this dataset intentionally provides, and only these -- an
# `OodaE2ETestStore` reading any other key from the loaded dataset is reading
# a domain this fixture does not (and should not) cover.
DATASET_DOMAINS = (
    "personas",
    "runtime_bindings",
    "bindings",
    "persona_bindings",
    "sessions",
    "persona_sessions",
    "teaching_sessions",
    "allowed_actions",
    "capability_snapshots",
    "capital_pools",
    "persona_league",
    "incidents",
    "evolution_decisions",
    "evolution_programs",
    "rebalances",
    "deployment_plans",
    "approval_decisions",
    "kill_switch",
    "jobs",
    "alerts",
    "telemetry_summaries",
    "governance_audit_events",
    "governance_review_queue_items",
    "v5_interventions",
)


def load_ooda_e2e_dataset() -> Dict[str, Any]:
    """Load a fresh, independent copy of the local OODA E2E dataset.

    Returns a `{domain: records}` mapping (records are dicts keyed by id, or
    lists, matching whatever shape the source domain used) ready to hand to
    an `OodaE2ETestStore._data`. Callers each get their own deep copy so one
    test mutating `_data` cannot leak state into another.
    """
    with _FIXTURE_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return copy.deepcopy(data)
