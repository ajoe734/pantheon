#!/usr/bin/env python3
"""
Smoke test for CapitalPool and PersonaCapitalBinding governance objects.

Task: CAP-001
Owner: Claude
Reviewer: Codex

Run:
    python3 services/control-plane/governance/smoke_test_capital_pool.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from capital_pool import (
    CapitalPool,
    CapitalPoolError,
    CapitalPoolStore,
    validate_pool,
)
from persona_capital_binding import (
    DeploymentScope,
    PersonaCapitalBinding,
    PersonaCapitalBindingError,
    PersonaCapitalBindingStore,
    validate_binding,
)

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def check_raises(label: str, exc_type: type, fn) -> None:
    global PASS, FAIL
    try:
        fn()
        FAIL += 1
        print(f"[FAIL] {label} (no exception raised)")
    except exc_type:
        PASS += 1
        print(f"[PASS] {label}")
    except Exception as e:
        FAIL += 1
        print(f"[FAIL] {label} (wrong exception: {e!r})")


# ---------------------------------------------------------------------------
# CapitalPool — construction and validation
# ---------------------------------------------------------------------------

def _make_pool(**kwargs) -> CapitalPool:
    defaults = dict(
        pool_id="pool-001",
        name="Alpha Fund",
        owner_id="org-001",
        owner_type="fund",
        status="active",
        created_at="2026-04-10T00:00:00Z",
    )
    defaults.update(kwargs)
    return CapitalPool(**defaults)


pool = _make_pool()
check("CapitalPool constructs with valid fields", pool.pool_id == "pool-001")
check("single_runtime_enforced defaults to True", pool.single_runtime_enforced is True)
check("validate_pool passes for valid pool", validate_pool(pool) == [])

check_raises(
    "invalid owner_type raises CapitalPoolError",
    CapitalPoolError,
    lambda: _make_pool(owner_type="unknown"),
)
check_raises(
    "invalid status raises CapitalPoolError",
    CapitalPoolError,
    lambda: _make_pool(status="zombie"),
)
check_raises(
    "negative budget raises CapitalPoolError",
    CapitalPoolError,
    lambda: _make_pool(budget=-1),
)

# to_dict / from_dict roundtrip
pool_with_budget = _make_pool(budget=1_000_000, currency="EUR", description="test pool")
restored = CapitalPool.from_dict(pool_with_budget.to_dict())
check("CapitalPool to_dict/from_dict roundtrip", restored == pool_with_budget)
check("to_dict excludes None optional fields", "budget" not in _make_pool().to_dict())

# ---------------------------------------------------------------------------
# CapitalPoolStore
# ---------------------------------------------------------------------------

store = CapitalPoolStore()
store.create(_make_pool())
check("store.create and get returns same pool", store.get("pool-001") == _make_pool())
check("store.require returns pool", store.require("pool-001").pool_id == "pool-001")
check_raises(
    "store.require missing pool raises",
    CapitalPoolError,
    lambda: store.require("nonexistent"),
)
check_raises(
    "store.create duplicate raises",
    CapitalPoolError,
    lambda: store.create(_make_pool()),
)

store2 = CapitalPoolStore()
store2.create(_make_pool(pool_id="p1", owner_id="org-A"))
store2.create(_make_pool(pool_id="p2", owner_id="org-B"))
check("list by owner filters correctly", len(store2.list(owner_id="org-A")) == 1)
check("list by status filters correctly", len(store2.list(status="active")) == 2)

# status transitions
store3 = CapitalPoolStore()
store3.create(_make_pool())
updated = store3.update_status("pool-001", "suspended")
check("active -> suspended transition", updated.status == "suspended")
active_again = store3.update_status("pool-001", "active")
check("suspended -> active transition", active_again.status == "active")
check_raises(
    "archived -> active is rejected",
    CapitalPoolError,
    lambda: (
        store3.update_status("pool-001", "archived"),
        store3.update_status("pool-001", "active"),
    ),
)

# single-runtime query
store4 = CapitalPoolStore()
store4.create(_make_pool())
store4.create(_make_pool(pool_id="pool-002", single_runtime_enforced=False))
check("is_single_runtime_enforced True by default", store4.is_single_runtime_enforced("pool-001") is True)
check("is_single_runtime_enforced False when disabled", store4.is_single_runtime_enforced("pool-002") is False)

# persistence
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "pools.json"
    s1 = CapitalPoolStore(path=path)
    s1.create(_make_pool(budget=500_000, currency="USD"))
    s2 = CapitalPoolStore(path=path)
    loaded = s2.get("pool-001")
    check("CapitalPool JSON persistence roundtrip", loaded is not None and loaded.budget == 500_000)

# ---------------------------------------------------------------------------
# DeploymentScope ordering
# ---------------------------------------------------------------------------

check("NONE.level == 0", DeploymentScope.NONE.level == 0)
check("PAPER.level == 1", DeploymentScope.PAPER.level == 1)
check("CANARY.level == 2", DeploymentScope.CANARY.level == 2)
check("LIVE.level == 3", DeploymentScope.LIVE.level == 3)
check("LIVE permits CANARY", DeploymentScope.LIVE.permits(DeploymentScope.CANARY))
check("LIVE permits PAPER", DeploymentScope.LIVE.permits(DeploymentScope.PAPER))
check("PAPER does not permit CANARY", not DeploymentScope.PAPER.permits(DeploymentScope.CANARY))
check("NONE does not permit PAPER", not DeploymentScope.NONE.permits(DeploymentScope.PAPER))

# ---------------------------------------------------------------------------
# PersonaCapitalBinding — construction and validation
# ---------------------------------------------------------------------------

def _make_binding(**kwargs) -> PersonaCapitalBinding:
    defaults = dict(
        binding_id="binding-001",
        persona_id="persona-alpha",
        capital_pool_id="pool-001",
        role="advisor",
        allowed_deployment_scope="none",
        status="pending",
        created_at="2026-04-10T00:00:00Z",
    )
    defaults.update(kwargs)
    return PersonaCapitalBinding(**defaults)


def _make_active(**kwargs) -> PersonaCapitalBinding:
    kw = dict(status="active", approval_decision_id="appr-001")
    kw.update(kwargs)
    return _make_binding(**kw)


b = _make_binding()
check("PersonaCapitalBinding constructs with valid fields", b.binding_id == "binding-001")
check("is_active False for pending binding", b.is_active is False)
check("validate_binding passes for valid pending", validate_binding(b) == [])

check_raises(
    "invalid role raises PersonaCapitalBindingError",
    PersonaCapitalBindingError,
    lambda: _make_binding(role="superuser"),
)
check_raises(
    "invalid allowed_deployment_scope raises",
    PersonaCapitalBindingError,
    lambda: _make_binding(allowed_deployment_scope="mega"),
)
check_raises(
    "invalid status raises PersonaCapitalBindingError",
    PersonaCapitalBindingError,
    lambda: _make_binding(status="zombie"),
)
check_raises(
    "negative budget raises PersonaCapitalBindingError",
    PersonaCapitalBindingError,
    lambda: _make_binding(budget=-1),
)

active_b = _make_active()
check("is_active True for active binding", active_b.is_active is True)
check("active with approval_decision_id passes validate", validate_binding(active_b) == [])

inactive_b = _make_binding(status="active")  # no approval_decision_id
check(
    "active without approval_decision_id fails validate",
    any("approval_decision_id" in e for e in validate_binding(inactive_b)),
)

# permits_deployment_to
canary_b = _make_active(role="live_owner", allowed_deployment_scope="canary")
check("canary scope permits paper", canary_b.permits_deployment_to("paper"))
check("canary scope permits canary", canary_b.permits_deployment_to("canary"))
check("canary scope does not permit live", not canary_b.permits_deployment_to("live"))
pending_b = _make_binding(role="live_owner", allowed_deployment_scope="live")
check("pending binding does not permit any deployment", not pending_b.permits_deployment_to("paper"))
check(
    "advisor role cannot claim paper deployment scope",
    any("deployment ceiling" in e for e in validate_binding(_make_binding(allowed_deployment_scope="paper"))),
)
check(
    "paper_owner cannot claim canary deployment scope",
    any(
        "deployment ceiling" in e
        for e in validate_binding(_make_binding(role="paper_owner", allowed_deployment_scope="canary"))
    ),
)
check(
    "effective_to must be later than effective_from",
    any(
        "effective_to" in e
        for e in validate_binding(
            _make_binding(
                role="live_owner",
                allowed_deployment_scope="paper",
                effective_from="2026-04-12T00:00:00Z",
                effective_to="2026-04-11T00:00:00Z",
            )
        )
    ),
)

# to_dict / from_dict roundtrip
b2 = _make_active(mandate="grow alpha", budget=100_000)
check("PersonaCapitalBinding to_dict/from_dict roundtrip", PersonaCapitalBinding.from_dict(b2.to_dict()) == b2)

# ---------------------------------------------------------------------------
# PersonaCapitalBindingStore — CRUD
# ---------------------------------------------------------------------------

bs = PersonaCapitalBindingStore()
bs.create(_make_binding())
check("store.create and get", bs.get("binding-001") == _make_binding())
check_raises(
    "store.create duplicate raises",
    PersonaCapitalBindingError,
    lambda: bs.create(_make_binding()),
)
check_raises(
    "store.require missing raises",
    PersonaCapitalBindingError,
    lambda: bs.require("nonexistent"),
)

bs2 = PersonaCapitalBindingStore()
bs2.create(_make_binding(binding_id="b1", persona_id="alice"))
bs2.create(_make_binding(binding_id="b2", persona_id="bob"))
check("list by persona_id filters correctly", len(bs2.list(persona_id="alice")) == 1)

bs3 = PersonaCapitalBindingStore()
bs3.create(_make_binding(binding_id="b1", capital_pool_id="pool-A"))
bs3.create(_make_binding(binding_id="b2", capital_pool_id="pool-B"))
check("list by capital_pool_id filters correctly", len(bs3.list(capital_pool_id="pool-A")) == 1)

# activation
bs4 = PersonaCapitalBindingStore()
bs4.create(_make_binding())
activated = bs4.activate("binding-001", "appr-XYZ")
check("activate sets status to active", activated.status == "active")
check("activate sets approval_decision_id", activated.approval_decision_id == "appr-XYZ")

check_raises(
    "activate revoked binding rejected",
    PersonaCapitalBindingError,
    lambda: (
        bs4.update_status("binding-001", "revoked"),
        bs4.activate("binding-001", "appr-X"),
    ),
)

# ---------------------------------------------------------------------------
# Single-live-owner rule
# ---------------------------------------------------------------------------

slo = PersonaCapitalBindingStore()
slo.create(_make_binding(binding_id="b1", persona_id="alice", role="live_owner"))
slo.create(_make_binding(binding_id="b2", persona_id="bob", role="live_owner"))
slo.activate("b1", "appr-001")
check("first live_owner activates ok", slo.live_owner_for_pool("pool-001") is not None)
check_raises(
    "second live_owner on same pool rejected",
    PersonaCapitalBindingError,
    lambda: slo.activate("b2", "appr-002"),
)

slo2 = PersonaCapitalBindingStore()
slo2.create(_make_binding(binding_id="b1", capital_pool_id="pool-001", role="live_owner"))
slo2.create(_make_binding(binding_id="b2", capital_pool_id="pool-002", role="live_owner"))
slo2.activate("b1", "appr-001")
slo2.activate("b2", "appr-002")
check("live_owner on different pools both allowed", True)

slo3 = PersonaCapitalBindingStore()
slo3.create(_make_binding(binding_id="b1", persona_id="alice", role="advisor"))
slo3.create(_make_binding(binding_id="b2", persona_id="bob", role="advisor"))
slo3.activate("b1", "appr-001")
slo3.activate("b2", "appr-002")
check("multiple advisors on same pool allowed", True)

slo4 = PersonaCapitalBindingStore()
slo4.create(_make_binding(binding_id="b1", persona_id="alice", role="paper_owner"))
slo4.create(_make_binding(binding_id="b2", persona_id="bob", role="paper_owner"))
slo4.activate("b1", "appr-001")
slo4.activate("b2", "appr-002")
check("multiple paper_owners on same pool allowed", True)

slo5 = PersonaCapitalBindingStore()
slo5.create(_make_binding(binding_id="b1", persona_id="alice", role="live_owner"))
slo5.create(_make_binding(binding_id="b2", persona_id="bob", role="live_owner"))
slo5.activate("b1", "appr-001")
slo5.update_status("b1", "revoked")
slo5.activate("b2", "appr-002")
check("live_owner after revoke allows new live_owner", True)

# create with active live_owner directly
slo6 = PersonaCapitalBindingStore()
slo6.create(_make_active(binding_id="b1", persona_id="alice", role="live_owner"))
check_raises(
    "create second active live_owner directly is rejected",
    PersonaCapitalBindingError,
    lambda: slo6.create(_make_active(binding_id="b2", persona_id="bob", role="live_owner")),
)

# ---------------------------------------------------------------------------
# Deployment admissibility
# ---------------------------------------------------------------------------

adm = PersonaCapitalBindingStore()
adm.create(_make_binding(role="live_owner", allowed_deployment_scope="canary"))
adm.activate("binding-001", "appr-001")
check("persona_may_deploy_to paper (within canary scope)", adm.persona_may_deploy_to("persona-alpha", "pool-001", "paper"))
check("persona_may_deploy_to canary (at ceiling)", adm.persona_may_deploy_to("persona-alpha", "pool-001", "canary"))
check("persona_may_deploy_to live (above ceiling) returns False", not adm.persona_may_deploy_to("persona-alpha", "pool-001", "live"))

adm2 = PersonaCapitalBindingStore()
adm2.create(_make_binding(role="live_owner", allowed_deployment_scope="live"))
check("pending binding: persona_may_deploy_to returns False", not adm2.persona_may_deploy_to("persona-alpha", "pool-001", "paper"))
check("no binding: persona_may_deploy_to returns False", not adm2.persona_may_deploy_to("unknown", "pool-001", "paper"))

adm3 = PersonaCapitalBindingStore()
adm3.create(_make_binding(role="advisor", allowed_deployment_scope="none"))
adm3.activate("binding-001", "appr-010")
check("advisor binding never permits deployment", not adm3.persona_may_deploy_to("persona-alpha", "pool-001", "paper"))

adm4 = PersonaCapitalBindingStore()
adm4.create(
    _make_binding(
        role="live_owner",
        allowed_deployment_scope="paper",
        effective_from="2099-01-01T00:00:00Z",
    )
)
adm4.activate("binding-001", "appr-011")
check(
    "future-dated binding is not yet admissible",
    not adm4.persona_may_deploy_to("persona-alpha", "pool-001", "paper"),
)

adm5 = PersonaCapitalBindingStore()
adm5.create(
    _make_binding(
        role="live_owner",
        allowed_deployment_scope="paper",
        effective_to="2000-01-01T00:00:00Z",
    )
)
adm5.activate("binding-001", "appr-012")
check(
    "expired binding is not admissible",
    not adm5.persona_may_deploy_to("persona-alpha", "pool-001", "paper"),
)

# ---------------------------------------------------------------------------
# PersonaCapitalBinding persistence
# ---------------------------------------------------------------------------

with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "bindings.json"
    ps1 = PersonaCapitalBindingStore(path=path)
    ps1.create(_make_binding(mandate="grow alpha"))
    ps2 = PersonaCapitalBindingStore(path=path)
    loaded_b = ps2.get("binding-001")
    check("PersonaCapitalBinding JSON persistence roundtrip", loaded_b is not None and loaded_b.mandate == "grow alpha")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

total = PASS + FAIL
print()
print("SUMMARY")
print(f"{PASS}/{total} checks passed")
if FAIL:
    sys.exit(1)
