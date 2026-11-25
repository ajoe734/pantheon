"""Production wiring of the card admission path: environment -> four collaborators, fail closed."""
from __future__ import annotations

import io
import json
import sys
import types
import urllib.error
from pathlib import Path

import pytest

from services.research.alpha_replication import card_production_wiring as wiring
from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.card_production_wiring import (
    OwnerCallError,
    build_card_wiring,
    build_dataset_resolver,
    build_demands_provider,
    build_matcher,
    missing_configuration,
)


def _env(tmp_path: Path) -> dict[str, str]:
    env = {
        "PANTHEON_REGISTRY_URL": "http://registry.test", "PANTHEON_GOVERNANCE_URL": "http://gov.test",
        "RESEARCH_API_URL": "http://research.test", "PERSONA_API_URL": "http://persona.test",
        "RESEARCH_GOVERNANCE_PROPOSER_ID": "research-proposer", "RESEARCH_GOVERNANCE_GATE_ID": "paper-gate",
    }
    for name in wiring.REQUIRED_CREDENTIALS:
        path = tmp_path / name
        path.write_text("tok-" + name)
        path.chmod(0o600)
        env[name + "_FILE"] = str(path)
    # The governed market store: only its manifest directory matters at wiring time; no persona authority file.
    (tmp_path / "market_data_store" / "daily_bars" / "_manifest").mkdir(parents=True, exist_ok=True)
    env["GOVERNED_MARKET_STORE_ROOT"] = str(tmp_path / "market_data_store")
    return env


def _build(tmp_path, env):
    return build_card_wiring(data_dir=tmp_path / "data", admission_store=ReplicationAdmissionStore(tmp_path / "data"),
                             env=env)


def test_complete_environment_builds_all_four_collaborators(tmp_path):
    result = _build(tmp_path, _env(tmp_path))
    assert result.enabled and result.reason is None
    assert result.registrar.proposer_id == "research-proposer" and result.registrar.gate_id == "paper-gate"
    assert result.registrar.governance_url == "http://gov.test" and result.registrar.registry_url == "http://registry.test"
    assert callable(result.matcher) and callable(result.demands_provider) and callable(result.dataset_resolver)


@pytest.mark.parametrize("name", list(wiring.REQUIRED_ADDRESSES + wiring.REQUIRED_IDENTITIES
                                      + wiring.REQUIRED_CREDENTIALS))
def test_any_missing_name_disables_the_path_with_a_readable_reason(tmp_path, name):
    env = _env(tmp_path)
    for key in [k for k in env if k == name or k == name + "_FILE"]:
        del env[key]
    result = _build(tmp_path, env)
    assert not result.enabled
    assert result.reason.startswith("card_path_disabled:unconfigured:") and name in result.reason
    assert (result.registrar, result.matcher, result.demands_provider, result.dataset_resolver) == (None,) * 4


def test_unreadable_credential_and_missing_store_are_named(tmp_path):
    env = _env(tmp_path)
    Path(env["RESEARCH_GOVERNANCE_GATE_TOKEN_FILE"]).chmod(0o644)  # unsafe mode is never read
    env["GOVERNED_MARKET_STORE_ROOT"] = str(tmp_path / "absent")
    missing = missing_configuration(env)
    assert "RESEARCH_GOVERNANCE_GATE_TOKEN" in missing and "GOVERNED_MARKET_STORE_ROOT(daily bars store not found)" in missing


def test_wiring_needs_no_persona_authority_file(tmp_path):
    env = _env(tmp_path)
    for name in ("RESEARCH_RETRAIN_CANONICAL_DATASET_PATH", "RESEARCH_RETRAIN_THRESHOLD_POLICY_PATH"):
        assert name not in env
    assert _build(tmp_path, env).enabled


def test_one_identity_for_proposer_and_gate_is_refused(tmp_path):
    env = _env(tmp_path)
    env["RESEARCH_GOVERNANCE_GATE_ID"] = env["RESEARCH_GOVERNANCE_PROPOSER_ID"]
    assert _build(tmp_path, env).reason == "card_path_disabled:proposer_and_gate_principals_must_differ"


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(monkeypatch, handler):
    seen = []

    def fake(request, timeout=None):
        seen.append(request)
        return handler(request)

    monkeypatch.setattr(wiring.urllib.request, "urlopen", fake)
    return seen


