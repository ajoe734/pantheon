"""EVOLOOP-003 tests for the minimal evolvable StrategyArtifact contract."""
from __future__ import annotations

import asyncio
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from services.governance.test_approval_authority import (advance_registry_http, advance_registry_unit, registry_unit_headers, configure_registry_unit_auth)
import httpx
from fastapi.testclient import TestClient

from services.runtime_auth_inbound import AuthContext

from .models import ArtifactState, ArtifactType, DeploymentStage
from . import service as service_module
from .service import _register_strategy_artifact, app, get_registry_service
from .split_api import RegistryError, RegistryService
from .storage import get_store, reset_store
from .strategy_artifact import (
    BUILTIN_STRATEGY_ARTIFACT_PATHS,
    FORBIDDEN_MUTATION_CONTROL_KEYS,
    REQUIRED_EXPRESSION_POLICY_CONTROLS,
    StrategyArtifactValidationError,
    build_market_transition_registry_payload,
    build_strategy_artifact_registry_payload,
    canonical_market_context,
    create_market_transition_revision,
    evaluate_strategy_action,
    evaluate_strategy_view,
    load_strategy_artifact_registration,
    mutate_strategy_artifact,
    strategy_artifact_checksum,
    validate_strategy_artifact,
)


@pytest.fixture(autouse=True)
def clean_store(monkeypatch):
    configure_registry_unit_auth(monkeypatch)
    reset_store()
    yield
    reset_store()


def _registration() -> dict:
    return load_strategy_artifact_registration(
        BUILTIN_STRATEGY_ARTIFACT_PATHS[0]
    )


_TEST_CTX = AuthContext(
    actor_id="test-operator", roles=frozenset({"operator"}), claims={}, token_kind="structured",
)


def _artifact() -> dict:
    return _registration()["strategy_artifact"]


def test_builtin_v1_is_schema_valid_and_maps_to_execution_bundle():
    registration = _registration()
    artifact = registration["strategy_artifact"]

    validate_strategy_artifact(artifact)
    registry_id, payload = build_strategy_artifact_registry_payload(registration)

    assert registry_id == artifact["artifact_id"]
    assert artifact["strategy_id"] == "tw_session_momentum"
    assert payload.artifact_type == ArtifactType.EXECUTION_BUNDLE
    assert payload.artifact_state == ArtifactState.CANDIDATE
    assert payload.storage_ref.path == "$.entry.metadata.strategy_artifact"
    assert payload.checksum == strategy_artifact_checksum(artifact)
    assert payload.metadata["strategy_artifact"] == artifact
    assert payload.lineage.source_run_ids == ["EVOLOOP-003"]
    assert artifact["binding_intent"]["persona_capital_binding_id"] == (
        "binding-tw-equity-paper"
    )
    assert artifact["binding_intent"]["observed_placeholder_artifact_id"] == (
        "artifact-tw-equity-session-v1"
    )


@pytest.fixture
def started_client():
    """TestClient inside its lifespan, so startup registers the built-ins."""
    with TestClient(app, headers=registry_unit_headers()) as client:
        yield client


def test_builtin_v1_is_registered_idempotently_after_store_reset():
    with TestClient(app, headers=registry_unit_headers()) as client:
        registry_id = "artifact-tw-session-momentum-v1"

        first = client.get(f"/api/registry/strategy-artifacts/{registry_id}")
        assert first.status_code == 200, first.text
        entry = first.json()["entry"]
        assert entry["registry_id"] == registry_id
        assert entry["strategy_id"] == "tw_session_momentum"
        assert entry["artifact_state"] == "candidate"
        assert first.json()["deployment_stage"] == "none"

        retry = client.post(
            "/api/registry/strategy-artifacts",
            json=_registration(),
        )
        assert retry.status_code == 200, retry.text
        assert retry.json()["entry"]["checksum"] == entry["checksum"]

        listed = client.get(
            "/api/registry/strategies/tw_session_momentum/strategy-artifacts"
        )
        assert listed.status_code == 200, listed.text
        assert [item["entry"]["registry_id"] for item in listed.json()] == [
            registry_id
        ]

    reset_store()
    with TestClient(app, headers=registry_unit_headers()) as restarted:
        after_reset = restarted.get(
            f"/api/registry/strategy-artifacts/{registry_id}"
        )
    assert after_reset.status_code == 200, after_reset.text


def test_v1_logic_interpreter_uses_declared_parameters():
    artifact = _artifact()

    assert evaluate_strategy_action(artifact, [100.0, 101.0]) == "BUY"
    assert evaluate_strategy_action(artifact, [100.0, 100.0]) == "SELL"
    assert evaluate_strategy_action(artifact, [100.0, 99.0]) == "SELL"

    with pytest.raises(StrategyArtifactValidationError, match="at least 2 closes"):
        evaluate_strategy_action(artifact, [100.0])

    child = mutate_strategy_artifact(
        artifact,
        new_artifact_id="artifact-tw-session-momentum-threshold",
        new_version="1.1.0",
        parameter_updates={"momentum_threshold": 0.01},
        source_run_ids=["training-session-threshold"],
    )
    assert evaluate_strategy_action(child, [100, 101]) == "SELL"
    assert evaluate_strategy_action(artifact, [10**1000, 10**1000 + 1]) == "BUY"


def test_mutation_api_creates_real_child_delta_and_preserves_parent(started_client):
    client = started_client
    parent_id = "artifact-tw-session-momentum-v1"
    child_id = "artifact-tw-session-momentum-v2"

    mutated = client.post(
        f"/api/registry/strategy-artifacts/{parent_id}/mutate",
        json={
            "new_artifact_id": child_id,
            "new_version": "1.1.0",
            "parameter_updates": {"momentum_threshold": 0.01},
            "source_run_ids": [
                "decision-evoloop-004",
                "work-item-evoloop-004",
                "training-session-evoloop-004",
            ],
        },
    )

    assert mutated.status_code == 200, mutated.text
    data = mutated.json()
    entry = data["entry"]
    child = entry["metadata"]["strategy_artifact"]
    assert entry["registry_id"] == child_id
    assert entry["artifact_state"] == "candidate"
    assert data["deployment_stage"] == "none"
    assert child["version"] == "1.1.0"
    assert child["strategy_id"] == "tw_session_momentum"
    assert child["parameters"]["momentum_threshold"] == 0.01
    assert child["lineage"]["parent_registry_ids"] == [parent_id]
    assert child["lineage"]["source_run_ids"][-1] == (
        "training-session-evoloop-004"
    )
    assert evaluate_strategy_action(child, [100.0, 100.5]) == "SELL"

    parent = client.get(
        f"/api/registry/strategy-artifacts/{parent_id}"
    ).json()["entry"]["metadata"]["strategy_artifact"]
    assert parent["version"] == "1.0.0"
    assert parent["parameters"]["momentum_threshold"] == 0.0


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"unknown_parameter": 1}, "unknown parameter"),
        ({"symbols": ["2330.TW"]}, "immutable"),
        ({"order_quantity": 2}, "immutable"),
        ({"lookback_bars": 2.5}, "requires an integer"),
        ({"momentum_threshold": -0.001}, "outside"),
        ({"momentum_threshold": 0.0105}, "not aligned to step"),
        ({"momentum_threshold": 0.0}, "must change"),
    ],
)
def test_mutation_fails_closed_for_invalid_parameter_updates(updates, message):
    with pytest.raises(StrategyArtifactValidationError, match=message):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-invalid",
            new_version="1.1.0",
            parameter_updates=updates,
            source_run_ids=["training-session-invalid"],
        )


