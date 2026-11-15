"""Pool admission receives every governed feature dataset the candidate rule references.

Only owner HTTP responses are faked. The feature dataset is materialized by the real
source dataset authority from rows normalized by the real FinMind fundamentals adapter
(published TSMC monthly revenue transcribed into the provider response shape).
"""
from __future__ import annotations

import copy
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services.persona.pool_dataset_authority import governed_dataset_loader
from services.persona.test_pool_dataset_authority import (  # noqa: F401  (fixtures + constants)
    BASE_URL, NOW, PERSONA, READBACK, source_case,
)
from services.source_ingestion.connectors.finmind_taiwan import FinMindTaiwanFundamentalsAdapter

ROOT = Path(__file__).resolve().parents[2]
REVENUE = json.loads(
    (ROOT / "services/source_ingestion/tests/fixtures/tw_fundamentals_official/tsmc_2330_finmind_shape.json").read_text()
)
RUN = "ingest-revenue-001"
CONNECTOR = "tw-finmind-monthly-revenue"
load_policy = importlib.import_module("services.control-plane.persona.pool_policy").load_policy


def _revenue_rows(symbols=("2330.TW", "2317.TW")):
    payload = {"TaiwanStockMonthRevenue": [
        {**row, "stock_id": code.split(".")[0]} for row in REVENUE["TaiwanStockMonthRevenue"] for code in symbols
    ]}
    return FinMindTaiwanFundamentalsAdapter().rows_from_payloads(
        "tw_monthly_revenue", payload, symbols=list(symbols), ingest_time="2024-02-12T00:00:00Z")


def _add_revenue_owner(env, responses, rows):
    root = Path(env["PERSONA_POOL_SOURCE_VOLUME_ROOT"])
    records = FinMindTaiwanFundamentalsAdapter().records_from_rows(rows, trace_id="trace-revenue")
    wrappers = [{"connector_id": CONNECTOR, "dataset": "tw_monthly_revenue", "source_id": r.source_id,
                 "content_ref": r.content_ref, "metadata": dict(r.metadata)} for r in records]
    text = "".join(json.dumps(w) + "\n" for w in wrappers)
    for sub in ("raw", "normalized"):
        (root / sub / f"{RUN}.jsonl").write_text(text)
    (root / "features" / f"{RUN}.jsonl").write_text("".join(json.dumps({
        "source_id": w["source_id"], "connector_id": CONNECTOR, "source_dataset": "tw_monthly_revenue",
        "feature_dataset": "tw_monthly_revenue", "feature_as_of_time": w["metadata"]["date"]}) + "\n" for w in wrappers))
    readback = responses[READBACK]
    entry = copy.deepcopy(readback["connectors"][0])
    text_entry = json.dumps(entry).replace("tw-twse-tpex-official-market", CONNECTOR)\
        .replace("tw_price_daily", "tw_monthly_revenue").replace("ingest-authority-001", RUN)\
        .replace("/fixture/source", str(root))
    entry = json.loads(text_entry)
    entry["latest_source_record"]["source_id"] = wrappers[-1]["source_id"]
    source_authority = importlib.import_module("services.training-session.source_dataset_authority")
    entry["desired_state_sha256"] = source_authority._desired_state_digest(entry["desired_state"])
    refs = entry["source_health"]["metadata"]["storage_refs"]
    for key in ("raw_refs", "normalized_refs", "feature_refs"):
        for ref in refs[key]:
            ref["row_count"] = len(wrappers)
    refs["summary"]["normalized_row_count"] = len(wrappers)
    entry["source_health"]["row_count_last_run"] = len(wrappers)
    refs["feature_refs"][0]["dataset"] = "tw_monthly_revenue"
    readback["connectors"].append(entry)
    readback["connector_count"] = 2
    readback["requirement_snapshot"]["bindings"]["requirement-revenue"] = CONNECTOR
    readback["requirement_snapshot"]["binding_count"] = 2
    job = copy.deepcopy(responses[BASE_URL + "/api/source-ingest/jobs/ingest-authority-001"])
    job["run"].update(ingest_run_id=RUN, connector_id=CONNECTOR, raw_count=len(wrappers), normalized_count=len(wrappers))
    responses[BASE_URL + f"/api/source-ingest/jobs/{RUN}"] = job
    bundles = responses[BASE_URL + "/api/source-ingest/evidence/bundles"]
    bundle = json.loads(json.dumps(bundles["bundles"][0]).replace("tw-twse-tpex-official-market", CONNECTOR)
                        .replace("tw_price_daily", "tw_monthly_revenue").replace("ingest-authority-001", RUN))
    bundle["source_ids"] = [w["source_id"] for w in wrappers]
    bundles["bundles"].append(bundle)
    catalog = responses[BASE_URL + "/api/source-ingest/data-sources/financial-catalog"]
    catalog["entries"].append({**copy.deepcopy(catalog["entries"][0]), "data_source_id": "tw_monthly_revenue",
        "connector_id": CONNECTOR, "datasets": [{"dataset_id": "tw_monthly_revenue", "dataset_class": "financial_fundamental",
                                                    "storage_targets": ["normalized/tw_monthly_revenue"]}]})


