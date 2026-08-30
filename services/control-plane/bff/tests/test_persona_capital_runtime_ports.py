"""Tests for Persona/Capital/Deployment/Runtime domain ports.

Validates:
1. PersonaFleetPort with a store-shaped object and with a records provider
2. CapitalPoolPort pool + binding reads and filters
3. DeploymentPlanPort plan reads and filters
4. RuntimePort runtime binding reads and lookups
5. get_surface_status() behavior for missing/unavailable/empty/ok states
6. PersonaCapitalRuntimeDomainPort combined facade delegation
"""

import sys
from pathlib import Path

BFF_DIR = Path(__file__).resolve().parent.parent
if str(BFF_DIR) not in sys.path:
    sys.path.insert(0, str(BFF_DIR))

from ports.persona_capital_runtime import (
    CapitalPoolPort,
    DeploymentPlanPort,
    PersonaCapitalRuntimeDomainPort,
    PersonaFleetPort,
    RuntimePort,
)


# ---------------------------------------------------------------------------
# 1. PersonaFleetPort
# ---------------------------------------------------------------------------

class FakePersonaStore:
    def __init__(self, personas):
        self._personas = personas

    def list_personas(self):
        return self._personas

    def get_persona(self, persona_id):
        for p in self._personas:
            if str(p.get("persona_id") or p.get("id")) == persona_id:
                return p
        return None


class TestPersonaFleetPort:
    def test_list_and_get_from_store(self):
        personas = [
            {"persona_id": "persona-a", "lifecycle_state": "active", "mandate": "growth", "strategy_family": "momentum"},
            {"persona_id": "persona-b", "lifecycle_state": "draft", "mandate": "income", "strategy_family": "meanrev"},
        ]
        port = PersonaFleetPort(store=FakePersonaStore(personas))
        assert port.get_surface_status()["status"] == "ok"
        assert port.get_surface_status()["source"] == "store"

        all_personas = port.list_personas()
        assert len(all_personas) == 2

        active = port.list_personas(lifecycle_state="active")
        assert len(active) == 1
        assert active[0]["persona_id"] == "persona-a"

        growth = port.list_personas(mandate="growth")
        assert len(growth) == 1

        momentum = port.list_personas(strategy_family="momentum")
        assert len(momentum) == 1

        found = port.get_persona("persona-b")
        assert found is not None
        assert found["mandate"] == "income"
        assert port.get_persona("nonexistent") is None
        assert port.get_persona(None) is None

    def test_operational_only_filter(self):
        personas = [
            {"persona_id": "persona-live", "lifecycle_state": "live_running"},
            {"persona_id": "persona-draft", "lifecycle_state": "draft"},
            {"persona_id": "persona-paper", "lifecycle_state": "paper"},
        ]
        port = PersonaFleetPort(records_provider=lambda: personas)
        operational = port.list_operational_personas()
        ids = {p["persona_id"] for p in operational}
        assert ids == {"persona-live", "persona-paper"}

    def test_records_provider_source(self):
        records = [{"persona_id": "persona-rec-1", "lifecycle_state": "active"}]
        port = PersonaFleetPort(records_provider=lambda: records)
        status = port.get_surface_status()
        assert status["status"] == "ok"
        assert status["source"] == "service"

    def test_unavailable_state(self):
        port = PersonaFleetPort()
        status = port.get_surface_status()
        assert status["status"] == "unavailable"
        assert port.list_personas() == []
        assert port.get_persona("any") is None

    def test_empty_records_degraded(self):
        port = PersonaFleetPort(records_provider=lambda: [])
        status = port.get_surface_status()
        assert status["status"] == "degraded"

    def test_provider_raises_is_unavailable(self):
        def boom():
            raise RuntimeError("boom")
        port = PersonaFleetPort(records_provider=boom)
        status = port.get_surface_status()
        assert status["status"] == "unavailable"
        assert port.list_personas() == []

    def test_include_market_persona_defaults_accepted_and_filters_applied(self):
        personas = [
            {"persona_id": "p-1", "lifecycle_state": "active", "mandate": "momentum"},
            {"persona_id": "p-2", "lifecycle_state": "draft", "mandate": "income"},
        ]
        port = PersonaFleetPort(records_provider=lambda: personas)
        res = port.list_personas(include_market_persona_defaults=True)
        assert len(res) == 2

        active = port.list_personas(lifecycle_state="active", include_market_persona_defaults=True)
        assert len(active) == 1
        assert active[0]["persona_id"] == "p-1"

    def test_rejects_broad_unsupported_kwargs(self):
        import pytest
        port = PersonaFleetPort(records_provider=lambda: [])
        with pytest.raises(TypeError):
            port.list_personas(unsupported_broad_kwarg=123)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 2. CapitalPoolPort
# ---------------------------------------------------------------------------