def test_mutation_is_pure_and_requires_greater_version_and_run_lineage():
    parent = _artifact()
    original = copy.deepcopy(parent)

    child = mutate_strategy_artifact(
        parent,
        new_artifact_id="artifact-tw-session-momentum-v2",
        new_version="1.1.0",
        parameter_updates={"lookback_bars": 3},
        source_run_ids=["training-session-001"],
    )

    assert parent == original
    assert child is not parent
    assert child["parameters"]["lookback_bars"] == 3
    assert child["mutation_surface"]["controls"][0]["current_value"] == 3

    with pytest.raises(StrategyArtifactValidationError, match="greater"):
        mutate_strategy_artifact(
            parent,
            new_artifact_id="artifact-tw-session-momentum-v2",
            new_version="1.0.0",
            parameter_updates={"lookback_bars": 3},
            source_run_ids=["training-session-001"],
        )
    with pytest.raises(StrategyArtifactValidationError, match="source_run_id"):
        mutate_strategy_artifact(
            parent,
            new_artifact_id="artifact-tw-session-momentum-v2",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 3},
            source_run_ids=[],
        )


def test_semantic_validation_requires_total_parameter_partition():
    artifact = _artifact()
    artifact["mutation_surface"]["immutable_parameters"].remove("order_quantity")

    with pytest.raises(StrategyArtifactValidationError, match="missing: order_quantity"):
        validate_strategy_artifact(artifact)


@pytest.mark.parametrize("actual_value", [True, 1.0])
def test_integer_control_validates_actual_parameter_type(actual_value):
    artifact = _artifact()
    artifact["parameters"]["integer_knob"] = actual_value
    artifact["mutation_surface"]["controls"].append(
        {
            "parameter_key": "integer_knob",
            "value_type": "integer",
            "current_value": 1,
            "allowed_range": {"min": 0, "max": 2},
            "step": 1,
        }
    )

    with pytest.raises(StrategyArtifactValidationError, match="requires an integer"):
        validate_strategy_artifact(artifact)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("lineage", {"source_run_ids": ["   "]}, "canonical non-blank ids"),
        ("algorithm_ref", {"path": " "}, "algorithm_ref.path"),
        ("binding_intent", {"observed_at": "not-a-date"}, "RFC3339"),
    ],
)
def test_semantic_validation_rejects_blank_refs_and_invalid_time(
    field,
    value,
    message,
):
    artifact = _artifact()
    artifact[field].update(value)

    with pytest.raises(StrategyArtifactValidationError, match=message):
        validate_strategy_artifact(artifact)


@pytest.mark.parametrize("wrapper_field", ["metadata", "evaluation_summary"])
def test_registration_rejects_falsy_non_object_wrappers(wrapper_field):
    registration = _registration()
    registration[wrapper_field] = []

    with pytest.raises(StrategyArtifactValidationError, match="JSON object"):
        build_strategy_artifact_registry_payload(registration)


def test_registration_rejects_non_canonical_supplemental_json():
    registration = _registration()
    registration["metadata"] = {"non_finite": float("nan")}

    with pytest.raises(StrategyArtifactValidationError, match="not canonical JSON"):
        build_strategy_artifact_registry_payload(registration)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("artifact_id", "artifact/route-break", "schema validation failed"),
        (
            "algorithm_path",
            "../outside.py",
            "normalized repository-relative path",
        ),
        (
            "logic_interpreter",
            "not-an-import-reference",
            "module:object Python reference",
        ),
    ],
)
def test_strategy_artifact_rejects_route_breaking_ids_and_unsafe_code_refs(
    field,
    value,
    message,
):
    artifact = _artifact()
    if field == "algorithm_path":
        artifact["algorithm_ref"]["path"] = value
    elif field == "logic_interpreter":
        artifact["algorithm_ref"]["logic_interpreter"] = value
    else:
        artifact[field] = value

    with pytest.raises(StrategyArtifactValidationError, match=message):
        validate_strategy_artifact(artifact)


def test_mutation_rejects_string_run_sequence_and_huge_out_of_range_integer():
    with pytest.raises(StrategyArtifactValidationError, match="not a string"):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-string-runs",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 3},
            source_run_ids="run-001",
        )

    with pytest.raises(StrategyArtifactValidationError, match="outside"):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-huge-lookback",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 10**1000},
            source_run_ids=["run-001"],
        )

    with pytest.raises(StrategyArtifactValidationError, match="finite number"):
        mutate_strategy_artifact(
            _artifact(),
            new_artifact_id="artifact-tw-session-momentum-digit-limit",
            new_version="1.1.0",
            parameter_updates={"lookback_bars": 10**10000},
            source_run_ids=["run-001"],
        )


def test_strategy_artifact_facade_rejects_plain_execution_bundle():
    client = TestClient(app, headers=registry_unit_headers())
    created = client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "execution_bundle",
            "strategy_id": "plain-execution",
            "version": "1.0.0",
            "artifact_state": "candidate",
            "lineage": {"source_run_ids": ["run-plain"]},
            "storage_ref": {
                "backend": "object_store",
                "path": "s3://bucket/plain.tar.gz",
            },
            "checksum": "sha256:plain",
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]

    specialized = client.get(
        f"/api/registry/strategy-artifacts/{registry_id}"
    )
    assert specialized.status_code == 404, specialized.text


@pytest.mark.parametrize(
    "embedded",
    [
        {},
        pytest.param(
            {**_artifact(), "artifact_id": "different-artifact-id"},
            id="envelope-id-mismatch",
        ),
    ],
)
def test_strategy_artifact_facade_rejects_malformed_or_mismatched_overlay(embedded):
    client = TestClient(app, headers=registry_unit_headers())
    created = client.post(
        "/api/registry/entries",
        json={
            "artifact_type": "execution_bundle",
            "strategy_id": "malformed-overlay",
            "version": "1.0.0",
            "artifact_state": "candidate",
            "lineage": {"source_run_ids": ["run-malformed"]},
            "storage_ref": {
                "backend": "inline",
                "path": "$.entry.metadata.strategy_artifact",
            },
            "checksum": "sha256:wrong",
            "metadata": {"strategy_artifact": embedded},
        },
    )
    assert created.status_code == 200, created.text
    registry_id = created.json()["entry"]["registry_id"]

    specialized = client.get(
        f"/api/registry/strategy-artifacts/{registry_id}"
    )
    assert specialized.status_code == 404, specialized.text
    listed = client.get(
        "/api/registry/strategies/malformed-overlay/strategy-artifacts"
    )
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("artifact_state", "draft"),
        ("metadata", {"caller_note": "different"}),
        ("evaluation_summary", {"score": 0.5}),
        ("producer_run_id", "different-producer"),
        ("rollback_target", "artifact-prior"),
    ],
)
def test_idempotent_retry_rejects_changed_registration_envelope(field, value, started_client):
    client = started_client
    registry_id = "artifact-tw-session-momentum-v1"
    seeded = client.get(f"/api/registry/strategy-artifacts/{registry_id}")
    assert seeded.status_code == 200, seeded.text

    changed = _registration()
    changed[field] = value
    response = client.post("/api/registry/strategy-artifacts", json=changed)

    assert response.status_code == 400, response.text
    assert "different content" in response.json()["detail"]


