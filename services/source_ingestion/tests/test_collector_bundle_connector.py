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
            "collector-tw-macro-dgbas", "collector-gdelt", "collector-library-books", "strategy-seed-paper-corpus",
            "collector-tw-twse-t86", "collector-tw-twse-price-history", "collector-tw-tpex-price-history"} <= ids
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


def _multi_symbol_bundle(tmp_path):
    files, entry, _ = make_bundle(source_id="collector-tw-cmoney-forum", run_id="cap1")
    base = "bundles/collector-tw-cmoney-forum/2026-11-10/cap1"
    rows = [json.loads(x) for x in files[f"{base}/items.jsonl"].decode().splitlines()]
    for row in rows:
        row["symbols"] = ["1101.TW", "2317.TW", "2330.TW"]
        row["metrics"] = {"sentiment": "bullish"}
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
    return files, entry


def test_capped_community_expansion_flushes_across_empty_cursor_pulls(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    files, entry = _multi_symbol_bundle(tmp_path)
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setattr(
        CollectorBundleAdapter, "_instrument_authority",
        lambda self: {"1101": ("1101.TWSE",), "2317": ("2317.TWSE",), "2330": ("2330.TWSE",)},
    )
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    config = {
        "source_id": "collector-tw-cmoney-forum", "source_type": "social", "dataset": "tw_community_daily",
        "platform": "cmoney", "host": HOST, "max_records": 2,
    }
    adapter = CollectorBundleAdapter(connector_id="conn-cap", **{k: v for k, v in config.items() if k != "host"})
    seen: set[str] = set()
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {}, "max_records": 2}
    raw_returned = False
    for _ in range(6):
        records = execute_provider_owned_adapter(connector=adapter.connector(), fetch=fetch, trace_id="t")
        assert len(records) <= 2
        seen |= {r.metadata["symbol"] for r in records if r.content_ref.startswith("collector-feature://")}
        raw_returned = raw_returned or any(not r.content_ref.startswith("collector-feature://") for r in records)
        if raw_returned:
            break
    assert seen == {"1101.TWSE", "2317.TWSE", "2330.TWSE"}
    assert raw_returned
    assert adapter.pull(cursor=None, max_records=2).next_cursor is not None


def test_capped_macro_expansion_delivers_every_derived_field_across_pulls(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    files, entry, _ = make_bundle(source_id="collector-tw-macro-ndc", run_id="mac1")
    base = "bundles/collector-tw-macro-ndc/2026-11-10/mac1"
    rows = [json.loads(x) for x in files[f"{base}/items.jsonl"].decode().splitlines()]
    for row, period in zip(rows, ("2026-09-30", "2026-08-31")):
        row.update({"series_id": "NDC:6099:MONITORING_SCORE", "period_end": period, "value": 28.0,
                    "metrics": {"leading_index": 101.0, "coincident_index": 99.0}})
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    config = {"source_id": "collector-tw-macro-ndc", "source_type": "macro", "dataset": "tw_macro_series",
              "platform": "ndc", "max_records": 2}
    adapter = CollectorBundleAdapter(connector_id="conn-mac", **config)
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {}, "max_records": 2}
    seen: set[tuple[str, str]] = set()
    raw_returned = False
    for _ in range(12):
        records = execute_provider_owned_adapter(connector=adapter.connector(), fetch=fetch, trace_id="t")
        assert len(records) <= 2
        for r in records:
            if r.content_ref.startswith("collector-feature://"):
                nr = r.metadata["normalized_row"]
                seen.add((nr["field"], nr["event_time"]))
            else:
                raw_returned = True
        if raw_returned:
            break
    fields = {"monitoring_score", "leading_index", "coincident_index"}
    assert {f for f, _ in seen} == fields
    assert len(seen) == 6
    assert raw_returned


