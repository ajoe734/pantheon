"""HTTP proof: evaluate and read against real stored rows, with the registry's own authentication."""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

# Shares fixtures with test_evaluator, which needs the fitted-model stack; skip where absent.
for _m in ("numpy", "pandas", "scipy", "statsmodels"):
    pytest.importorskip(_m)

from fastapi.testclient import TestClient

from services.governance.test_approval_authority import advance_registry_http
from services.registry import main as registry_service
from services.registry import service as registry_auth
from services.registry.regime import routes
from services.registry.regime.store import WRITE_METHODS, get_regime_store
from services.registry.regime.tests import test_evaluator as S
from services.registry.storage import reset_store
from services.runtime_auth_inbound import encode_jwt_hs256

SECRET = "regime-routes-test-secret"
PKG = Path(registry_auth.__file__).resolve().parent
_REAL_LOADER = routes._observations_loader


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setenv("PANTHEON_REGISTRY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_SECRET", SECRET)
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_ISSUER", "")
    monkeypatch.setenv("PANTHEON_REGISTRY_JWT_AUDIENCE", "")
    reset_store()
    yield
    reset_store()


def _jwt(tenant="tenant-a", roles=("operator",), sub="op-1"):
    return {"Authorization": "Bearer " + encode_jwt_hs256(
        {"sub": sub, "tenant": tenant, "roles": list(roles), "exp": time.time() + 3600}, secret=SECRET)}


@pytest.fixture
def client():
    return TestClient(registry_service.app)


def _register_models(client, headers, state="approved"):
    for entry in S.model_entries("draft"):
        meta = entry["metadata"]
        created = client.post("/api/registry/entries", headers=headers, json={
            "artifact_type": "model_artifact", "strategy_id": f"regime-tw-{meta['axis']}-{meta['horizon']}", "version": "1.0.0",
            "storage_ref": {"backend": "inline", "path": "inline:m"}, "checksum": "sha256:" + "a" * 64,
            "lineage": {"source_run_ids": ["run-1"]}, "metadata": meta})
        assert created.status_code == 200, created.text
        rid = created.json()["entry"]["registry_id"]
        for target, expected in (("candidate", "draft"), ("approved", "candidate")):
            if state == "candidate" and target == "approved":
                break
            # Governance approval transport double: the existing registry-test pattern (the real
            # decision reader needs a Governance service, which is not part of this task).
            r = advance_registry_http(client, f"/api/registry/entries/{rid}/advance", headers=headers, json={
                "target_state": target, "expected_artifact_state": expected, "command_key": f"{rid}-{target}"})
            assert r.status_code == 200, r.text


def _register_spec(client, headers, strategy_id, **meta):
    spec = {"spec_version": "1.0", "strategy_id": strategy_id, "title": "Regime route probe",
            "hypothesis": "Deterministic probe hypothesis for regime route tests.", "objective": "Probe.",
            "market_scope": {"symbols": ["TEST"], "frequency": "1d"}, "data_dependencies": [{"ref": "fx", "kind": "note"}],
            "execution_profile": {"signal_schema_version": "1.0", "quantity_type": "SHARES"},
            "evaluation_plan": {"metrics": ["sharpe"]}, "governance": {"approval_required": True},
            "provenance": {"source_kind": "manual", "created_at": "2026-01-01T00:00:00Z"}}
    if meta:
        spec["metadata"] = meta
    r = client.post("/api/registry/strategy-specs", headers=headers, json={
        "strategy_id": strategy_id, "version": "1.0.0", "lineage": {"source_run_ids": ["run-1"]}, "strategy_spec": spec})
    assert r.status_code == 200, r.text


@pytest.fixture
def seeded(client, monkeypatch):
    monkeypatch.setattr(routes, "_observations_loader", lambda: (lambda: S.observations()))
    a, b = _jwt("tenant-a"), _jwt("tenant-b", sub="op-2")
    _register_models(client, a)
    _register_spec(client, a, "strat-a", regime={"primary_axis": "volatility", "horizon": "short"})
    _register_spec(client, b, "strat-b")
    return a, b, S.sessions()[-1]