def _candidate_with_revenue_rule():
    from services.registry.test_factor_rule import _sample_factor_rule_artifact

    artifact = _sample_factor_rule_artifact()
    artifact["parameters"]["symbols"] = ["2330.TW", "2317.TW"]
    return {"artifact_ref": "reg-revenue-yoy-rule", "checksum": "sha256:" + "0" * 64, "strategy_artifact": artifact}


def test_revenue_feature_reaches_admission_backtest_point_in_time(source_case):
    env, responses, _ = source_case
    rows = _revenue_rows()
    _add_revenue_owner(env, responses, rows)
    env["PERSONA_POOL_FEATURE_CONNECTOR_IDS"] = json.dumps({"tw_monthly_revenue": CONNECTOR})
    candidate = _candidate_with_revenue_rule()
    dataset = governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, candidate)
    series = dataset["features_by_instrument"]["2330.TWSE"]["tw_monthly_revenue"]["yoy"]
    assert [o["event_time"] for o in series] == ["2023-01-31", "2023-12-31", "2024-01-31"][-len(series):]
    jan = series[-1]
    assert jan["value"] == pytest.approx((215785 / 200050 - 1) * 100)
    assert jan["available_time"].startswith("2024-02-10")  # statutory tenth of the following month
    # The very same dataset prepares through the unchanged admission backtest input adapter.
    from services.research.vectorbt.adapter.vectorbt_adapter import GovernedVectorbtInputAdapter
    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    assert "tw_monthly_revenue" in prepared.features_by_instrument["2330.TWSE"]


def test_admission_replay_evaluates_revenue_rule_on_loaded_features(source_case):
    from services.persona.pool_autonomy_cycle import candidate_virtual_book

    env, responses, _ = source_case
    _add_revenue_owner(env, responses, _revenue_rows())
    env["PERSONA_POOL_FEATURE_CONNECTOR_IDS"] = json.dumps({"tw_monthly_revenue": CONNECTOR})
    candidate = _candidate_with_revenue_rule()
    dataset = governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, candidate)
    # The same replay admission uses. Only two published revenue months carry a yoy here, so the rule's
    # four-observation z-score window cannot be filled: the interpreter must refuse for insufficient
    # point-in-time history (it read the loaded series), never for absent or unlabelled feature input.
    with pytest.raises(ValueError) as refused:
        candidate_virtual_book(dataset=dataset, artifact=candidate["strategy_artifact"],
                               checksum=candidate["checksum"], policy=load_policy())
    assert "sufficient point-in-time history" in str(refused.value)
    assert "requires catalogued timestamped observations" not in str(refused.value)
    # With the window shortened to the published history the replay completes on the loaded observations.
    shorter = copy.deepcopy(candidate["strategy_artifact"])
    shorter["strategy_logic"]["signal"] = {"op": "sub", "args": [{"ref": "rev_yoy"}, {"const_param": "exit_z"}]}
    book = candidate_virtual_book(dataset=dataset, artifact=shorter, checksum=candidate["checksum"], policy=load_policy())
    assert book["book_kind"] == "governed_replay" and book["returns"]


def test_rule_referencing_dataset_without_coverage_fails_closed(source_case):
    env, responses, _ = source_case
    # Revenue exists only for an instrument outside the admission universe.
    _add_revenue_owner(env, responses, _revenue_rows(symbols=("2454.TW",)))
    env["PERSONA_POOL_FEATURE_CONNECTOR_IDS"] = json.dumps({"tw_monthly_revenue": CONNECTOR})
    candidate = _candidate_with_revenue_rule()
    authority = importlib.import_module("services.training-session.evaluation_authority")
    with pytest.raises(authority.AuthorityValidationError, match="feature dataset coverage missing"):
        governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, candidate)


def test_unconfigured_feature_connector_fails_closed(source_case):
    env, _, _ = source_case
    candidate = _candidate_with_revenue_rule()
    authority = importlib.import_module("services.training-session.evaluation_authority")
    with pytest.raises(authority.AuthorityValidationError, match="no governed connector configured"):
        governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, candidate)


def test_price_only_rule_is_unchanged(source_case):
    env, _, _ = source_case
    dataset = governed_dataset_loader(env, clock=lambda: NOW)(PERSONA, {"artifact_ref": "candidate"})
    assert "features_by_instrument" not in dataset