def test_unequal_public_cap_macro_emission_neither_loses_nor_duplicates_across_restarts(tmp_path, monkeypatch):
    """Adapter default cap (500) is larger than the public fetch cap (2): the public path must
    forward the fetch cap, so unreturned derived rows stay pending and emitted ones never repeat."""
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    files, entry, _ = make_bundle(source_id="collector-tw-macro-ndc", run_id="mac2")
    base = "bundles/collector-tw-macro-ndc/2026-11-10/mac2"
    rows = [json.loads(x) for x in files[f"{base}/items.jsonl"].decode().splitlines()]
    for row, period in zip(rows, ("2026-09-30", "2026-08-31")):
        row.update({"series_id": "NDC:6099:MONITORING_SCORE", "period_end": period, "value": 28.0,
                    "metrics": {"leading_index": 101.0, "coincident_index": 99.0}})
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    config = {"source_id": "collector-tw-macro-ndc", "source_type": "macro", "dataset": "tw_macro_series",
              "platform": "ndc"}  # adapter max_records stays at its default (500)
    assert CollectorBundleAdapter(connector_id="conn-mac2", **config).max_records > 2
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {}, "max_records": 2}
    emitted: list[tuple[str, str]] = []
    raw_returned = False
    for _ in range(12):  # a fresh adapter per tick models a process restart
        adapter = CollectorBundleAdapter(connector_id="conn-mac2", **config)
        records = execute_provider_owned_adapter(connector=adapter.connector(), fetch=fetch, trace_id="t")
        assert len(records) <= 2
        for r in records:
            if r.content_ref.startswith("collector-feature://"):
                nr = r.metadata["normalized_row"]
                emitted.append((nr["field"], nr["event_time"]))
            else:
                raw_returned = True
        if raw_returned:
            break
    assert len(emitted) == len(set(emitted)), "already-emitted observation was duplicated"
    assert {f for f, _ in emitted} == {"monitoring_score", "leading_index", "coincident_index"}
    assert len(set(emitted)) == 6
    assert raw_returned


def test_unequal_public_cap_community_does_not_lose_unreturned_rows(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    files, entry = _multi_symbol_bundle(tmp_path)
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setattr(
        CollectorBundleAdapter, "_instrument_authority",
        lambda self: {"1101": ("1101.TWSE",), "2317": ("2317.TWSE",), "2330": ("2330.TWSE",)},
    )
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    config = {"source_id": "collector-tw-cmoney-forum", "source_type": "social", "dataset": "tw_community_daily",
              "platform": "cmoney", "host": HOST}
    kw = {k: v for k, v in config.items() if k != "host"}
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {}, "max_records": 2}
    seen: set[str] = set()
    raw_returned = False
    for _ in range(8):
        adapter = CollectorBundleAdapter(connector_id="conn-cap2", **kw)
        records = execute_provider_owned_adapter(connector=adapter.connector(), fetch=fetch, trace_id="t")
        assert len(records) <= 2
        seen |= {r.metadata["symbol"] for r in records if r.content_ref.startswith("collector-feature://")}
        raw_returned = raw_returned or any(not r.content_ref.startswith("collector-feature://") for r in records)
        if raw_returned:
            break
    assert seen == {"1101.TWSE", "2317.TWSE", "2330.TWSE"}
    assert raw_returned


