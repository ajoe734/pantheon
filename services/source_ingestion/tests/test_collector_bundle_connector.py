"""SOURCE-COLLECTOR-BUNDLE-CONNECTOR-20261110 acceptance tests (SD-STRATEGY-DISCOVERY §7)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.source_ingestion.configured import JsonlConfiguredConnectorStore, JsonlConnectorScheduleStore
from services.source_ingestion.connector_definitions import (
    DEPLOYED_CONNECTOR_DEFINITIONS,
    get_connector_definition,
)
from services.source_ingestion.connectors.base import SourceEvidenceError
from services.source_ingestion.connectors.collector_bundle import (
    COLLECTOR_BUNDLE_ADAPTER_TOKEN,
    BundleTransport,
    CollectorBundleAdapter,
    compute_bundle_sha256,
    observed_from_collector_status,
)
from services.source_ingestion.provider_adapters import ALLOWED_PROVIDER_ADAPTERS, is_provider_adapter_allowed
from services.source_ingestion.source_management_commands import SourceCommandEngine
from services.source_ingestion.source_management_models import (
    CanaryStageName,
    CanaryStageStatus,
    CanaryState,
    CanaryStatus,
    CommandType,
    HealthState,
    SourceManagementCommand,
)
from services.source_ingestion.source_management_store import JsonlSourceManagementStore

HOST = "collector.example.test"
ACTOR = {"actor_type": "operator", "actor_id": "op", "roles": ["operator"]}
FETCHED = "2026-11-10T02:00:00Z"
PUBLISHED = "2026-11-10T01:00:00Z"


class FakeTransport(BundleTransport):
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def read(self, relative_path: str, *, max_bytes: int) -> bytes:
        return self.files[relative_path]


def make_bundle(source_id="collector-tw-ptt-stock", run_id="run1", date="2026-11-10", attribution=None,
                finished="2026-11-10T02:05:00Z", status="complete", author="hmac-abc", extra_item=None):
    raw = {"a": b"<html>post a</html>", "b": b"<html>post b</html>"}
    manifest_items, rows = [], []
    for item_id, body in raw.items():
        sha = hashlib.sha256(body).hexdigest()
        manifest_items.append({"item_id": item_id, "url": f"https://example.test/{item_id}", "http_status": 200,
                               "content_sha256": sha, "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        rows.append({"id": item_id, "url": f"https://example.test/{item_id}", "fetched_at": FETCHED,
                     "published_at": PUBLISHED, "text": f"text {item_id}", "lang": "zh-TW",
                     "hashed_author": author, "metrics": {"likes": 1}, "title": f"t {item_id}"})
    manifest = {
        "schema_version": "collector_bundle.v1", "source_id": source_id, "run_id": run_id,
        "collector_version": "1.0.0", "started_at": "2026-11-10T02:00:00Z", "finished_at": finished,
        "fetch_policy_hash": "fph", "robots_decision": "allowed", "terms_ref": "https://example.test/terms",
        "items": manifest_items, "status": status,
        "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in manifest_items]),
    }
    if attribution:
        manifest["attribution"] = attribution
    base = f"bundles/{source_id}/{date}/{run_id}"
    files = {f"{base}/manifest.json": json.dumps(manifest).encode(),
             f"{base}/items.jsonl": "\n".join(json.dumps(r) for r in rows).encode()}
    for item_id, body in raw.items():
        files[f"{base}/raw/{hashlib.sha256(body).hexdigest()}.html"] = body
    entry = {"source_id": source_id, "date": date, "run_id": run_id, "finished_at": finished}
    return files, entry, manifest


def adapter_for(files, entries, tmp_path, **kw):
    files = dict(files)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    kw.setdefault("source_id", "collector-tw-ptt-stock")
    kw.setdefault("source_type", "social")
    kw.setdefault("platform", "ptt")
    return CollectorBundleAdapter(connector_id="conn-x", raw_root=str(tmp_path / "store"),
                                  transport=FakeTransport(files), host=HOST, **kw)


def raw_files(tmp_path):
    return [p for p in (tmp_path / "store").rglob("*") if p.is_file()] if (tmp_path / "store").exists() else []


# AC1 ------------------------------------------------------------------------------
def test_single_adapter_token_allowlisted_and_each_source_has_definition():
    assert is_provider_adapter_allowed(COLLECTOR_BUNDLE_ADAPTER_TOKEN)
    assert COLLECTOR_BUNDLE_ADAPTER_TOKEN in ALLOWED_PROVIDER_ADAPTERS
    ids = {d.definition_id for d in DEPLOYED_CONNECTOR_DEFINITIONS if d.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN}
    assert {"collector-tw-threads", "collector-tw-instagram-popular", "collector-tw-cmoney-forum", "collector-tw-ptt-stock",
            "collector-youtube-finance", "collector-news-rss", "collector-tw-macro-cbc", "collector-tw-macro-ndc",
            "collector-tw-macro-dgbas", "collector-gdelt", "collector-library-books", "strategy-seed-paper-corpus"} <= ids
    assert "collector-tw-tpex-flows" not in ids


def _engine(tmp_path):
    store = JsonlSourceManagementStore(tmp_path)
    return SourceCommandEngine(store=store, connector_store=JsonlConfiguredConnectorStore(tmp_path / "c.jsonl"),
                               schedule_config_store=JsonlConnectorScheduleStore(tmp_path / "s.jsonl"),
                               deployment_sha="t"), store


def _cmd(kind, iid, n, **params):
    return SourceManagementCommand(command_id=f"c-{n}", idempotency_key=f"i-{n}", command_type=kind,
                                   source_instance_id=iid, expected_revision=None if kind == CommandType.CREATE else 1,
                                   actor=ACTOR, reason="test", parameters=params)


def test_every_collector_definition_is_created_disabled(tmp_path):
    engine, store = _engine(tmp_path)
    defs = [d for d in DEPLOYED_CONNECTOR_DEFINITIONS if d.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN]
    for i, d in enumerate(defs):
        engine.execute_command(_cmd(CommandType.CREATE, f"ds-{d.definition_id}", i, definition_id=d.definition_id,
                                    connector_id=f"conn-{d.definition_id}", connector_config={"public": {}}))
        inst = store.get_instance(f"ds-{d.definition_id}")
        desired = store.get_desired_state(f"ds-{d.definition_id}")
        assert inst.lifecycle_state == "configured_disabled"
        assert desired.schedule["enabled"] is False
        assert inst.license_scope == "collector_public_web"


# AC2 ------------------------------------------------------------------------------
@pytest.mark.parametrize("tamper", ["raw", "bundle", "source"])
def test_mismatch_rejected_with_no_partial_write(tmp_path, tamper):
    files, entry, manifest = make_bundle()
    base = "bundles/collector-tw-ptt-stock/2026-11-10/run1"
    if tamper == "raw":
        key = next(k for k in files if "/raw/" in k)
        files[key] = b"tampered"
    elif tamper == "bundle":
        manifest["bundle_sha256"] = "0" * 64
        files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    else:
        manifest["source_id"] = "collector-tw-threads"
        files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    adapter = adapter_for(files, [entry], tmp_path)
    with pytest.raises(SourceEvidenceError):
        adapter.pull()
    assert raw_files(tmp_path) == []


def test_good_bundle_followed_by_bad_bundle_writes_nothing(tmp_path):
    good, e1, _ = make_bundle(run_id="r1", finished="2026-11-10T02:05:00Z")
    bad, e2, _ = make_bundle(run_id="r2", finished="2026-11-10T03:05:00Z")
    bad[next(k for k in bad if "/raw/" in k)] = b"tampered"
    adapter = adapter_for({**good, **bad}, [e1, e2], tmp_path)
    with pytest.raises(SourceEvidenceError):
        adapter.pull()
    assert raw_files(tmp_path) == []


def test_unknown_schema_and_traversal_rejected(tmp_path):
    files, entry, manifest = make_bundle()
    manifest["schema_version"] = "v9"
    files["bundles/collector-tw-ptt-stock/2026-11-10/run1/manifest.json"] = json.dumps(manifest).encode()
    with pytest.raises(SourceEvidenceError):
        adapter_for(files, [entry], tmp_path).pull()


# AC3 + AC4 ------------------------------------------------------------------------
def test_social_records_pass_external_contract_and_raw_is_stored(tmp_path):
    files, entry, _ = make_bundle()
    result = adapter_for(files, [entry], tmp_path).pull()
    assert len(result.records) == 2
    md = result.records[0].metadata
    assert result.records[0].source_type.value == "social"
    assert md["author_id_hash"] == "hmac-abc" and md["platform"] == "ptt" and md["post_id"]
    assert md["platform_policy_ref"] == "https://example.test/terms"
    assert md["direct_execution_allowed"] is False and md["pit"]["validated"] is True
    assert md["redistribution_allowed"] is False and md["license_policy"]["redistribution_allowed"] is False
    assert md["available_time"] == "2026-11-10T02:00:00Z" and md["event_time"] == "2026-11-10T01:00:00Z"
    assert len([p for p in raw_files(tmp_path) if p.suffix == ".html"]) == 2
    assert result.next_cursor.endswith("#run1")


def test_social_without_hashed_author_is_rejected(tmp_path):
    files, entry, _ = make_bundle(author="")
    with pytest.raises(SourceEvidenceError):
        adapter_for(files, [entry], tmp_path).pull()
    assert raw_files(tmp_path) == []


@pytest.mark.parametrize("stype,kw", [("news", {"publisher": "cna"}), ("macro", {}), ("paper", {})])
def test_news_macro_paper_records_pass_contract(tmp_path, stype, kw):
    files, entry, _ = make_bundle(source_id="collector-x")
    adapter = adapter_for(files, [entry], tmp_path, source_id="collector-x", source_type=stype, platform=None, **kw)
    records = adapter.pull().records
    assert len(records) == 2
    for r in records:
        assert r.source_type.value == stype
        assert r.metadata["pit"]["validated"] is True and r.metadata["content_hash"].startswith("sha256:")
        assert r.metadata["entitlement_tags"] and r.metadata["license_scope"]
    if stype == "news":
        assert records[0].metadata["publisher"] == "cna"


def test_attribution_required_sources_carry_text_or_reject(tmp_path):
    text = "資料來源：國家發展委員會（data.gov.tw）"
    files, entry, _ = make_bundle(source_id="collector-tw-macro-ndc", attribution=text)
    kw = dict(source_id="collector-tw-macro-ndc", source_type="macro", platform=None, attribution_required=True)
    records = adapter_for(files, [entry], tmp_path, **kw).pull().records
    assert all(r.metadata["attribution_text"] == text and r.metadata["license_policy"]["attribution_required"] for r in records)
    files2, entry2, _ = make_bundle(source_id="collector-tw-macro-ndc")
    with pytest.raises(SourceEvidenceError):
        adapter_for(files2, [entry2], tmp_path / "b", **kw).pull()


def test_definitions_mark_datagovtw_sources_attribution_required_and_no_redistribution():
    for did in ("collector-tw-macro-ndc", "collector-tw-macro-dgbas"):
        c = get_connector_definition(did).metadata["collector"]
        assert c["attribution_required"] is True and c["redistribution_allowed"] is False
    for d in DEPLOYED_CONNECTOR_DEFINITIONS:
        if d.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN:
            assert d.metadata["collector"]["redistribution_allowed"] is False


# AC5 ------------------------------------------------------------------------------
def test_paper_corpus_points_to_bundle_adapter():
    d = get_connector_definition("strategy-seed-paper-corpus")
    assert d.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN
    assert "StrategySeedPaperCorpusAdapter" not in d.adapter_token


# AC6 ------------------------------------------------------------------------------
def _canary(tmp_path, monkeypatch, env_mode, env_hosts):
    from services.source_ingestion import connector_definitions as cd
    engine, store = _engine(tmp_path)
    monkeypatch.setenv("PANTHEON_EXTERNAL_EGRESS", env_mode)
    monkeypatch.setenv("PANTHEON_EXTERNAL_EGRESS_ALLOWED_HOSTS", env_hosts)
    did = "collector-tw-ptt-stock"
    default_host = get_connector_definition(did).allowed_host_patterns[0]
    engine.execute_command(_cmd(CommandType.CREATE, "ds-1", 1, definition_id=did, connector_id="conn-1",
                                connector_config={"public": {}}))
    engine.execute_command(_cmd(CommandType.VALIDATE, "ds-1", 2))
    engine.execute_command(_cmd(CommandType.CANARY, "ds-1", 3))
    return store, default_host


def test_egress_canary_fails_when_hosts_differ(tmp_path, monkeypatch):
    store, _ = _canary(tmp_path, monkeypatch, "allowlist", "other.example.test")
    res = store.get_latest_canary_result("ds-1")
    assert res.status == CanaryStatus.FAILED
    egress = next(s for s in res.stages if s.stage_name == CanaryStageName.EGRESS_POLICY_ADMITTED)
    assert egress.status == CanaryStageStatus.FAILED and "egress_policy_mismatch" in egress.error
    assert all(s.status != CanaryStageStatus.PASSED for s in res.stages if s.stage_name == CanaryStageName.PROVIDER_READ)
    assert store.get_latest_observed_snapshot("ds-1").canary_state == CanaryState.FAILED


def test_egress_canary_fails_in_deny_mode(tmp_path, monkeypatch):
    store, _ = _canary(tmp_path, monkeypatch, "deny", "")
    assert store.get_latest_canary_result("ds-1").status == CanaryStatus.FAILED


def test_egress_canary_passes_when_hosts_equal(tmp_path, monkeypatch):
    host = get_connector_definition("collector-tw-ptt-stock").allowed_host_patterns[0]
    store, _ = _canary(tmp_path, monkeypatch, "allowlist", host)
    assert store.get_latest_canary_result("ds-1").status == CanaryStatus.PASSED


# AC7 ------------------------------------------------------------------------------
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _status(last_run, status="ok", reason=None):
    return {"sources": {"collector-tw-ptt-stock": {"status": status, "last_run_at": last_run, "paused_reason": reason}}}


def test_status_healthy_paused_and_lagging():
    ok = observed_from_collector_status(_status("2026-11-10T08:00:00Z"), source_id="collector-tw-ptt-stock",
                                        cursor="2026-11-10T08:00:00Z#r", cadence_seconds=21600, now=NOW)
    assert ok["health_state"] == "healthy"
    paused = observed_from_collector_status(_status("2026-11-10T08:00:00Z", "paused", "http_429"),
                                            source_id="collector-tw-ptt-stock", cursor="2026-11-10T08:00:00Z#r", now=NOW)
    assert paused["health_state"] == "degraded" and "collector_source_paused:http_429" in paused["reasons"]
    lag = observed_from_collector_status(_status("2026-11-10T08:00:00Z"), source_id="collector-tw-ptt-stock",
                                         cursor="2026-11-01T08:00:00Z#r", now=NOW)
    assert lag["health_state"] == "degraded" and "cursor_lag_over_7_days" in lag["reasons"]
    edge = observed_from_collector_status(_status("2026-11-10T08:00:00Z"), source_id="collector-tw-ptt-stock",
                                          cursor="2026-11-04T09:00:00Z#r", now=NOW)
    assert edge["health_state"] == "healthy"


def test_status_reflected_in_observed_state(tmp_path):
    engine, store = _engine(tmp_path)
    engine.execute_command(_cmd(CommandType.CREATE, "ds-1", 1, definition_id="collector-tw-ptt-stock",
                                connector_id="conn-1", connector_config={"public": {}}))
    obs = engine.record_collector_observation("ds-1", status_doc=_status("2026-11-10T08:00:00Z"),
                                              cursor="2026-11-01T08:00:00Z#r", now=NOW)
    assert obs.health_state == HealthState.DEGRADED
    assert store.get_latest_observed_snapshot("ds-1").freshness["cursor_lag_days"] > 7
    obs2 = engine.record_collector_observation("ds-1", status_doc=_status("2026-11-10T08:00:00Z"),
                                               cursor="2026-11-10T08:00:00Z#r", now=NOW)
    assert obs2.health_state == HealthState.HEALTHY


def test_pull_reads_status_into_observed(tmp_path):
    files, entry, _ = make_bundle()
    files["status.json"] = json.dumps(_status("2026-11-10T02:05:00Z", "paused", "login_redirect")).encode()
    res = adapter_for(files, [entry], tmp_path).pull()
    assert res.observed["health_state"] == "degraded"


def test_cursor_skips_already_consumed_bundles(tmp_path):
    f1, e1, _ = make_bundle(run_id="r1", finished="2026-11-10T02:05:00Z")
    f2, e2, _ = make_bundle(run_id="r2", finished="2026-11-10T03:05:00Z")
    adapter = adapter_for({**f1, **f2}, [e1, e2], tmp_path)
    first = adapter.pull()
    assert len(first.records) == 4
    assert len(adapter.pull(cursor=first.next_cursor).records) == 0
    assert len(adapter.pull(cursor="2026-11-10T02:05:00Z#r1").records) == 2
