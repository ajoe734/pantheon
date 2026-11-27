"""SOURCE-COLLECTOR-BUNDLE-CONNECTOR-20261110 acceptance tests (SD-STRATEGY-DISCOVERY §7)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
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
                finished="2026-11-10T02:05:00Z", status="complete", author="hmac-abc", extra_item=None,
                fetched=FETCHED):
    raw = {"a": b"<html>post a</html>", "b": b"<html>post b</html>"}
    manifest_items, rows = [], []
    for item_id, body in raw.items():
        sha = hashlib.sha256(body).hexdigest()
        manifest_items.append({"item_id": item_id, "url": f"https://example.test/{item_id}", "http_status": 200,
                               "content_sha256": sha, "raw_path": f"raw/{sha}.html", "bytes": len(body)})
        rows.append({"id": item_id, "url": f"https://example.test/{item_id}", "fetched_at": fetched,
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


def _news_refetch_records(tmp_path):
    """Real adapter outputs with the run differences measured on hosted news rows.

    The second bundle contains only a, so its batch hash changes even though
    article a's raw bytes, title, URL and text are identical.
    """
    from services.source_ingestion.connectors.base import SourceRecord

    records = []
    for hour in (2, 3):
        run = f"run-20261127T{hour:02d}0000Z"
        fetched = f"2026-11-27T{hour:02d}:00:00Z"
        finished = f"2026-11-27T{hour:02d}:05:00Z"
        files, entry, manifest = make_bundle(
            source_id="collector-news-rss", date="2026-11-27", run_id=run,
            fetched=fetched, finished=finished,
        )
        base = f"bundles/collector-news-rss/2026-11-27/{run}"
        if hour == 3:
            manifest["items"] = manifest["items"][:1]
            manifest["bundle_sha256"] = compute_bundle_sha256(
                [manifest["items"][0]["content_sha256"]]
            )
            files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
            files[f"{base}/items.jsonl"] = files[f"{base}/items.jsonl"].splitlines()[0]
        adapter = adapter_for(
            files, [entry], tmp_path / run, source_id="collector-news-rss", source_type="news",
        )
        result = adapter.pull_bundles(trace_id=f"trace-{run}")
        payload = result.records[0].to_dict()
        payload["created_at"] = finished
        payload["metadata"].update(
            tenant_id="tenant-dev", source_ingest_run_id=f"ingest-{run}", ingest_time=finished,
        )
        records.append(SourceRecord.from_dict(payload))
    return tuple(records)


def test_news_refetch_preserves_observation_fields_but_has_one_content_identity(tmp_path):
    from services.source_ingestion.distillation_worker import source_version_digest

    first, later = _news_refetch_records(tmp_path)
    assert first.source_id == later.source_id
    assert first.metadata["body"] == later.metadata["body"]
    for key in ("available_time", "ingest_time", "raw_uri", "source_ingest_run_id", "trace_id"):
        assert first.metadata[key] != later.metadata[key]
    for key in ("run_id", "bundle_sha256"):
        assert first.metadata["collector"][key] != later.metadata["collector"][key]
    assert first.metadata["pit"]["available_time"] != later.metadata["pit"]["available_time"]
    assert first.created_at != later.created_at
    assert first.trace_id != later.trace_id
    assert source_version_digest(first) == source_version_digest(later)


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


def test_readonly_threads_complete_metadata_requires_verified_boundary(tmp_path, monkeypatch):
    # Exact status metadata from the retained 20261113T043111 readonly proof.
    # Payload bytes are generated contract fixtures, not native Threads rows.
    source = "collector-tw-threads"
    run = "run-20261113T001922Z"
    finished = "2026-11-13T00:19:22Z"
    status = {"sources": {source: {"status": "complete", "paused_reason": None,
                                  "last_run_at": finished, "last_run_id": run}}}
    now = datetime(2026, 11, 13, 4, 25, 14, tzinfo=timezone.utc)
    cursor = finished + "#" + run
    observed = observed_from_collector_status(status, source_id=source, cursor=cursor,
                                             cadence_seconds=86400, now=now)
    assert observed["health_state"] == "degraded"
    assert "collector_complete_boundary_unverified" in observed["reasons"]
    files, entry, _ = make_bundle(source_id=source, run_id=run, date="2026-11-13", finished=finished)
    files["status.json"] = json.dumps(status).encode()
    import services.source_ingestion.connectors.collector_bundle as module
    real_observed = module.observed_from_collector_status
    monkeypatch.setattr(module, "observed_from_collector_status",
                        lambda *a, **kw: real_observed(*a, **{**kw, "now": now}))
    adapter = adapter_for(files, [entry], tmp_path, source_id=source)
    first = adapter.pull()
    assert len(first.records) == 2 and first.observed["health_state"] == "healthy"
    assert first.no_new_data is False


@pytest.mark.parametrize("change", ["no_proof", "partial_bundle", "incomplete_delivery", "partial_cursor",
                                   "wrong_cursor", "wrong_source", "missing_run", "wrong_run", "older_time",
                                   "newer_time", "missing_time", "stale", "future", "missing_cadence", "paused_reason"])
def test_complete_status_never_overrides_unverified_or_contradictory_evidence(tmp_path, change):
    from dataclasses import replace
    from services.source_ingestion.connectors.collector_bundle import verify_bundle

    files, entry, _ = make_bundle()
    bundle = verify_bundle(FakeTransport(files), expected_source_id=entry["source_id"], entry=entry)
    status = _status(entry["finished_at"], "complete")
    source = status["sources"][entry["source_id"]]
    source["last_run_id"] = entry["run_id"]
    kwargs = dict(verified_bundle=bundle, delivery_complete=True, cursor=bundle.cursor,
                  cadence_seconds=86400, now=datetime(2026, 11, 10, 3, tzinfo=timezone.utc))
    if change == "no_proof":
        kwargs["verified_bundle"] = None
    elif change == "partial_bundle":
        kwargs["verified_bundle"] = replace(bundle, manifest={**bundle.manifest, "status": "partial"})
    elif change == "incomplete_delivery":
        kwargs["delivery_complete"] = False
    elif change == "partial_cursor":
        kwargs["cursor"] += "|items=1"
    elif change == "wrong_cursor":
        kwargs["cursor"] = "2026-11-10T02:05:00Z#other"
    elif change == "wrong_source":
        kwargs["verified_bundle"] = replace(bundle, source_id="other")
    elif change == "missing_run":
        source.pop("last_run_id")
    elif change == "wrong_run":
        source["last_run_id"] = "other"
    elif change in {"older_time", "newer_time"}:
        source["last_run_at"] = "2026-11-10T02:04:59Z" if change == "older_time" else "2026-11-10T02:05:01Z"
    elif change == "missing_time":
        source.pop("last_run_at")
    elif change == "stale":
        kwargs["now"] += timedelta(days=3)
    elif change == "future":
        kwargs["now"] -= timedelta(hours=2)
    elif change == "missing_cadence":
        kwargs["cadence_seconds"] = None
    elif change == "paused_reason":
        source["paused_reason"] = "login_required"
    observed = observed_from_collector_status(status, source_id=entry["source_id"], **kwargs)
    assert observed["health_state"] == "degraded" and observed["reasons"]


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


@pytest.mark.parametrize("source", ["collector-tw-ptt-stock", "collector-tw-threads"])
@pytest.mark.parametrize("symbols", [None, [], ["", "  "], "2330.TW", ["2330.TWSE", "6488.TWO"]])
def test_social_pull_without_bare_symbols_never_reads_price_authority(tmp_path, monkeypatch, source, symbols):
    from services.source_ingestion.connectors import collector_bundle as cb

    files, entry, _ = make_bundle(source_id=source)
    base = f"bundles/{source}/2026-11-10/run1"
    items = [json.loads(line) for line in files[f"{base}/items.jsonl"].decode().splitlines()]
    for item in items:
        item["symbols"] = symbols
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(item) for item in items).encode()

    def forbidden(*args, **kwargs):
        pytest.fail("social pull opened historical price authority without a bare ticker")

    monkeypatch.setattr(cb, "stored_instrument_authority", forbidden)
    def make():
        return adapter_for(files, [entry], tmp_path, source_id=source, dataset="tw_community_daily",
                           clock=lambda: datetime(2026, 11, 11, tzinfo=timezone.utc))
    with cb.deferred_collector_acknowledgement():
        result = make().pull()
    assert result.next_cursor == f"{entry['finished_at']}#run1"
    feature_symbols = {r.metadata["symbol"] for r in result.records
                       if r.content_ref.startswith("collector-feature://")}
    expected = {"2330.TWSE"} if symbols == "2330.TW" else (
        {"2330.TWSE", "6488.TPEX"} if symbols == ["2330.TWSE", "6488.TWO"] else set()
    )
    assert feature_symbols == expected
    assert result.feature_rejected == ()
    # Planning and failed persistence never acknowledge ledger/markers or cursor.
    assert not make()._community_ledger_path().exists()
    assert not make()._resume_cursor_path().exists()
    result.delivery.validate(result.records)
    result.delivery.acknowledge()
    boundary = cb.verify_bundle(make().transport, expected_source_id=source, entry=entry)
    assert make()._has_verified_boundary(boundary, persisted_only=True)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in raw_files(tmp_path)}
    # Fresh adapters model cold process state as well as subsequent empty ticks.
    for _ in range(2):
        with cb.deferred_collector_acknowledgement():
            empty = make().pull(cursor=result.next_cursor)
        assert empty.records == ()
        empty.delivery.validate(empty.records)
        empty.delivery.acknowledge()
    assert {p.relative_to(tmp_path): p.read_bytes() for p in raw_files(tmp_path)} == before
    if not symbols or symbols == ["", "  "]:
        assert not make()._community_ledger_path().exists()
        assert not make()._community_ledger_path().with_name("community_emitted.jsonl").exists()


def test_qualified_social_symbol_rejection_does_not_consult_authority(tmp_path, monkeypatch):
    from services.source_ingestion.connectors import collector_bundle as cb
    from services.source_ingestion.tests.test_feature_catalogue import _post

    monkeypatch.setattr(cb, "stored_instrument_authority", lambda *a: pytest.fail("price scan"))
    adapter = adapter_for({}, [], tmp_path, dataset="tw_community_daily")
    plan = adapter._plan_governed_features([_post(1, "2026-11-10", ["2330.BAD"])])
    assert not plan.rows and not plan.fresh_entries
    assert len(plan.rejected) == 1
    assert not adapter._community_ledger_path().exists()


def test_falsy_social_symbols_stay_empty_refusals_without_authority_scan(tmp_path, monkeypatch):
    from services.source_ingestion.connectors import collector_bundle as cb
    from services.source_ingestion.tests.test_feature_catalogue import _post

    monkeypatch.setattr(cb, "stored_instrument_authority", lambda *a: pytest.fail("price scan"))
    adapter = adapter_for({}, [], tmp_path, dataset="tw_community_daily")
    plan = adapter._plan_governed_features([_post(1, "2026-11-10", [None, False, 0, ""])])
    assert not plan.rows and not plan.fresh_entries
    assert sum("empty" in r["reason"] for r in plan.rejected) == 3  # None/False/0 refused, "" skipped


def test_empty_social_pull_still_flushes_persisted_ledger_after_day_close(tmp_path, monkeypatch):
    from services.source_ingestion.connectors import collector_bundle as cb

    files, entry, _ = make_bundle()
    base = "bundles/collector-tw-ptt-stock/2026-11-10/run1"
    items = [json.loads(line) for line in files[f"{base}/items.jsonl"].decode().splitlines()]
    for item in items:
        item["symbol"] = "2330.TW"
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(item) for item in items).encode()
    monkeypatch.setattr(cb, "stored_instrument_authority", lambda *a: pytest.fail("price scan"))
    early = adapter_for(files, [entry], tmp_path, dataset="tw_community_daily",
                        clock=lambda: datetime(2026, 11, 10, 3, tzinfo=timezone.utc))
    with cb.deferred_collector_acknowledgement():
        first = early.pull()
    first.delivery.validate(first.records)
    first.delivery.acknowledge()
    assert early._community_ledger_path().exists()
    assert not early._community_ledger_path().with_name("community_emitted.jsonl").exists()
    late = replace(early, clock=lambda: datetime(2026, 11, 11, tzinfo=timezone.utc))
    with cb.deferred_collector_acknowledgement():
        flushed = late.pull(cursor=first.next_cursor)
    features = [r.metadata["normalized_row"] for r in flushed.records]
    assert len(features) == 1
    assert features[0]["symbol"] == "2330.TWSE"
    assert features[0]["post_volume"] == 2
    flushed.delivery.validate(flushed.records)
    flushed.delivery.acknowledge()
    with cb.deferred_collector_acknowledgement():
        assert late.pull(cursor=first.next_cursor).records == ()


def test_bare_social_symbols_share_current_governed_evidence_and_refusals(tmp_path, monkeypatch):
    import gzip
    from services.source_ingestion.connectors import collector_bundle as cb

    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "run_files")
    raw_root, market_root = tmp_path / "store", tmp_path / "market"
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(market_root))

    def write(root, name, symbols):
        path = root / "normalized/tw_price_daily/date=2026-11-10" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        opener = gzip.open if name.endswith(".gz") else Path.open
        with opener(path, "wt", encoding="utf-8") as handle:
            for symbol in symbols:
                handle.write(json.dumps({"metadata": {"normalized_row": {"symbol_canonical": symbol}}}) + "\n")
        return path

    write(raw_root, "a.jsonl", ["2330.TWSE", "1234.TWSE"])
    changed = write(market_root, "b.jsonl.gz", ["6488.TWO", "1234.TPEX"])
    expected = cb.stored_instrument_authority(raw_root, market_root)
    files, entry, _ = make_bundle()
    base = "bundles/collector-tw-ptt-stock/2026-11-10/run1"
    items = [json.loads(line) for line in files[f"{base}/items.jsonl"].decode().splitlines()]
    for item in items:
        item["symbols"] = ["2330", "6488", "1234", "9999", "1101.TW"]
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(item) for item in items).encode()
    adapter = adapter_for(files, [entry], tmp_path, dataset="tw_community_daily",
                          clock=lambda: datetime(2026, 11, 11, tzinfo=timezone.utc))
    bundle = cb.verify_bundle(adapter.transport, expected_source_id=adapter.source_id, entry=entry)
    records = tuple(adapter._record(bundle, row, trace_id="test") for row in bundle.items)
    original = CollectorBundleAdapter._instrument_authority
    snapshots = []

    def counted(self):
        snapshot = original(self)
        snapshots.append(snapshot)
        return snapshot

    monkeypatch.setattr(CollectorBundleAdapter, "_instrument_authority", counted)
    for _ in range(2):  # cold and warm, never reuse stale authority across requests
        plan = adapter._plan_governed_features(records)
        assert snapshots[-1] == expected
        assert {r["symbol"] for r in plan.rows} == {"2330.TWSE", "6488.TWO", "1101.TWSE"}
        assert sum("ambiguous" in r["reason"] for r in plan.rejected) == 2
        assert sum("no governed instrument" in r["reason"] for r in plan.rejected) == 2
    assert len(snapshots) == 2  # one complete authority snapshot per request, not per post/symbol
    write(market_root, changed.name, ["6488.TWO", "1234.TWSE", "9999.TWSE"])
    updated = adapter._plan_governed_features(records)
    assert {r["symbol"] for r in updated.rows} == {"2330.TWSE", "6488.TWO", "1234.TWSE", "9999.TWSE", "1101.TWSE"}
    changed.unlink()
    removed = adapter._plan_governed_features(records)
    assert {r["symbol"] for r in removed.rows} == {"2330.TWSE", "1234.TWSE", "1101.TWSE"}
    assert len(snapshots) == 4


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


def _oversized_files(source_id):
    files, entries = {}, []
    for n, size in ((1, 600), (2, 1500)):  # run2 = newest day, larger than max_records
        f, e = _day_bundle(source_id, n, size)
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    return files


def test_oversized_newest_day_is_delivered_in_bounded_batches_without_exceeding_cap(tmp_path):
    source_id = "collector-tw-twse-price-history"
    files = _oversized_files(source_id)

    def make():
        return CollectorBundleAdapter(
            connector_id="conn-big", raw_root=str(tmp_path / "store"), transport=FakeTransport(files), host=HOST,
            source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
            max_records=1000, cadence_seconds=600,
        )

    adapter = make()
    first = adapter.pull(cursor=adapter.load_resume_cursor())
    assert len(first.records) == 1000 and not first.priority_done and first.priority_delivered == 1000
    assert {r.metadata["trade_date"] for r in first.records} == {"2024-11-02"}
    adapter.save_resume_cursor(first)
    adapter = make()
    second = adapter.pull(cursor=adapter.load_resume_cursor())
    assert len(second.records) <= 1000 and second.priority_done
    newest = [r for r in second.records if r.metadata["trade_date"] == "2024-11-02"]
    assert len(newest) == 500
    assert {r.source_id for r in newest}.isdisjoint({r.source_id for r in first.records})


def test_pull_honors_a_caller_cap_of_one_for_priority_bundles(tmp_path):
    source_id = "collector-tw-twse-price-history"
    adapter = CollectorBundleAdapter(
        connector_id="conn-cap1", raw_root=str(tmp_path / "store"),
        transport=FakeTransport(_oversized_files(source_id)), host=HOST,
        source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
        max_records=1000, cadence_seconds=600,
    )
    result = adapter.pull(max_records=1)
    assert len(result.records) == 1 and result.priority_delivered == 1 and not result.priority_done


def test_public_path_never_exceeds_fetch_max_records_and_acks_accurately(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    source_id = "collector-tw-twse-price-history"
    files = _oversized_files(source_id)
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(files))
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    config = {"source_id": source_id, "source_type": "market", "attribution_required": True,
              "dataset": "tw_price_daily", "cadence_seconds": 600, "host": HOST, "max_records": 1000}
    fetch = {"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": config, "request": {}, "max_records": 1000}
    first = execute_provider_owned_adapter(
        connector=CollectorBundleAdapter(connector_id="conn-pub", **config).connector(), fetch=fetch, trace_id="t"
    )
    assert len(first) == 1000
    assert {r.metadata["trade_date"] for r in first} == {"2024-11-02"}
    fetch["max_records"] = 1
    config["max_records"] = 1
    one = execute_provider_owned_adapter(
        connector=CollectorBundleAdapter(connector_id="conn-pub1", **config).connector(), fetch=fetch, trace_id="t"
    )
    assert len(one) == 1


# COLLECTOR-NEWS-PUBLISHED-AT-20261113 ---------------------------------------------
def _news_with_published(tmp_path, literal):
    files, entry, _ = make_bundle(source_id="collector-x")
    key = next(k for k in files if k.endswith("items.jsonl"))
    rows = [json.loads(line) for line in files[key].decode().splitlines()]
    for r in rows:
        r["published_at"] = literal
    files[key] = "\n".join(json.dumps(r) for r in rows).encode()
    return adapter_for(files, [entry], tmp_path, source_id="collector-x", source_type="news",
                       platform=None, publisher="cna")


@pytest.mark.parametrize("literal", ["Tue, 10 Nov 2026 01:00:00 GMT", "2026-11-10T01:00:00Z",
                                     "Tue, 10 Nov 2026 09:00:00 +0800"])
def test_rss_rfc2822_and_iso_published_at_keep_the_same_instant(tmp_path, literal):
    records = _news_with_published(tmp_path, literal).pull().records
    assert {r.metadata["published_at"] for r in records} == {"2026-11-10T01:00:00Z"}


@pytest.mark.parametrize("literal", ["Tue, 10 Nov 2026 01:00:00", "Tue, 10 Nov 2026 01:00:00 -0000",
                                     "yesterday"])
def test_rss_published_at_without_provable_timezone_or_malformed_still_fails(tmp_path, literal):
    adapter = _news_with_published(tmp_path, literal)
    with pytest.raises(SourceEvidenceError, match="ISO-8601"):
        adapter.pull()
    assert raw_files(tmp_path) == []


def test_rss_rfc2822_replay_is_stable_and_hash_mismatch_still_rejected(tmp_path):
    literal = "Tue, 10 Nov 2026 01:00:00 GMT"
    first = _news_with_published(tmp_path / "a", literal).pull().records
    again = _news_with_published(tmp_path / "b", literal).pull().records
    assert [r.metadata["content_hash"] for r in first] == [r.metadata["content_hash"] for r in again]
    files, entry, _ = make_bundle(source_id="collector-x")
    files[next(k for k in files if "/raw/" in k)] = b"tampered"
    with pytest.raises(SourceEvidenceError):
        adapter_for(files, [entry], tmp_path / "c", source_id="collector-x", source_type="news",
                    platform=None, publisher="cna").pull()


def test_rss_published_after_fetch_time_keeps_pit_failure(tmp_path):
    # the exact retained literal is later than this bundle's fetched_at: no time substitution
    adapter = _news_with_published(tmp_path, "Wed, 11 Nov 2026 04:37:00 GMT")
    with pytest.raises(SourceEvidenceError, match="available_time must be >= event_time"):
        adapter.pull()
    assert raw_files(tmp_path) == []


_BAD_RFC2822 = ["Tue, 10 Nov 2026 01:00:00 +080", "Tue, 10 Nov 2026 01:00:00 +1260",
                "Tue, 10 Nov 2026 01:00:00 GMT trailing", "Tue, 10 Nov 2026 01:00:00 EST",
                "Wed, 10 Nov 2026 01:00:00 GMT", "Tue, 31 Nov 2026 01:00:00 GMT"]


@pytest.mark.parametrize("literal", _BAD_RFC2822)
def test_rss_malformed_or_contradictory_rfc2822_is_rejected_by_verified_reader(tmp_path, literal):
    adapter = _news_with_published(tmp_path, literal)
    with pytest.raises(SourceEvidenceError, match="ISO-8601"):
        adapter.pull()
    assert raw_files(tmp_path) == []


def _recorded_rss_bundle(literal, fetched="2026-11-11T05:00:00Z", raw_tamper=False):
    item_id = "news-cna-202611110105-aspx"
    body = (f"<item><guid>{item_id}</guid><title>t</title><link>https://x.test/{item_id}</link>"
            f"<pubDate>{literal}</pubDate></item>").encode()
    sha = hashlib.sha256(body).hexdigest()
    src, run, date = "collector-news-rss", "run1", "2026-11-11"
    manifest = {
        "schema_version": "collector_bundle.v1", "source_id": src, "run_id": run, "collector_version": "1.0.0",
        "started_at": "2026-11-11T04:59:00Z", "finished_at": fetched, "fetch_policy_hash": "fph",
        "robots_decision": "allowed", "terms_ref": "https://example.test/terms", "status": "complete",
        "items": [{"item_id": item_id, "url": f"https://x.test/{item_id}", "http_status": 200,
                   "content_sha256": sha, "raw_path": f"raw/{sha}.xml", "bytes": len(body)}],
        "bundle_sha256": compute_bundle_sha256([sha]),
    }
    row = {"id": item_id, "url": f"https://x.test/{item_id}", "fetched_at": fetched, "published_at": literal,
           "text": "t", "lang": "zh-TW", "hashed_author": "hmac-abc", "metrics": {}, "title": "t"}
    base = f"bundles/{src}/{date}/{run}"
    files = {f"{base}/manifest.json": json.dumps(manifest).encode(), f"{base}/items.jsonl": json.dumps(row).encode(),
             f"{base}/raw/{sha}.xml": b"tampered" if raw_tamper else body}
    return files, {"source_id": src, "date": date, "run_id": run, "finished_at": fetched}


def _pull_rss(tmp_path, literal, **kw):
    files, entry = _recorded_rss_bundle(literal, **kw)
    return adapter_for(files, [entry], tmp_path, source_id="collector-news-rss", source_type="news",
                       platform=None, publisher="cna").pull()


def test_recorded_rss_bundle_exact_literal_and_equivalent_iso_yield_same_instant_and_hash(tmp_path):
    exact = _pull_rss(tmp_path / "a", "Wed, 11 Nov 2026 04:37:00 GMT").records
    iso = _pull_rss(tmp_path / "b", "2026-11-11T04:37:00Z").records
    assert {r.metadata["published_at"] for r in exact} == {"2026-11-11T04:37:00Z"}
    assert {r.metadata["published_at"] for r in iso} == {"2026-11-11T04:37:00Z"}
    assert exact[0].metadata["content_hash"] == "sha256:" + hashlib.sha256(
        b"<item><guid>news-cna-202611110105-aspx</guid><title>t</title>"
        b"<link>https://x.test/news-cna-202611110105-aspx</link>"
        b"<pubDate>Wed, 11 Nov 2026 04:37:00 GMT</pubDate></item>").hexdigest()
    replay = _pull_rss(tmp_path / "c", "Wed, 11 Nov 2026 04:37:00 GMT").records
    assert replay[0].metadata["content_hash"] == exact[0].metadata["content_hash"]


def test_recorded_rss_bundle_negatives_future_time_order_and_hash(tmp_path):
    with pytest.raises(SourceEvidenceError, match="available_time must be >= event_time"):
        _pull_rss(tmp_path / "f", "Wed, 11 Nov 2026 04:37:00 GMT", fetched="2026-11-11T04:00:00Z")
    with pytest.raises(SourceEvidenceError, match="ISO-8601"):
        _pull_rss(tmp_path / "n", "Wed, 11 Nov 2026 04:37:00")
    with pytest.raises(SourceEvidenceError):
        _pull_rss(tmp_path / "h", "Wed, 11 Nov 2026 04:37:00 GMT", raw_tamper=True)


# Exact NATIVE bytes (not generated): private evidence pack extracted by the operator; set
# PANTHEON_NATIVE_RSS_PACK_DIR to the extraction dir. Skipped when absent (pack is not in the repo).
_NATIVE_PACK = os.environ.get("PANTHEON_NATIVE_RSS_PACK_DIR")
_NATIVE_RUN = "bundles/collector-news-rss/2026-11-11/run-20261111T044920Z"


@pytest.mark.skipif(not _NATIVE_PACK, reason="private native RSS pack not provided")
def test_native_recorded_rss_bundle_exact_bytes_replay_through_verified_reader(tmp_path):
    root = Path(_NATIVE_PACK)
    files = {str(p.relative_to(root)): p.read_bytes() for p in (root / _NATIVE_RUN).rglob("*") if p.is_file()}
    entry = {"source_id": "collector-news-rss", "date": "2026-11-11", "run_id": "run-20261111T044920Z",
             "finished_at": "2026-11-11T04:49:20Z"}
    native_row = next(json.loads(l) for l in files[f"{_NATIVE_RUN}/items.jsonl"].decode().splitlines()
                      if json.loads(l)["id"] == "news-cna-202611110105-aspx")
    assert native_row["published_at"] == "Wed, 11 Nov 2026 04:37:00 GMT"  # exact retained literal
    res = adapter_for(files, [entry], tmp_path, source_id="collector-news-rss", source_type="news",
                      platform=None, publisher="cna").pull()
    manifest = json.loads(files[f"{_NATIVE_RUN}/manifest.json"])
    native_sha = next(i["content_sha256"] for i in manifest["items"] if i["item_id"] == "news-cna-202611110105-aspx")
    rec = next(r for r in res.records if r.metadata["content_hash"] == "sha256:" + native_sha)
    assert rec.metadata["collector"]["bundle_sha256"] == "d31fa3480140d40c59d6f54481dce8f17f15e128a173985abfd3179022800c2e"
    assert rec.metadata["available_time"] == "2026-11-11T04:49:20Z"
    assert rec.metadata["published_at"] == "2026-11-11T04:37:00Z"
    assert len(res.records) == 20


# Steady request bound: whole-pull raw read budget ---------------------------------
class _SlowTransport(_CountingTransport):
    """Every raw read costs `cost` fake seconds on a deterministic clock (no sleeping)."""

    def __init__(self, files, clock, cost):
        super().__init__(files)
        self.clock, self.cost = clock, cost

    def read(self, relative_path, *, max_bytes):
        if "/raw/" in relative_path:
            self.clock[0] += self.cost
        return super().read(relative_path, max_bytes=max_bytes)


def _slow_setup(tmp_path, monkeypatch, *, n_rows=12, cost=5.0, budget=15.0, tamper_index=None):
    from types import SimpleNamespace

    import services.source_ingestion.connectors.collector_bundle as cb

    clock = [0.0]
    monkeypatch.setattr(cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    source_id = "collector-tw-twse-price-history"
    files, entry = _day_bundle(source_id, 1, n_rows)
    if tamper_index is not None:
        key = sorted(k for k in files if "/raw/" in k)[tamper_index]
        files[key] = b"tampered"
    files["bundles/index.json"] = json.dumps({"bundles": [entry]}).encode()
    transport = _SlowTransport(files, clock, cost)

    def make():
        return CollectorBundleAdapter(
            connector_id="conn-x", raw_root=str(tmp_path / "store"), transport=transport, host=HOST,
            source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
            max_records=500, read_budget_seconds=budget, history_raw_read_concurrency=1,
        )

    return make, transport, clock


def test_slow_reads_stop_at_budget_and_resume_without_loss_or_duplicates(tmp_path, monkeypatch):
    make, transport, clock = _slow_setup(tmp_path, monkeypatch)
    cursor, delivered, per_pull_elapsed, completes = None, [], [], []
    for _ in range(10):
        start, transport.reads = clock[0], []
        result = make().pull(cursor=cursor)
        per_pull_elapsed.append(clock[0] - start)
        delivered.extend(r.source_id for r in result.records)
        completes.append(result.complete)
        cursor = result.next_cursor
        if result.complete:
            break
        # a budget-cut pull never claims completion and keeps a partial cursor
        assert "|items=" in cursor
    # 12 rows at 5s each under a 15s budget: bounded slices (<= budget + one read), then complete.
    assert max(per_pull_elapsed) <= 15.0 + 5.0
    assert completes[:-1] == [False] * (len(completes) - 1) and completes[-1] is True
    assert len(delivered) == 12 and len(set(delivered)) == 12


def test_budget_never_starves_a_pull_even_when_one_read_exceeds_it(tmp_path, monkeypatch):
    make, _, _ = _slow_setup(tmp_path, monkeypatch, cost=30.0, budget=1.0, n_rows=3)
    cursor, total = None, 0
    for _ in range(5):
        result = make().pull(cursor=cursor)
        assert len(result.records) >= 1
        total += len(result.records)
        cursor = result.next_cursor
        if result.complete:
            break
    assert total == 3 and result.complete


def test_budget_cut_slice_still_fails_on_tampered_raw_in_later_slice(tmp_path, monkeypatch):
    make, _, _ = _slow_setup(tmp_path, monkeypatch, tamper_index=7)
    cursor = None
    with pytest.raises(SourceEvidenceError, match="hash mismatch"):
        for _ in range(10):
            cursor = make().pull(cursor=cursor).next_cursor


def test_no_budget_keeps_single_full_window(tmp_path, monkeypatch):
    make, transport, _ = _slow_setup(tmp_path, monkeypatch)
    adapter = make()
    object.__setattr__(adapter, "read_budget_seconds", None)
    result = adapter.pull()
    assert len(result.records) == 12 and result.complete


# Reviewer counterexamples: one operation budget across bundles, priority path, read timeout ----
def _singleton_bundles(tmp_path, monkeypatch, *, count=12, cost=5.0, cadence=0):
    from types import SimpleNamespace

    import services.source_ingestion.connectors.collector_bundle as cb

    clock = [0.0]
    monkeypatch.setattr(cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    source_id = "collector-tw-twse-price-history"
    files, entries = {}, []
    for n in range(1, count + 1):
        f, e = _day_bundle(source_id, n, 1)
        files.update(f)
        entries.append(e)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    transport = _SlowTransport(files, clock, cost)

    def make():
        return CollectorBundleAdapter(
            connector_id="conn-y", raw_root=str(tmp_path / "store"), transport=transport, host=HOST,
            source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
            max_records=500, cadence_seconds=cadence,
        )

    return make, clock


@pytest.mark.parametrize("cadence", [0, 1800])
def test_many_singleton_bundles_stay_within_budget_and_drain_without_loss(tmp_path, monkeypatch, cadence):
    make, clock = _singleton_bundles(tmp_path, monkeypatch, cadence=cadence)
    cursor, seen, elapsed = None, [], []
    for _ in range(20):
        start = clock[0]
        result = make().pull(cursor=cursor)
        elapsed.append(clock[0] - start)
        seen.extend(r.source_id for r in result.records)
        cursor = result.next_cursor
        if result.complete:
            break
        assert not result.complete
    assert max(elapsed) <= 15.0 + 5.0 and len(elapsed) > 1
    # priority-latest replays (idempotent, state saved by the persistence owner) may repeat ids
    assert result.complete and len(set(seen)) == 12 and (cadence or len(seen) == 12)


def test_http_read_timeout_is_capped_by_remaining_operation_budget(monkeypatch):
    from types import SimpleNamespace

    import services.source_ingestion.connectors.collector_bundle as cb

    clock, timeouts = [100.0], []
    monkeypatch.setattr(cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "tok")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_open(request, *, caller, timeout, **_kw):
        timeouts.append(timeout)
        return _Resp()

    monkeypatch.setattr(cb, "open_external_url", fake_open)
    monkeypatch.setattr(cb, "read_bounded_response", lambda r, max_bytes: b"{}")
    transport = cb.HttpBundleTransport(host=HOST)
    with cb.operation_deadline(15.0):
        transport.read("a", max_bytes=10)
        clock[0] += 14.0
        transport.read("b", max_bytes=10)  # 1s left: must not get the full 20s
        clock[0] += 5.0
        transport.read("c", max_bytes=10)  # budget spent: 1s floor
    transport.read("d", max_bytes=10)  # outside an operation: unchanged
    assert timeouts == [15.0, 1.0, 1.0, 20.0]


def test_body_stream_stops_at_operation_deadline_not_returned_as_data(monkeypatch):
    """Slow-drip body: four 10s chunks under a 15s budget must fail, never return data."""
    from services.source_ingestion.connectors import collector_bundle as cb
    from services.source_ingestion.connectors.bounded_reader import read_bounded_response

    now = [0.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])

    class _Slow:
        headers: dict = {}

        def read(self, _n):
            now[0] += 10.0
            return b"x"

    with pytest.raises(TimeoutError):
        read_bounded_response(cb._DeadlineBody(_Slow(), 15.0), max_bytes=100)
    assert now[0] <= 20.0


def test_request_scope_deadline_is_shared_across_serial_pulls(monkeypatch):
    """A request-level deadline is not reset by each pull's own read budget."""
    from services.source_ingestion.connectors import collector_bundle as cb

    now = [100.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: now[0])
    seen = []
    adapter = cb.CollectorBundleAdapter.__new__(cb.CollectorBundleAdapter)
    object.__setattr__(adapter, "read_budget_seconds", 15.0)
    monkeypatch.setattr(
        cb.CollectorBundleAdapter, "_pull_bounded",
        lambda self, **kw: (seen.append(cb._OP_DEADLINE.get()), now.__setitem__(0, now[0] + 10.0)),
    )
    with cb.operation_deadline(15.0):
        adapter.pull()
        adapter.pull()
    assert seen == [115.0, 115.0]