def test_demands_provider_reads_open_demands_with_the_reader_credential(tmp_path, monkeypatch):
    env = _env(tmp_path)
    seen = _urlopen(monkeypatch, lambda r: _Response(json.dumps({"demands": [
        {"demand_id": "d1", "status": "open"}, {"demand_id": "d2", "status": "satisfied"}]}).encode()))
    rows = build_demands_provider(env)("tenant-dev")
    assert [r["demand_id"] for r in rows] == ["d1"]
    request = seen[0]
    assert request.full_url == "http://research.test/api/research/persona-demands?status=open"
    assert request.get_header("Authorization") == "Bearer tok-ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN"
    assert request.get_header("X-tenant-id") == "tenant-dev"


def test_owner_errors_are_readable_and_never_fall_back(tmp_path, monkeypatch):
    env = _env(tmp_path)

    def deny(request):
        raise urllib.error.HTTPError(request.full_url, 403, "no", {}, io.BytesIO(b'{"detail":"role missing"}'))

    _urlopen(monkeypatch, deny)
    with pytest.raises(OwnerCallError, match="read_open_demands: research returned HTTP 403: role missing") as info:
        build_demands_provider(env)("tenant-dev")
    assert info.value.status == 403
    del env["ALPHA_REPLICATION_RESEARCH_SERVICE_TOKEN_FILE"]
    with pytest.raises(OwnerCallError, match="credential is not configured"):
        build_demands_provider(env)("tenant-dev")


def test_not_found_carries_the_status_the_registrar_reads(tmp_path, monkeypatch):
    env = _env(tmp_path)

    def gone(request):
        raise urllib.error.HTTPError(request.full_url, 404, "nf", {}, io.BytesIO(b"{}"))

    _urlopen(monkeypatch, gone)
    registrar = build_card_wiring(data_dir=tmp_path / "d", admission_store=ReplicationAdmissionStore(tmp_path / "d"),
                                  env=env).registrar
    assert registrar._read_optional(step="s", service="governance", url="http://gov.test/x", tenant_id="t",
                                    token_variable="RESEARCH_GOVERNANCE_PROPOSER_TOKEN") is None


def _matcher_world(monkeypatch, persona, discover, *, snapshot=None):
    """Persona owner reads by step: the persona record, then its capability snapshot (404 when it has none)."""
    reads = []

    def owner_request(**kw):
        reads.append(kw["step"])
        if kw["step"] == "read_capability_snapshot":
            if snapshot is None:
                raise OwnerCallError("read_capability_snapshot: persona returned HTTP 404", status=404)
            return snapshot
        return persona

    monkeypatch.setattr(wiring, "_owner_request", owner_request)
    if discover is not None:
        monkeypatch.setattr(wiring, "_discovery_module", lambda: types.SimpleNamespace(discover_for_demand=discover))
    return reads


def _catalog(*policies):
    """The route policy owner catalog the persona daily cycle reads, holding ``policies``."""
    from services.control_plane.bff.ports.operations_consultation import DomainWorkflowCatalogPort

    return lambda: DomainWorkflowCatalogPort(datasets={"route_policies": list(policies)})


ENTRY = {"registry_id": "reg-1", "strategy_id": "s1", "artifact_state": "candidate",
         "metadata": {"strategy_spec": {"strategy_id": "s1", "metadata": {"card_wiring": {"status": "gate_ready"}}}}}
DEMAND = {"demand_id": "d1", "persona_id": "p1", "tenant_id": "tenant-dev"}
# The stored shape of the three trading personas (persona owner record): the route policy is referenced by id,
# never carried inline, and no capability snapshot is inline.
TRADING_PERSONA = {
    "persona_id": "p1", "tenant_id": "tenant-dev", "name": "Momentum", "mandate": "TW equity momentum",
    "strategy_family": "momentum", "lifecycle_state": "paper_running", "status": "active",
    "route_policy_id": "rp-trading-momentum", "consult_policy_id": "control-plane.default",
    "tool_profile_id": "persona-default", "capability_snapshot_id": None,
    "metadata": {"preferred_markets": ["TWSE listed"], "asset_classes": ["equity"], "holding_period": "1m"},
}