def _two_bundles(source_id, *, macro=False):
    files, entries = {}, []
    for run_id, finished in (("cap-a", "2026-11-10T02:05:00Z"), ("cap-b", "2026-11-10T03:05:00Z")):
        f, e, _ = make_bundle(source_id=source_id, run_id=run_id, finished=finished)
        if macro:
            base = f"bundles/{source_id}/2026-11-10/{run_id}"
            rows = [json.loads(x) for x in f[f"{base}/items.jsonl"].decode().splitlines()]
            periods = ("2026-09-30", "2026-08-31") if run_id == "cap-a" else ("2026-07-31", "2026-06-30")
            for row, period in zip(rows, periods):
                row.update({"series_id": "NDC:6099:MONITORING_SCORE", "period_end": period, "value": 28.0,
                            "metrics": {"leading_index": 101.0, "coincident_index": 99.0}})
            f[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    return files, entries


def test_raw_only_bundle_larger_than_cap_makes_bounded_progress_across_bundles(tmp_path):
    files, entries = _two_bundles("collector-x")
    cursor, delivered = None, []
    for _ in range(10):
        adapter = adapter_for(files, entries, tmp_path, source_id="collector-x", source_type="news",
                              platform=None, publisher="cna")
        result = adapter.pull(cursor=cursor, max_records=1)
        assert len(result.records) <= 1
        delivered.extend(r.source_id for r in result.records)
        if result.next_cursor == cursor and not result.records:
            break
        cursor = result.next_cursor
    assert len(delivered) == 4
    assert cursor == "2026-11-10T03:05:00Z#cap-b"


def _public_sweep(config, fetch_extra, tmp_path, monkeypatch, files, rounds=40):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {},
             "max_records": 1, **fetch_extra}
    derived, raw = [], set()
    for _ in range(rounds):
        adapter = CollectorBundleAdapter(connector_id="conn-capx", **config)  # fresh adapter every call
        records = execute_provider_owned_adapter(connector=adapter.connector(), fetch=fetch, trace_id="t")
        assert len(records) <= 1
        for r in records:
            if r.content_ref.startswith("collector-feature://"):
                nr = r.metadata["normalized_row"]
                derived.append((nr["field"], nr["event_time"]))
            else:
                raw.add(r.metadata["raw_uri"])
    return derived, raw


def test_public_cap_below_bundle_size_macro_progresses_across_bundles_and_fresh_adapters(tmp_path, monkeypatch):
    files, _ = _two_bundles("collector-tw-macro-ndc", macro=True)
    config = {"source_id": "collector-tw-macro-ndc", "source_type": "macro", "dataset": "tw_macro_series",
              "platform": "ndc"}
    derived, raw = _public_sweep(config, {}, tmp_path, monkeypatch, files)
    assert len(derived) == len(set(derived)), "derived observation duplicated"
    assert len(set(derived)) == 12, "every item of every bundle must yield its derived rows"
    assert {f for f, _ in derived} == {"monitoring_score", "leading_index", "coincident_index"}
    assert len(raw) == 4, "raw evidence starved behind a bundle larger than the public cap"
    manifests = list((tmp_path / "raw_store" / "raw").rglob("manifest.json"))
    assert len(manifests) == 2


def test_public_cap_raw_only_reaches_every_item_and_bundle_across_fresh_adapters(tmp_path, monkeypatch):
    files, _ = _two_bundles("collector-x")
    config = {"source_id": "collector-x", "source_type": "news", "publisher": "cna"}
    _, raw = _public_sweep(config, {}, tmp_path, monkeypatch, files, rounds=12)
    assert len(raw) == 4
    assert len(list((tmp_path / "raw_store" / "raw").rglob("manifest.json"))) == 2


# COLLECTOR-HOSTED-ADMISSION-20261112: two-year price history is point-in-time per row ----------------
def _market_files(source_id, rows):
    files, entry, _ = make_bundle(source_id=source_id, attribution="TWSE")
    base = f"bundles/{source_id}/{entry['date']}/{entry['run_id']}"
    items = [
        {"id": item_id, "url": f"https://example.test/{item_id}", "fetched_at": FETCHED, **row}
        for item_id, row in zip(("a", "b"), rows)
    ]
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in items).encode()
    return files, entry


_TWSE_ROW = {"symbol": "2330", "security_name": "台積電", "exchange": "TWSE", "dataset": "tw_price_daily",
             "open": 940.0, "high": 955.0, "low": 938.0, "close": 950.0, "volume_shares": 20000000, "transactions": 12000}


@pytest.mark.parametrize(
    "source_id,dataset,expected_time",
    [
        ("collector-tw-twse-price-history", "tw_price_daily", "T06:30:00Z"),
        ("collector-tw-tpex-price-history", "tw_price_daily", "T06:30:00Z"),
        ("collector-tw-twse-t86", "tw_institutional_flow", "T08:30:00Z"),
    ],
)
def test_backfilled_market_rows_use_their_own_trade_date_as_event_time(tmp_path, source_id, dataset, expected_time):
    rows = [{**_TWSE_ROW, "dataset": dataset, "trade_date": "2024-11-12"},
            {**_TWSE_ROW, "dataset": dataset, "trade_date": "2025-03-04"}]
    files, entry = _market_files(source_id, rows)
    adapter = adapter_for(files, [entry], tmp_path, source_id=source_id, source_type="market", platform=None,
                          attribution_required=True, dataset=dataset)
    records = adapter.pull().records
    # Fetched in 2026, but each row is event-dated by its own trade date, and available when fetched.
    assert [r.metadata["event_time"] for r in records] == ["2024-11-12" + expected_time, "2025-03-04" + expected_time]
    assert all(r.metadata["available_time"] == FETCHED and r.metadata["pit"]["validated"] for r in records)
    assert [r.metadata["trade_date"] for r in records] == ["2024-11-12", "2025-03-04"]