def _trickle_response(payload: bytes, clock: list, step: float, chunk: int = 1024):
    """Real http.client.HTTPResponse over a buffered raw stream; each receive costs `step` fake seconds."""
    import http.client
    import io

    head = f"HTTP/1.1 200 OK\r\nContent-Length: {len(payload)}\r\n\r\n".encode()

    class _Raw(io.RawIOBase):
        def __init__(self):
            self.header, self.pos, self.receives = head, 0, 0

        def readable(self):
            return True

        def readinto(self, buf):
            if self.header:
                n = min(len(buf), len(self.header))
                buf[:n] = self.header[:n]
                self.header = self.header[n:]
                return n
            n = min(len(buf), chunk, len(payload) - self.pos)
            if n <= 0:
                return 0
            buf[:n] = payload[self.pos:self.pos + n]
            self.pos += n
            self.receives += 1
            clock[0] += step
            return n

    raw = _Raw()

    class _Sock:
        def makefile(self, mode):
            return io.BufferedReader(raw)

    response = http.client.HTTPResponse(_Sock())
    response.begin()
    return response, raw


def test_real_http_body_deadline_enforced_between_socket_receives(monkeypatch):
    """40 receives of 1 fake second each (every one inside the idle timeout) must stop at the 15s budget."""
    from types import SimpleNamespace
    from services.source_ingestion.connectors import collector_bundle as cb

    clock = [0.0]
    response, raw = _trickle_response(b"x" * (40 * 1024), clock, 1.0)
    monkeypatch.setattr(cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(cb, "open_external_url", lambda request, *, caller, timeout, **_kw: response)
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "fixture-only")
    with pytest.raises(TimeoutError):
        with cb.operation_deadline(15.0):
            cb.HttpBundleTransport(host=HOST).read("raw/body", max_bytes=100000)
    assert clock[0] <= 16.0
    assert raw.receives < 40


