"""Capital policy and ownership regressions on real owner/route boundaries."""
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from services.capital.capital_guard import CapitalGuardError
from services.capital.risk_policy import RiskPolicy, RiskPolicyError, RiskPolicyEvaluator
from services.capital.test_capital_guard import KW, _guard
from services.capital.test_capital_guard_routes import _ACT, _BIND, _policy, _seed
from services.capital.test_service import _apply_payload, _rebalance_payload, pg_identity_dsn, client  # noqa: F401
from services.capital.test_tenant_scoping import _auth_headers, capital_test_env  # noqa: F401


@pytest.mark.parametrize("field", [
    "gross_limit", "net_limit", "max_single_name_weight", "max_single_weight",
    "max_leverage", "turnover_limit", "max_target_overlap", "max_signal_correlation",
    "max_pairwise_correlation", "max_canary_capital_scale_pct", "max_canary_gross_scale_pct",
])
@pytest.mark.parametrize("value", ["bad", "", "NaN", float("inf"), -float("inf"), True])
def test_policy_parser_rejects_malformed_scalar_limits(field, value):
    with pytest.raises(RiskPolicyError, match="Malformed risk policy"):
        RiskPolicy.from_mapping({field: value})


@pytest.mark.parametrize("field", [
    "max_sector_exposure", "max_factor_exposure", "max_strategy_family_concentration",
    "liquidity_constraints", "drawdown_actions", "pause_rules", "liquidation_rules",
])
@pytest.mark.parametrize("value", [{"x": None}, {"x": "NaN"}, {"x": "Infinity"}, {"x": False}])
def test_policy_parser_never_drops_invalid_map_entries(field, value):
    with pytest.raises(RiskPolicyError, match="Malformed risk policy"):
        RiskPolicy.from_mapping({field: value})


@pytest.mark.parametrize("canonical,alias", [
    ("max_single_name_weight", "max_single_weight"),
    ("max_signal_correlation", "max_pairwise_correlation"),
])
def test_alias_limits_and_canonical_precedence(canonical, alias):
    assert getattr(RiskPolicy.from_mapping({alias: "0.3"}), canonical) == 0.3
    assert getattr(RiskPolicy.from_mapping({canonical: 0, alias: 0.3}), canonical) == 0
    assert getattr(RiskPolicy.from_mapping({canonical: None, alias: 0.3}), canonical) is None
    with pytest.raises(RiskPolicyError):
        RiskPolicy.from_mapping({canonical: 0.3, alias: "NaN"})


def test_guard_parses_policy_once_for_multiple_contexts():
    with patch.object(RiskPolicy, "from_mapping", wraps=RiskPolicy.from_mapping) as parse:
        _guard().authorize(**{**KW, "contexts": [{"stage": "paper"}, {"stage": "live"}]})
    assert parse.call_count == 1


@pytest.mark.parametrize("target", ["capital_pool_activation", "capital_binding_activation", "rebalance_apply"])
def test_evaluator_rejects_invalid_partial_observations_before_normalization(target):
    with pytest.raises(RiskPolicyError, match="target_weights unavailable"):
        RiskPolicyEvaluator().evaluate(
            {"max_single_weight": 0.5},
            {"target_type": target, "target_weights": {"good": 0.1, "unknown": "NaN"}},
        )


@pytest.mark.parametrize("tenant", ["", None, "tenant-b"])
def test_guard_formal_ownership_cannot_adopt_metadata(tenant):
    pool = SimpleNamespace(**{**vars(KW["pool"]), "tenant_id": tenant, "metadata": {"tenant_id": "tenant-a"}})
    with pytest.raises(CapitalGuardError, match="tenant"):
        _guard().authorize(**{**KW, "pool": pool})


@pytest.mark.parametrize("operation", ["pool", "binding", "rebalance"])
@pytest.mark.parametrize("limits", [
    {"gross_limit": "NaN"}, {"max_single_weight": "Infinity"},
    {"max_pairwise_correlation": "bad"}, {"liquidity_constraints": {"min_avg_daily_volume": 10}},
    {"drawdown_actions": {"risk_off": 0.1}}, {"max_canary_capital_scale_pct": 5},
    {"max_canary_gross_scale_pct": 10},
])
def test_routes_reject_malformed_or_missing_facts_despite_metadata(client, monkeypatch, operation, limits):
    c, _ = client
    _seed(c, lines=[{**_rebalance_payload()["lines"][0], "stage": "canary_running"}])
    module = sys.modules["services.capital.main"]
    pool = module.pool_store.require("pool-001")
    binding = module.binding_store.require("binding-001")
    # Even plausibly fresh caller metadata is not an authenticated observation.
    forged = {"liquidity": {"avg_daily_volume": 1000000}, "drawdown_pct": 0,
              "capital_scale_pct": 1, "gross_scale_pct": 1, "observed_at": "2099-01-01T00:00:00Z"}
    pool.metadata.update(forged)
    binding.metadata.update(forged)
    object.__setattr__(binding, "allowed_deployment_scope", "canary")
    if operation == "pool":
        assert c.post("/api/bindings/binding-001/activate", json=_BIND).status_code == 200
        assert c.patch("/api/capital-pools/pool-001/status", json={**_ACT, "status": "suspended"}).status_code == 200
    elif operation == "rebalance":
        # Existing canary binding is part of the complete projected pool context.
        assert c.post("/api/bindings/binding-001/activate", json=_BIND).status_code == 200
    _policy(monkeypatch, **limits)
    if operation == "pool":
        response = c.patch("/api/capital-pools/pool-001/status", json=_ACT)
        assert module.pool_store.require("pool-001").status == "suspended"
    elif operation == "binding":
        response = c.post("/api/bindings/binding-001/activate", json=_BIND)
        assert module.binding_store.require("binding-001").status == "pending"
    else:
        response = c.post("/api/rebalances/rb-001/apply", json=_apply_payload())
        assert module.allocation_authority_store.list_allocations() == []
    assert response.status_code == 403, response.text
    assert "Malformed risk policy" in response.text or "unavailable" in response.text


