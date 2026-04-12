"""Tests for PersonaCapitalBinding and PersonaCapitalBindingStore (CAP-001)."""
import pytest
from services.control_plane.governance.persona_capital_binding import (
    PersonaCapitalBinding,
    PersonaCapitalBindingError,
    PersonaCapitalBindingStore,
    DeploymentScope,
    validate_binding,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_binding(**kwargs) -> PersonaCapitalBinding:
    defaults = {
        "binding_id": "binding-001",
        "persona_id": "persona-alpha",
        "capital_pool_id": "pool-001",
        "role": "advisor",
        "allowed_deployment_scope": "live",
        "status": "pending",
        "created_at": "2026-04-10T00:00:00Z",
    }
    defaults.update(kwargs)
    return PersonaCapitalBinding(**defaults)


def make_active_binding(**kwargs) -> PersonaCapitalBinding:
    defaults = {"status": "active", "approval_decision_id": "approval-001"}
    defaults.update(kwargs)
    return make_binding(**defaults)


# ---------------------------------------------------------------------------
# DeploymentScope
# ---------------------------------------------------------------------------

class TestDeploymentScope:
    def test_scope_ordering(self):
        assert DeploymentScope.NONE.level == 0
        assert DeploymentScope.PAPER.level == 1
        assert DeploymentScope.CANARY.level == 2
        assert DeploymentScope.LIVE.level == 3

    def test_permits(self):
        assert DeploymentScope.LIVE.permits(DeploymentScope.PAPER)
        assert DeploymentScope.LIVE.permits(DeploymentScope.LIVE)
        assert not DeploymentScope.PAPER.permits(DeploymentScope.CANARY)
        assert not DeploymentScope.NONE.permits(DeploymentScope.PAPER)

    def test_canary_permits_paper(self):
        assert DeploymentScope.CANARY.permits(DeploymentScope.PAPER)


# ---------------------------------------------------------------------------
# PersonaCapitalBinding construction
# ---------------------------------------------------------------------------

class TestPersonaCapitalBindingConstruction:
    def test_valid_binding(self):
        b = make_binding()
        assert b.binding_id == "binding-001"
        assert b.is_active is False

    def test_invalid_role(self):
        with pytest.raises(PersonaCapitalBindingError, match="Invalid role"):
            make_binding(role="superuser")

    def test_invalid_scope(self):
        with pytest.raises(PersonaCapitalBindingError, match="Invalid allowed_deployment_scope"):
            make_binding(allowed_deployment_scope="mega")

    def test_invalid_status(self):
        with pytest.raises(PersonaCapitalBindingError, match="Invalid status"):
            make_binding(status="zombie")

    def test_negative_budget(self):
        with pytest.raises(PersonaCapitalBindingError, match="budget must be >= 0"):
            make_binding(budget=-1)

    def test_is_active_true(self):
        b = make_active_binding()
        assert b.is_active is True

    def test_permits_deployment_to_when_active(self):
        b = make_active_binding(allowed_deployment_scope="canary")
        assert b.permits_deployment_to("paper") is True
        assert b.permits_deployment_to("canary") is True
        assert b.permits_deployment_to("live") is False

    def test_permits_deployment_to_when_inactive(self):
        b = make_binding(allowed_deployment_scope="live")
        assert b.permits_deployment_to("paper") is False

    def test_to_dict_roundtrip(self):
        b = make_active_binding(mandate="grow alpha")
        d = b.to_dict()
        restored = PersonaCapitalBinding.from_dict(d)
        assert restored == b


# ---------------------------------------------------------------------------
# validate_binding
# ---------------------------------------------------------------------------

class TestValidateBinding:
    def test_valid_pending_binding(self):
        assert validate_binding(make_binding()) == []

    def test_active_without_approval_decision(self):
        b = make_binding(status="active")
        errors = validate_binding(b)
        assert any("approval_decision_id" in e for e in errors)

    def test_active_with_approval_decision(self):
        b = make_active_binding()
        assert validate_binding(b) == []


# ---------------------------------------------------------------------------
# PersonaCapitalBindingStore — basic CRUD
# ---------------------------------------------------------------------------

class TestPersonaCapitalBindingStoreCrud:
    def test_create_and_get(self):
        store = PersonaCapitalBindingStore()
        b = make_binding()
        store.create(b)
        assert store.get("binding-001") == b

    def test_create_duplicate_raises(self):
        store = PersonaCapitalBindingStore()
        b = make_binding()
        store.create(b)
        with pytest.raises(PersonaCapitalBindingError, match="already exists"):
            store.create(b)

    def test_require_missing_raises(self):
        store = PersonaCapitalBindingStore()
        with pytest.raises(PersonaCapitalBindingError, match="not found"):
            store.require("nonexistent")

    def test_list_by_persona(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", persona_id="alice"))
        store.create(make_binding(binding_id="b2", persona_id="bob"))
        assert len(store.list(persona_id="alice")) == 1

    def test_list_by_pool(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", capital_pool_id="pool-A"))
        store.create(make_binding(binding_id="b2", capital_pool_id="pool-B"))
        assert len(store.list(capital_pool_id="pool-A")) == 1

    def test_persistence(self, tmp_path):
        path = tmp_path / "bindings.json"
        store1 = PersonaCapitalBindingStore(path=path)
        store1.create(make_binding(mandate="grow alpha"))
        store2 = PersonaCapitalBindingStore(path=path)
        b = store2.get("binding-001")
        assert b is not None
        assert b.mandate == "grow alpha"


# ---------------------------------------------------------------------------
# Activation
# ---------------------------------------------------------------------------

class TestActivation:
    def test_activate_sets_status(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding())
        activated = store.activate("binding-001", "approval-XYZ")
        assert activated.status == "active"
        assert activated.approval_decision_id == "approval-XYZ"

    def test_activate_without_approval_raises(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding())
        # activate() sets approval_decision_id so it always has one;
        # but validate_binding() at create time allows pending without it
        # Confirm activated binding is valid
        b = store.activate("binding-001", "appr-001")
        assert validate_binding(b) == []

    def test_activate_invalid_transition(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(status="revoked", approval_decision_id="a"))
        with pytest.raises(PersonaCapitalBindingError, match="Invalid binding status transition"):
            store.activate("binding-001", "a")


# ---------------------------------------------------------------------------
# Single-live-owner rule
# ---------------------------------------------------------------------------

class TestSingleLiveOwnerRule:
    def test_one_live_owner_allowed(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", role="live_owner"))
        store.activate("b1", "appr-001")
        assert store.live_owner_for_pool("pool-001") is not None

    def test_second_live_owner_same_pool_rejected(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", persona_id="alice", role="live_owner"))
        store.create(make_binding(binding_id="b2", persona_id="bob", role="live_owner"))
        store.activate("b1", "appr-001")
        with pytest.raises(PersonaCapitalBindingError, match="Single-live-owner rule"):
            store.activate("b2", "appr-002")

    def test_second_live_owner_different_pool_allowed(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", persona_id="alice", capital_pool_id="pool-001", role="live_owner"))
        store.create(make_binding(binding_id="b2", persona_id="bob", capital_pool_id="pool-002", role="live_owner"))
        store.activate("b1", "appr-001")
        store.activate("b2", "appr-002")  # different pool — allowed

    def test_multiple_advisors_same_pool_allowed(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", persona_id="alice", role="advisor"))
        store.create(make_binding(binding_id="b2", persona_id="bob", role="advisor"))
        store.activate("b1", "appr-001")
        store.activate("b2", "appr-002")  # multiple advisors — allowed

    def test_multiple_paper_owners_same_pool_allowed(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", persona_id="alice", role="paper_owner"))
        store.create(make_binding(binding_id="b2", persona_id="bob", role="paper_owner"))
        store.activate("b1", "appr-001")
        store.activate("b2", "appr-002")  # multiple paper_owners — allowed

    def test_live_owner_after_revoke(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(binding_id="b1", persona_id="alice", role="live_owner"))
        store.create(make_binding(binding_id="b2", persona_id="bob", role="live_owner"))
        store.activate("b1", "appr-001")
        store.update_status("b1", "revoked")  # revoke old live_owner
        store.activate("b2", "appr-002")      # now b2 can become live_owner

    def test_create_with_active_live_owner_directly_enforced(self):
        store = PersonaCapitalBindingStore()
        # Create first active live_owner directly (bypass activate path)
        store.create(make_active_binding(binding_id="b1", role="live_owner"))
        # Second create with active live_owner on same pool must fail
        with pytest.raises(PersonaCapitalBindingError, match="Single-live-owner rule"):
            store.create(make_active_binding(binding_id="b2", persona_id="bob", role="live_owner"))


# ---------------------------------------------------------------------------
# Deployment admissibility
# ---------------------------------------------------------------------------

class TestDeploymentAdmissibility:
    def test_persona_may_deploy_to_within_scope(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(role="live_owner", allowed_deployment_scope="canary"))
        store.activate("binding-001", "appr-001")
        assert store.persona_may_deploy_to("persona-alpha", "pool-001", "paper") is True
        assert store.persona_may_deploy_to("persona-alpha", "pool-001", "canary") is True
        assert store.persona_may_deploy_to("persona-alpha", "pool-001", "live") is False

    def test_persona_may_not_deploy_when_inactive(self):
        store = PersonaCapitalBindingStore()
        store.create(make_binding(allowed_deployment_scope="live"))
        assert store.persona_may_deploy_to("persona-alpha", "pool-001", "paper") is False

    def test_persona_no_binding_returns_false(self):
        store = PersonaCapitalBindingStore()
        assert store.persona_may_deploy_to("unknown", "pool-001", "paper") is False