class TestCapitalPoolPort:
    def test_pool_and_binding_reads(self):
        pools = [
            {"pool_id": "pool-a", "status": "active"},
            {"pool_id": "pool-b", "status": "frozen"},
        ]
        bindings = [
            {"binding_id": "b-1", "persona_id": "persona-a", "capital_pool_id": "pool-a", "role": "paper_owner", "validity": "active"},
            {"binding_id": "b-2", "persona_id": "persona-b", "capital_pool_id": "pool-a", "role": "live_owner", "validity": "active"},
            {"binding_id": "b-3", "persona_id": "persona-a", "capital_pool_id": "pool-b", "role": "paper_owner", "validity": "revoked"},
        ]
        port = CapitalPoolPort(
            pools_provider=lambda: pools,
            bindings_provider=lambda: bindings,
        )
        status = port.get_surface_status()
        assert status["status"] == "ok"

        assert len(port.list_capital_pools()) == 2
        active_pools = port.list_capital_pools(status="active")
        assert len(active_pools) == 1
        assert active_pools[0]["pool_id"] == "pool-a"

        assert port.get_capital_pool("pool-b")["status"] == "frozen"
        assert port.get_capital_pool("missing") is None

        pool_a_bindings = port.get_bindings_for_pool("pool-a")
        assert len(pool_a_bindings) == 2

        persona_a_bindings = port.get_bindings_for_persona("persona-a")
        assert len(persona_a_bindings) == 2

        live_owner = port.list_bindings(role="live_owner")
        assert len(live_owner) == 1
        assert live_owner[0]["binding_id"] == "b-2"

        active_only = port.list_bindings(validity="active")
        assert len(active_only) == 2

        assert port.get_binding("b-1")["persona_id"] == "persona-a"
        assert port.get_binding("nope") is None

        assert port.get_bindings_for_pool(None) == []
        assert port.get_bindings_for_persona(None) == []

    def test_store_shaped_source(self):
        class FakeCapitalStore:
            def list_capital_pools(self):
                return [{"pool_id": "pool-store"}]

            def list_bindings(self):
                return [{"binding_id": "b-store", "persona_id": "p1", "capital_pool_id": "pool-store"}]

        port = CapitalPoolPort(store=FakeCapitalStore())
        status = port.get_surface_status()
        assert status["status"] == "ok"
        assert status["source"] == "store"
        assert port.list_capital_pools()[0]["pool_id"] == "pool-store"

    def test_unavailable_and_degraded(self):
        port = CapitalPoolPort()
        status = port.get_surface_status()
        assert status["status"] == "unavailable"

        port2 = CapitalPoolPort(pools_provider=lambda: [], bindings_provider=lambda: [])
        status2 = port2.get_surface_status()
        assert status2["status"] == "degraded"

    def test_include_market_persona_defaults_accepted(self):
        pools = [{"pool_id": "pool-1", "status": "active"}]
        bindings = [{"binding_id": "b-1", "persona_id": "p-1", "capital_pool_id": "pool-1", "role": "paper_owner"}]
        port = CapitalPoolPort(pools_provider=lambda: pools, bindings_provider=lambda: bindings)
        assert len(port.list_capital_pools(include_market_persona_defaults=True)) == 1
        assert len(port.list_bindings(include_market_persona_defaults=True)) == 1

    def test_rejects_broad_unsupported_kwargs(self):
        import pytest
        port = CapitalPoolPort(pools_provider=lambda: [], bindings_provider=lambda: [])
        with pytest.raises(TypeError):
            port.list_capital_pools(unsupported_kwarg=123)  # type: ignore[call-arg]
        with pytest.raises(TypeError):
            port.list_bindings(unsupported_kwarg=123)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# 3. DeploymentPlanPort
# ---------------------------------------------------------------------------

class TestDeploymentPlanPort:
    def test_list_and_get_with_filters(self):
        plans = [
            {"plan_id": "plan-1", "status": "pending_review", "capital_pool_id": "pool-a"},
            {"plan_id": "plan-2", "status": "Active", "target_pool_id": "pool-b"},
        ]
        port = DeploymentPlanPort(plans_provider=lambda: plans)
        assert port.get_surface_status()["status"] == "ok"

        assert len(port.list_deployment_plans()) == 2
        active = port.list_deployment_plans(status="active")
        assert len(active) == 1
        assert active[0]["plan_id"] == "plan-2"

        pool_b = port.list_deployment_plans(capital_pool_id="pool-b")
        assert len(pool_b) == 1
        assert pool_b[0]["plan_id"] == "plan-2"

        assert port.get_deployment_plan("plan-1")["status"] == "pending_review"
        assert port.get_deployment_plan("missing") is None
        assert port.get_deployment_plan(None) is None

    def test_unavailable(self):
        port = DeploymentPlanPort()
        assert port.get_surface_status()["status"] == "unavailable"
        assert port.list_deployment_plans() == []