def test_same_child_id_concurrent_mutations_never_overwrite():
    parent = _artifact()
    registry_service = get_registry_service()
    child_id = "artifact-tw-session-momentum-concurrent-v2"
    children = [
        mutate_strategy_artifact(
            parent,
            new_artifact_id=child_id,
            new_version="1.1.0",
            parameter_updates={"momentum_threshold": threshold},
            source_run_ids=[f"training-session-{index}"],
        )
        for index, threshold in enumerate((0.01, 0.02), start=1)
    ]
    barrier = Barrier(2)

    def register(child):
        barrier.wait()
        try:
            view = _register_strategy_artifact(
                registry_service,
                {
                    "registry_id": child_id,
                    "artifact_state": "candidate",
                    "strategy_artifact": child,
                },
                ctx=_TEST_CTX,
            )
            return "created", view.entry.checksum
        except RegistryError as exc:
            return "collision", str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(register, children))

    assert sorted(result[0] for result in results) == ["collision", "created"]
    stored = registry_service.get(child_id).entry
    assert stored.metadata["strategy_artifact"]["parameters"][
        "momentum_threshold"
    ] in {0.01, 0.02}


def test_fastapi_startup_registers_builtin_before_health_only_request():
    registry_id = "artifact-tw-session-momentum-v1"
    with TestClient(app, headers=registry_unit_headers()) as client:
        health = client.get("/health")
        assert health.status_code == 200, health.text
        assert get_store().get(registry_id) is not None


def test_startup_failure_is_visible_and_requests_do_not_bootstrap(monkeypatch):
    calls = []
    real = service_module.ensure_builtin_strategy_artifacts

    def counting(registry_service):
        calls.append(registry_service)
        return real(registry_service)

    monkeypatch.setattr(service_module, "ensure_builtin_strategy_artifacts", counting)
    registry_id = "artifact-tw-session-momentum-v1"
    with TestClient(app, headers=registry_unit_headers()) as client:
        assert len(calls) == 1
        assert client.get("/health").status_code == 200
        assert client.get(f"/api/registry/strategy-artifacts/{registry_id}").status_code == 200
        assert client.get(f"/api/registry/entries/{registry_id}").status_code == 200
        assert len(calls) == 1

    def failing(_registry_service):
        raise RegistryError("conflicting builtin")

    monkeypatch.setattr(service_module, "ensure_builtin_strategy_artifacts", failing)
    with pytest.raises(RegistryError, match="conflicting builtin"):
        with TestClient(app, headers=registry_unit_headers()):
            pass


@pytest.mark.parametrize(
    ("method", "path", "body", "blocked_method"),
    [
        ("GET", "/api/registry/entries/reg-blocked-owner-call", None, "get"),
        (
            "POST",
            "/api/registry/strategy-specs",
            {
                "strategy_id": "blocked-owner-strat",
                "version": "1.0.0",
                "lineage": {"source_run_ids": ["source"]},
                "strategy_spec": {
                    "spec_version": "1.0",
                    "strategy_id": "blocked-owner-strat",
                    "title": "Blocked owner probe strategy",
                    "hypothesis": "Deterministic probe hypothesis for event loop tests.",
                    "objective": "Prove a blocked owner call leaves the event loop free.",
                    "market_scope": {"symbols": ["TEST"], "frequency": "1d"},
                    "data_dependencies": [{"ref": "test-fixture", "kind": "note"}],
                    "execution_profile": {
                        "signal_schema_version": "1.0",
                        "quantity_type": "SHARES",
                    },
                    "evaluation_plan": {"metrics": ["sharpe"]},
                    "governance": {"approval_required": True},
                    "provenance": {
                        "source_kind": "manual",
                        "created_at": "2026-01-01T00:00:00Z",
                    },
                },
            },
            "register_strategy_spec_revision",
        ),
    ],
)
def test_blocked_owner_call_does_not_block_event_loop(
    monkeypatch, method, path, body, blocked_method
):
    service_module.ensure_builtin_strategy_artifacts(RegistryService(get_store()))
    entered = threading.Event()
    release = threading.Event()
    real = getattr(RegistryService, blocked_method)

    def blocking(self, *args, **kwargs):
        if blocked_method == "get" and args[:1] != ("reg-blocked-owner-call",):
            return real(self, *args, **kwargs)
        entered.set()
        assert release.wait(10)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(RegistryService, blocked_method, blocking)
    headers = registry_unit_headers()

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://registry") as client:
            blocked = asyncio.ensure_future(
                client.request(method, path, json=body, headers=headers)
            )
            try:
                loop = asyncio.get_running_loop()
                assert await asyncio.wait_for(
                    loop.run_in_executor(None, entered.wait, 5), 8
                )
                health = await asyncio.wait_for(client.get("/health"), 5)
                assert health.status_code == 200
                peer = await asyncio.wait_for(
                    client.get(
                        "/api/registry/entries/artifact-tw-session-momentum-v1",
                        headers=headers,
                    ),
                    5,
                )
                assert peer.status_code == 200, peer.text
                assert not blocked.done()
            finally:
                release.set()
            await asyncio.wait_for(blocked, 10)

    asyncio.run(scenario())