def test_evaluate_then_read_real_rows(client, seeded):
    a, b, session = seeded
    r = client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    assert r.status_code == 200, r.text
    body = r.json()["sets"][0]
    assert body["model_status"] == "approved" and body["computed"] is True and len(body["new_regime_ids"]) == 1
    # Stored rows are what GET serves.
    rows = get_regime_store().list_regimes(served_only=False)
    assert [x["record"]["state"] for x in rows] == ["active"]
    got = client.get("/api/registry/regime/states", headers=a, params={"strategy_id": "strat-a"}).json()["regime_states"]
    assert got == [rows[0]["record"]] and got[0]["feature_snapshot"]["profile"]["primary_axis"] == "volatility"
    active = client.get("/api/registry/regime/strategies/strat-a/active", headers=a)
    assert active.status_code == 200 and active.json()["regime_id"] == got[0]["regime_id"]
    ms = client.get(f"/api/registry/regime/market-state/TW/{session}", headers=a).json()["market_states"]
    assert ms[0]["axes"]["volatility"]["short"]["available"]


def test_second_evaluate_returns_stored_result_and_gets_never_write(client, seeded, monkeypatch):
    a, _, session = seeded
    client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    store = get_regime_store()
    before = (store.list_regimes(served_only=False), store.list_market_states("TW", served_only=False))
    again = client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    assert again.status_code == 200 and again.json()["sets"][0]["computed"] is False
    for path in ("/api/registry/regime/states", f"/api/registry/regime/market-state/TW/{session}",
                 "/api/registry/regime/strategies/strat-a/active"):
        assert client.get(path, headers=a).status_code == 200
    after = (store.list_regimes(served_only=False), store.list_market_states("TW", served_only=False))
    assert after == before


def test_get_before_evaluate_serves_nothing_and_does_not_compute(client, seeded):
    a, _, session = seeded
    assert client.get(f"/api/registry/regime/market-state/TW/{session}", headers=a).status_code == 404
    assert client.get("/api/registry/regime/strategies/strat-a/active", headers=a).status_code == 404
    assert client.get("/api/registry/regime/states", headers=a).json() == {"regime_states": []}
    assert get_regime_store().list_regimes(served_only=False) == []


def test_tenant_scoping_of_strategy_records(client, seeded):
    a, b, session = seeded
    client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    client.post("/api/registry/regime/evaluate", headers=b, json={"session": session})
    mine = [r["strategy_id"] for r in client.get("/api/registry/regime/states", headers=a).json()["regime_states"]]
    theirs = [r["strategy_id"] for r in client.get("/api/registry/regime/states", headers=b).json()["regime_states"]]
    assert mine == ["strat-a"] and theirs == ["strat-b"]
    assert client.get("/api/registry/regime/strategies/strat-b/active", headers=a).status_code == 404
    assert client.get("/api/registry/regime/states", headers={**a, "X-Tenant-Id": "tenant-b"}).status_code == 403


def test_candidate_models_store_proposed_and_serve_nothing(client, monkeypatch):
    monkeypatch.setattr(routes, "_observations_loader", lambda: (lambda: S.observations()))
    a = _jwt()
    _register_models(client, a, state="candidate")
    _register_spec(client, a, "strat-a")
    session = S.sessions()[-1]
    r = client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    assert r.status_code == 200 and r.json()["sets"][0]["model_status"] == "candidate"
    assert [x["record"]["state"] for x in get_regime_store().list_regimes(served_only=False)] == ["proposed"]
    assert client.get("/api/registry/regime/states", headers=a).json() == {"regime_states": []}
    assert client.get(f"/api/registry/regime/market-state/TW/{session}", headers=a).status_code == 404


def test_refusals_store_nothing(client, seeded, monkeypatch):
    a, _, session = seeded
    assert client.post("/api/registry/regime/evaluate", headers=a, json={"session": "not-a-date"}).status_code == 422
    assert client.post("/api/registry/regime/evaluate", headers=a, json={"session": session, "market": "US"}).status_code == 422
    empty = [o for o in S.observations() if o["dataset"] != "tw_index_daily"]
    monkeypatch.setattr(routes, "_observations_loader", lambda: (lambda: empty))
    r = client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "session_data_incomplete"
    assert get_regime_store().list_regimes(served_only=False) == []


