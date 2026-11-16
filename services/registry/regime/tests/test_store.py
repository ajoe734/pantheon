"""Store behaviour: writes only through the evaluator methods, lifecycle bookkeeping, Postgres parity."""
from __future__ import annotations

import os

import pytest

from services.registry.regime.store import SERVED_STATES, WRITE_METHODS, RegimeStore, market_state_id


def _ms(session="2026-11-13", version="set-1", revision=1, status="approved"):
    return {"market": "TW", "session": session, "model_set_version": version, "model_status": status,
            "revision": revision, "state": "current", "input_digest": "d", "axes": {}}


def _row(strategy="s", session="2026-11-13", revision=1, state="validated", version="set-1", status="approved"):
    rid = f"regime:TW:{strategy}:{session}:{version}:r{revision}"
    return {"record": {"regime_id": rid, "strategy_id": strategy, "state": state, "metadata": {}}, "market": "TW",
            "session": session, "revision": revision, "model_set_version": version, "model_status": status,
            "owner_tenant": "t"}


def test_write_surface_is_exactly_the_evaluator_methods():
    public_writers = {n for n in dir(RegimeStore) if not n.startswith("_") and n.split("_")[0] in ("put", "insert", "update", "delete", "set")}
    assert public_writers == set()
    assert set(WRITE_METHODS) == {"evaluator_commit", "evaluator_invalidate"}
    assert all(hasattr(RegimeStore, n) for n in WRITE_METHODS)


def test_commit_is_idempotent_per_key():
    store = RegimeStore()
    first = store.evaluator_commit(_ms(), [_row()])
    second = store.evaluator_commit(_ms(), [_row()])
    assert first["market_state_stored"] and len(first["regime_ids"]) == 1
    assert not second["market_state_stored"] and second["regime_ids"] == []
    assert len(store.list_regimes(served_only=False)) == 1


def test_proposed_is_stored_but_never_served_or_activated():
    store = RegimeStore()
    store.evaluator_commit(_ms(status="candidate"), [_row(state="proposed", status="candidate")])
    assert store.list_regimes() == [] and [r["record"]["state"] for r in store.list_regimes(served_only=False)] == ["proposed"]
    assert "proposed" not in SERVED_STATES
    assert store.list_market_states("TW") == [] and len(store.list_market_states("TW", served_only=False)) == 1


def test_supersede_chain_and_per_strategy_isolation():
    store = RegimeStore()
    store.evaluator_commit(_ms("2026-11-11"), [_row("a", "2026-11-11"), _row("b", "2026-11-11")])
    store.evaluator_commit(_ms("2026-11-12"), [_row("a", "2026-11-12")])
    by = {(r["record"]["strategy_id"], r["session"]): r["record"] for r in store.list_regimes()}
    assert by[("a", "2026-11-11")]["state"] == "superseded"
    assert by[("a", "2026-11-11")]["superseded_by"] == by[("a", "2026-11-12")]["regime_id"]
    assert by[("a", "2026-11-12")]["state"] == "active" and by[("b", "2026-11-11")]["state"] == "active"


def test_invalidate_marks_measurement_and_records():
    store = RegimeStore()
    store.evaluator_commit(_ms(), [_row()])
    out = store.evaluator_invalidate("TW", "2026-11-13", "set-1", 1, "revised_inputs")
    assert len(out["invalidated"]) == 1
    assert store.get_market_state("TW", "2026-11-13", "set-1")["state"] == "invalidated"
    rec = store.list_regimes()[0]["record"]
    assert rec["state"] == "invalidated" and rec["metadata"]["lifecycle"][-1]["reason"] == "revised_inputs"
    assert store.evaluator_invalidate("TW", "2026-11-13", "set-1", 1, "revised_inputs")["invalidated"] == []


def test_reads_return_copies():
    store = RegimeStore()
    store.evaluator_commit(_ms(), [_row()])
    store.list_regimes()[0]["record"]["state"] = "invalidated"
    assert store.list_regimes()[0]["record"]["state"] == "active"


@pytest.mark.skipif(not os.environ.get("REGIME_TEST_POSTGRES_DSN"), reason="needs REGIME_TEST_POSTGRES_DSN")
def test_postgres_backend_creates_both_tables_and_round_trips():
    import psycopg

    dsn = os.environ["REGIME_TEST_POSTGRES_DSN"]
    store = RegimeStore.postgres(dsn)
    with psycopg.connect(dsn) as conn:
        names = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='registry'")}
    assert {"market_state", "regime_states"} <= names
    store.evaluator_commit(_ms("2026-01-02", "set-pg"), [_row("pg", "2026-01-02", version="set-pg")])
    assert store.get_market_state("TW", "2026-01-02", "set-pg")["revision"] == 1
    assert store.list_regimes(strategy_id="pg")[0]["record"]["state"] == "active"