def test_matcher_runs_the_existing_scorer_on_the_one_spec(tmp_path, monkeypatch):
    calls = []

    def discover(persona, demand, **kw):
        calls.append((persona, demand, kw))
        return {"matches": [{"matched_object_id": "x"}]}

    _matcher_world(monkeypatch, {"persona_id": "p1", "metadata": {"capability_snapshot": {"k": 1}}}, discover)
    assert build_matcher(_env(tmp_path), catalog_factory=_catalog())(ENTRY, DEMAND) is True
    persona, demand, kw = calls[0]
    # no policy referenced: scored with the daily cycle's empty constraint ({}), the inline snapshot wins
    assert demand is DEMAND and kw["capability_snapshot"] == {"k": 1} and kw["route_policy"] == {}
    spec = kw["strategy_specs"][0]
    assert spec["strategy_spec_registry_id"] == "reg-1" and spec["lifecycle_state"] == "candidate"
    _matcher_world(monkeypatch, {"persona_id": "p1"}, lambda *a, **k: {"matches": []})
    assert build_matcher(_env(tmp_path), catalog_factory=_catalog())(ENTRY, DEMAND) is False


def test_matcher_resolves_a_referenced_route_policy_and_snapshot_from_their_owners(tmp_path, monkeypatch):
    """Acceptance 2: the trading persona's referenced policy is selected from the owner catalog by its own id
    (a stale persona-scoped row is ignored) and its snapshot is read from the persona owner."""
    calls = []
    reads = _matcher_world(monkeypatch, TRADING_PERSONA, lambda p, d, **kw: calls.append(kw) or {"matches": [1]},
                           snapshot={"snapshot_id": "cap-1", "persona_id": "p1", "effective_tools": ["read"]})
    policy = {"policy_id": "rp-trading-momentum", "blocked_backends": ["qlib"]}
    stale = {"policy_id": "rp-old", "persona_id": "p1"}
    assert build_matcher(_env(tmp_path), catalog_factory=_catalog(stale, policy))(ENTRY, DEMAND) is True
    assert calls[0]["route_policy"] == policy
    assert calls[0]["capability_snapshot"]["snapshot_id"] == "cap-1"
    assert reads == ["read_persona", "read_capability_snapshot"]


def test_matcher_never_scores_a_referenced_policy_without_its_constraints(tmp_path, monkeypatch):
    scored = []
    discover = lambda *a, **k: scored.append(1) or {"matches": [1]}  # noqa: E731
    # Production default: the owner catalog holds no route policy, so a referenced one is unavailable.
    _matcher_world(monkeypatch, TRADING_PERSONA, discover)
    with pytest.raises(OwnerCallError, match="route_policy_unavailable:rp-trading-momentum"):
        build_matcher(_env(tmp_path))(ENTRY, DEMAND)
    with pytest.raises(OwnerCallError, match="route_policy_unavailable:rp-trading-momentum"):
        build_matcher(_env(tmp_path), catalog_factory=_catalog({"policy_id": "rp-other"}))(ENTRY, DEMAND)
    # An unreadable catalog is never an empty one.
    def broken():
        raise ConnectionError("catalog down")
    with pytest.raises(OwnerCallError, match="read_route_policies: route policy owner unavailable"):
        build_matcher(_env(tmp_path), catalog_factory=broken)(ENTRY, DEMAND)
    # A referenced capability snapshot the persona owner does not return fails closed too.
    _matcher_world(monkeypatch, {**TRADING_PERSONA, "capability_snapshot_id": "cap-9"}, discover)
    policy = {"policy_id": "rp-trading-momentum"}
    with pytest.raises(OwnerCallError, match="capability_snapshot_unavailable:cap-9"):
        build_matcher(_env(tmp_path), catalog_factory=_catalog(policy))(ENTRY, DEMAND)
    assert scored == []


def test_matcher_and_daily_cycle_share_one_resolution(tmp_path, monkeypatch):
    """The persona daily cycle's ``_route_policy`` / ``_capability_snapshot`` call the same two functions."""
    import inspect

    from services.persona import discovery_constraints

    main_source = (Path(__file__).resolve().parents[2] / "control-plane" / "persona" / "main.py").read_text()
    assert "resolve_route_policy(" in main_source and "resolve_capability_snapshot(" in main_source
    matcher_source = inspect.getsource(wiring.build_matcher)
    assert "resolve_route_policy(" in matcher_source and "resolve_capability_snapshot(" in matcher_source
    assert discovery_constraints.resolve_route_policy(
        TRADING_PERSONA, lambda: [{"id": "rp-trading-momentum", "k": 1}]) == {"id": "rp-trading-momentum", "k": 1}