def test_real_http_body_within_budget_returns_exact_bytes(monkeypatch):
    from types import SimpleNamespace
    from services.source_ingestion.connectors import collector_bundle as cb

    clock = [0.0]
    payload = bytes(range(256)) * 16
    response, _raw = _trickle_response(payload, clock, 1.0)
    monkeypatch.setattr(cb, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(cb, "open_external_url", lambda request, *, caller, timeout, **_kw: response)
    monkeypatch.setenv(cb.COLLECTOR_BUNDLE_TOKEN_ENV, "fixture-only")
    with cb.operation_deadline(15.0):
        assert cb.HttpBundleTransport(host=HOST).read("raw/body", max_bytes=100000) == payload


# COLLECTOR-CLOSE-CONTINUATION-20261113: held symbols beyond the first bounded prefix ----
def _held_session(tmp_path, *, n_rows=600, held_pos=(531, 538), tamper=None):
    """Newest-session bundle shaped like the captured manifest (``twse-price-history-<date>-<code>``)."""
    source_id = "collector-tw-twse-price-history"
    files, entries = _day_bundle(source_id, 3, n_rows)
    base = f"bundles/{source_id}/{entries['date']}/{entries['run_id']}"
    manifest = json.loads(files[f"{base}/manifest.json"])
    rows = [json.loads(line) for line in files[f"{base}/items.jsonl"].decode().splitlines()]
    codes = {pos: code for pos, code in zip(held_pos, ("2317", "2330"))}
    for i, (m, r) in enumerate(zip(manifest["items"], rows)):
        code = codes.get(i, f"{1000 + i}")
        m["item_id"] = r["id"] = f"twse-price-history-{entries['date']}-{code}"
        r["symbol"] = code
    manifest["bundle_sha256"] = compute_bundle_sha256([m["content_sha256"] for m in manifest["items"]])
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
    files["bundles/index.json"] = json.dumps({"bundles": [entries]}).encode()
    if tamper:
        sha = next(m["content_sha256"] for m in manifest["items"] if m["item_id"].endswith(tamper))
        files[f"{base}/raw/{sha}.html"] = b"tampered"

    def make():
        return CollectorBundleAdapter(
            connector_id="conn-held", raw_root=str(tmp_path / "store"), transport=FakeTransport(files), host=HOST,
            source_id=source_id, source_type="market", attribution_required=True, dataset="tw_price_daily",
            max_records=55, cadence_seconds=1800,
        )
    return make


def _symbols(result):
    return [r.metadata["normalized_row"]["symbol"] for r in result.records]


def test_close_reads_overlap_under_one_deadline_and_drain_before_return(tmp_path):
    import threading
    from services.source_ingestion.connectors import collector_bundle as cb

    adapter = replace(_held_session(tmp_path, n_rows=8)(), max_records=8)
    barrier = threading.Barrier(4, timeout=2)
    lock = threading.Lock()
    active, peak, deadlines = [0], [0], []

    class OverlappingTransport(FakeTransport):
        def read(self, relative_path, *, max_bytes):
            if "/raw/" not in relative_path:
                return super().read(relative_path, max_bytes=max_bytes)
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
                deadlines.append(cb._OP_DEADLINE.get())
            try:
                assert max_bytes == cb.MAX_RAW_BYTES
                assert cb._DEFER_ACK.get() is True
                barrier.wait()
                return super().read(relative_path, max_bytes=max_bytes)
            finally:
                with lock:
                    active[0] -= 1

    adapter = replace(adapter, transport=OverlappingTransport(adapter.transport.files))
    with cb.deferred_collector_acknowledgement():
        result = adapter.pull()
    assert result.priority_done and len(result.records) == 8
    assert peak[0] == 4 and active[0] == 0
    assert len(set(deadlines)) == 1 and deadlines[0] is not None
    assert not adapter._priority_key_path().exists()
    result.delivery.validate(result.records)
    result.delivery.acknowledge()
    assert adapter._load_priority_state()[1:3] == (8, True)


@pytest.mark.parametrize("bad_hash", [False, True])
def test_parallel_close_deadline_preserves_prefix_but_never_hides_bad_hash(tmp_path, monkeypatch, bad_hash):
    import threading
    import types
    from services.source_ingestion.connectors import collector_bundle as cb

    adapter = replace(_held_session(tmp_path, n_rows=8)(), max_records=8, read_budget_seconds=1)
    paths = [p for p in adapter.transport.files if "/raw/" in p]
    barrier = threading.Barrier(4, timeout=2)
    clock = [0.0]
    monkeypatch.setattr(cb, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))

    class CutTransport(FakeTransport):
        def read(self, relative_path, *, max_bytes):
            if "/raw/" in relative_path:
                barrier.wait()
                clock[0] = 2.0
                if relative_path == paths[1]:
                    raise TimeoutError("shared operation deadline")
                if bad_hash and relative_path == paths[3]:
                    return b"bad hash after the timeout gap"
            return super().read(relative_path, max_bytes=max_bytes)

    cut = replace(adapter, transport=CutTransport(adapter.transport.files))
    with cb.deferred_collector_acknowledgement():
        if bad_hash:
            with pytest.raises(SourceEvidenceError, match="hash mismatch"):
                cut.pull()
            assert not cut._priority_key_path().exists()
            return
        result = cut.pull()
    assert len(result.records) == 1 and result.priority_delivered == 1 and not result.priority_done
    assert not cut._priority_key_path().exists()
    result.delivery.validate(result.records)
    result.delivery.acknowledge()
    assert cut._load_priority_state()[1:3] == (1, False)
    # Restart from durable prefix, re-reading both the hole and any later prefetch.
    clock[0] = 0
    with cb.deferred_collector_acknowledgement():
        retry = adapter.pull(max_records=7)
    assert len(retry.records) == 7 and retry.priority_done
    assert {r.source_id for r in result.records}.isdisjoint(r.source_id for r in retry.records)
    retry.delivery.validate(retry.records)
    retry.delivery.acknowledge()
    assert adapter._load_priority_state()[1:3] == (8, True)