def test_market_row_published_on_another_date_than_its_trade_date_is_rejected(tmp_path):
    rows = [{**_TWSE_ROW, "trade_date": "2024-11-12", "published_at": "2026-11-10T06:30:00Z"}, {**_TWSE_ROW, "trade_date": "2024-11-13"}]
    files, entry = _market_files("collector-tw-twse-price-history", rows)
    adapter = adapter_for(files, [entry], tmp_path, source_id="collector-tw-twse-price-history", source_type="market",
                          platform=None, attribution_required=True, dataset="tw_price_daily")
    with pytest.raises(SourceEvidenceError, match="trade_date"):
        adapter.pull()
    assert raw_files(tmp_path) == []


def test_collector_history_rows_agree_with_official_tw_price_daily_and_leave_it_unchanged(tmp_path):
    from services.source_ingestion.connectors.taiwan_official import TaiwanOfficialMarketDatasetAdapter
    from services.source_ingestion.tests.test_taiwan_official_connectors import TWSE_PRICE_HISTORY_PAYLOAD

    official = TaiwanOfficialMarketDatasetAdapter(max_records=10).records_from_price_history_payload(
        "2330.TWSE", "TWSE", TWSE_PRICE_HISTORY_PAYLOAD,
        api_endpoint="bundle://collector-tw-twse-price-history?date=20260601&symbol=2330", trace_id="official",
    )
    before = [r.to_dict() for r in official]
    truth = official[0].metadata["normalized_row"]  # 2330.TWSE 2026-06-09

    rows = [{**_TWSE_ROW, "trade_date": truth["date"]}, {**_TWSE_ROW, "trade_date": "2024-11-12", "close": 900.0}]
    files, entry = _market_files("collector-tw-twse-price-history", rows)
    adapter = adapter_for(files, [entry], tmp_path, source_id="collector-tw-twse-price-history", source_type="market",
                          platform=None, attribution_required=True, dataset="tw_price_daily")
    collector = adapter.pull().records

    same_day = next(r for r in collector if r.metadata["trade_date"] == truth["date"])
    row = same_day.metadata["normalized_row"]
    assert (row["symbol"], row["trade_date"]) == (truth["symbol"], truth["date"])
    for collector_key, official_key in (("open", "open"), ("high", "high"), ("low", "low"), ("close", "close"),
                                        ("volume_shares", "volume"), ("transactions", "transactions")):
        assert row[collector_key] == truth[official_key], collector_key
    # The collector copy is a separate lineage, not an official record, and official truth is untouched.
    assert same_day.connector_id != official[0].connector_id
    assert same_day.source_id.startswith("collector:collector-tw-twse-price-history:")
    assert not same_day.source_id.startswith("tw-official:")
    assert [r.to_dict() for r in official] == before


# COLLECTOR-HOSTED-ADMISSION-20261112: per-pull verification work is bounded by the cap ----------------
class _CountingTransport(FakeTransport):
    def __init__(self, files):
        super().__init__(files)
        self.reads: list[str] = []

    def read(self, relative_path, *, max_bytes):
        self.reads.append(relative_path)
        return super().read(relative_path, max_bytes=max_bytes)