def test_authentication_is_the_registrys_own(client, seeded):
    _, _, session = seeded
    assert client.post("/api/registry/regime/evaluate", json={"session": session}).status_code == 401
    assert client.get("/api/registry/regime/states").status_code == 401
    reader = _jwt(roles=("registry-reader",))
    assert client.post("/api/registry/regime/evaluate", headers=reader, json={"session": session}).status_code == 403
    assert client.get("/api/registry/regime/states", headers=reader).status_code == 200


def test_only_the_evaluator_route_mutates_regime_tables():
    """No other route writes the two tables: one regime POST, no other mutating regime verb, and no
    caller of the store's write methods outside the evaluator module."""
    paths = registry_service.app.openapi()["paths"]
    mutating = {(m.upper(), p) for p, ops in paths.items() if "regime" in p for m in ops if m.lower() != "get"}
    assert mutating == {("POST", "/api/registry/regime/evaluate")}
    assert {p for p in paths if "regime" in p} == {
        "/api/registry/regime/evaluate", "/api/registry/regime/market-state/{market}/{session}",
        "/api/registry/regime/states", "/api/registry/regime/strategies/{strategy_id}/active"}
    callers = []
    for path in PKG.rglob("*.py"):
        if "tests" in path.parts or path.name in ("evaluator.py", "store.py"):
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in WRITE_METHODS:
                callers.append(f"{path.name}:{node.attr}")
    assert callers == []
    # The evaluator module is the one caller of the write methods.
    ev = ast.parse((PKG / "regime" / "evaluator.py").read_text(encoding="utf-8"))
    assert {n.attr for n in ast.walk(ev) if isinstance(n, ast.Attribute) and n.attr in WRITE_METHODS} == set(WRITE_METHODS)
    # Nothing outside the store touches the table owners directly.
    for path in PKG.rglob("*.py"):
        if path.name == "store.py" or "tests" in path.parts:
            continue
        assert "registry.market_state" not in path.read_text(encoding="utf-8") or path.name == "evaluator.py"


def test_regime_evaluator_role_is_accepted_on_evaluate_only(client, seeded):
    _, _, session = seeded
    evaluator = _jwt(roles=("registry-reader", "regime-evaluator"), sub="persona-eval")
    r = client.post("/api/registry/regime/evaluate", headers=evaluator, json={"session": session})
    assert r.status_code == 200, r.text
    # Idempotent per session+market: a second evaluate stores no new record.
    n = len(get_regime_store().list_regimes(served_only=False))
    assert client.post("/api/registry/regime/evaluate", headers=evaluator, json={"session": session}).status_code == 200
    assert len(get_regime_store().list_regimes(served_only=False)) == n
    # Refused on every other registry mutation route (role is in no write allowlist).
    only = _jwt(roles=("regime-evaluator",))
    assert "regime-evaluator" not in registry_auth._REGISTRY_WRITE_ROLES
    assert "regime-evaluator" not in registry_auth._REGISTRY_READ_ROLES
    for path, method in sorted({(p, m.upper()) for p, ops in registry_service.app.openapi()["paths"].items()
                                for m in ops if m.lower() in ("post", "put", "patch", "delete")}):
        if path == "/api/registry/regime/evaluate":
            continue
        resp = client.request(method, path.replace("{", "x").replace("}", ""), headers=evaluator, json={})
        assert resp.status_code in (401, 403, 404, 405, 422), (method, path, resp.status_code)
        assert resp.status_code != 200, (method, path)
    assert client.get("/api/registry/regime/states", headers=only).status_code == 403


# -- hosted inputs and memory (REGIME-EVALUATE-HOSTED-INPUTS-AND-MEMORY-20261119) ------------------------

HOSTED_ENTRY_COUNT = 7200  # registry.entries had 7162 rows on 2026-11-19
EVALUATE_PEAK_BOUND_BYTES = 24 * 1024 * 1024  # tracemalloc peak of one evaluate call at hosted entry count