def test_matcher_fails_closed_without_the_persona(tmp_path, monkeypatch):
    _matcher_world(monkeypatch, {"persona_id": "other"}, lambda *a, **k: {"matches": [1]})
    with pytest.raises(OwnerCallError, match="no exact record"):
        build_matcher(_env(tmp_path), catalog_factory=_catalog())(ENTRY, DEMAND)


def test_production_route_policy_catalog_is_the_daily_cycle_catalog():
    from services.control_plane.bff.ports.operations_consultation import DomainWorkflowCatalogPort

    catalog = wiring.route_policy_catalog()
    assert isinstance(catalog, DomainWorkflowCatalogPort)
    assert catalog.dataset_source("route_policies") == "missing" and catalog.list_route_policies() == []


def test_dataset_resolver_serves_the_governed_market_and_fails_closed(tmp_path):
    from datetime import datetime, timezone
    from services.research import governed_market_dataset as gmd
    from services.research.tests.test_governed_market_dataset import NOW, build_store

    build_store(tmp_path / "market_data_store", days=70)  # default policy: 60 bars minimum
    env = _env(tmp_path)
    resolve = build_dataset_resolver(env, now=lambda: NOW)
    resolved = resolve({"dataset_id": wiring.GOVERNED_MARKET_DATASET_ID, "symbols": ["2330.TW"]})
    assert resolved["dataset_id"] == wiring.GOVERNED_MARKET_DATASET_ID and resolved["records"]
    assert resolved["snapshot_id"].startswith("gmd-") and "candidate_pool_market" not in resolved  # symbol binding
    assert resolved["source_dataset_refs"] == ["daily_bars:" + resolved["snapshot_id"]]
    # a pinned snapshot id is reproduced and keeps its own id
    pinned = resolve({"dataset_id": resolved["snapshot_id"], "symbols": ["2330.TW"]})
    assert pinned["dataset_id"] == resolved["snapshot_id"]
    # not a governed market id, a symbol outside the boundary, a stale store: each raises a readable reason
    with pytest.raises(gmd.GovernedMarketDatasetError, match="dataset_id_not_governed_market"):
        resolve({"dataset_id": "persona-candidate-ds", "symbols": ["2330.TW"]})
    with pytest.raises(gmd.GovernedMarketDatasetError, match="symbols_not_in_store"):
        resolve({"dataset_id": wiring.GOVERNED_MARKET_DATASET_ID, "symbols": ["0000"]})
    late = build_dataset_resolver(env, now=lambda: datetime(2026, 12, 25, tzinfo=timezone.utc))
    with pytest.raises(gmd.GovernedMarketDatasetError, match="daily_bars_store_stale"):
        late({"dataset_id": wiring.GOVERNED_MARKET_DATASET_ID, "symbols": ["2330.TW"]})


def test_whole_market_binding_gets_the_unattested_pool_market_of_the_same_bars(tmp_path):
    """HYPOTHESIS-CARD-GOVERNED-BINDING-20261125: a card bound to the whole governed boundary gets the screen's
    ``candidate_pool_market`` from the same stored bars, without a survivorship attestation (the gate refuses it
    as ``survivorship_limited`` until the source owner attests coverage)."""
    from services.research.tests.test_governed_market_dataset import NOW, build_store

    build_store(tmp_path / "market_data_store", days=70)
    resolve = build_dataset_resolver(_env(tmp_path), now=lambda: NOW)
    plain = resolve({"dataset_id": wiring.GOVERNED_MARKET_DATASET_ID, "symbols": ["2330.TW", "2317.TW"]})
    pinned = plain["snapshot_id"]
    bound = resolve({"dataset_id": pinned, "symbols": ["2317.TW", "2330.TW"], "scope": "all_governed_tw",
                     "binding": "governed_reference", "source_dataset_refs": [f"daily_bars:{pinned}"]})
    market = bound["candidate_pool_market"]
    assert bound["dataset_id"] == pinned and sorted(market["symbols"]) == ["2317.TW", "2330.TW"]
    assert market["scope"] == "all_governed_tw" and market["source_refs"] == [f"daily_bars:{pinned}"]
    assert market["includes_delisted"] is False and market["includes_suspended"] is False