def test_strategy_artifact_advance_preserves_deployment_split():
    """Checked-in built-ins are immutable via caller routes (reviewer finding
    1: "deny unauthorized builtin mutation") — this proof registers a fresh,
    non-builtin StrategyArtifact (a caller-owned entry, not a bootstrap
    artifact) and advances *that* instead of the shared built-in fixture."""
    client = TestClient(app, headers=registry_unit_headers())
    artifact = copy.deepcopy(_artifact())
    artifact["artifact_id"] = "artifact-advance-split-test-v1"
    artifact["strategy_id"] = "advance-split-test"
    registered = client.post(
        "/api/registry/strategy-artifacts",
        json={"strategy_artifact": artifact},
    )
    assert registered.status_code == 200, registered.text
    registry_id = registered.json()["entry"]["registry_id"]

    approved = advance_registry_http(
        client, f"/api/registry/strategy-artifacts/{registry_id}/advance",
        json={
            "target_state": "approved",
            "expected_artifact_state": "candidate",
            "approver": "test-reviewer",
            "approval_decision_id": "decision-evoloop-006",
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["entry"]["artifact_state"] == "approved"
    assert approved.json()["entry"]["approver"] == "test-reviewer"
    assert approved.json()["entry"]["approval_decision_id"] == (
        "decision-evoloop-006"
    )
    assert approved.json()["deployment_stage"] == DeploymentStage.NONE.value


def test_strategy_artifact_accepts_canonical_market_contexts():
    # US market with bare symbol or .US suffix
    us_artifact = copy.deepcopy(_artifact())
    us_artifact["parameters"]["symbols"] = ["SPY", "AAPL.US"]
    us_artifact["parameters"]["market"] = "US"
    us_artifact["mutation_surface"]["immutable_parameters"].append("market")
    validate_strategy_artifact(us_artifact)

    # TW market with .TW symbols
    tw_artifact = copy.deepcopy(_artifact())
    tw_artifact["parameters"]["symbols"] = ["2330", "2330.TW"]
    tw_artifact["parameters"]["market"] = "TW"
    tw_artifact["mutation_surface"]["immutable_parameters"].append("market")
    validate_strategy_artifact(tw_artifact)

    # CRYPTO market with crypto pair
    crypto_artifact = copy.deepcopy(_artifact())
    crypto_artifact["parameters"]["symbols"] = ["BTCUSDT", "ETHUSD"]
    crypto_artifact["parameters"]["market"] = "CRYPTO"
    crypto_artifact["mutation_surface"]["immutable_parameters"].append("market")
    validate_strategy_artifact(crypto_artifact)


def test_strategy_artifact_rejects_unsupported_or_non_canonical_market():
    artifact = copy.deepcopy(_artifact())
    artifact["parameters"]["symbols"] = ["SPY"]
    artifact["parameters"]["market"] = "MARS"
    artifact["mutation_surface"]["immutable_parameters"].append("market")
    with pytest.raises(StrategyArtifactValidationError, match="unsupported market context"):
        validate_strategy_artifact(artifact)

    artifact["parameters"]["market"] = "us"  # lowercase is non-canonical
    with pytest.raises(StrategyArtifactValidationError, match="must be canonical 'US', got 'us'"):
        validate_strategy_artifact(artifact)

    artifact["parameters"]["market"] = 123
    with pytest.raises(StrategyArtifactValidationError, match="parameter 'market' must be a string"):
        validate_strategy_artifact(artifact)


def test_strategy_artifact_rejects_contradictory_market_and_symbols():
    artifact = copy.deepcopy(_artifact())
    artifact["parameters"]["market"] = "US"
    artifact["parameters"]["symbols"] = ["2330.TW"]
    artifact["mutation_surface"]["immutable_parameters"].append("market")
    with pytest.raises(StrategyArtifactValidationError, match="contradicts explicit market 'US'"):
        validate_strategy_artifact(artifact)


def test_strategy_artifact_requires_market_in_immutable_parameters():
    artifact = copy.deepcopy(_artifact())
    artifact["parameters"]["symbols"] = ["SPY"]
    artifact["parameters"]["market"] = "US"
    # Omit "market" from immutable_parameters
    with pytest.raises(StrategyArtifactValidationError, match="every parameter must be classified.*market"):
        validate_strategy_artifact(artifact)


def test_strategy_artifact_mutation_cannot_modify_immutable_market():
    artifact = copy.deepcopy(_artifact())
    artifact["parameters"]["symbols"] = ["SPY"]
    artifact["parameters"]["market"] = "US"
    artifact["mutation_surface"]["immutable_parameters"].append("market")
    with pytest.raises(StrategyArtifactValidationError, match="parameter 'market' is immutable"):
        mutate_strategy_artifact(
            artifact,
            new_artifact_id="artifact-child-mutate-market",
            new_version="1.1.0",
            parameter_updates={"market": "TW"},
            source_run_ids=["training-session-market"],
        )


def test_canonical_market_context_bare_quote_suffixes_do_not_override_owner_market():
    # ABNB ends in BNB, GBTC ends in BTC, EURUSD ends in USD - none should guess CRYPTO
    assert canonical_market_context("US", ["ABNB"]) == "US"
    assert canonical_market_context("US", ["GBTC"]) == "US"
    assert canonical_market_context("FX", ["EURUSD"]) == "FX"
    assert canonical_market_context("US", ["SPY"]) == "US"


def test_canonical_market_context_retains_real_dotted_market_contradictions():
    with pytest.raises(StrategyArtifactValidationError, match="symbol '2330.TW' intrinsic market 'TW' contradicts explicit market 'US'"):
        canonical_market_context("US", ["2330.TW"])

    with pytest.raises(StrategyArtifactValidationError, match="symbol 'SPY.US' intrinsic market 'US' contradicts explicit market 'TW'"):
        canonical_market_context("TW", ["SPY.US"])

    with pytest.raises(StrategyArtifactValidationError, match="symbol 'BTCUSD.CRYPTO' intrinsic market 'CRYPTO' contradicts explicit market 'US'"):
        canonical_market_context("US", ["BTCUSD.CRYPTO"])

    with pytest.raises(StrategyArtifactValidationError, match="symbol 'ETH.US' intrinsic market 'US' contradicts explicit market 'CRYPTO'"):
        canonical_market_context("CRYPTO", ["ETH.US"])

    with pytest.raises(StrategyArtifactValidationError, match="symbol 'EURUSD.CRYPTO' intrinsic market 'CRYPTO' contradicts explicit market 'FX'"):
        canonical_market_context("FX", ["EURUSD.CRYPTO"])


def test_strategy_artifact_accepts_bare_symbols_with_explicit_market():
    # US equity artifact with ABNB and GBTC
    us_artifact = copy.deepcopy(_artifact())
    us_artifact["parameters"]["symbols"] = ["ABNB", "GBTC", "SPY"]
    us_artifact["parameters"]["market"] = "US"
    us_artifact["mutation_surface"]["immutable_parameters"].append("market")
    validate_strategy_artifact(us_artifact)

    # FX artifact with EURUSD
    fx_artifact = copy.deepcopy(_artifact())
    fx_artifact["parameters"]["symbols"] = ["EURUSD"]
    fx_artifact["parameters"]["market"] = "FX"
    fx_artifact["mutation_surface"]["immutable_parameters"].append("market")
    validate_strategy_artifact(fx_artifact)


def test_create_market_transition_revision_preserves_parent_and_sets_canonical_market():
    parent = copy.deepcopy(_artifact())
    parent["artifact_id"] = "artifact-legacy-v1"
    parent["version"] = "1.0.0"
    parent["parameters"]["symbols"] = ["SPY"]
    assert "market" not in parent["parameters"]
    parent_checksum = strategy_artifact_checksum(parent)
    parent_copy = copy.deepcopy(parent)

    child = create_market_transition_revision(
        parent,
        market="US",
        new_artifact_id="artifact-legacy-v1-rev1",
        new_version="1.0.1",
        source_run_ids=["transition-run-001"],
    )

    # Parent remains byte-identical and completely unmutated
    assert parent == parent_copy
    assert strategy_artifact_checksum(parent) == parent_checksum
    assert "market" not in parent["parameters"]

    # Child is valid, monotonic, cites parent, and carries canonical market
    validate_strategy_artifact(child)
    assert child["artifact_id"] == "artifact-legacy-v1-rev1"
    assert child["version"] == "1.0.1"
    assert child["lineage"]["parent_registry_ids"] == ["artifact-legacy-v1"]
    assert child["lineage"]["source_run_ids"] == ["transition-run-001"]
    assert child["parameters"]["market"] == "US"
    assert "market" in child["mutation_surface"]["immutable_parameters"]
    assert child["parameters"]["symbols"] == ["SPY"]


def test_create_market_transition_revision_rejects_contradictory_or_invalid_market():
    parent = copy.deepcopy(_artifact())
    parent["parameters"]["symbols"] = ["2330.TW"]

    with pytest.raises(StrategyArtifactValidationError, match="contradicts explicit market 'US'"):
        create_market_transition_revision(
            parent,
            market="US",
            new_artifact_id="artifact-tw-child",
            new_version="1.0.1",
            source_run_ids=["transition-run-002"],
        )

    with pytest.raises(StrategyArtifactValidationError, match="unsupported market context 'INVALID'"):
        create_market_transition_revision(
            parent,
            market="INVALID",
            new_artifact_id="artifact-invalid-child",
            new_version="1.0.1",
            source_run_ids=["transition-run-002"],
        )


def test_build_market_transition_registry_payload():
    parent = copy.deepcopy(_artifact())
    parent["artifact_id"] = "artifact-parent-001"
    parent["version"] = "1.0.0"
    parent["parameters"]["symbols"] = ["SPY"]
    parent_entry = {
        "registry_id": "artifact-parent-001",
        "metadata": {"strategy_artifact": parent},
    }

    registry_id, payload = build_market_transition_registry_payload(
        parent_entry,
        market="US",
        new_artifact_id="artifact-parent-001-rev1",
        new_version="1.0.1",
        source_run_ids=["transition-run-003"],
    )

    assert registry_id == "artifact-parent-001-rev1"
    assert payload.artifact_type == ArtifactType.EXECUTION_BUNDLE
    assert payload.version == "1.0.1"
    assert payload.lineage.parent_registry_ids == ["artifact-parent-001"]
    assert payload.metadata["strategy_artifact"]["parameters"]["market"] == "US"
    assert payload.metadata["parent_registry_id"] == "artifact-parent-001"


def test_lightweight_canonical_source_snapshot_admission_subprocess_forbids_pydantic():
    """REGISTRY-LIGHTWEIGHT-MARKET-IMPORT-20261018:
    Paper workers (paper-fleet-reconciler, paper-signal-producer) validate canonical
    Source snapshots via admit_market_snapshot -> admit_canonical_source_snapshot ->
    canonical_market_context without pydantic installed. Verify in a fresh subprocess
    with pydantic imports forbidden that valid and contradictory market cases behave
    identically without importing pydantic or services.governance.approval_authority.
    """
    import json
    from pathlib import Path
    import subprocess
    import sys
    import textwrap

    repo_root = Path(__file__).resolve().parents[2]
    script = textwrap.dedent(
        """
        import importlib.abc
        import json
        import sys

        class _ForbidPydanticFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == "pydantic" or fullname.startswith("pydantic."):
                    raise ModuleNotFoundError("No module named 'pydantic'")
                return None

        for mod in list(sys.modules):
            if mod == "pydantic" or mod.startswith("pydantic."):
                del sys.modules[mod]
        sys.meta_path.insert(0, _ForbidPydanticFinder())

        try:
            import pydantic  # noqa: F401
        except ModuleNotFoundError:
            pass
        else:
            raise AssertionError("pydantic import was not blocked")

        from services.execution.lean_runtime import paper_signal_producer
        from services.execution.market_snapshot_admission import (
            admit_canonical_source_snapshot,
            admit_market_snapshot,
        )
        from services.paper_fleet_reconciler import paper_fleet_reconciler
        from services.registry.strategy_artifact import (
            StrategyArtifactValidationError,
            canonical_market_context,
        )
        from services.source_ingestion.requirement_state import (
            LatestMarketSnapshot,
            MarketSnapshotPoint,
        )
        assert paper_fleet_reconciler.admit_market_snapshot is admit_market_snapshot
        assert paper_signal_producer.admit_market_snapshot is admit_market_snapshot

        now_iso = "2026-10-07T06:00:00Z"
        us_snap = LatestMarketSnapshot(
            symbol="SPY",
            points=(
                MarketSnapshotPoint(
                    event_time="2026-10-07T05:50:00Z",
                    close=500.0,
                    source_id="us-src:p1",
                    connector_id="us-conn",
                    content_ref="ref-1",
                    ingest_run_id="run-1",
                    market="US",
                ),
                MarketSnapshotPoint(
                    event_time="2026-10-07T05:55:00Z",
                    close=502.0,
                    source_id="us-src:p2",
                    connector_id="us-conn",
                    content_ref="ref-2",
                    ingest_run_id="run-1",
                    market="US",
                ),
            ),
            observed_at=now_iso,
            market="US",
        )
        us_internal = us_snap.to_dict()
        us_public = us_snap.to_public_dict(requested_symbol="SPY")

        dec_internal = admit_market_snapshot(
            us_internal, expected_symbol="SPY", max_age_seconds=86400, now_iso=now_iso
        )
        assert dec_internal.admitted is True, dec_internal
        assert dec_internal.snapshot_id == us_internal["snapshot_id"]

        dec_public = admit_market_snapshot(
            us_public, expected_symbol="SPY", max_age_seconds=86400, now_iso=now_iso
        )
        assert dec_public.admitted is True, dec_public
        assert dec_public.snapshot_id == us_public["snapshot_id"]

        tw_snap = LatestMarketSnapshot(
            symbol="2330.TWSE",
            points=(
                MarketSnapshotPoint(
                    event_time="2026-10-06T05:30:00Z",
                    close=950.0,
                    source_id="tw-official:tw_price_daily:TWSE:2330:p1",
                    connector_id="tw-twse-tpex-official-market",
                    content_ref="tw-official://ref1",
                    ingest_run_id="run-tw-1",
                    market="TWSE",
                ),
                MarketSnapshotPoint(
                    event_time="2026-10-07T05:30:00Z",
                    close=955.0,
                    source_id="tw-official:tw_price_daily:TWSE:2330:p2",
                    connector_id="tw-twse-tpex-official-market",
                    content_ref="tw-official://ref2",
                    ingest_run_id="run-tw-1",
                    market="TWSE",
                ),
            ),
            observed_at=now_iso,
            market="TWSE",
        )
        tw_public = tw_snap.to_public_dict(requested_symbol="2330.TW")
        dec_tw = admit_market_snapshot(
            tw_public, expected_symbol="2330.TW", max_age_seconds=86400, now_iso=now_iso
        )
        assert dec_tw.admitted is True, dec_tw

        assert canonical_market_context("US", ["ABNB", "SPY"]) == "US"
        try:
            canonical_market_context("US", ["2330.TW"])
        except StrategyArtifactValidationError as exc:
            assert "contradicts explicit market 'US'" in str(exc)
        else:
            raise AssertionError("Expected StrategyArtifactValidationError for 2330.TW in US")

        contradictory_tw_in_us = dict(us_public, symbol="2330.TW", market="US")
        dec_contra_1 = admit_market_snapshot(
            contradictory_tw_in_us,
            expected_symbol="2330.TW",
            max_age_seconds=86400,
            now_iso=now_iso,
        )
        assert dec_contra_1.admitted is False
        assert dec_contra_1.reason_code == "market_input_invalid"
        assert "contradicts explicit market 'US'" in (dec_contra_1.detail or "")

        contradictory_us_in_tw = dict(tw_public, symbol="SPY.US", market="TW")
        dec_contra_2 = admit_canonical_source_snapshot(
            contradictory_us_in_tw,
            expected_symbol="SPY.US",
            max_age_seconds=86400,
            now_iso=now_iso,
        )
        assert dec_contra_2.admitted is False
        assert dec_contra_2.reason_code == "market_input_invalid"
        assert "contradicts explicit market 'TW'" in (dec_contra_2.detail or "")

        unsupported_market = dict(us_public, market="MARS")
        dec_bad_mkt = admit_market_snapshot(
            unsupported_market,
            expected_symbol="SPY",
            max_age_seconds=86400,
            now_iso=now_iso,
        )
        assert dec_bad_mkt.admitted is False
        assert dec_bad_mkt.reason_code == "market_input_invalid"
        assert "unsupported market context" in (dec_bad_mkt.detail or "")

        assert not any(
            m == "pydantic" or m.startswith("pydantic.") for m in sys.modules
        )
        assert "services.governance.approval_authority" not in sys.modules

        print(
            json.dumps(
                {
                    "us_internal_admitted": dec_internal.admitted,
                    "us_public_admitted": dec_public.admitted,
                    "tw_public_admitted": dec_tw.admitted,
                    "contra_tw_in_us_reason": dec_contra_1.reason_code,
                    "contra_us_in_tw_reason": dec_contra_2.reason_code,
                    "unsupported_market_reason": dec_bad_mkt.reason_code,
                    "pydantic_imported": False,
                    "governance_approval_authority_imported": False,
                }
            )
        )
        """
    )

    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    payload = json.loads(proc.stdout.strip())
    assert payload == {
        "us_internal_admitted": True,
        "us_public_admitted": True,
        "tw_public_admitted": True,
        "contra_tw_in_us_reason": "market_input_invalid",
        "contra_us_in_tw_reason": "market_input_invalid",
        "unsupported_market_reason": "market_input_invalid",
        "pydantic_imported": False,
        "governance_approval_authority_imported": False,
    }


def _spec_entry_with_recipe(recipe_overrides=None, **entry_overrides):
    from .strategy_artifact import strategy_spec_payload_checksum

    recipe = {
        "kind": "close_to_close_momentum",
        "symbols": ["2330.TW"],
        "bar_frequency": "1d",
        "data_source": "source-ingest:normalized/x",
        "lookback_bars": 3,
        "momentum_threshold": 0.02,
        "positive_action": "BUY",
        "non_positive_action": "HOLD",
        "order_quantity": 2,
        "quantity_type": "SHARES",
        **(recipe_overrides or {}),
    }
    spec = {
        "strategy_id": "strat-x",
        "market_scope": {"symbols": ["2330.TW"], "frequency": "1d"},
        "data_dependencies": [{"ref": "source-ingest:normalized/x", "kind": "dataset"}],
        "execution_profile": {"quantity_type": "SHARES"},
        "metadata": {"execution_recipe": recipe},
    }
    entry = {
        "registry_id": "spec-reg-1",
        "version": "1.2.0",
        "checksum": strategy_spec_payload_checksum(spec),
        "lineage": {"source_run_ids": ["seed-1"], "source_dataset_refs": ["src-1"]},
        "metadata": {"strategy_spec": spec},
    }
    entry.update(entry_overrides)
    return entry


def test_execution_artifact_is_distinct_and_keeps_spec_identity_and_lineage():
    from .strategy_artifact import build_execution_artifact_registration

    entry = _spec_entry_with_recipe()
    original = copy.deepcopy(entry)
    registration = build_execution_artifact_registration(
        entry, artifact_id="exec-1", algorithm_commit="b" * 40
    )
    artifact = registration["strategy_artifact"]

    assert entry == original
    assert artifact["artifact_id"] == "exec-1" != entry["registry_id"]
    assert (artifact["strategy_id"], artifact["version"]) == ("strat-x", "1.2.0")
    assert artifact["lineage"]["source_strategy_spec_id"] == "spec-reg-1"
    assert artifact["lineage"]["source_run_ids"] == ["seed-1"]
    assert evaluate_strategy_action(artifact, [100, 100, 103]) == "BUY"
    assert evaluate_strategy_action(artifact, [100, 100, 101]) == "HOLD"
    registry_id, _ = build_strategy_artifact_registry_payload(registration)
    assert registry_id == "exec-1"


@pytest.mark.parametrize(
    "mutate, reason",
    [
        (lambda e: e.update(checksum="sha256:" + "0" * 64), "checksum does not match"),
        (lambda e: e["metadata"]["strategy_spec"]["metadata"]["execution_recipe"].update(kind="pairs"), "unsupported execution_recipe kind"),
    ],
)
def test_execution_artifact_rejects_tampered_or_unsupported_spec(mutate, reason):
    from .strategy_artifact import (
        build_execution_artifact_registration,
        strategy_spec_payload_checksum,
    )

    entry = _spec_entry_with_recipe()
    mutate(entry)
    if reason != "checksum does not match":
        entry["checksum"] = strategy_spec_payload_checksum(entry["metadata"]["strategy_spec"])
    with pytest.raises(StrategyArtifactValidationError, match=reason):
        build_execution_artifact_registration(
            entry, artifact_id="exec-1", algorithm_commit="b" * 40
        )


def test_execution_artifact_rejects_recipe_spec_contradiction_and_same_id():
    from .strategy_artifact import build_execution_artifact_registration

    with pytest.raises(StrategyArtifactValidationError, match="quantity_type contradicts"):
        build_execution_artifact_registration(
            _spec_entry_with_recipe({"quantity_type": "CASH_VALUE"}),
            artifact_id="exec-1",
            algorithm_commit="b" * 40,
        )
    with pytest.raises(StrategyArtifactValidationError, match="distinct"):
        build_execution_artifact_registration(
            _spec_entry_with_recipe(), artifact_id="spec-reg-1", algorithm_commit="b" * 40
        )


def test_list_strategy_artifacts_catalog_flat_projection_and_paging():
    """GET /api/registry/strategy-artifacts returns flat execution_bundle rows without full metadata,
    excludes strategy_spec entries, extracts risk_level from evaluation_summary, and pages with total."""
    from .models import Lineage, RegistryEntryCreate, StorageBackend, StorageRef
    from .pg_store import strategy_artifact_rows_from_columns, strategy_artifact_select_sql
    from .storage import STRATEGY_ARTIFACT_EVAL_KEYS, STRATEGY_ARTIFACT_ROW_KEYS

    unit_tenant = service_module._caller_tenant(
        service_module._authenticate_registry_read(registry_unit_headers()["Authorization"])
    )
    store = get_store()
    # Seed a strategy_spec row that must NOT appear in GET /api/registry/strategy-artifacts
    store.create(
        RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id="spec-only-family",
            version="1.0.0",
            artifact_state=ArtifactState.APPROVED,
            lineage=Lineage(source_run_ids=["run-spec"]),
            storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_spec"),
            checksum="sha256:spec-only",
            evaluation_summary={"risk_level": "medium"},
            metadata={"strategy_spec": {"strategy_id": "spec-only-family"}},
        ),
        "spec-only-1",
        actor={"tenant": unit_tenant},
    )

    # Seed 3 execution_bundle rows with real evaluation_summary shape and ~5.9 KB metadata
    expected_keys = set(STRATEGY_ARTIFACT_ROW_KEYS) | set(STRATEGY_ARTIFACT_EVAL_KEYS)
    for idx, (state, risk, ts) in enumerate(
        [
            (ArtifactState.APPROVED, "low", "2026-11-04T01:00:00Z"),
            (ArtifactState.CANDIDATE, "medium", "2026-11-04T02:00:00Z"),
            (ArtifactState.RETIRED, "high", "2026-11-04T03:00:00Z"),
        ],
        start=1,
    ):
        entry = store.create(
            RegistryEntryCreate(
                artifact_type=ArtifactType.EXECUTION_BUNDLE,
                strategy_id=f"strat-bundle-{idx}",
                version=f"1.{idx}.0",
                artifact_state=state,
                lineage=Lineage(source_run_ids=[f"run-{idx}"]),
                storage_ref=StorageRef(backend=StorageBackend.INLINE, path="$.entry.metadata.strategy_artifact"),
                checksum=f"sha256:bundle-{idx:04d}",
                evaluation_summary={
                    "admission": "persona_paper_bootstrap",
                    "risk_level": risk,
                    "capital_scale_pct": 0.0,
                    "execution_context": "paper",
                },
                metadata={"strategy_artifact": {"artifact_id": f"bundle-{idx}", "blob": "x" * 5800}},
            ),
            f"bundle-{idx}",
            actor={"tenant": unit_tenant},
        )
        entry.created_at = ts
        entry.approver = f"approver-{idx}" if state == ArtifactState.APPROVED else None
        store.put(entry)

    client = TestClient(app, headers=registry_unit_headers())
    page1 = client.get("/api/registry/strategy-artifacts?page_size=2")
    assert page1.status_code == 200, page1.text
    body1 = page1.json()
    assert body1["total"] == 3
    assert body1["page_size"] == 2
    assert body1["next_page_token"] == "2"
    assert [r["registry_id"] for r in body1["items"]] == ["bundle-3", "bundle-2"]
    for row in body1["items"]:
        assert set(row.keys()) == expected_keys
        assert "metadata" not in row

    assert body1["items"][0]["risk_level"] == "high"
    assert body1["items"][0]["artifact_state"] == "retired"
    assert body1["items"][1]["risk_level"] == "medium"
    assert body1["items"][1]["artifact_state"] == "candidate"

    page2 = client.get(f"/api/registry/strategy-artifacts?page_size=2&page_token={body1['next_page_token']}")
    assert page2.status_code == 200, page2.text
    body2 = page2.json()
    assert body2["total"] == 3
    assert body2["next_page_token"] is None
    assert [r["registry_id"] for r in body2["items"]] == ["bundle-1"]
    assert body2["items"][0]["approver"] == "approver-1"
    assert body2["items"][0]["risk_level"] == "low"

    # Verify Postgres SQL select expression and column assembly twin
    select_sql = strategy_artifact_select_sql()
    assert "metadata" not in select_sql
    assert "evaluation_summary" in select_sql
    assembled = strategy_artifact_rows_from_columns(
        [
            (
                '"bundle-1"',
                '"strat-bundle-1"',
                '"1.1.0"',
                '"approved"',
                '"sha256:bundle-0001"',
                '"2026-11-04T01:00:00Z"',
                '"approver-1"',
                '"low"',
            )
        ]
    )
    assert assembled == [body2["items"][0]]


def _artifact_with_expression_policy() -> dict:
    artifact = copy.deepcopy(_artifact())
    artifact["parameters"]["strength_scale_k"] = 1.0
    artifact["parameters"]["strength_floor_momentum"] = 0.01
    artifact["expression_policy"] = {
        "version": "1.0",
        "kind": "notional_target_v1",
        "parameters": {
            "lever_min_strength": 0.5,
            "notional_fraction_at_full_strength": 1.0,
            "allow_pyramiding": False,
            "close_before_reverse": True,
        },
    }
    artifact["mutation_surface"]["controls"].extend(
        [
            {
                "parameter_key": "strength_scale_k",
                "value_type": "number",
                "current_value": 1.0,
                "allowed_range": {"min": 0.25, "max": 4.0},
                "step": 0.05,
            },
            {
                "parameter_key": "strength_floor_momentum",
                "value_type": "number",
                "current_value": 0.01,
                "allowed_range": {"min": 0.002, "max": 0.05},
                "step": 0.001,
            },
            {
                "parameter_key": "lever_min_strength",
                "value_type": "number",
                "current_value": 0.5,
                "allowed_range": {"min": 0.0, "max": 1.0},
                "step": 0.05,
            },
            {
                "parameter_key": "notional_fraction_at_full_strength",
                "value_type": "number",
                "current_value": 1.0,
                "allowed_range": {"min": 0.1, "max": 1.0},
                "step": 0.05,
            },
        ]
    )
    return artifact


def test_evaluate_strategy_view_legacy_and_expression_policy_pin_decimal_strength():
    from decimal import Decimal

    legacy = _artifact()
    assert "expression_policy" not in legacy

    # Positive momentum on legacy artifact: strength is None, action/direction match
    view_legacy_buy = evaluate_strategy_view(legacy, [100.0, 100.5])
    assert evaluate_strategy_action(legacy, [100.0, 100.5]) == "BUY"
    assert view_legacy_buy == {
        "action": "BUY",
        "direction": "LONG",
        "momentum": 0.005,
        "strength": None,
    }

    # Negative momentum on legacy artifact: SELL -> SHORT, strength is None
    view_legacy_sell = evaluate_strategy_view(legacy, [100.0, 99.4])
    assert evaluate_strategy_action(legacy, [100.0, 99.4]) == "SELL"
    assert view_legacy_sell == {
        "action": "SELL",
        "direction": "SHORT",
        "momentum": -0.006,
        "strength": None,
    }

    # HOLD baseline artifact: HOLD -> FLAT, strength is None
    baseline = copy.deepcopy(legacy)
    baseline["strategy_logic"]["positive_action"] = "HOLD"
    baseline["strategy_logic"]["non_positive_action"] = "HOLD"
    baseline["parameters"]["zero_momentum_action"] = "HOLD"
    view_flat = evaluate_strategy_view(baseline, [100.0, 100.0])
    assert evaluate_strategy_action(baseline, [100.0, 100.0]) == "HOLD"
    assert view_flat == {
        "action": "HOLD",
        "direction": "FLAT",
        "momentum": 0.0,
        "strength": None,
    }

    # Artifact with expression_policy: strength computed with Decimal
    # momentum = (100.6 - 100.0) / 100.0 = 0.006; denom = 1.0 * max(0.0, 0.01) = 0.01 -> strength = 0.6
    expr_art = _artifact_with_expression_policy()
    view_expr_buy = evaluate_strategy_view(expr_art, [100.0, 100.6])
    assert evaluate_strategy_action(expr_art, [100.0, 100.6]) == "BUY"
    expected_mom = (Decimal("100.6") - Decimal("100.0")) / Decimal("100.0")
    expected_strength = min(
        Decimal("1"),
        abs(expected_mom) / (Decimal("1.0") * max(Decimal("0.0"), Decimal("0.01"))),
    )
    assert view_expr_buy == {
        "action": "BUY",
        "direction": "LONG",
        "momentum": float(expected_mom),
        "strength": float(expected_strength),
    }
    assert view_expr_buy["strength"] == 0.6

    # Negative momentum that saturates min(1, ...) at 1.0
    view_expr_sell_cap = evaluate_strategy_view(expr_art, [100.0, 97.5])
    assert evaluate_strategy_action(expr_art, [100.0, 97.5]) == "SELL"
    assert view_expr_sell_cap == {
        "action": "SELL",
        "direction": "SHORT",
        "momentum": -0.025,
        "strength": 1.0,
    }

    # Custom momentum_threshold > strength_floor_momentum and custom strength_scale_k
    mutated = mutate_strategy_artifact(
        expr_art,
        new_artifact_id="artifact-tw-session-momentum-v2-expr",
        new_version="1.0.1",
        parameter_updates={
            "momentum_threshold": 0.02,
            "strength_scale_k": 1.5,
        },
        source_run_ids=["run-strength-test"],
    )
    # momentum = 0.015 <= threshold 0.02 -> SELL / SHORT
    # denom = 1.5 * max(0.02, 0.01) = 0.03 -> strength = 0.015 / 0.03 = 0.5
    view_mutated = evaluate_strategy_view(mutated, [100.0, 101.5])
    assert evaluate_strategy_action(mutated, [100.0, 101.5]) == "SELL"
    assert view_mutated == {
        "action": "SELL",
        "direction": "SHORT",
        "momentum": 0.015,
        "strength": 0.5,
    }


def test_expression_policy_schema_and_forbidden_keys_and_controls():
    valid = _artifact_with_expression_policy()
    validate_strategy_artifact(valid)

    # Unknown key in expression_policy top-level
    bad_top = copy.deepcopy(valid)
    bad_top["expression_policy"]["extra_field"] = "nope"
    with pytest.raises(StrategyArtifactValidationError, match="schema validation failed"):
        validate_strategy_artifact(bad_top)

    # Unknown key in expression_policy.parameters
    bad_param = copy.deepcopy(valid)
    bad_param["expression_policy"]["parameters"]["unknown_policy_knob"] = 1
    with pytest.raises(StrategyArtifactValidationError):
        validate_strategy_artifact(bad_param)

    # Reject any parameter key matching prefer_.*_when_.* in parameters or expression_policy.parameters
    for bad_key in ("prefer_future_when_short", "prefer_shares_when_long", "prefer_ssf_when_volatile"):
        bad_in_params = copy.deepcopy(valid)
        bad_in_params["parameters"][bad_key] = True
        bad_in_params["mutation_surface"]["immutable_parameters"].append(bad_key)
        with pytest.raises(StrategyArtifactValidationError, match="prefer_"):
            validate_strategy_artifact(bad_in_params)

        bad_in_expr = copy.deepcopy(valid)
        bad_in_expr["expression_policy"]["parameters"][bad_key] = True
        with pytest.raises(StrategyArtifactValidationError, match="prefer_"):
            validate_strategy_artifact(bad_in_expr)

    # Missing required control when expression_policy is present
    for required_key in REQUIRED_EXPRESSION_POLICY_CONTROLS:
        missing_ctrl = copy.deepcopy(valid)
        missing_ctrl["mutation_surface"]["controls"] = [
            c
            for c in missing_ctrl["mutation_surface"]["controls"]
            if c["parameter_key"] != required_key
        ]
        if required_key in missing_ctrl["parameters"]:
            missing_ctrl["mutation_surface"]["immutable_parameters"].append(required_key)
        with pytest.raises(StrategyArtifactValidationError, match=required_key):
            validate_strategy_artifact(missing_ctrl)

    # Wrong range or step on expression control
    wrong_step = copy.deepcopy(valid)
    for c in wrong_step["mutation_surface"]["controls"]:
        if c["parameter_key"] == "strength_scale_k":
            c["step"] = 0.1
    with pytest.raises(StrategyArtifactValidationError, match="strength_scale_k"):
        validate_strategy_artifact(wrong_step)

    # Reject forbidden controls in mutation_surface.controls
    for forbidden_ctrl in FORBIDDEN_MUTATION_CONTROL_KEYS:
        bad_ctrl = copy.deepcopy(valid)
        bad_ctrl["parameters"][forbidden_ctrl] = 1.0
        bad_ctrl["mutation_surface"]["controls"].append(
            {
                "parameter_key": forbidden_ctrl,
                "value_type": "number",
                "current_value": 1.0,
                "allowed_range": {"min": 0.0, "max": 5.0},
                "step": 0.5,
            }
        )
        with pytest.raises(StrategyArtifactValidationError, match=forbidden_ctrl):
            validate_strategy_artifact(bad_ctrl)


def test_mutate_strategy_artifact_accepts_all_four_expression_controls():
    parent = _artifact_with_expression_policy()
    child = mutate_strategy_artifact(
        parent,
        new_artifact_id="artifact-tw-session-momentum-v1-expr-child",
        new_version="1.0.1",
        parameter_updates={
            "strength_scale_k": 1.25,
            "strength_floor_momentum": 0.015,
            "lever_min_strength": 0.65,
            "notional_fraction_at_full_strength": 0.85,
        },
        source_run_ids=["run-mutate-expr-001"],
    )
    validate_strategy_artifact(child)
    assert child["parameters"]["strength_scale_k"] == 1.25
    assert child["parameters"]["strength_floor_momentum"] == 0.015
    assert child["expression_policy"]["parameters"]["lever_min_strength"] == 0.65
    assert child["expression_policy"]["parameters"]["notional_fraction_at_full_strength"] == 0.85
    ctrl_map = {
        c["parameter_key"]: c["current_value"]
        for c in child["mutation_surface"]["controls"]
    }
    assert ctrl_map["strength_scale_k"] == 1.25
    assert ctrl_map["strength_floor_momentum"] == 0.015
    assert ctrl_map["lever_min_strength"] == 0.65
    assert ctrl_map["notional_fraction_at_full_strength"] == 0.85

    # Reject mutating allow_pyramiding or close_before_reverse
    for immutable_policy_key in ("allow_pyramiding", "close_before_reverse"):
        with pytest.raises(StrategyArtifactValidationError, match=immutable_policy_key):
            mutate_strategy_artifact(
                parent,
                new_artifact_id="artifact-bad-child",
                new_version="1.0.2",
                parameter_updates={immutable_policy_key: True},
                source_run_ids=["run-mutate-expr-002"],
            )


def test_existing_builtin_and_persona_artifact_checksums_unchanged():
    import json
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]

    # 1. Built-in artifact
    builtin_artifact = _artifact()
    validate_strategy_artifact(builtin_artifact)
    assert (
        strategy_artifact_checksum(builtin_artifact)
        == "sha256:59fd62555bdda65f4215352402c6ef7cedd5acb998957f55f77579073e35fe92"
    )

    # 2. Checked-in persona and H12 artifacts from governance baseline evidence
    evidence_Artifact_files = [
        (
            "docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/input-qinghe-2330.json",
            "sha256:a11791e9d18c0f0dcec765a7df477a7fc65ae0a66f7c892f777ce01a162db880",
        ),
        (
            "docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/input-hengyue-0050.json",
            "sha256:d6bb93411b738b9f50022ecf473be327ffb07aa017eca01a14cb8f503becbe1b",
        ),
        (
            "docs/deployment/evidence/PAPER-PERSONA-BASELINES-20261105/input-chengchuan-2317.json",
            "sha256:2383a5084ab3110d83282accddb168e724e3174e9a8cfece3fbb8f84dada6040",
        ),
        (
            "docs/deployment/evidence/H12-DRAWDOWN-POLICY-CONFIG-20261025/input.json",
            "sha256:fa3a3cdf56c61c8ada5cf29863a89204e13aafa89845b98fe60bff419c6aed75",
        ),
        (
            "docs/deployment/evidence/H12-4923-DRAWDOWN-POLICY-CONFIG-20261101/input.json",
            "sha256:db3d764e77031c82745deb9d5ab92ae676c2eb0175f33cd1b059c4ec682b9d83",
        ),
    ]
    for rel_path, expected_checksum in evidence_Artifact_files:
        payload = json.loads((repo_root / rel_path).read_text(encoding="utf-8"))
        persona_artifact = payload["actual_current_artifact"]
        validate_strategy_artifact(persona_artifact)
        assert strategy_artifact_checksum(persona_artifact) == expected_checksum
        if "registry_entry" in payload:
            assert payload["registry_entry"]["checksum"] == expected_checksum
        assert evaluate_strategy_view(persona_artifact, [100.0, 101.0])["strength"] is None