def _inflate_registry(client, headers, count):
    """Add ``count`` unrelated registry entries (allocation-policy type, padded metadata) to the store."""
    from services.registry import service as svc
    from services.registry.models import ArtifactType, RegistryEntry

    store = svc.get_registry_service().store
    template = next(iter(store.list_all_entries())).to_dict()
    for i in range(count):
        raw = dict(template, registry_id=f"filler-{i}", strategy_id=f"filler-{i}",
                   artifact_type=ArtifactType.ALLOCATION_POLICY.value,
                   metadata={"pad": "x" * 2000, "n": i, "regime_classifier": False})
        entry = RegistryEntry.from_dict(raw)
        store._entries[entry.registry_id] = entry
        store._strategy_index.setdefault(entry.strategy_id, []).append(entry.registry_id)


def test_evaluate_never_materializes_every_registry_entry(client, seeded, monkeypatch):
    import tracemalloc

    a, _, session = seeded
    _inflate_registry(client, a, HOSTED_ENTRY_COUNT)
    from services.registry import service as svc

    def forbidden(self):
        raise AssertionError("evaluate must not call list_all_entries")

    monkeypatch.setattr(type(svc.get_registry_service()), "list_all_entries", forbidden)
    tracemalloc.start()
    try:
        r = client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert r.status_code == 200, r.text
    assert peak < EVALUATE_PEAK_BOUND_BYTES, f"peak {peak} bytes"


def test_missing_inputs_refuse_before_any_registry_read(client, seeded, monkeypatch, tmp_path):
    a, _, session = seeded
    from services.registry import service as svc

    root = tmp_path / "market_data_store"
    (root / "normalized" / "tw_index_daily").mkdir(parents=True)
    monkeypatch.setenv(routes.STORE_ROOT_ENV, str(root))
    monkeypatch.setattr(routes, "_observations_loader", _REAL_LOADER)  # the real store reader, not the seeded double

    def forbidden(*_a, **_k):
        raise AssertionError("registry entries read before inputs were checked")

    cls = type(svc.get_registry_service())
    for name in ("list_all_entries", "list_by_strategy"):
        monkeypatch.setattr(cls, name, forbidden)
    monkeypatch.setattr(cls.__mro__[0], "_to_view", forbidden)  # any entry view built means entries were read
    r = client.post("/api/registry/regime/evaluate", headers=a, json={"session": session})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "session_data_incomplete"
    expected = [d for d in routes_inputs() if d != "tw_index_daily"]
    assert detail["detail"] == "missing_datasets:" + ",".join(expected)


def routes_inputs():
    from services.research.regime.features import REGIME_INPUT_DATASETS

    return list(REGIME_INPUT_DATASETS)


def test_default_root_is_the_market_data_store_and_matches_compose_and_contract():
    import re

    repo = PKG.parents[1]
    assert routes.DEFAULT_STORE_ROOT == "/data/source-ingest/market_data_store"
    compose = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(r"REGISTRY_REGIME_STORE_ROOT: \$\{REGISTRY_REGIME_STORE_ROOT:-/data/source-ingest/market_data_store\}", compose)
    assert "/data/source-ingest/market_data_store" in (PKG / "contract.md").read_text(encoding="utf-8")


def test_reader_follows_source_ingest_run_file_and_keyed_trees(tmp_path, monkeypatch):
    from services.research.regime.features import REGIME_INPUT_DATASETS, missing_input_datasets
    from services.source_ingestion.daily_bars_store import KEYED_DATASETS

    root = tmp_path / "market_data_store"
    assert missing_input_datasets(root) == list(REGIME_INPUT_DATASETS)
    for d in REGIME_INPUT_DATASETS:
        (root / "normalized" / d).mkdir(parents=True)
    assert missing_input_datasets(root) == []
    monkeypatch.setenv("PANTHEON_KEYED_DATASET_READER", "keyed")
    keyed = [d for d in REGIME_INPUT_DATASETS if d in KEYED_DATASETS]
    assert keyed and set(missing_input_datasets(root)) == set(keyed)
    for d in keyed:
        (root / "keyed" / d).mkdir(parents=True)
    assert missing_input_datasets(root) == []