# ---------------------------------------------------------------------------
# 4. RuntimePort
# ---------------------------------------------------------------------------

class TestRuntimePort:
    def test_list_and_lookups(self):
        bindings = [
            {"runtime_id": "rt-1", "binding_id": "bind-1", "deployment_mode": "paper", "version": "1.0.0"},
            {"runtime_id": "rt-2", "id": "bind-2", "deployment_mode": "live", "version": "2.0.0"},
        ]
        port = RuntimePort(runtime_bindings_provider=lambda: bindings)
        assert port.get_surface_status()["status"] == "ok"

        assert len(port.list_runtime_bindings()) == 2
        paper = port.list_runtime_bindings(deployment_mode="paper")
        assert len(paper) == 1
        assert paper[0]["runtime_id"] == "rt-1"

        v2 = port.list_runtime_bindings(version="2.0.0")
        assert len(v2) == 1

        found = port.get_runtime_binding("bind-1")
        assert found is not None
        assert found["runtime_id"] == "rt-1"

        found2 = port.get_runtime_binding_by_runtime_id("rt-2")
        assert found2 is not None
        assert found2["deployment_mode"] == "live"

        assert port.get_runtime_binding("missing") is None
        assert port.get_runtime_binding_by_runtime_id(None) is None

    def test_store_shaped_source(self):
        class FakeRuntimeStore:
            def list_runtime_bindings(self):
                return [{"runtime_id": "rt-store"}]

        port = RuntimePort(store=FakeRuntimeStore())
        status = port.get_surface_status()
        assert status["source"] == "store"
        assert port.list_runtime_bindings()[0]["runtime_id"] == "rt-store"

    def test_include_market_persona_defaults_accepted(self):
        bindings = [{"runtime_id": "rt-1", "deployment_mode": "paper"}]
        port = RuntimePort(runtime_bindings_provider=lambda: bindings)
        assert len(port.list_runtime_bindings(include_market_persona_defaults=True)) == 1

    def test_rejects_broad_unsupported_kwargs(self):
        import pytest
        port = RuntimePort(runtime_bindings_provider=lambda: [])
        with pytest.raises(TypeError):
            port.list_runtime_bindings(unsupported_kwarg=123)  # type: ignore[call-arg]

    def test_unavailable(self):
        port = RuntimePort()
        assert port.get_surface_status()["status"] == "unavailable"
        assert port.list_runtime_bindings() == []


# ---------------------------------------------------------------------------
# 5. Combined PersonaCapitalRuntimeDomainPort
# ---------------------------------------------------------------------------

class TestPersonaCapitalRuntimeDomainPortDelegation:
    def test_combined_facade_delegates(self):
        port = PersonaCapitalRuntimeDomainPort(
            persona_port=PersonaFleetPort(records_provider=lambda: [{"persona_id": "persona-1", "lifecycle_state": "active"}]),
            capital_port=CapitalPoolPort(
                pools_provider=lambda: [{"pool_id": "pool-1"}],
                bindings_provider=lambda: [{"binding_id": "b-1", "persona_id": "persona-1", "capital_pool_id": "pool-1"}],
            ),
            deployment_port=DeploymentPlanPort(plans_provider=lambda: [{"plan_id": "plan-1", "status": "active"}]),
            runtime_port=RuntimePort(runtime_bindings_provider=lambda: [{"runtime_id": "rt-1"}]),
        )

        assert len(port.list_personas()) == 1
        assert port.get_persona("persona-1")["persona_id"] == "persona-1"
        assert port.list_operational_personas()[0]["persona_id"] == "persona-1"

        assert len(port.list_capital_pools()) == 1
        assert port.get_capital_pool("pool-1")["pool_id"] == "pool-1"
        assert len(port.list_bindings()) == 1
        assert len(port.get_bindings_for_pool("pool-1")) == 1
        assert len(port.get_bindings_for_persona("persona-1")) == 1

        assert len(port.list_deployment_plans()) == 1
        assert port.get_deployment_plan("plan-1")["status"] == "active"

        assert len(port.list_runtime_bindings()) == 1
        assert port.get_runtime_binding_by_runtime_id("rt-1")["runtime_id"] == "rt-1"

        surface = port.get_surface_status()
        assert surface["persona"]["status"] == "ok"
        assert surface["capital"]["status"] == "ok"
        assert surface["deployment"]["status"] == "ok"
        assert surface["runtime"]["status"] == "ok"
        # Ranking and evolution default (unconstructed) ports report unavailable.
        assert surface["ranking"]["status"] == "unavailable"
        assert surface["evolution"]["status"] == "unavailable"

    def test_combined_facade_defaults_to_unavailable_subports(self):
        port = PersonaCapitalRuntimeDomainPort()
        assert port.list_personas() == []
        assert port.list_capital_pools() == []
        assert port.list_deployment_plans() == []
        assert port.list_runtime_bindings() == []
        assert port.get_persona("anything") is None
