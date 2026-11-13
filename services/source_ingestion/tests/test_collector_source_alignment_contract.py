"""Host policy -> deployed definitions -> default desired admission, without a second source list."""
import json
from pathlib import Path

from services.source_ingestion.connector_definitions import COLLECTOR_SOURCE_SPECS, deployed_connector_definitions
from services.source_ingestion.connectors.collector_bundle import COLLECTOR_BUNDLE_ADAPTER_TOKEN

ROOT = Path(__file__).resolve().parents[3]


def test_every_enabled_host_source_has_its_own_admitted_definition_or_explicit_hold():
    policy = json.loads((ROOT / "deploy/collector/fetch-policy.json").read_text())["sources"]
    desired = json.loads((ROOT / "services/source_ingestion/default_desired_state.json").read_text())
    definitions = deployed_connector_definitions()
    by_source = {d.metadata["collector"]["source_id"]: d for d in definitions.values()
                 if d.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN}
    admitted = {cid for persona in desired["personas"] for req in persona["required_data_sources"]
                for cid in req["connector_candidates"]}
    held = {item["connector_id"]: item["reason"] for item in desired["held_collector_sources"]}
    exclusions = desired.get("collector_ingestion_exclusions", {})
    assert {s["id"] for s in COLLECTOR_SOURCE_SPECS} <= set(policy)
    for sid, host_policy in policy.items():
        if not host_policy["enabled"]:
            continue
        if sid not in by_source:
            assert exclusions.get(sid), f"{sid} has no hosted reader or explicit ingestion exclusion"
            continue
        definition = by_source[sid]
        assert definition.definition_id in admitted or held.get(definition.definition_id), f"{sid} lacks admission/reason"
    assert by_source["collector-macro-dbnomics"].datasets[0] == "collector_macro_series"
    assert by_source["collector-macro-dbnomics"].definition_id in admitted
    assert by_source["collector-papers"].definition_id == "strategy-seed-paper-corpus"
    for sid in ("collector-paper-arxiv", "collector-paper-openalex", "collector-paper-nber-rss"):
        assert by_source[sid].metadata["collector"]["source_id"] == sid
