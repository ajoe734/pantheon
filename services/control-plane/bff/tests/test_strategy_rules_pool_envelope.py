"""Strategy detail rules: pool envelopes and members, on the recorded hosted shape."""
from __future__ import annotations

import json
from pathlib import Path

from services.control_plane.bff.strategies.service import project_strategy_rules

FIXTURE = json.loads((Path(__file__).resolve().parents[4] / "acceptance" / "fixtures"
                      / "hosted_strategy_rules_20261119.json").read_text())
SID = FIXTURE["strategy_id"]
BASELINE = "artifact-persona-paper-baseline-06d4775a9b6a8f63b311"
MEMBER = "artifact-persona-paper-06d4775a9b6a8f63b311"
ENVELOPE = "artifact-persona-pool-06d4775a9b6a8f63b311-v2"


def rules(entries=None):
    return {r["registryId"]: r for r in project_strategy_rules(
        [{"entry": e} for e in (entries or FIXTURE["entries"])], strategy_id=SID)}


def test_recorded_hosted_roles():
    by_id = rules()
    assert {k: v["role"] for k, v in by_id.items()} == {
        BASELINE: "rollback_baseline", MEMBER: "pool_member", ENVELOPE: "pool_envelope"}


def test_envelope_carries_combination_rule_limits_and_members_unchanged():
    envelope = rules()[ENVELOPE]
    source = next(e for e in FIXTURE["entries"] if e["registry_id"] == ENVELOPE)
    logic = source["metadata"]["strategy_artifact"]["strategy_logic"]
    assert envelope["combinationRule"] == logic["combination_rule"] == "clipped_weighted_sum_v1"
    assert envelope["riskLimits"] == logic["risk_limits"]
    [member] = envelope["members"]
    artifact = logic["members"][0]["artifact"]
    assert member == {
        "registryId": MEMBER, "riskWeight": logic["members"][0]["risk_weight"],
        "strategyLogic": artifact["strategy_logic"], "parameters": artifact["parameters"],
        "adjustableControls": artifact["mutation_surface"]["controls"]}
    assert member["strategyLogic"]["kind"] == "close_to_close_momentum"


def test_baseline_of_non_envelope_bundle_keeps_rollback_role():
    assert rules()[BASELINE]["role"] == "rollback_baseline"
    assert "members" not in rules()[MEMBER]


def test_bundle_named_only_by_envelope_lineage_is_pool_member():
    entries = [e for e in FIXTURE["entries"] if e["registry_id"] != BASELINE]
    entries = json.loads(json.dumps(entries))
    for e in entries:
        if e["registry_id"] == MEMBER:
            e["lineage"] = {"parent_registry_ids": None}
    envelope = next(e for e in entries if e["registry_id"] == ENVELOPE)
    envelope["metadata"]["strategy_artifact"]["strategy_logic"]["members"] = []
    assert rules(entries)[MEMBER]["role"] == "pool_member"  # via lineage.parent_registry_ids alone


def test_plain_bundles_keep_trading_role():
    entries = [e for e in FIXTURE["entries"] if e["registry_id"] == MEMBER]
    entries = json.loads(json.dumps(entries))
    entries[0]["lineage"] = {"parent_registry_ids": None}
    assert rules(entries)[MEMBER]["role"] == "trading"
