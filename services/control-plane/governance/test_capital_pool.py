"""Tests for CapitalPool and CapitalPoolStore (CAP-001)."""
import pytest
from services.control_plane.governance.capital_pool import (
    CapitalPool,
    CapitalPoolError,
    CapitalPoolStore,
    validate_pool,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def make_pool(**kwargs) -> CapitalPool:
    defaults = {
        "pool_id": "pool-001",
        "name": "Alpha Fund",
        "owner_id": "org-001",
        "owner_type": "fund",
        "status": "active",
        "created_at": "2026-04-10T00:00:00Z",
    }
    defaults.update(kwargs)
    return CapitalPool(**defaults)


# ---------------------------------------------------------------------------
# CapitalPool construction
# ---------------------------------------------------------------------------

class TestCapitalPoolConstruction:
    def test_valid_pool(self):
        pool = make_pool()
        assert pool.pool_id == "pool-001"
        assert pool.single_runtime_enforced is True  # default

    def test_invalid_owner_type(self):
        with pytest.raises(CapitalPoolError, match="Invalid owner_type"):
            make_pool(owner_type="unknown")

    def test_invalid_status(self):
        with pytest.raises(CapitalPoolError, match="Invalid status"):
            make_pool(status="zombie")

    def test_negative_budget(self):
        with pytest.raises(CapitalPoolError, match="budget must be >= 0"):
            make_pool(budget=-1)

    def test_zero_budget_allowed(self):
        pool = make_pool(budget=0)
        assert pool.budget == 0

    def test_single_runtime_enforced_default_true(self):
        pool = make_pool()
        assert pool.single_runtime_enforced is True

    def test_single_runtime_enforced_can_be_false(self):
        pool = make_pool(single_runtime_enforced=False)
        assert pool.single_runtime_enforced is False

    def test_to_dict_roundtrip(self):
        pool = make_pool(budget=1_000_000, currency="EUR")
        d = pool.to_dict()
        restored = CapitalPool.from_dict(d)
        assert restored == pool

    def test_to_dict_excludes_none(self):
        pool = make_pool()
        d = pool.to_dict()
        assert "description" not in d
        assert "budget" not in d


# ---------------------------------------------------------------------------
# validate_pool
# ---------------------------------------------------------------------------

class TestValidatePool:
    def test_valid_pool_no_errors(self):
        assert validate_pool(make_pool()) == []

    def test_empty_pool_id(self):
        # bypass __post_init__ by using object.__setattr__ on frozen dataclass
        pool = make_pool()
        object.__setattr__(pool, "pool_id", "")
        errors = validate_pool(pool)
        assert any("pool_id" in e for e in errors)

    def test_empty_name(self):
        pool = make_pool()
        object.__setattr__(pool, "name", "")
        errors = validate_pool(pool)
        assert any("name" in e for e in errors)


# ---------------------------------------------------------------------------
# CapitalPoolStore
# ---------------------------------------------------------------------------

class TestCapitalPoolStore:
    def test_create_and_get(self):
        store = CapitalPoolStore()
        pool = make_pool()
        store.create(pool)
        retrieved = store.get("pool-001")
        assert retrieved == pool

    def test_create_duplicate_raises(self):
        store = CapitalPoolStore()
        pool = make_pool()
        store.create(pool)
        with pytest.raises(CapitalPoolError, match="already exists"):
            store.create(pool)

    def test_require_missing_raises(self):
        store = CapitalPoolStore()
        with pytest.raises(CapitalPoolError, match="not found"):
            store.require("nonexistent")

    def test_list_by_owner(self):
        store = CapitalPoolStore()
        store.create(make_pool(pool_id="p1", owner_id="org-A"))
        store.create(make_pool(pool_id="p2", owner_id="org-B"))
        assert len(store.list(owner_id="org-A")) == 1

    def test_list_by_status(self):
        store = CapitalPoolStore()
        store.create(make_pool(pool_id="p1", status="active"))
        store.create(make_pool(pool_id="p2", status="suspended"))
        assert len(store.list(status="active")) == 1

    def test_update_status_valid_transition(self):
        store = CapitalPoolStore()
        store.create(make_pool())
        updated = store.update_status("pool-001", "suspended")
        assert updated.status == "suspended"

    def test_update_status_archive_from_active(self):
        store = CapitalPoolStore()
        store.create(make_pool())
        updated = store.update_status("pool-001", "archived")
        assert updated.status == "archived"

    def test_update_status_invalid_transition(self):
        store2 = CapitalPoolStore()
        store2.create(make_pool(pool_id="p2", status="suspended"))
        store2.update_status("p2", "archived")
        with pytest.raises(CapitalPoolError, match="Invalid status transition"):
            store2.update_status("p2", "active")

    def test_is_single_runtime_enforced(self):
        store = CapitalPoolStore()
        store.create(make_pool())
        assert store.is_single_runtime_enforced("pool-001") is True

    def test_is_single_runtime_not_enforced(self):
        store = CapitalPoolStore()
        store.create(make_pool(single_runtime_enforced=False))
        assert store.is_single_runtime_enforced("pool-001") is False

    def test_persistence(self, tmp_path):
        path = tmp_path / "pools.json"
        store1 = CapitalPoolStore(path=path)
        store1.create(make_pool(budget=500_000, currency="USD"))

        store2 = CapitalPoolStore(path=path)
        pool = store2.get("pool-001")
        assert pool is not None
        assert pool.budget == 500_000