def _day_bundle(source_id, run_no, n_rows, dataset="tw_price_daily"):
    day = f"2024-11-{run_no:02d}"
    run_id = f"run{run_no}"
    finished = f"2026-11-10T02:{run_no:02d}:00Z"
    manifest_items, rows, raw = [], [], {}
    for i in range(n_rows):
        body = f"<r>{run_no}-{i}</r>".encode()
        sha = hashlib.sha256(body).hexdigest()
        item_id = f"i{i}"
        manifest_items.append({"item_id": item_id, "url": f"https://example.test/{run_no}/{i}", "http_status": 200,
                               "content_sha256": sha, "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        rows.append({"id": item_id, "url": f"https://example.test/{run_no}/{i}", "fetched_at": FETCHED,
                     **{**_TWSE_ROW, "dataset": dataset, "symbol": f"{1000 + i}", "trade_date": day}})
        raw[sha] = body
    manifest = {"schema_version": "collector_bundle.v1", "source_id": source_id, "run_id": run_id,
                "collector_version": "1.0.0", "started_at": "2026-11-10T02:00:00Z", "finished_at": finished,
                "fetch_policy_hash": "fph", "robots_decision": "allowed", "terms_ref": "https://example.test/terms",
                "attribution": "TWSE", "items": manifest_items, "status": "complete",
                "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in manifest_items])}
    base = f"bundles/{source_id}/{day}/{run_id}"
    files = {f"{base}/manifest.json": json.dumps(manifest).encode(),
             f"{base}/items.jsonl": "\n".join(json.dumps(r) for r in rows).encode()}
    for sha, body in raw.items():
        files[f"{base}/raw/{sha}.html"] = body
    return files, {"source_id": source_id, "date": day, "run_id": run_id, "finished_at": finished}


def test_multi_bundle_history_pull_reads_only_what_the_cap_can_return(tmp_path):
    source_id = "collector-tw-twse-price-history"
    files, entries = {}, []
    for n in (1, 2, 3):
        f, e = _day_bundle(source_id, n, 600)
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    transport = _CountingTransport(files)

    def make():
        return CollectorBundleAdapter(
            connector_id="conn-x", raw_root=str(tmp_path / "store"), transport=transport, host=HOST,
            source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
            max_records=500,
        )

    cursor, seen, max_raw_reads = None, [], 0
    for _ in range(6):
        transport.reads.clear()
        result = make().pull(cursor=cursor)
        raw_reads = [r for r in transport.reads if "/raw/" in r]
        max_raw_reads = max(max_raw_reads, len(raw_reads))
        assert len(raw_reads) <= 500 and len(transport.reads) <= 500 + 8
        seen.extend((r.metadata["trade_date"], r.metadata["event_time"]) for r in result.records)
        cursor = result.next_cursor
        if result.complete:
            break
    # Every one of the 1800 rows is delivered exactly once, event-dated by its own trade date,
    # while no single pull read more than the cap's worth of raw files.
    assert len(seen) == 1800 and len(set(seen)) == 3
    assert max_raw_reads <= 500
    # A tampered raw file beyond the first window is still caught when its window is reached.
    bad = dict(files)
    key = next(k for k in bad if "/run3/raw/" in k)
    bad[key] = b"tampered"
    t2 = _CountingTransport(bad)
    adapter = CollectorBundleAdapter(
        connector_id="conn-y", raw_root=str(tmp_path / "store2"), transport=t2, host=HOST,
        source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily", max_records=500)
    cursor = None
    with pytest.raises(SourceEvidenceError):
        for _ in range(6):
            r = adapter.pull(cursor=cursor)
            cursor = r.next_cursor


# TW-SAME-DAY-CLOSE-VIA-COLLECTOR-20261112: newest trading day is delivered ahead of the backfill sweep ----
def _priority_setup(tmp_path, *, priority=True):
    source_id = "collector-tw-twse-price-history"
    files, entries = {}, []
    for n in (1, 2, 3):  # run3 = newest trade date (2024-11-03); the sweep is oldest-first
        f, e = _day_bundle(source_id, n, 600)
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()

    def make():
        return CollectorBundleAdapter(
            connector_id="conn-p", raw_root=str(tmp_path / "store"), transport=FakeTransport(files), host=HOST,
            source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
            max_records=1000, cadence_seconds=1800 if priority else 86400,
        )
    return make


def test_newest_trade_date_is_delivered_first_even_when_backfill_is_pending(tmp_path):
    make = _priority_setup(tmp_path)
    first = make().pull()
    # newest session first; the remaining cap goes to the oldest-first sweep
    assert {r.metadata["trade_date"] for r in first.records[:600]} == {"2024-11-03"}
    assert {r.metadata["trade_date"] for r in first.records[600:]} == {"2024-11-01"}
    close = first.records[0].metadata["collector_close"]
    assert close["provisional"] is True and close["official"] is False
    assert close["collector_source_id"] == "collector-tw-twse-price-history"
    assert close["exchange_endpoint"].startswith("https://") and len(close["bundle_sha256"]) == 64
    assert first.priority_key and first.priority_done


def test_priority_not_redelivered_after_save_and_sweep_continues(tmp_path):
    make = _priority_setup(tmp_path)
    adapter = make()
    first = adapter.pull(cursor=adapter.load_resume_cursor())
    adapter.save_resume_cursor(first)
    second_adapter = make()
    second = second_adapter.pull(cursor=second_adapter.load_resume_cursor())
    dates = [r.metadata["trade_date"] for r in second.records]
    assert second.priority_key is None  # nothing new to prioritise; the ordinary sweep runs
    assert dates and dates[0] == "2024-11-01"


def test_priority_disabled_keeps_oldest_first_cursor_order(tmp_path):
    make = _priority_setup(tmp_path, priority=False)
    result = make().pull()
    assert result.priority_key is None
    assert result.records[0].metadata["trade_date"] == "2024-11-01"


def test_definitions_pull_price_history_every_30_minutes():
    from services.source_ingestion.connector_definitions import DEPLOYED_CONNECTOR_DEFINITIONS
    defs = {d.definition_id: d for d in DEPLOYED_CONNECTOR_DEFINITIONS}
    for sid in ("collector-tw-twse-price-history", "collector-tw-tpex-price-history"):
        meta = defs[sid].metadata["collector"]
        assert meta["cadence_seconds"] <= 1800
    assert defs["collector-tw-twse-t86"].metadata["collector"]["cadence_seconds"] == 86400


def test_oversized_newest_day_is_delivered_whole_in_one_pull(tmp_path):
    source_id = "collector-tw-twse-price-history"
    files, entries = {}, []
    for n, size in ((1, 600), (2, 1500)):  # run2 = newest day, larger than max_records
        f, e = _day_bundle(source_id, n, size)
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    adapter = CollectorBundleAdapter(
        connector_id="conn-big", raw_root=str(tmp_path / "store"), transport=FakeTransport(files), host=HOST,
        source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
        max_records=1000, cadence_seconds=1800,
    )
    first = adapter.pull()
    newest = [r for r in first.records if r.metadata["trade_date"] == "2024-11-02"]
    assert len(newest) == 1500 and first.priority_done


def test_public_path_returns_every_priority_row_above_max_records_and_acks_accurately(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    source_id = "collector-tw-twse-price-history"
    files, entries = {}, []
    for n, size in ((1, 600), (2, 1500)):  # run2 = newest day, larger than fetch.max_records
        f, e = _day_bundle(source_id, n, size)
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    config = {"source_id": source_id, "source_type": "market", "attribution_required": True,
              "dataset": "tw_price_daily", "cadence_seconds": 1800, "host": HOST, "max_records": 1000}
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {}, "max_records": 1000}

    def pull():
        adapter = CollectorBundleAdapter(connector_id="conn-pub", **config)
        return execute_provider_owned_adapter(connector=adapter.connector(), fetch=fetch, trace_id="t")

    first = pull()
    newest = [r for r in first if r.metadata["trade_date"] == "2024-11-02"]
    assert len(newest) == 1500  # whole newest session on the first governed pull
    second = pull()
    # the oldest-first sweep resumes with the backfill; any later replay of the newest day is
    # idempotent (identical source ids), never a different or partial row set
    assert {r.metadata["trade_date"] for r in second[:600]} == {"2024-11-01"}
    assert {r.source_id for r in second if r.metadata["trade_date"] == "2024-11-02"} <= {r.source_id for r in newest}