@pytest.mark.parametrize("formal", ["tenant-alpha", "", None])
@pytest.mark.parametrize("postgres", [False, True])
def test_authenticated_routes_and_guard_agree_on_formal_tenant(capital_test_env, formal, postgres, request, monkeypatch):
    c, module, _ = capital_test_env
    if postgres:
        import uuid
        from services.capital.pg_store import PostgresCapitalPoolStore, PostgresPersonaCapitalBindingStore
        dsn = request.getfixturevalue("pg_identity_dsn")
        suffix = uuid.uuid4().hex[:8]
        monkeypatch.setattr(module, "pool_store", PostgresCapitalPoolStore(dsn, table="capital.policy_pools_" + suffix))
        monkeypatch.setattr(module, "binding_store", PostgresPersonaCapitalBindingStore(dsn, table="capital.policy_bindings_" + suffix))
    headers = _auth_headers("tenant-alpha", actor_id="owner")
    payload = {"actor_id": "owner", "actor_role": "capital.admin", "pool_id": "formal-pool",
               "name": "Formal", "owner_id": "org", "owner_type": "org", "status": "suspended",
               "metadata": {"execution_context": "paper"}}
    assert c.post("/api/capital-pools", headers=headers, json=payload).status_code == 201
    binding_payload = {"actor_id": "owner", "actor_role": "persona.admin", "binding_id": "formal-binding",
                       "persona_id": "persona", "capital_pool_id": "formal-pool",
                       "role": "paper_owner", "allowed_deployment_scope": "paper"}
    assert c.post("/api/bindings", headers=headers, json=binding_payload).status_code == 201
    pool = module.pool_store.require("formal-pool")
    binding = module.binding_store.require("formal-binding")
    for entity in (pool, binding):
        object.__setattr__(entity, "tenant_id", formal)
        entity.metadata["tenant_id"] = "tenant-beta"
    if postgres:
        import json
        for store, entity, record_id in ((module.pool_store, pool, "formal-pool"), (module.binding_store, binding, "formal-binding")):
            with store._records._connect() as conn:
                conn.execute(f"UPDATE {store._records.table} SET tenant_id=%s, payload=%s::jsonb WHERE record_id=%s",
                             (formal, json.dumps(entity.to_dict()), record_id))
            # A new owner instance reloads the formal column, including null/blank.
            replacement = type(store)(dsn, table=store._records.table_name)
            monkeypatch.setattr(module, "pool_store" if record_id == "formal-pool" else "binding_store", replacement)
    for tenant in ("tenant-alpha", "tenant-beta"):
        auth = _auth_headers(tenant, actor_id="owner")
        expected = 200 if tenant == formal else 404
        for path in ("/api/capital-pools/formal-pool", "/api/bindings/formal-binding"):
            response = c.get(path, headers=auth)
            assert response.status_code == expected
            if expected == 200:
                assert response.json()["tenant_id"] == formal
        response = c.patch("/api/capital-pools/formal-pool/status", headers=auth,
                           json={"actor_id": "owner", "actor_role": "capital.admin", "status": "active"})
        assert response.status_code == expected, response.text
        response = c.post("/api/bindings/formal-binding/activate", headers=auth,
                          json={"actor_id": "owner", "actor_role": "persona.admin"})
        assert response.status_code == expected, response.text
    if not formal:
        assert pool.status == "suspended" and binding.status == "pending"


def test_isolated_legacy_json_reload_and_idempotent_owner_replay(capital_test_env):
    c, module, _ = capital_test_env
    headers = _auth_headers("tenant-alpha", actor_id="owner")
    payload = {"actor_id": "owner", "actor_role": "capital.admin", "pool_id": "legacy-pool",
               "name": "Legacy", "owner_id": "org", "owner_type": "org", "status": "active",
               "idempotency_key": "legacy-pool-create", "request_hash": "legacy-create-v1", "metadata": {"execution_context": "paper", "tenant_id": "forged"}}
    assert c.post("/api/capital-pools", headers=headers, json=payload).status_code == 201
    module.pool_store._pools.clear()
    module.pool_store._load(module.POOL_STORE_PATH)
    pool = module.pool_store.require("legacy-pool")
    assert not hasattr(pool, "tenant_id")  # explicit isolated JSON compatibility boundary
    assert pool.metadata["tenant_id"] == "tenant-alpha"  # owner stamped, not caller supplied
    response = c.post("/api/capital-pools", headers=headers, json=payload)
    assert response.status_code == 201 and response.json()["idempotent_replay"]
    module.capital_guard.authorize(pool=pool, tenant_id="tenant-alpha", decision_id=None,
                                   target_type="capital_pool_activation", target_id=pool.pool_id, expected={})


def test_pg_record_adapter_does_not_promote_metadata_to_formal_owner():
    from services.capital.pg_store import _fetch_records, _put_record

    class Records:
        def __init__(self):
            self.records = []

        def list_all(self):
            return self.records

        def put(self, key, payload):
            self.records = [payload]

    records = Records()
    payload = {"pool_id": "pool", "metadata": {"tenant_id": "tenant-b"}}
    for formal in ("tenant-a", "", None):
        _put_record(records, "pool", payload, formal)
        assert _fetch_records(records, "pool_id")[0][2] == formal
    records.records = [payload]
    assert _fetch_records(records, "pool_id")[0][2] is None