def test_held_symbols_beyond_first_prefix_are_delivered_in_the_first_bounded_pull(tmp_path):
    make = _held_session(tmp_path)
    # old behaviour: manifest order, 55 rows, held positions 531/538 wait ~10 pulls
    plain = make().pull()
    assert "2330" not in _symbols(plain) and "2317" not in _symbols(plain)
    first = make().pull(priority_symbols=("2330.TWSE", "2317.TWSE"))
    assert len(first.records) == 55 and not first.priority_done
    assert _symbols(first)[:2] == ["2317", "2330"]  # held first, manifest order among them
    assert first.priority_held == ("2317", "2330")
    # selected hash/size and priority provenance are intact
    close = first.records[0].metadata["collector_close"]
    assert close["provisional"] is True and close["official"] is False


def test_held_first_order_continues_across_ticks_without_gaps(tmp_path):
    make = _held_session(tmp_path)
    seen, done = [], False
    for _ in range(20):
        adapter = make()
        result = adapter.pull(cursor=adapter.load_resume_cursor(), priority_symbols=("2330", "2317"))
        seen.extend(r.source_id for r in result.records if r.metadata["trade_date"] == "2024-11-03")
        adapter.save_resume_cursor(result)
        if result.priority_done:
            done = True
            break
    assert done and len(seen) == len(set(seen)) == 600


def test_held_set_change_preserves_progress_and_prioritizes_undelivered_holdings(tmp_path):
    make = _held_session(tmp_path)
    adapter = make()
    first = adapter.pull(priority_symbols=("2330",))
    adapter.save_resume_cursor(first)
    adapter = make()
    second = adapter.pull(cursor=adapter.load_resume_cursor(), priority_symbols=("2330", "2317"))
    assert second.priority_delivered == 110 and _symbols(second)[0] == "2317"
    assert _symbols(first)[0] == "2330"
    expected = replace(_held_session(tmp_path / "reference")(), max_records=600).pull(
        priority_symbols=("2330", "2317"))
    identity = lambda r: (r.source_id, r.metadata["content_hash"])
    seen = [identity(r) for r in first.records + second.records]
    assert len(seen) == len(set(seen)) == 110
    result = second
    for _ in range(10):
        adapter.save_resume_cursor(result)
        if result.priority_done:
            break
        adapter = make()
        result = adapter.pull(cursor=adapter.load_resume_cursor(), priority_symbols=("2330", "2317"))
        seen.extend(identity(r) for r in result.records)
    assert result.priority_done and result.priority_delivered == 600
    assert len(seen) == len(set(seen)) == 600
    assert set(seen) == {identity(r) for r in expected.records}


def test_deferred_ack_does_not_persist_held_progress_before_persistence(tmp_path):
    from services.source_ingestion.connectors.collector_bundle import deferred_collector_acknowledgement

    make = _held_session(tmp_path)
    adapter = make()
    with deferred_collector_acknowledgement():
        result = adapter.pull(priority_symbols=("2330",))
    assert not adapter._priority_key_path().exists()
    result.delivery.acknowledge()
    assert json.loads(adapter._priority_key_path().read_text())["held"] == ["2330"]


def test_tampered_held_raw_fails_truthfully_and_acknowledges_nothing(tmp_path):
    make = _held_session(tmp_path, tamper="-2330")
    adapter = make()
    with pytest.raises(SourceEvidenceError):
        adapter.pull(priority_symbols=("2330",))
    assert not adapter._priority_key_path().exists()


def test_provider_handler_uses_active_paper_symbol_environment(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import _collector_bundle

    # Same configured-ingestion handler path; the adapter itself reads the existing env identity.
    monkeypatch.setenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", "2330.TWSE,2317.TWSE")
    result = _collector_bundle(_held_session(tmp_path)(), {"_effective_max_records": 55}, "trace")
    assert {"2330", "2317"} <= set(_symbols(result))
    monkeypatch.delenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS")
    result = _collector_bundle(_held_session(tmp_path / "b")(), {"_effective_max_records": 55}, "trace")
    assert "2330" not in _symbols(result)


def test_provider_handler_forwards_request_priority_symbols_without_environment(tmp_path, monkeypatch):
    from services.source_ingestion.provider_adapters import _collector_bundle

    monkeypatch.delenv("SOURCE_INGEST_ACTIVE_PAPER_SYMBOLS", raising=False)
    request = {"_effective_max_records": 55, "priority_symbols": ["2330.TWSE", "2317.TWSE"]}
    result = _collector_bundle(_held_session(tmp_path)(), request, "trace")
    assert {"2330", "2317"} <= set(_symbols(result))


def test_partial_session_and_history_continue_until_both_are_finished(tmp_path):
    make = _held_session(tmp_path)
    first = make().pull(priority_symbols=("2330.TWSE",))
    assert not first.priority_done and first.observed.get("continuation_pending") is True
    result = first
    for _ in range(60):
        if result.complete:
            break
        make().save_resume_cursor(result)
        result = make().pull(cursor=result.next_cursor, priority_symbols=("2330.TWSE",))
    assert result.complete and "continuation_pending" not in result.observed


# Real-size host intake fixtures: per-date backfill run ids, recorded row/raw JSON shape.
_INTAKE_SIZES = (("collector-tw-twse-price-history", 1375, "tw_price_daily", "TWSE", 1000, 600),
                 ("collector-tw-tpex-price-history", 6000, "tw_price_daily", "TPEX", 1000, 600),
                 ("collector-tw-twse-t86", 1349, "tw_institutional_flow", "TWSE", 500, 86400))


def _intake_bundle(source, count, dataset, exchange, trade_date, *, newest=False):
    fixture = ("twse_t86_20261110.json" if dataset == "tw_institutional_flow" else
               "tpex_daily_quotes_20261110.json" if exchange == "TPEX" else "twse_mi_index_20261110.json")
    captured = json.loads((Path(__file__).resolve().parents[3] / "deploy/collector/tests/fixtures" / fixture).read_text())
    table = captured.get("tables", [captured])[0]
    run = f"{'close' if newest else 'backfill'}-{trade_date}"
    date = "2026-11-13" if newest else "2026-11-11"
    finished = f"{date}T12:00:00Z"
    base = f"bundles/{source}/{date}/{run}"
    files, items, rows = {}, [], []
    for n in range(count):
        symbol = f"{10000+n}"
        normalized = {**_TWSE_ROW, "dataset": dataset, "exchange": exchange, "symbol": symbol, "trade_date": trade_date}
        if dataset == "tw_institutional_flow":
            normalized = {k: normalized[k] for k in ("dataset", "exchange", "symbol", "security_name", "trade_date")}
            normalized.update(foreign_net_shares=6500000, investment_trust_net_shares=900000,
                              dealer_net_shares=450000, total_institutional_net_shares=7850000)
        data = list(table["data"][0]); data[0] = symbol
        body = json.dumps({"date": trade_date.replace("-", ""), "fields": table["fields"],
                           "data": [data], "record": normalized}, ensure_ascii=False, sort_keys=True).encode()
        sha = hashlib.sha256(body).hexdigest()
        item_id = f"{source}-{trade_date}-{symbol}"
        url = ("https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes" if exchange == "TPEX" else
               "https://www.twse.com.tw/fund/T86" if dataset == "tw_institutional_flow" else
               "https://www.twse.com.tw/exchangeReport/MI_INDEX")
        rows.append({"id": item_id, "url": url, "fetched_at": finished, **normalized})
        items.append({"item_id": item_id, "url": url, "http_status": 200,
                      "content_sha256": sha, "bytes": len(body), "raw_path": f"raw/{sha}.json"})
        files[f"{base}/raw/{sha}.json"] = body
    manifest = {"schema_version": "collector_bundle.v1", "source_id": source, "run_id": run,
                "collector_version": "1.0.0", "started_at": finished, "finished_at": finished,
                "fetch_policy_hash": "fixture", "robots_decision": "allowed", "terms_ref": "https://example.test/terms",
                "attribution": exchange, "status": "complete", "items": items,
                "bundle_sha256": compute_bundle_sha256([m["content_sha256"] for m in items])}
    files[f"{base}/manifest.json"] = json.dumps(manifest).encode()
    files[f"{base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rows).encode()
    return files, {"source_id": source, "date": date, "run_id": run, "finished_at": finished}


def _measure_real_size_sweep(tmp_path, monkeypatch, spec):
    import threading
    import types
    import time as real_time
    from services.source_ingestion.connectors import collector_bundle as cb

    source, count, dataset, exchange, cap, cadence = spec
    files, entries = {}, []
    for day, newest in (("2024-11-12", False), ("2024-11-13", False), ("2026-11-13", True)):
        f, entry = _intake_bundle(source, count, dataset, exchange, day, newest=newest)
        files.update(f); entries.append(entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {source: {"status": "ok", "last_run_at": entries[-1]["finished_at"],
                                                          "cadence_seconds": 86400}}}).encode()
    now, active, peak, raw_reads = [0.0], [0], [0], [0]
    lock = threading.Lock()

    class Transport(FakeTransport):
        def read(self, path, *, max_bytes):
            with lock:
                start = now[0]
                active[0] += 1; peak[0] = max(peak[0], active[0])
            try:
                real_time.sleep(0.005)  # allow all sixteen readers to enter before charging 270ms
                return super().read(path, max_bytes=max_bytes)
            finally:
                with lock:
                    now[0] = max(now[0], start + 0.27)
                    active[0] -= 1
                    raw_reads[0] += int("/raw/" in path)

    monkeypatch.setattr(cb, "time", types.SimpleNamespace(monotonic=lambda: now[0]))
    adapter = CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market", dataset=dataset,
                                     max_records=cap, cadence_seconds=cadence, attribution_required=True,
                                     transport=Transport(files), raw_root=str(tmp_path / "store"))
    cursor, seen, durations, batches = None, set(), [], []
    startup_seconds, history_seconds, history_rows = 0.0, 0.0, 0
    startup_wall, history_wall = 0.0, 0.0
    wall_start = real_time.perf_counter()
    for _ in range(150):
        start, wall_pull = now[0], real_time.perf_counter()
        result = adapter.pull(cursor=cursor)
        wall_pull = real_time.perf_counter() - wall_pull
        if result.priority_key:
            startup_seconds += now[0] - start + 1
            startup_wall += wall_pull
        else:
            history_seconds += now[0] - start + 1
            history_wall += wall_pull
            history_rows += len(result.records)
        durations.append(now[0] - start); batches.append(len(result.records))
        if dataset == "tw_price_daily" and not seen:
            assert {r.metadata["trade_date"] for r in result.records} == {"2026-11-13"}
        seen.update((r.metadata["trade_date"], r.metadata["symbol"]) for r in result.records)
        cursor = result.next_cursor
        adapter.save_resume_cursor(result)
        assert active[0] == 0
        if result.complete:
            assert "continuation_pending" not in result.observed
            break
        assert result.observed["continuation_pending"] is True
    assert result.complete and len(seen) == count * 3
    assert "|items=" not in cursor and cursor.endswith("#close-2026-11-13")
    assert max(durations) < 17  # one 15-second pull, small metadata/read floor overhead
    assert 1 < peak[0] <= cb.HISTORY_RAW_READ_CONCURRENCY
    # Allow two newest sessions during 48 hours. Scale the measured ordered
    # row rate (including metadata, newest replay and mixed-bundle overhead),
    # rather than multiplying a cold newest-session startup 488 times.
    extrapolated = 2*(startup_seconds + startup_wall) + (history_seconds + history_wall)*488*count/history_rows
    assert extrapolated < 48 * 3600
    print("INTAKE_RATE " + json.dumps({"source": source, "days": 3, "unique_rows": len(seen),
          "pulls": len(batches), "rows_per_pull": batches, "model_seconds": sum(durations),
          "max_pull_seconds": max(durations), "wall_seconds": real_time.perf_counter() - wall_start,
          "raw_reads": raw_reads[0], "peak_inflight": peak[0], "startup_model_seconds_with_pauses": startup_seconds,
          "startup_wall_seconds": startup_wall, "history_model_seconds_with_pauses": history_seconds,
          "history_wall_seconds": history_wall, "history_rows": history_rows,
          "extrapolated_488_days_seconds": extrapolated}))
    return extrapolated


