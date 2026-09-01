"""OPGAP-READINESS-CONTRACT-20260901 — deploy gates may only assert declared fields.

`GET /bff/auth/readiness` mixes two kinds of signal in one payload: local-authority
facts about this build/session, and advisory observability about things the build
does not own (assistant-provider health, which depends on an external credential
that rotates on its own schedule).

A deploy gate that asserts an advisory field makes every release conditional on
something unrelated to whether the release is correct, and it recurs on every
rotation. That is not hypothetical: a gate asserted `providerReady is True` and
auto-rolled-back four healthy releases before it was removed
(OPGAP-DEPLOY-PROVIDER-GATE-20260901).

This test is deliberately static — it parses the deploy script rather than
importing the BFF — so it runs in the repo-root `tests/` suite that CI actually
executes, with no service dependencies.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = REPO_ROOT / "scripts" / "deploy_nonprod_vm.sh"
CONTRACT_MODULE = (
    REPO_ROOT / "services" / "control-plane" / "bff" / "readiness_release_contract.py"
)


def _load_contract():
    """Load the declaration by path: the BFF ships its own `integrations`
    package, so putting its directory on sys.path shadows the repository-root
    one and breaks unrelated imports."""
    spec = importlib.util.spec_from_file_location(
        "readiness_release_contract", CONTRACT_MODULE
    )
    assert spec and spec.loader, f"cannot load {CONTRACT_MODULE}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTRACT = _load_contract()


def _readiness_gate_block() -> str:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = script.index("strict browser readiness contract is not satisfied")
    # Walk back to the assertion block that produces that failure message.
    block_start = script.rindex("expected_sha = sys.argv[1]", 0, start)
    return script[block_start:start]


def _asserted_fields() -> set[str]:
    """Field paths the deploy gate *asserts* on, in `data`-relative dotted form.

    Only `assert` statements count. Merely reading a field is fine and expected:
    the gate reads advisory fields to report them, which is the behaviour we
    want, as distinct from gating on them.
    """
    fields: set[str] = set()
    for line in _readiness_gate_block().splitlines():
        stripped = line.strip()
        if not stripped.startswith("assert "):
            continue
        for match in re.finditer(r'\bdata\.get\("([A-Za-z0-9_]+)"\)', stripped):
            fields.add(match.group(1))
        for match in re.finditer(r'\bauth\.get\("([A-Za-z0-9_]+)"\)', stripped):
            fields.add(f"auth.{match.group(1)}")
    return fields


def test_gate_block_is_locatable() -> None:
    """Guard the parser itself: a silently-unparseable gate would make every
    assertion below vacuously true."""
    fields = _asserted_fields()
    assert fields, "could not extract any asserted field from the deploy gate"
    assert "ready" in fields, f"expected the gate to assert `ready`; found {sorted(fields)}"


def test_gate_asserts_only_declared_release_blocking_fields() -> None:
    overreach = _asserted_fields() - CONTRACT.RELEASE_BLOCKING_FIELDS
    assert not overreach, (
        "deploy gate asserts readiness field(s) not declared release-blocking: "
        f"{sorted(overreach)}. Either the field belongs in RELEASE_BLOCKING_FIELDS "
        "(readiness_release_contract.py) because its producer treats it as blocking, "
        "or the gate must stop asserting it. Advisory signals must not block releases."
    )


def test_provider_health_is_advisory_and_never_asserted() -> None:
    for field in ("providerReady", "provider"):
        assert field in CONTRACT.ADVISORY_FIELDS
        assert field not in CONTRACT.RELEASE_BLOCKING_FIELDS
    assert not ({"providerReady", "provider"} & _asserted_fields()), (
        "assistant-provider health is advisory: it depends on an external credential "
        "that rotates independently of any release, so gating on it blocks healthy "
        "builds and will recur on every rotation"
    )


def test_blocking_and_advisory_sets_are_disjoint() -> None:
    assert not (CONTRACT.RELEASE_BLOCKING_FIELDS & CONTRACT.ADVISORY_FIELDS), (
        "a readiness field cannot be both release-blocking and advisory; pick one"
    )


@pytest.mark.parametrize(
    "field",
    ["sourceCommitSha", "ready", "authReady", "auth.mode", "auth.stub"],
)
def test_core_release_facts_stay_blocking(field: str) -> None:
    """The inverse failure mode: nothing should be able to quietly demote a real
    release fact to advisory to make a red gate go green."""
    assert field in CONTRACT.RELEASE_BLOCKING_FIELDS
