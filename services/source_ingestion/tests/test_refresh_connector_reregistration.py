"""RC2: a repeated bounded FinMind fundamentals refresh must re-register without a contract conflict.

Real runtime, real persona source reconciler, real PREPARE_DATASET_PY payload; the reconciler is the
only writer of the persona_source_reconciliation marker, and the contract check stays strict.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from services.source_ingestion.api_models import ConfigureConnectorRequest
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.persona_source_reconciler import RECONCILIATION_METADATA_KEY
from services.source_ingestion.runtime import SourceIngestionRuntime

ROOT = Path(__file__).resolve().parents[3]
FUNDAMENTALS = {
    "tw-finmind-monthly-revenue": "tw_monthly_revenue",
    "tw-finmind-quarterly-statements": "tw_financial_statement",
    "tw-finmind-valuation": "tw_valuation_bwibbu",
}


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINMIND_API_TOKEN", "local-test-token")


def _prepare(connector_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    import sys

    if str(ROOT / "scripts") not in sys.path:
        sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("_deploy_prepare_helper", ROOT / "scripts/test_deploy_nonprod_vm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    desired, posted = module._prepare_finmind_dataset(3, connector_id=connector_id, dataset=FUNDAMENTALS[connector_id])
    registration = next(item for item in posted if item["path"].endswith("/connectors"))
    return desired, {"connector": registration["connector"], "fetch": registration["fetch"]}


@pytest.fixture(scope="module")
def prepared() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    import os

    os.environ["FINMIND_API_TOKEN"] = "local-test-token"
    try:
        return {cid: _prepare(cid) for cid in FUNDAMENTALS}
    finally:
        os.environ.pop("FINMIND_API_TOKEN", None)


def _register(rt: SourceIngestionRuntime, payload: dict[str, Any]) -> dict[str, Any]:
    return rt._configure_connector(ConfigureConnectorRequest.model_validate(payload))


def _reconcile(rt: SourceIngestionRuntime, desired: dict[str, Any], connector_id: str) -> None:
    for persona in desired["personas"]:
        result = rt._source_provisioning_reconciler().reconcile_persona(persona)
        for action in result.actions:
            if action.connector_id == connector_id:
                assert action.status != "conflict", action
                assert not getattr(action, "failed_policy_gates", None)


def _stored(rt: SourceIngestionRuntime, connector_id: str) -> dict[str, Any]:
    return rt.connector_store.get_config(connector_id).connector.to_dict()


def test_prepare_payload_no_longer_carries_reconciler_marker_or_approval(prepared) -> None:
    for connector_id, (_, payload) in prepared.items():
        metadata = payload["connector"]["metadata"]
        assert RECONCILIATION_METADATA_KEY not in metadata, connector_id
        assert "approval_status" not in metadata and "approval_authority" not in metadata, connector_id


@pytest.mark.parametrize("connector_id", sorted(FUNDAMENTALS))
@pytest.mark.parametrize("order", ["register_reconcile_register", "reconcile_register_register"])
def test_two_consecutive_refresh_runs_register_in_both_orders(tmp_path, prepared, connector_id, order) -> None:
    desired, payload = prepared[connector_id]
    rt = SourceIngestionRuntime(data_dir=tmp_path)
    if order == "register_reconcile_register":
        _register(rt, payload)
        _reconcile(rt, desired, connector_id)
    else:
        _reconcile(rt, desired, connector_id)
    _register(rt, payload)
    after_first = _stored(rt, connector_id)
    _register(rt, payload)  # second consecutive run
    _reconcile(rt, desired, connector_id)  # and a reconciler pass afterwards
    _register(rt, payload)
    final = _stored(rt, connector_id)
    assert final == after_first
    marker = final["metadata"][RECONCILIATION_METADATA_KEY]
    assert marker["managed_by"] == "persona_source_provisioning_reconciler"
    assert marker["desired_state_sha256"]  # written by the reconciler, not the deploy script
    assert final["metadata"] == rt.manager.get_connector(connector_id).to_dict()["metadata"]


@pytest.mark.parametrize("connector_id", sorted(FUNDAMENTALS))
def test_policy_gates_keep_their_result_across_refresh_and_reconcile(tmp_path, prepared, connector_id) -> None:
    desired, payload = prepared[connector_id]
    rt = SourceIngestionRuntime(data_dir=tmp_path)
    gates: list[dict[str, Any]] = []
    for step in ("register", "reconcile", "register", "reconcile"):
        _register(rt, payload) if step == "register" else _reconcile(rt, desired, connector_id)
        marker = _stored(rt, connector_id)["metadata"].get(RECONCILIATION_METADATA_KEY) or {}
        results = (marker.get("desired_state") or {}).get("policy_gate_results")
        if results:
            gates.append(results)
    assert gates and all(g == gates[0] for g in gates)
    assert gates[0]["require_connector_approved"]["passed"] is True
    assert "approval_status" not in _stored(rt, connector_id)["metadata"]  # nothing forged


def _set(connector: dict[str, Any], path: str, value: Any) -> None:
    node = connector
    *parents, leaf = path.split(".")
    for part in parents:
        node = node[part]
    node[leaf] = value


# provider, auth, secret reference, licence, rate limit, source metadata and the dataset/endpoint
# facts carried in connector metadata. Each is a real contract difference the check must refuse.
CONTRACT_DIFFERENCES = {
    "provider": "Other Provider",
    "secret_ref_id": "env://OTHER_TOKEN",
    "rate_limit_policy.policy_ref": "source-ingest://policy/other",
    "rate_limit_policy.requests_per_minute": 7,
    "rate_limit_policy.concurrency": 9,
    "auth_policy.auth_scope": ["other:scope"],
    "license_policy.license_scope": "internal",
    "license_policy.redistribution_allowed": True,
    "license_policy.allowed_use": ["research"],
    "source_metadata.owner": "Someone Else",
    "metadata.refresh_profile": {"dataset": "other"},
    "metadata.entitlement_tier": "free",
    "metadata.dataset_catalog": [],
}


@pytest.mark.parametrize("with_marker", [False, True])
@pytest.mark.parametrize("path", sorted(CONTRACT_DIFFERENCES))
def test_contract_check_still_refuses_real_differences(tmp_path, prepared, path, with_marker) -> None:
    from services.source_ingestion.connectors.base import SourceConnector

    desired, payload = prepared["tw-finmind-valuation"]
    rt = SourceIngestionRuntime(data_dir=tmp_path)
    _register(rt, payload)
    _reconcile(rt, desired, "tw-finmind-valuation")
    stored = _stored(rt, "tw-finmind-valuation")
    mutated = json.loads(json.dumps(_incoming(payload)))
    _set(mutated, path, CONTRACT_DIFFERENCES[path])
    if with_marker:
        mutated["metadata"][RECONCILIATION_METADATA_KEY] = stored["metadata"][RECONCILIATION_METADATA_KEY]
    with pytest.raises(SourceEvidenceError):
        rt._register_or_validate_connector(SourceConnector.from_dict(mutated))
    assert _stored(rt, "tw-finmind-valuation") == stored


def _incoming(payload: dict[str, Any]) -> dict[str, Any]:
    return ConfigureConnectorRequest.model_validate(payload).connector.to_domain().to_dict()


@pytest.mark.parametrize("key", ["definition_id", "refresh_profile", "dataset_catalog"])
def test_contract_check_still_refuses_metadata_differences_through_the_api_payload(tmp_path, prepared, key) -> None:
    desired, payload = prepared["tw-finmind-valuation"]
    rt = SourceIngestionRuntime(data_dir=tmp_path)
    _register(rt, payload)
    _reconcile(rt, desired, "tw-finmind-valuation")
    mutated = json.loads(json.dumps(payload["connector"]))
    mutated["metadata"][key] = {"changed": True}
    with pytest.raises(SourceEvidenceError, match="different contract"):
        _register(rt, {"connector": mutated, "fetch": payload["fetch"]})


def test_bare_registration_with_extra_metadata_is_still_refused(tmp_path, prepared) -> None:
    desired, payload = prepared["tw-finmind-valuation"]
    rt = SourceIngestionRuntime(data_dir=tmp_path)
    _register(rt, payload)
    _reconcile(rt, desired, "tw-finmind-valuation")
    forged = json.loads(json.dumps(payload["connector"]))
    forged["metadata"]["approval_status"] = "approved"
    with pytest.raises(SourceEvidenceError, match="different contract"):
        _register(rt, {"connector": forged, "fetch": payload["fetch"]})