def test_real_size_two_day_sweep_continues_and_preserves_newest_priority(tmp_path, monkeypatch):
    # Steady pulls share one controller slot: sum all sources, never claim that
    # three individually sub-48h sweeps necessarily fit the shared 48h budget.
    total = sum(_measure_real_size_sweep(tmp_path / spec[0], monkeypatch, spec) for spec in _INTAKE_SIZES)
    assert total + 600 < 48*3600  # include one worst-phase source-admission delay


def test_market_partial_producer_bundle_does_not_retry_hot(tmp_path):
    source, count, dataset, exchange, cap, cadence = _INTAKE_SIZES[2]
    files, entry = _intake_bundle(source, 3, dataset, exchange, "2024-11-12")
    key = next(k for k in files if k.endswith("manifest.json"))
    doc = json.loads(files[key]); doc["status"] = "partial"; files[key] = json.dumps(doc).encode()
    result = adapter_for(files, [entry], tmp_path, source_id=source, source_type="market", dataset=dataset).pull()
    assert not result.complete and "continuation_pending" not in result.observed


def test_bee62bc29_reader_parses_new_cursor_status_and_additive_readback(tmp_path):
    """Run the actual rollback reader when the repository retains the release object."""
    import importlib.util
    import subprocess
    import sys
    result = subprocess.run(["git", "show", "bee62bc29:services/source_ingestion/connectors/collector_bundle.py"],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        pytest.skip("rollback release object unavailable in shallow clone; execute with full release history")
    path = tmp_path / "rollback.py"; path.write_text(result.stdout)
    name = "services.source_ingestion.connectors.rollback_reader"
    spec = importlib.util.spec_from_file_location(name, path)
    legacy = importlib.util.module_from_spec(spec); sys.modules[name] = legacy
    try:
        spec.loader.exec_module(legacy)
        source, _, dataset, exchange, _, cadence = _INTAKE_SIZES[0]
        files, entry = _intake_bundle(source, 5, dataset, exchange, "2024-11-12")
        latest_files, latest = _intake_bundle(source, 2, dataset, exchange, "2024-11-13")
        files.update(latest_files)
        files["status.json"] = json.dumps({"sources": {source: {
            "status": "ok", "last_run_at": entry["finished_at"], "cadence_seconds": 86400,
            "offered": False, "offered_reason": "no_timer", "timer_enabled": False}}}).encode()
        adapter = adapter_for(files, [entry, latest], tmp_path, source_id=source, source_type="market", dataset=dataset,
                              max_records=2, cadence_seconds=cadence)
        pull = adapter.pull(); adapter.save_resume_cursor(pull)
        # Drain priority, leaving the normal backward-compatible partial sweep cursor.
        for _ in range(4):
            if pull.next_cursor:
                break
            pull = adapter.pull(cursor=pull.next_cursor); adapter.save_resume_cursor(pull)
        assert pull.next_cursor and "|items=" in pull.next_cursor
        assert {r.metadata["trade_date"] for r in pull.records} == {"2024-11-12"}
        old = legacy.CollectorBundleAdapter(connector_id=adapter.connector_id, source_id=source,
                                           source_type="market", dataset=dataset, raw_root=str(tmp_path / "store"))
        assert old.load_resume_cursor() == pull.next_cursor
        old_observed = legacy.observed_from_collector_status(
            json.loads(files["status.json"]), source_id=source, cursor=pull.next_cursor, cadence_seconds=86400,
            now=datetime(2026, 11, 11, 13, tzinfo=timezone.utc))
        assert old_observed["health_state"] == "healthy"
        # bee62bc29 predates latest_priority.json and ignores that sidecar. The format
        # of its cursor and status readers is unchanged; new readback is additive.
        assert adapter._priority_key_path().is_file()
    finally:
        sys.modules.pop(name, None)


@pytest.mark.parametrize("spec", _INTAKE_SIZES, ids=[s[0] for s in _INTAKE_SIZES])
def test_candidate_and_current_real_size_ordered_pull_measurement(tmp_path, monkeypatch, spec):
    """AC1 ordered-sweep comparison after the separately acknowledged latest session."""
    import importlib.util
    import subprocess
    import sys
    import threading
    import time as real_time
    import types
    from services.source_ingestion.connectors import collector_bundle as cb
    source_text = subprocess.run(["git", "show", "3ed0ea555:services/source_ingestion/connectors/collector_bundle.py"],
                                 capture_output=True, text=True, timeout=10)
    if source_text.returncode:
        pytest.skip("candidate object unavailable in shallow clone; use full release history")
    path = tmp_path / "candidate.py"; path.write_text(source_text.stdout)
    name = "services.source_ingestion.connectors.candidate_measurement"
    loader = importlib.util.spec_from_file_location(name, path)
    candidate = importlib.util.module_from_spec(loader); sys.modules[name] = candidate
    loader.loader.exec_module(candidate)
    source, count, dataset, exchange, cap, cadence = spec
    files, entry = _intake_bundle(source, count, dataset, exchange, "2024-11-12")
    entries = [entry]
    if dataset == "tw_price_daily":
        latest_files, latest = _intake_bundle(source, 2, dataset, exchange, "2024-11-13")
        files.update(latest_files)
        entries.append(latest)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode()
    files["status.json"] = json.dumps({"sources": {source: {"status": "ok", "last_run_at": entry["finished_at"]}}}).encode()
    measurements = []
    try:
        for label, module in (("3ed0ea555", candidate), ("current", cb)):
            clock, reads = [0.0], [0]
            lock = threading.Lock()
            class Transport(FakeTransport):
                def read(self, path, *, max_bytes):
                    with lock:
                        start = clock[0]
                    if "/raw/" in path:
                        real_time.sleep(0.005)  # permits all sixteen reads to overlap, 270ms model cost
                    try:
                        return super().read(path, max_bytes=max_bytes)
                    finally:
                        with lock:
                            clock[0] = max(clock[0], start + 0.27)
                            reads[0] += int("/raw/" in path)
            monkeypatch.setattr(module, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
            adapter = module.CollectorBundleAdapter(connector_id=source, source_id=source, source_type="market",
                        dataset=dataset, attribution_required=True, max_records=cap, cadence_seconds=cadence,
                        raw_root=str(tmp_path / label), transport=Transport(files))
            if dataset == "tw_price_daily":
                priority = adapter._priority_key_path()
                priority.parent.mkdir(parents=True, exist_ok=True)
                priority.write_text(json.dumps({"source_id": source, "key": latest["finished_at"] + "#" + latest["run_id"],
                                                "delivered": 2, "done": True, "held": []}))
            start = real_time.perf_counter(); result = adapter.pull()
            measurements.append({"version": label, "source": source, "bundle_rows": count,
                                 "rows": len(result.records), "raw_reads": reads[0], "wall_seconds": real_time.perf_counter()-start,
                                 "model_seconds": clock[0], "cap": cap, "cadence_seconds": cadence,
                                 "continuation_pending": result.observed.get("continuation_pending", False)})
            assert len(result.records) > 0 and clock[0] < 16
            assert {r.metadata["trade_date"] for r in result.records} == {"2024-11-12"}
            assert len({(r.source_id, r.metadata["content_hash"]) for r in result.records}) == len(result.records)
        assert measurements[0]["rows"] < cap  # 15 seconds binds the candidate
        assert measurements[1]["rows"] > measurements[0]["rows"]
        assert measurements[1]["rows"] <= cap  # cap and shared deadline both remain binding
        if dataset == "tw_institutional_flow":
            assert measurements[0]["continuation_pending"] is False
            assert measurements[1]["continuation_pending"] is True
        print("INTAKE_ORDERED_PULL " + json.dumps(measurements))
    finally:
        sys.modules.pop(name, None)


def test_capped_history_maps_only_verified_window_and_retains_partial_cursor(tmp_path, monkeypatch):
    source, count, dataset, exchange, cap, cadence = _INTAKE_SIZES[1]
    files, entry = _intake_bundle(source, count, dataset, exchange, "2024-11-12")
    adapter = adapter_for(files, [entry], tmp_path, source_id=source, source_type="market", dataset=dataset,
                          max_records=cap, cadence_seconds=86400)  # ordered path
    mapped = []
    record = CollectorBundleAdapter._record
    def count_record(self, bundle, row, **kw):
        mapped.append(row["id"])
        assert bundle.manifest_items[row["id"]]["content_sha256"] in bundle.raw_bytes
        return record(self, bundle, row, **kw)
    monkeypatch.setattr(CollectorBundleAdapter, "_record", count_record)
    first = adapter.pull()
    assert len(mapped) == len(first.records) == cap
    assert first.next_cursor.endswith("|items=1000") and not first.complete
    second = adapter.pull(cursor=first.next_cursor)
    assert len(mapped) == 2*cap and second.next_cursor.endswith("|items=2000")
    assert {r.source_id for r in first.records}.isdisjoint(r.source_id for r in second.records)


# REGIME-DATA-INDEX-BREADTH-INTAKE-20261114: real collector bundles -> production pull -> governed datasets ----------
_REGIME_SOURCES = ("collector-tw-twse-index-breadth", "collector-tw-twse-taiex-history")


def regime_collector_files(tmp_path: Path) -> dict[str, bytes]:
    """Run the real collector spiders over the recorded official TWSE responses; return the bundle tree."""
    import sys

    repo = Path(__file__).resolve().parents[3]
    fixtures = repo / "deploy/collector/tests/fixtures"
    sys.path.insert(0, str(repo / "deploy/collector"))
    try:
        from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
        from collector_stack.fetchers import FixtureTransport
        from collector_stack.privacy import AuthorHasher
        from collector_stack.runner import CollectorRunner
        from collector_stack.spiders import TwseIndexBreadthSpider, TwseTaiexHistorySpider
    finally:
        sys.path.remove(str(repo / "deploy/collector"))
    transport = FixtureTransport()
    transport.register(TwseIndexBreadthSpider.build_date_url("2026-11-10"),
                       (fixtures / "twse_mi_index_full_20261110.json").read_bytes())
    transport.register(TwseTaiexHistorySpider.build_date_url("2012-01"),
                       (fixtures / "twse_mi_5mins_hist_201201.json").read_bytes())
    tmp_path.mkdir(parents=True, exist_ok=True)
    salt = tmp_path / "salt"
    salt.write_bytes(b"regime-intake-test-salt-0123456789abcdef-0123456789")
    root = tmp_path / "collector_root"
    runner = CollectorRunner(load_collector_config(DEFAULT_POLICY_PATH, env={}), root_dir=root,
                             hasher=AuthorHasher.from_file(salt), transport=transport)
    assert runner.run_source("collector-tw-twse-index-breadth", run_id="close-2026-11-10",
                             collect_kwargs={"dates": ["2026-11-10"]}).status == "complete"
    assert runner.run_source("collector-tw-twse-taiex-history", run_id="backfill-2012-01",
                             collect_kwargs={"dates": ["2012-01"]}).status == "complete"
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def pull_regime_records(files: dict[str, bytes], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, source_id: str):
    """Production pull entry point (execute_provider_owned_adapter) over a collector bundle tree."""
    from services.source_ingestion.connector_definitions import collector_host
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    class _Transport(BundleTransport):
        def read(self, relative_path: str, *, max_bytes: int) -> bytes:
            return files[relative_path]

    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: _Transport())
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(tmp_path / "raw_store"))
    definition = get_connector_definition(source_id)
    config = {"source_id": source_id, "source_type": "market", "dataset": definition.datasets[0],
              "attribution_required": True, "max_records": 200}
    adapter = CollectorBundleAdapter(connector_id=source_id, **config)
    return adapter, execute_provider_owned_adapter(
        connector=adapter.connector(),
        fetch={"adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN, "adapter_config": {**config, "host": collector_host()},
               "request": {}, "max_records": 200},
        trace_id="regime-intake",
    )


def test_regime_sources_are_registered_definitions_and_admitted():
    desired = json.loads((Path(__file__).resolve().parents[1] / "default_desired_state.json").read_text())
    admitted = {(r["dataset"], c) for p in desired["personas"] for r in p["required_data_sources"]
                for c in r["connector_candidates"]}
    for source_id in _REGIME_SOURCES:
        definition = get_connector_definition(source_id)
        assert definition.adapter_token == COLLECTOR_BUNDLE_ADAPTER_TOKEN
        assert definition.datasets[0] == "tw_index_daily"
        assert definition.metadata["collector"]["source_id"] == source_id
        assert ("tw_index_daily", source_id) in admitted
    # One connector can prove one persona requirement; the breadth dataset rides the index-breadth connector.
    assert "tw_market_breadth_daily" in get_connector_definition(_REGIME_SOURCES[0]).datasets
    assert not set(_REGIME_SOURCES) & set(desired["collector_ingestion_exclusions"])


def test_production_pull_of_real_collector_bundles_stores_index_and_breadth_datasets(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from services.source_ingestion.market_data_storage import MarketDataStorageWriter, measure_stored_history_depth

    files = regime_collector_files(tmp_path / "real")
    _, index_records = pull_regime_records(files, tmp_path, monkeypatch, "collector-tw-twse-index-breadth")
    _, history_records = pull_regime_records(files, tmp_path, monkeypatch, "collector-tw-twse-taiex-history")

    by_dataset: dict[str, list] = {}
    for record in (*index_records, *history_records):
        by_dataset.setdefault(record.metadata["dataset"], []).append(record)
    assert {k: len(v) for k, v in by_dataset.items()} == {"tw_index_daily": 56 + 15, "tw_market_breadth_daily": 1}

    taiex = next(r for r in index_records if r.metadata["normalized_row"]["series_id"] == "發行量加權股價指數")
    row = taiex.metadata["normalized_row"]
    assert (row["close"], row["date"], row["change_points"], row["change_percent"]) == (42711.92, "2026-11-10", 1050.22, 2.52)
    assert "open" not in row  # not in the MI_INDEX table; never filled in
    assert all(r.metadata["normalized_row"]["close"] > 0 and r.metadata["normalized_row"]["series_id"]
               for r in by_dataset["tw_index_daily"])

    # The 2012 monthly history is in the same dataset, with open/high/low, and keeps the source's own values.
    history = {r.metadata["normalized_row"]["date"]: r.metadata["normalized_row"] for r in history_records}
    assert min(history) == "2012-01-02" and len(history) == 15
    first = history["2012-01-02"]
    assert (first["open"], first["high"], first["low"], first["close"]) == (7071.35, 7073.48, 6948.67, 6952.21)
    assert first["dataset"] == "tw_index_daily" and first["source_collector_dataset"] == "tw_index_ohlc_daily"
    assert first["source_dataset"] == "MI_5MINS_HIST"

    breadth = by_dataset["tw_market_breadth_daily"][0].metadata["normalized_row"]
    for group in ("whole_market", "stocks"):
        for key, value in breadth[group].items():
            assert breadth[f"{group}_{key}"] == value  # flat catalogue field equals the source count
    assert breadth["whole_market_advancers"] >= breadth["whole_market_limit_up"]

    store = tmp_path / "store"
    for source_id, records in (("collector-tw-twse-index-breadth", index_records),
                               ("collector-tw-twse-taiex-history", history_records)):
        adapter, _ = pull_regime_records(files, tmp_path, monkeypatch, source_id)
        MarketDataStorageWriter(store).write_run(
            result=SimpleNamespace(records=list(records), run=SimpleNamespace(ingest_run_id=f"run-{source_id}")),
            connector=adapter.connector())
    assert not (store / "normalized" / "tw_price_daily").exists()
    depth = measure_stored_history_depth(store)["datasets"]
    assert depth["tw_index_daily"]["first_event_date"] == "2012-01-02"
    assert depth["tw_index_daily"]["last_event_date"] == "2026-11-10"
    assert depth["tw_index_daily"]["entity_count"] >= 56
    assert depth["tw_market_breadth_daily"]["row_count"] == 1
    assert depth["tw_price_daily"]["row_count"] == 0


def test_regime_rows_fail_closed_on_missing_key_close_or_breadth_counts():
    mapper = CollectorBundleAdapter._regime_market_row
    good = {"dataset": "tw_index_daily", "trade_date": "2026-11-10", "index_name": "X", "close": 1.0}
    assert mapper("i", good, {})["series_id"] == "X"
    for bad in ({**good, "index_name": ""}, {**good, "close": None}, {**good, "close": 0}, {**good, "trade_date": ""}):
        with pytest.raises(SourceEvidenceError):
            mapper("i", bad, {})
    breadth = {"dataset": "tw_market_breadth_daily", "trade_date": "2026-11-10",
               "whole_market": {"advancers": 1}, "stocks": {}}
    with pytest.raises(SourceEvidenceError):
        mapper("i", breadth, {})


# --- SOURCE-DAILY-BARS-READERS-COMPLETE-20261118: stored_instrument_authority through DailyBarsReader -----------
def _authority_roots(tmp_path):
    pytest.importorskip("pyarrow")
    from scripts import backfill_daily_bars as bf
    from services.source_ingestion.security_listing import ListingState

    roots = []
    for name, symbols in (("a", (("2330", "2330.TWSE"), ("2317", "2317.TWSE"), ("6488", "6488.TWO"))),
                          ("b", (("2330", "2330.TWSE"), ("1101", "1101.TWSE")))):
        root = tmp_path / name
        for day in ("2026-11-09", "2026-11-10"):
            d = root / "normalized" / "tw_price_daily" / f"date={day}"
            d.mkdir(parents=True)
            lines = []
            for code, canonical in symbols:
                body = {"symbol": code, "date": day, "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000,
                        "market": "TWSE"}
                lines.append(json.dumps({"source_id": f"{code}-{day}", "metadata": {
                    "symbol_canonical": canonical, "normalized_row": {**body, "symbol_canonical": canonical}}}))
            (d / "a.jsonl").write_text("\n".join(lines) + "\n")
        state = ListingState(last_success_date="2026-11-17")
        for code, _ in symbols:
            state.allowlist[code] = {"venue": "TWSE", "category": "x", "cfi": "ESVUFR", "is_warrant": False,
                                     "first_seen": "2026-01-01", "last_seen": "2026-11-17"}
        bf.backfill(root, state, today="2026-11-17", apply=True)
        roots.append(root)
    return roots


def test_instrument_authority_from_daily_bars_equals_the_run_file_read(tmp_path, monkeypatch):
    from services.source_ingestion.connectors.collector_bundle import stored_instrument_authority

    roots = _authority_roots(tmp_path)
    monkeypatch.delenv("PANTHEON_DAILY_PRICE_READER", raising=False)
    old = stored_instrument_authority(*roots)
    assert old == {"2330": ("2330.TWSE",), "2317": ("2317.TWSE",), "6488": ("6488.TWO",), "1101": ("1101.TWSE",)}
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    assert stored_instrument_authority(*roots) == old  # zero mismatches
    assert stored_instrument_authority(roots[0]) == {k: v for k, v in old.items() if k != "1101"}
    assert stored_instrument_authority(tmp_path / "missing") == {}


def test_instrument_authority_from_daily_bars_survives_run_file_deletion(tmp_path, monkeypatch):
    import shutil

    from services.source_ingestion.connectors.collector_bundle import resolve_social_symbol, stored_instrument_authority

    roots = _authority_roots(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    for root in roots:
        shutil.rmtree(root / "normalized")
    authority = stored_instrument_authority(*roots)
    assert resolve_social_symbol("2330", authority) == "2330.TWSE"
    with pytest.raises(SourceEvidenceError):
        resolve_social_symbol("9999", authority)  # unknown stays refused, never guessed
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "run_files")
    assert stored_instrument_authority(*roots) == {}  # the old read has nothing left: this is what the switch protects


def test_instrument_authority_lists_symbols_through_the_reader_not_the_store(tmp_path, monkeypatch):
    from services.source_ingestion import daily_bars_store as dbs
    from services.source_ingestion.connectors.collector_bundle import stored_instrument_authority

    roots = _authority_roots(tmp_path)
    monkeypatch.setenv("PANTHEON_DAILY_PRICE_READER", "daily_bars")
    monkeypatch.setattr(dbs.DailyBarsStore, "symbol_stats", lambda *a, **k: (_ for _ in ()).throw(AssertionError("store listing used")))
    assert "2330" in stored_instrument_authority(*roots)


# --- COLLECTOR-PRIORITY-METADATA-REVERIFY-R2-20261122: RC2 republish workset > cache cap & verification boundaries ---
def _rc2_tpex_bundle(
    run_id: str,
    entry_date: str,
    finished_at: str,
    trade_date: str,
    *,
    n_rows: int = 16,
    tag: str = "nov20-close",
    manifest_target_bytes: int = 0,
    items_target_bytes: int = 0,
) -> tuple[dict[str, bytes], dict[str, str], dict[str, str]]:
    source_id = "collector-tw-tpex-price-history"
    manifest_items = []
    rows = []
    raw_files: dict[str, bytes] = {}
    item_hashes: dict[str, str] = {}
    codes = ("2330", "2317", "2454")
    for idx in range(n_rows):
        code = codes[idx] if idx < len(codes) else f"{5000 + idx}"
        item_id = f"tpex-price-history-{trade_date}-{code}"
        raw_doc = {
            "dataset": "tw_price_daily",
            "exchange": "TPEX",
            "record": {
                "close": 100.0 + idx,
                "high": 101.0 + idx,
                "low": 99.0 + idx,
                "open": 100.0 + idx,
                "symbol": code,
                "tag": tag,
                "trade_date": trade_date,
                "volume": 1000 + idx,
            },
        }
        raw_bytes = json.dumps(raw_doc, sort_keys=True).encode("utf-8")
        sha = hashlib.sha256(raw_bytes).hexdigest()
        raw_path = f"raw/{sha}.json"
        manifest_items.append(
            {
                "item_id": item_id,
                "url": f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={trade_date}&code={code}",
                "http_status": 200,
                "content_sha256": sha,
                "raw_path": raw_path,
                "bytes": len(raw_bytes),
            }
        )
        rows.append(
            {
                "id": item_id,
                "url": f"https://www.tpex.org.tw/www/zh-tw/afterTrading/dailyQuotes?date={trade_date}&code={code}",
                "fetched_at": finished_at,
                "dataset": "tw_price_daily",
                "exchange": "TPEX",
                "symbol": code,
                "trade_date": trade_date,
                "open": 100.0 + idx,
                "high": 101.0 + idx,
                "low": 99.0 + idx,
                "close": 100.0 + idx,
                "volume": 1000 + idx,
                "turnover": 100000.0 + idx,
                "transactions": 50 + idx,
                "change": 0.5,
            }
        )
        raw_files[raw_path] = raw_bytes
        item_hashes[item_id] = sha
    bundle_sha = compute_bundle_sha256(item_hashes.values())
    manifest = {
        "schema_version": "collector_bundle.v1",
        "source_id": source_id,
        "run_id": run_id,
        "collector_version": "1.0.0",
        "started_at": finished_at,
        "finished_at": finished_at,
        "fetch_policy_hash": "rc2-tpex-policy-hash",
        "robots_decision": "allowed",
        "terms_ref": "https://www.tpex.org.tw/terms",
        "attribution": "證券櫃買中心 (TPEx)",
        "items": manifest_items,
        "status": "complete",
        "bundle_sha256": bundle_sha,
    }
    base = f"bundles/{source_id}/{entry_date}/{run_id}"
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    if manifest_target_bytes > len(manifest_bytes):
        manifest_bytes += b" " * (manifest_target_bytes - len(manifest_bytes))
    items_bytes = "\n".join(json.dumps(r) for r in rows).encode("utf-8")
    if items_target_bytes > len(items_bytes):
        items_bytes += b" " * (items_target_bytes - len(items_bytes))
    files = {
        f"{base}/manifest.json": manifest_bytes,
        f"{base}/items.jsonl": items_bytes,
    }
    for rel_raw, raw_body in raw_files.items():
        files[f"{base}/{rel_raw}"] = raw_body
    entry = {
        "source_id": source_id,
        "date": entry_date,
        "run_id": run_id,
        "finished_at": finished_at,
        "status": "complete",
        "bundle_sha256": bundle_sha,
    }
    return files, entry, item_hashes


def test_rc2_retained_receipt_and_republished_sessions_advance_historical_offset_within_budget(
    tmp_path, monkeypatch
):
    """Acceptance 1: reproduce the RC2 retained priority receipt (2026-11-21T18:16:40Z#run-20261121T181640Z),
    12 eligible upper-bound candidates with digest groups [9, 2, 1], 4 measured runs totaling 87,025,300 bytes
    (> 71,680,000-byte _METADATA_CACHE cap), and historical partial cursor
    2026-11-11T12:35:01Z#backfill-2026-10-28|items=804 (8,993,131 metadata bytes) under the 15s budget."""
    import threading
    import types
    from services.source_ingestion.connectors import collector_bundle as cb

    assert cb.COLLECTOR_REQUEST_READ_BUDGET_SECONDS == 15.0
    assert cb.VERIFIED_METADATA_CACHE_BYTES == 71_680_000
    assert cb.VERIFIED_METADATA_CACHE_ENTRIES == 16
    assert cb.PRIORITY_TRADE_DATE_MEMO_ENTRIES == 512
    assert cb.PRIORITY_DELIVERED_MEMO_ENTRIES == 64

    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()

    source_id = "collector-tw-tpex-price-history"
    files: dict[str, bytes] = {}
    entries: list[dict[str, str]] = []

    # Historical bundle at partial offset 2026-11-11T12:35:01Z#backfill-2026-10-28|items=804 (8,993,131 metadata bytes).
    hist_files, hist_entry, _ = _rc2_tpex_bundle(
        "backfill-2026-10-28",
        "2026-11-11",
        "2026-11-11T12:35:01Z",
        "2026-10-28",
        n_rows=868,
        tag="oct28-backfill",
        manifest_target_bytes=2_345_000,
        items_target_bytes=8_993_131 - 2_345_000,
    )
    files.update(hist_files)
    entries.append(hist_entry)

    # 3 earlier-session runs fetched on 2026-11-20 carrying trade_date 2026-11-19 (digest groups 2 and 1).
    for run_id, finished_at, tag in (
        ("run-20261120T001802Z", "2026-11-20T00:18:02Z", "nov19-early"),
        ("close-2026-11-20-0511", "2026-11-20T05:11:13Z", "nov19-close"),
        ("run-20261120T061626Z", "2026-11-20T06:16:26Z", "nov19-close"),
    ):
        c_files, c_entry, _ = _rc2_tpex_bundle(
            run_id, "2026-11-20", finished_at, "2026-11-19", n_rows=16, tag=tag
        )
        files.update(c_files)
        entries.append(c_entry)

    # 9 equivalent republished runs carrying trade_date 2026-11-20; the 4 measured runs match RC2 byte sizes
    # (5,674,342 + 16,081,983 = 21,756,325 bytes each; 4 * 21,756,325 = 87,025,300 bytes > 71,680,000 cap).
    measured_four = {
        "run-20261121T181640Z",
        "run-20261122T001805Z",
        "run-20261122T061626Z",
        "run-20261122T121941Z",
    }
    nov20_runs = (
        ("run-20261120T122009Z", "2026-11-20", "2026-11-20T12:20:09Z"),
        ("run-20261120T181629Z", "2026-11-20", "2026-11-20T18:16:29Z"),
        ("run-20261121T001805Z", "2026-11-21", "2026-11-21T00:18:05Z"),
        ("run-20261121T061617Z", "2026-11-21", "2026-11-21T06:16:17Z"),
        ("run-20261121T121953Z", "2026-11-21", "2026-11-21T12:19:53Z"),
        ("run-20261121T181640Z", "2026-11-21", "2026-11-21T18:16:40Z"),
        ("run-20261122T001805Z", "2026-11-22", "2026-11-22T00:18:05Z"),
        ("run-20261122T061626Z", "2026-11-22", "2026-11-22T06:16:26Z"),
        ("run-20261122T121941Z", "2026-11-22", "2026-11-22T12:19:41Z"),
    )
    nov20_hashes: dict[str, str] = {}
    measured_bytes_sum = 0
    for run_id, entry_date, finished_at in nov20_runs:
        is_measured = run_id in measured_four
        m_target = 5_674_342 if is_measured else 0
        i_target = 16_081_983 if is_measured else 0
        c_files, c_entry, item_hashes = _rc2_tpex_bundle(
            run_id,
            entry_date,
            finished_at,
            "2026-11-20",
            n_rows=16,
            tag="nov20-close",
            manifest_target_bytes=m_target,
            items_target_bytes=i_target,
        )
        if is_measured:
            base = f"bundles/{source_id}/{entry_date}/{run_id}"
            measured_bytes_sum += len(c_files[f"{base}/manifest.json"]) + len(c_files[f"{base}/items.jsonl"])
        files.update(c_files)
        entries.append(c_entry)
        nov20_hashes = item_hashes

    assert measured_bytes_sum == 87_025_300
    assert measured_bytes_sum > cb.VERIFIED_METADATA_CACHE_BYTES
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode("utf-8")
    files["status.json"] = json.dumps(
        {
            "sources": {
                source_id: {
                    "status": "ok",
                    "last_run_at": "2026-11-22T12:19:41Z",
                    "cadence_seconds": 21600,
                }
            }
        }
    ).encode("utf-8")

    clock = [0.0]
    lock = threading.Lock()
    reads: list[str] = []

    class ModeledRC2Transport(FakeTransport):
        def read(self, relative_path: str, *, max_bytes: int) -> bytes:
            data = super().read(relative_path, max_bytes=max_bytes)
            with lock:
                reads.append(relative_path)
                if relative_path.endswith(("manifest.json", "items.jsonl")):
                    # Proportional to RC2 measured 1977.4 ms for 21,756,325 metadata bytes.
                    clock[0] += len(data) * (1.9774 / 21_756_325)
                elif "/raw/" in relative_path:
                    clock[0] += 0.015
            return data

    monkeypatch.setattr(cb, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    transport = ModeledRC2Transport(files)

    def make_adapter():
        return CollectorBundleAdapter(
            connector_id=source_id,
            source_id=source_id,
            source_type="market",
            dataset="tw_price_daily",
            cadence_seconds=1800,
            max_records=32,
            attribution_required=True,
            raw_root=str(tmp_path / "store"),
            transport=transport,
            host=HOST,
        )

    adapter = make_adapter()
    # Write the retained existing priority receipt at 2026-11-21T18:16:40Z#run-20261121T181640Z
    # and the partial historical cursor at 2026-11-11T12:35:01Z#backfill-2026-10-28|items=804.
    priority_path = adapter._priority_key_path()
    priority_path.parent.mkdir(parents=True, exist_ok=True)
    priority_path.write_text(
        json.dumps(
            {
                "source_id": source_id,
                "key": "2026-11-21T18:16:40Z#run-20261121T181640Z",
                "trade_date": "2026-11-20",
                "delivered": len(nov20_hashes),
                "done": True,
                "held": ["2317", "2330", "2454"],
                "items": nov20_hashes,
            }
        ),
        encoding="utf-8",
    )
    cursor_0 = "2026-11-11T12:35:01Z#backfill-2026-10-28|items=804"
    cursor_path = adapter._resume_cursor_path()
    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(
        json.dumps({"source_id": source_id, "cursor": cursor_0}),
        encoding="utf-8",
    )

    # Pull 1 (from cold caches): verifies equivalent republished candidates without downloading their 16.08 MB
    # items.jsonl files, updates priority key to run-20261122T121941Z, and advances backfill-2026-10-28|items=804.
    t0 = clock[0]
    pull_1 = adapter.pull(cursor=adapter.load_resume_cursor(), priority_symbols=("2330.TWO", "2317.TWSE", "2454.TWSE"))
    elapsed_1 = clock[0] - t0
    adapter.save_resume_cursor(pull_1)

    assert elapsed_1 < cb.COLLECTOR_REQUEST_READ_BUDGET_SECONDS
    assert pull_1.priority_key == "2026-11-22T12:19:41Z#run-20261122T121941Z"
    assert pull_1.priority_done is True
    assert len(pull_1.records) == 32
    assert {r.metadata["trade_date"] for r in pull_1.records} == {"2026-10-28"}
    assert pull_1.next_cursor == "2026-11-11T12:35:01Z#backfill-2026-10-28|items=836"
    assert pull_1.observed["continuation_pending"] is True
    assert pull_1.observed["newest_session_pending"] is False
    assert "collector_complete_boundary_unverified" not in pull_1.observed.get("reasons", ())
    assert json.loads(priority_path.read_text())["key"] == "2026-11-22T12:19:41Z#run-20261122T121941Z"
    # None of the 9 equivalent 2026-11-20 runs read items.jsonl, and the 2nd 2026-11-19 close run also skipped items.jsonl.
    for run_id, _, _ in nov20_runs:
        assert sum(1 for r in reads if f"/{run_id}/manifest.json" in r) == 1
        assert sum(1 for r in reads if f"/{run_id}/items.jsonl" in r) == 0
    assert sum(1 for r in reads if "/close-2026-11-20-0511/items.jsonl" in r) == 0

    # Force-load all 4 measured 21.75 MB runs (87,025,300 bytes > 71,680,000-byte _METADATA_CACHE cap)
    # to confirm _HISTORY_READING protects backfill-2026-10-28 in _METADATA_CACHE while priority runs evict.
    for entry in entries:
        if entry["run_id"] in measured_four:
            adapter._verified_metadata(transport, entry)
    with cb._METADATA_CACHE_LOCK:
        cached_bytes = sum(size for _, _, size in cb._METADATA_CACHE.values())
        cached_runs = {b.run_id for _, b, _ in cb._METADATA_CACHE.values()}
    assert cached_bytes <= cb.VERIFIED_METADATA_CACHE_BYTES
    assert "backfill-2026-10-28" in cached_runs

    # Now publish a new 6-hourly equivalent run (run-20261122T181640Z); previously verified runs evicted from
    # _METADATA_CACHE by the 71.68 MB cap must not be re-read, and backfill-2026-10-28 completes to the next boundary.
    new_files, new_entry, _ = _rc2_tpex_bundle(
        "run-20261122T181640Z",
        "2026-11-22",
        "2026-11-22T18:16:40Z",
        "2026-11-20",
        n_rows=16,
        tag="nov20-close",
        manifest_target_bytes=5_674_342,
        items_target_bytes=16_081_983,
    )
    files.update(new_files)
    entries.append(new_entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode("utf-8")
    reads.clear()
    t1 = clock[0]
    pull_2 = make_adapter().pull(
        cursor=adapter.load_resume_cursor(), priority_symbols=("2330.TWO", "2317.TWSE", "2454.TWSE")
    )
    elapsed_2 = clock[0] - t1
    adapter.save_resume_cursor(pull_2)

    assert elapsed_2 < cb.COLLECTOR_REQUEST_READ_BUDGET_SECONDS
    assert pull_2.priority_key == "2026-11-22T18:16:40Z#run-20261122T181640Z"
    assert len(pull_2.records) == 32
    assert pull_2.next_cursor == "2026-11-11T12:35:01Z#backfill-2026-10-28"
    # Zero metadata re-reads for all 12 prior candidates and zero metadata re-reads for backfill-2026-10-28!
    assert [r for r in reads if r.endswith(("manifest.json", "items.jsonl"))] == [
        f"bundles/{source_id}/2026-11-22/run-20261122T181640Z/manifest.json"
    ]


def test_priority_republish_preserves_row_identity_and_handles_revisions_changed_manifests_and_cold_restart(
    tmp_path, monkeypatch
):
    """Acceptance 1 & 3: positive complete pending input with held-symbol priority, equivalent republish,
    swapped item_id row identity with identical bundle_sha256, tampered manifest, revised data correction,
    resume after cold restart, and unverified empty input failing closed."""
    from services.source_ingestion.connectors import collector_bundle as cb
    from services.source_ingestion.provider_adapters import execute_provider_owned_adapter

    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()

    source_id = "collector-tw-tpex-price-history"
    hist_files, hist_entry, _ = _rc2_tpex_bundle(
        "backfill-2026-10-28", "2026-11-11", "2026-11-11T12:35:01Z", "2026-10-28", n_rows=6, tag="oct28"
    )
    nov20_files, nov20_entry, nov20_hashes = _rc2_tpex_bundle(
        "run-20261121T181640Z", "2026-11-21", "2026-11-21T18:16:40Z", "2026-11-20", n_rows=4, tag="nov20"
    )
    files = {**hist_files, **nov20_files}
    entries = [hist_entry, nov20_entry]
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode("utf-8")
    files["status.json"] = json.dumps(
        {"sources": {source_id: {"status": "ok", "last_run_at": "2026-11-21T18:16:40Z", "cadence_seconds": 21600}}}
    ).encode("utf-8")
    transport = _CountingTransport(files)

    def make_adapter(max_records=6):
        return CollectorBundleAdapter(
            connector_id=source_id,
            source_id=source_id,
            source_type="market",
            dataset="tw_price_daily",
            cadence_seconds=1800,
            max_records=max_records,
            attribution_required=True,
            raw_root=str(tmp_path / "store"),
            transport=transport,
            host=HOST,
        )

    # 1. Positive complete pending input: held symbol 2454 comes first, followed by remaining newest-session rows
    # and the first 2 historical rows (partial cursor |items=2).
    a = make_adapter(max_records=6)
    first = a.pull(cursor=None, priority_symbols=("2454.TWO",))
    a.save_resume_cursor(first)
    assert first.priority_done is True
    assert [r.metadata["symbol"] for r in first.records[:4]] == ["2454", "2330", "2317", "5003"]
    assert [r.metadata["trade_date"] for r in first.records] == [
        "2026-11-20",
        "2026-11-20",
        "2026-11-20",
        "2026-11-20",
        "2026-10-28",
        "2026-10-28",
    ]
    assert first.next_cursor == "2026-11-11T12:35:01Z#backfill-2026-10-28|items=2"
    for rec in first.records:
        assert rec.metadata["content_hash"].startswith("sha256:")
        assert rec.metadata["event_time"] <= rec.metadata["available_time"]

    # 2. Changed manifest with swapped item_id -> content_sha256 mapping but IDENTICAL sorted bundle_sha256:
    # must not be skipped as equivalent because verified row identity changed.
    swapped_files, swapped_entry, _ = _rc2_tpex_bundle(
        "run-20261122T001805Z", "2026-11-22", "2026-11-22T00:18:05Z", "2026-11-20", n_rows=4, tag="nov20"
    )
    swapped_base = f"bundles/{source_id}/2026-11-22/run-20261122T001805Z"
    swapped_manifest = json.loads(swapped_files[f"{swapped_base}/manifest.json"])
    swapped_manifest["items"][0]["item_id"], swapped_manifest["items"][1]["item_id"] = (
        swapped_manifest["items"][1]["item_id"],
        swapped_manifest["items"][0]["item_id"],
    )
    assert compute_bundle_sha256([m["content_sha256"] for m in swapped_manifest["items"]]) == nov20_entry["bundle_sha256"]
    swapped_files[f"{swapped_base}/manifest.json"] = json.dumps(swapped_manifest).encode("utf-8")
    files.update(swapped_files)
    files["bundles/index.json"] = json.dumps({"bundles": [*entries, swapped_entry]}).encode("utf-8")
    transport.reads.clear()
    swapped_pull = make_adapter(max_records=6).pull(
        cursor=a.load_resume_cursor(), priority_symbols=("2454.TWO",)
    )
    # Row identity mismatch forced items.jsonl verification and re-delivery of the 2 swapped item_ids.
    assert any(r.endswith("/run-20261122T001805Z/items.jsonl") for r in transport.reads)
    assert len([r for r in swapped_pull.records if r.metadata["trade_date"] == "2026-11-20"]) == 2

    # 3. Tampered manifest claiming the trusted bundle_sha256 in index.json fails closed.
    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()
    bad_manifest = dict(swapped_manifest)
    bad_manifest["attribution"] = ""
    files[f"{swapped_base}/manifest.json"] = json.dumps(bad_manifest).encode("utf-8")
    with pytest.raises(SourceEvidenceError, match="requires attribution"):
        make_adapter(max_records=6).pull(cursor=a.load_resume_cursor(), priority_symbols=("2454.TWO",))

    # Restore valid equivalent republish run-20261122T001805Z and simulate cold restart.
    equiv_files, equiv_entry, _ = _rc2_tpex_bundle(
        "run-20261122T001805Z", "2026-11-22", "2026-11-22T00:18:05Z", "2026-11-20", n_rows=4, tag="nov20"
    )
    files.update(equiv_files)
    entries.append(equiv_entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode("utf-8")
    with cb._METADATA_CACHE_LOCK:
        cb._METADATA_CACHE.clear()
    assert not cb._PRIORITY_TRADE_DATE_MEMO and not cb._PRIORITY_DELIVERED_MEMO

    # 4. Resume after cold restart: priority receipt skips items.jsonl for equivalent republish, updates key,
    # and resumes historical backfill from |items=2 to |items=4.
    transport.reads.clear()
    resumed = make_adapter(max_records=2).pull(cursor=a.load_resume_cursor(), priority_symbols=("2454.TWO",))
    a.save_resume_cursor(resumed)
    assert resumed.priority_key == "2026-11-22T00:18:05Z#run-20261122T001805Z"
    assert not any(r.endswith("/run-20261122T001805Z/items.jsonl") for r in transport.reads)
    assert [r.metadata["trade_date"] for r in resumed.records] == ["2026-10-28", "2026-10-28"]
    assert resumed.next_cursor == "2026-11-11T12:35:01Z#backfill-2026-10-28|items=4"

    # 5. Revised data (correction to 2330 close price in run-20261122T061626Z): verifies items.jsonl and raw file,
    # delivers only the corrected 2330 record for 2026-11-20 plus the final 2 historical rows of 2026-10-28.
    rev_files, rev_entry, _ = _rc2_tpex_bundle(
        "run-20261122T061626Z", "2026-11-22", "2026-11-22T06:16:26Z", "2026-11-20", n_rows=4, tag="nov20"
    )
    rev_base = f"bundles/{source_id}/2026-11-22/run-20261122T061626Z"
    rev_manifest = json.loads(rev_files[f"{rev_base}/manifest.json"])
    rev_rows = [json.loads(line) for line in rev_files[f"{rev_base}/items.jsonl"].decode("utf-8").splitlines()]
    old_raw_path = rev_manifest["items"][0]["raw_path"]
    raw_doc = json.loads(rev_files.pop(f"{rev_base}/{old_raw_path}").decode("utf-8"))
    raw_doc["record"]["close"] = 999.0
    new_raw_bytes = json.dumps(raw_doc, sort_keys=True).encode("utf-8")
    new_sha = hashlib.sha256(new_raw_bytes).hexdigest()
    rev_manifest["items"][0]["content_sha256"] = new_sha
    rev_manifest["items"][0]["raw_path"] = f"raw/{new_sha}.json"
    rev_manifest["items"][0]["bytes"] = len(new_raw_bytes)
    rev_manifest["bundle_sha256"] = compute_bundle_sha256([m["content_sha256"] for m in rev_manifest["items"]])
    rev_entry["bundle_sha256"] = rev_manifest["bundle_sha256"]
    rev_rows[0]["close"] = 999.0
    rev_files[f"{rev_base}/raw/{new_sha}.json"] = new_raw_bytes
    rev_files[f"{rev_base}/manifest.json"] = json.dumps(rev_manifest).encode("utf-8")
    rev_files[f"{rev_base}/items.jsonl"] = "\n".join(json.dumps(r) for r in rev_rows).encode("utf-8")
    files.update(rev_files)
    entries.append(rev_entry)
    files["bundles/index.json"] = json.dumps({"bundles": entries}).encode("utf-8")

    corrected = make_adapter(max_records=4).pull(cursor=a.load_resume_cursor(), priority_symbols=("2454.TWO",))
    a.save_resume_cursor(corrected)
    assert corrected.priority_key == "2026-11-22T06:16:26Z#run-20261122T061626Z"
    nov20_corrected = [r for r in corrected.records if r.metadata["trade_date"] == "2026-11-20"]
    assert len(nov20_corrected) == 1
    assert nov20_corrected[0].metadata["symbol"] == "2330"
    assert nov20_corrected[0].metadata["normalized_row"]["close"] == 999.0
    assert corrected.complete is True
    assert corrected.next_cursor == "2026-11-22T06:16:26Z#run-20261122T061626Z"

    # 6. Unverified empty input fails closed through execute_provider_owned_adapter and never advances cursor/state.
    unverified_dir = tmp_path / "unverified_store"
    partial_files, partial_entry, _ = _rc2_tpex_bundle(
        "run-partial", "2026-11-22", "2026-11-22T12:00:00Z", "2026-11-20", n_rows=0, tag="partial"
    )
    p_base = f"bundles/{source_id}/2026-11-22/run-partial"
    p_manifest = json.loads(partial_files[f"{p_base}/manifest.json"])
    p_manifest["status"] = "partial"
    partial_entry["status"] = "partial"
    partial_files[f"{p_base}/manifest.json"] = json.dumps(p_manifest).encode("utf-8")
    partial_files["bundles/index.json"] = json.dumps({"bundles": [partial_entry]}).encode("utf-8")
    partial_files["status.json"] = json.dumps(
        {"sources": {source_id: {"status": "complete", "last_run_at": "2026-11-22T12:00:00Z", "cadence_seconds": 21600}}}
    ).encode("utf-8")
    monkeypatch.setattr(CollectorBundleAdapter, "_transport", lambda self: FakeTransport(partial_files))
    monkeypatch.setenv("SOURCE_INGEST_MARKET_DATA_STORAGE_ROOT", str(unverified_dir))
    unverified_adapter = CollectorBundleAdapter(
        connector_id=source_id,
        source_id=source_id,
        source_type="market",
        dataset="tw_price_daily",
        cadence_seconds=1800,
        max_records=10,
        attribution_required=True,
        raw_root=str(unverified_dir),
        host=HOST,
    )
    with cb.deferred_collector_acknowledgement():
        unverified_result = execute_provider_owned_adapter(
            connector=unverified_adapter.connector(),
            fetch={
                "adapter": COLLECTOR_BUNDLE_ADAPTER_TOKEN,
                "adapter_config": {
                    "source_id": source_id,
                    "source_type": "market",
                    "dataset": "tw_price_daily",
                    "cadence_seconds": 1800,
                    "attribution_required": True,
                    "raw_root": str(unverified_dir),
                    "host": HOST,
                },
                "request": {},
                "max_records": 10,
            },
            trace_id="rc2-unverified-empty",
            preserve_collector_outcome=True,
        )
    assert unverified_result.records == ()
    assert unverified_result.complete is False
    assert unverified_result.no_new_data is False
    assert "collector_complete_boundary_unverified" in unverified_result.observed["reasons"]
    assert not unverified_adapter._resume_cursor_path().exists()
    assert not unverified_adapter._priority_key_path().exists()

