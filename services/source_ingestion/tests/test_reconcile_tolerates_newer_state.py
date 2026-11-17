"""RC1 WP1.5: reconcile tolerates connector/schedule records and requirements a newer release wrote.

A "synthetic newer release" is injected into the stores of the current release: a connector
whose provider-owned adapter, connector definition and dataset this release does not define,
plus its schedule record and a requirement-snapshot binding. The tick must skip and report
them, leave their stored bytes alone and still reach the pull stage for the connectors it does
define. A known-but-malformed record keeps failing closed.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from services.source_ingestion.connectors.base import SourceConnector
from services.source_ingestion.persona_source_reconciler import SourceProvisioningReconciler

KNOWN_ID = "tolerate-known-notes"
NEWER_ADAPTER_ID = "future-adapter-connector"
NEWER_DEFINITION_ID = "future-definition-connector"
NEWER_DATASET_ID = "collector-tw-trading-holidays"
NEWER_IDS = (NEWER_ADAPTER_ID, NEWER_DEFINITION_ID)
TOKEN_FILE = "controller_token"


def _requirement(dataset: str, candidates: list[str]) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "market": "GLOBAL",
        "cadence": "daily",
        "source_class": "live_pull",
        "connector_candidates": candidates,
        "policy_gates": ["require_connector_approved", "require_schedule_active"],
    }


def _persona() -> dict[str, Any]:
    return {
        "persona_id": "persona-tolerate",
        "name": "Tolerate newer state",
        "mandate": "Reconcile with records a newer release wrote",
        "lifecycle_state": "research_only",
        "created_at": "2026-11-17T00:00:00Z",
        "required_data_sources": [_requirement("tolerate_known_note", [KNOWN_ID])],
    }


class _KnownProvider:
    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=KNOWN_ID,
            source_type="internal_note",
            provider="tolerate fixture",
            license_scope="internal",
        )

    def fetch_config(self) -> dict[str, Any]:
        return {
            "mode": "static_records",
            "next_watermark": "tolerate-watermark-1",
            "records": [
                {
                    "source_id": "src-tolerate-note-1",
                    "title": "Known connector still pulls",
                    "content_ref": "memory://tolerate/1",
                    "metadata": {
                        "body": "pulled although records of a newer release sit in the stores",
                        "access_scope": ["operator"],
                        "available_time": "2026-11-17T00:00:00Z",
                    },
                }
            ],
        }


@pytest.fixture()
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOURCE_INGEST_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOURCE_INGEST_MAX_RECORDS", "10")
    monkeypatch.setenv("SOURCE_INGEST_EVIDENCE_BACKEND", "jsonl")
    monkeypatch.delenv("PANTHEON_COLLECTOR_HOST", raising=False)
    from services.source_ingestion.controller_state import ControllerState, ControllerStateStore

    # Controller-owned connectors pull under the controller's tenant identity.
    ControllerStateStore(tmp_path / "controller_state.json").save(
        ControllerState(
            controller_id="ctrl-tolerate", controller_name="test-controller", environment="test",
            tenant_id="tenant-dev", deployment={},
        )
    )
    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)
    runtime = module.runtime
    runtime._source_provisioning_reconciler = lambda: SourceProvisioningReconciler(
        manager=runtime.manager,
        connector_store=runtime.connector_store,
        schedule_store=runtime.schedule_config_store,
        provider_factories={KNOWN_ID: lambda _id: _KnownProvider()},
    )
    with TestClient(module.app) as client:
        yield client, module, tmp_path


def _headers(module) -> dict[str, str]:
    return {"Authorization": f"Bearer {module.runtime.controller_token}"}


def _reconcile(client, module, personas, **extra):
    payload = {
        "personas": personas,
        "authoritative_snapshot": True,
        "desired_state_sha256": module.runtime._desired_state_digest(personas),
        "source_authority": "test://reconcile-tolerates-newer-state",
        **extra,
    }
    return client.post(
        "/api/source-ingest/persona-source-provisioning/reconcile", headers=_headers(module), json=payload
    )


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _inject_newer_release_records(module, data_dir: Path, *, bind: bool = True) -> dict[str, list[str]]:
    """Append the records a newer release would have left on the volume; return their exact lines."""

    config_path = data_dir / "connector_config.jsonl"
    schedule_path = data_dir / "connector_schedule.jsonl"
    template = next(
        entry for entry in _jsonl(config_path) if entry["payload"]["connector"]["connector_id"] == KNOWN_ID
    )

    def newer(connector_id: str, *, definition_id: str | None, adapter: str, dataset: str, cadence: str) -> dict:
        entry = json.loads(json.dumps(template))
        connector = entry["payload"]["connector"]
        connector["connector_id"] = connector_id
        connector["metadata"]["definition_id"] = definition_id or connector["metadata"].get("definition_id")
        if definition_id is None:
            connector["metadata"].pop("definition_id", None)
        marker = connector["metadata"]["persona_source_reconciliation"]
        marker["desired_state"].update({"dataset": dataset, "cadence": cadence})
        entry["payload"]["fetch"] = {
            "mode": "provider_owned_adapter",
            "adapter": adapter,
            "max_records": 10,
            "adapter_config": {},
            "request": {},
        }
        entry["record_id"] = connector_id
        return entry

    entries = [
        newer(NEWER_ADAPTER_ID, definition_id=None, adapter="FutureNewsAdapter.records_from_feed",
              dataset="future_dataset", cadence="daily"),
        newer(NEWER_DEFINITION_ID, definition_id="definition-from-a-newer-release", adapter="FinMindTaiwanDatasetAdapter",
              dataset="future_dataset", cadence="daily"),
    ]
    lines: dict[str, list[str]] = {"config": [], "schedule": []}
    with config_path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            handle.write(line + "\n")
            lines["config"].append(line)
    schedule_template = _jsonl(schedule_path)[0]
    with schedule_path.open("a", encoding="utf-8") as handle:
        for connector_id in NEWER_IDS:
            entry = json.loads(json.dumps(schedule_template))
            entry["payload"]["connector_id"] = connector_id
            entry["record_id"] = connector_id
            line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
            handle.write(line + "\n")
            lines["schedule"].append(line)
    if bind:
        snapshot = module.runtime.requirement_snapshot_store.reload()
        bindings = dict(snapshot.bindings)
        for connector_id in NEWER_IDS:
            bindings[f"persona-newer:GLOBAL:future_dataset:daily:live_pull:{connector_id}"] = connector_id
        module.runtime.requirement_snapshot_store.append(
            desired_state_sha256="a" * 64,
            bindings=bindings,
            persona_count=2,
            authority="test://newer-release",
            authoritative=True,
        )
    module.runtime.connector_store.reload()
    module.runtime.schedule_config_store.reload()
    return lines


def _lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_unknown_records_are_skipped_reported_left_alone_and_known_connector_still_pulls(api) -> None:
    client, module, data_dir = api
    personas = [_persona()]
    first = _reconcile(client, module, personas)
    assert first.status_code == 200, first.text
    assert first.json()["skipped"] == [] and first.json()["skipped_count"] == 0

    injected = _inject_newer_release_records(module, data_dir)
    config_before = _lines(data_dir / "connector_config.jsonl")
    schedule_before = _lines(data_dir / "connector_schedule.jsonl")
    for line in injected["config"]:
        assert line in config_before

    response = _reconcile(client, module, personas)
    assert response.status_code == 200, response.text
    body = response.json()

    # Reported, each with kind + id + reason, and counted.
    skipped = {(item["kind"], item["id"]): item["reason"] for item in body["skipped"]}
    assert body["skipped_count"] == len(body["skipped"]) == len(skipped) == 6
    assert skipped[("connector_record", NEWER_ADAPTER_ID)].startswith("adapter-not-defined: FutureNewsAdapter")
    assert skipped[("connector_record", NEWER_DEFINITION_ID)].startswith(
        "connector-definition-not-defined: definition-from-a-newer-release"
    )
    for connector_id in NEWER_IDS:
        assert skipped[("schedule_record", connector_id)] == "connector-not-defined-by-this-release"
    assert sum(1 for kind, _ in skipped if kind == "binding") == 2
    # Nothing it does not define was retired or disabled.
    assert body["retirement_actions"] == []
    for connector_id in NEWER_IDS:
        assert module.runtime.connector_store.get_config(connector_id).connector.status.value != "disabled"

    # Stored bytes of the unknown connector and schedule records are unchanged (asserted line for line).
    config_after = _lines(data_dir / "connector_config.jsonl")
    schedule_after = _lines(data_dir / "connector_schedule.jsonl")
    for line in injected["config"]:
        assert line in config_after
    for line in injected["schedule"]:
        assert line in schedule_after
    assert config_after[: len(config_before)] == config_before
    assert schedule_after[: len(schedule_before)] == schedule_before

    # The snapshot appended states the bindings it did not interpret: they are carried, not dropped.
    accepted = body["accepted_requirement_snapshot"]
    assert accepted["binding_count"] == 3
    assert sorted(v for v in accepted["bindings"].values() if v in NEWER_IDS) == sorted(NEWER_IDS)

    # It continues with every item it does define: the known connector reaches the pull stage.
    ran = client.post("/api/source-ingest/run-scheduled", headers=_headers(module), json={"max_concurrency": 1})
    assert ran.status_code == 200, ran.text
    summary = ran.json()["summary"]
    assert summary["total_ran"] == 1 and summary["total_failed"] == 0
    assert [item["connector_id"] for item in ran.json()["ran"]] == [KNOWN_ID]
    assert set(NEWER_IDS) <= set(ran.json()["skipped"])  # skipped by the scheduler, not failed

    readback = client.get("/api/source-ingest/controller/readback")
    assert readback.status_code == 200
    # Nothing was written to controller_state by the API alone: zero and empty until the worker records it.
    assert readback.json()["skipped_count"] == 0 and readback.json()["skipped_ids"] == []


def test_unbound_unknown_records_are_left_alone_not_retired(api) -> None:
    """No snapshot binding carries the unknown connectors: the retirement scan reaches them and must skip."""

    client, module, data_dir = api
    personas = [_persona()]
    assert _reconcile(client, module, personas).status_code == 200
    injected = _inject_newer_release_records(module, data_dir, bind=False)
    config_before = _lines(data_dir / "connector_config.jsonl")
    schedule_before = _lines(data_dir / "connector_schedule.jsonl")

    response = _reconcile(client, module, personas)

    assert response.status_code == 200, response.text
    body = response.json()
    actions = {a["connector_id"]: a["action"] for a in body["retirement_actions"]}
    assert actions == {connector_id: "left_alone_not_defined_by_release" for connector_id in NEWER_IDS}
    assert body["skipped_count"] == 4
    assert {item["kind"] for item in body["skipped"]} == {"connector_record", "schedule_record"}
    assert _lines(data_dir / "connector_config.jsonl") == config_before  # no retirement rewrite, no append
    assert _lines(data_dir / "connector_schedule.jsonl") == schedule_before
    for line in injected["config"]:
        assert line in config_before
    schedule = module.runtime.schedule_config_store.get_schedule(NEWER_ADAPTER_ID)
    assert schedule is not None and schedule.enabled is True


def test_known_connector_that_is_malformed_still_fails_closed(api) -> None:
    """A record of a kind this release defines (a known adapter) that is malformed is refused, not skipped."""

    client, module, data_dir = api
    personas = [_persona()]
    assert _reconcile(client, module, personas).status_code == 200
    _inject_newer_release_records(module, data_dir, bind=False)
    config_path = data_dir / "connector_config.jsonl"
    # A known adapter (allowlisted) with an invalid fetch contract: max_records out of range.
    entries = _jsonl(config_path)
    broken = json.loads(json.dumps(next(e for e in entries if e["payload"]["connector"]["connector_id"] == NEWER_DEFINITION_ID)))
    broken["payload"]["connector"]["connector_id"] = "known-but-malformed"
    broken["payload"]["connector"]["metadata"].pop("definition_id", None)
    broken["payload"]["fetch"]["max_records"] = 5000
    broken["record_id"] = "known-but-malformed"
    with config_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(broken, sort_keys=True, separators=(",", ":")) + "\n")
    module.runtime.connector_store.reload()

    response = _reconcile(client, module, personas)

    assert response.status_code == 400, response.text
    assert "fetch.max_records must be > 0 and <= 1000" in response.json()["detail"]


def test_unknown_requirement_candidate_is_skipped_with_reason_and_known_one_proceeds(api) -> None:
    client, module, data_dir = api
    personas = [_persona()]
    assert _reconcile(client, module, personas).status_code == 200
    _inject_newer_release_records(module, data_dir, bind=False)
    personas[0]["required_data_sources"].append(_requirement("future_dataset", [NEWER_ADAPTER_ID]))

    response = _reconcile(client, module, personas)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["conflicts"] == 0 and body["summary"]["unsupported"] == 0
    assert body["summary"]["skipped"] == 1 and body["summary"]["satisfied"] == 1
    requirement = [item for item in body["skipped"] if item["kind"] == "requirement"]
    assert len(requirement) == 1 and requirement[0]["reason"].startswith("adapter-not-defined")
    assert body["accepted_requirement_snapshot"]["binding_count"] == 1


def test_stored_record_with_cadence_or_dataset_this_release_does_not_define_is_undefined(api) -> None:
    client, module, data_dir = api
    reconciler = module.runtime._source_provisioning_reconciler()
    base = SourceConnector(connector_id="x", source_type="internal_note", provider="p", license_scope="internal")

    def config(connector_id: str, desired: dict[str, Any]):
        connector = SourceConnector.from_dict(
            {**base.to_dict(), "connector_id": connector_id,
             "metadata": {"persona_source_reconciliation": {"managed_by": "persona_source_provisioning_reconciler",
                                                            "desired_state": desired}}}
        )
        return type("Cfg", (), {"connector": connector, "fetch": {"mode": "static_records"}})()

    assert reconciler.undefined_reason(config("k", {"cadence": "monthly", "dataset": "d"})) == "cadence-not-defined: monthly"
    assert reconciler.undefined_reason(config("collector-tw-trading-holidays", {"cadence": "daily", "dataset": "not_a_dataset"})) == "dataset-not-defined: not_a_dataset"
    assert reconciler.undefined_reason(config("collector-tw-trading-holidays", {"cadence": "daily", "dataset": "tw_trading_holidays"})) is None


# ---------------------------------------------------------------------------------------------
# Controller diagnosability, skipped fields in controller state / readback / health (AC6, AC7)
# ---------------------------------------------------------------------------------------------

import http.server
import threading

from services.source_ingestion.controller_state import (
    MAX_SKIPPED_ID_LENGTH,
    MAX_SKIPPED_IDS,
    ControllerState,
    ControllerStateStore,
    summarize_reconcile,
)
from services.source_ingestion.controller_worker import (
    HTTP_ERROR_DETAIL_LIMIT,
    ControllerTickError,
    _request_json,
)


class _Handler(http.server.BaseHTTPRequestHandler):
    body = b""
    content_type = "application/json"

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        self.send_response(400)
        self.send_header("Content-Type", self.content_type)
        self.send_header("X-Secret-Header", "must-never-appear")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args: Any) -> None:
        return


def _serve(body: bytes, content_type: str = "application/json"):
    handler = type("H", (_Handler,), {"body": body, "content_type": content_type})
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/x"


def _failure_reason(url: str, token: str = "tok-secret-123") -> str:
    with pytest.raises(ControllerTickError) as raised:
        _request_json(url, method="POST", payload={}, bearer_token=token)
    state = ControllerState("c", "n", "test", "t", {})
    state.record_failure(stage=raised.value.stage, reason=str(raised.value))
    log_line = f"{type(raised.value).__name__}: {raised.value}"
    assert state.last_failure_reason == str(raised.value)
    assert log_line.endswith(str(raised.value))
    return str(raised.value)


def test_http_400_with_json_detail_reaches_failure_reason_and_log_line() -> None:
    detail = "provider-owned adapter is not allowlisted: FinMindTaiwanFundamentalsAdapter.records_from_rows"
    server, url = _serve(json.dumps({"detail": detail}).encode())
    try:
        reason = _failure_reason(url)
    finally:
        server.shutdown()
    assert reason == f"HTTPError: HTTP Error 400: Bad Request: {detail}"
    assert "must-never-appear" not in reason and "tok-secret-123" not in reason


def test_http_400_with_non_json_body_and_bounds_and_token_redaction() -> None:
    server, url = _serve(b"<html>upstream said:   tok-secret-123 " + b"x" * 5000 + b"</html>", "text/html")
    try:
        reason = _failure_reason(url)
    finally:
        server.shutdown()
    prefix = "HTTPError: HTTP Error 400: Bad Request: "
    assert reason.startswith(prefix)
    assert len(reason) - len(prefix) <= HTTP_ERROR_DETAIL_LIMIT == 500
    assert "tok-secret-123" not in reason and "must-never-appear" not in reason
    assert "[redacted]" in reason and "upstream said" in reason


def test_http_400_with_structured_or_empty_body() -> None:
    server, url = _serve(json.dumps({"detail": [{"loc": ["body"], "msg": "bad"}]}).encode())
    try:
        assert '"msg":"bad"' in _failure_reason(url)
    finally:
        server.shutdown()
    server, url = _serve(b"")
    try:
        assert _failure_reason(url).endswith("<empty response body>")
    finally:
        server.shutdown()


def test_controller_state_carries_skipped_count_and_first_50_ids_in_one_replaced_file(tmp_path: Path) -> None:
    items = [{"kind": "connector_record", "id": f"future-connector-{index:04d}-" + "x" * 140, "reason": "r"} for index in range(120)]
    reconcile = {"summary": {"skipped": 120}, "skipped": items, "skipped_count": 120}

    summary = summarize_reconcile(reconcile)
    assert summary["skipped_count"] == 120
    assert len(summary["skipped_ids"]) == MAX_SKIPPED_IDS == 50
    assert all(len(value) <= MAX_SKIPPED_ID_LENGTH for value in summary["skipped_ids"])
    assert summarize_reconcile(summary)["skipped_ids"] == summary["skipped_ids"]  # idempotent on reload
    empty = summarize_reconcile({"summary": {}})
    assert empty["skipped_count"] == 0 and empty["skipped_ids"] == []

    state = ControllerState("ctrl", "test-controller", "test", "tenant-dev", {})
    path = tmp_path / "controller_state.json"
    store = ControllerStateStore(path)
    state.record_success(desired_state={}, reconcile=reconcile, schedule={}, actual_readback={})
    store.save(state)
    first_size = path.stat().st_size
    store.save(state)
    store.save(state)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["controller_state.json"]  # one file, replaced each tick
    assert path.stat().st_size == first_size
    # 50 ids of at most 128 chars add at most ~7 KB; the whole file with 50 ids stays far below 64 KiB.
    skipped_bytes = len(json.dumps(summary["skipped_ids"]))
    assert skipped_bytes <= 50 * (MAX_SKIPPED_ID_LENGTH + 4)
    assert first_size < 65536
    loaded = store.load()
    assert loaded is not None and loaded.reconcile["skipped_count"] == 120
    assert len(loaded.reconcile["skipped_ids"]) == 50


def test_readback_and_health_show_skipped_fields_zero_and_empty_by_default(api) -> None:
    client, module, data_dir = api
    readback = client.get("/api/source-ingest/controller/readback").json()
    assert readback["skipped_count"] == 0 and readback["skipped_ids"] == []
    health = client.get("/health").json()
    assert health["skipped_count"] == 0 and health["skipped_ids"] == []

    state = ControllerStateStore(data_dir / "controller_state.json").load()
    assert state is not None
    items = [{"kind": "connector_record", "id": f"future-{index}", "reason": "adapter-not-defined: X"} for index in range(60)]
    state.record_success(
        desired_state={}, reconcile={"summary": {}, "skipped": items, "skipped_count": 60}, schedule={}, actual_readback={}
    )
    ControllerStateStore(data_dir / "controller_state.json").save(state)

    readback = client.get("/api/source-ingest/controller/readback").json()
    assert readback["skipped_count"] == 60 and len(readback["skipped_ids"]) == 50
    assert readback["skipped_ids"][0] == "connector_record:future-0"
    health = client.get("/health").json()
    assert health["skipped_count"] == 60 and len(health["skipped_ids"]) == 50


# ---------------------------------------------------------------------------------------------
# Previous-release harness: reproduce the 2026-11-17 failure and prove rollback safety (AC2, AC9, AC10)
# ---------------------------------------------------------------------------------------------

import shutil
import subprocess

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
import previous_release_reader as harness  # noqa: E402

BASELINE_REVISION = "e7ec353d6"  # the release the hosted rollback landed on (2026-11-17)
PRE_CHANGE_REVISION = "d88a1a86f"  # dev immediately before this change
CURRENT_REVISION = "HEAD"

# Runs under whatever code the harness put on sys.path. argv: data_dir mode
_RECONCILE_DRIVER = r'''
import importlib, json, os, sys, traceback
data_dir, mode = sys.argv[1], sys.argv[2]
os.environ.update({
    "SOURCE_INGEST_DATA_DIR": data_dir, "SOURCE_INGEST_EVIDENCE_BACKEND": "jsonl",
    "PANTHEON_COLLECTOR_HOST": "http://collector.invalid:9", "FINMIND_API_TOKEN": "local-test-token",
})
from fastapi.testclient import TestClient
module = importlib.import_module("services.source_ingestion.main")
from services.source_ingestion.controller_worker import load_desired_state
personas, meta = load_desired_state()
personas = [dict(p) for p in personas]
payload = {"personas": personas, "authoritative_snapshot": True, "dry_run": False,
           "desired_state_sha256": module.runtime._desired_state_digest(personas),
           "source_authority": meta["authority"]}
headers = {"Authorization": f"Bearer {module.runtime.controller_token}"}
out = {"requirement_count": meta["requirement_count"], "module_file": module.__file__}
with TestClient(module.app) as client:
    if mode == "readback":
        r = client.get("/api/source-ingest/controller/readback")
        body = r.json()
        out.update(status=r.status_code, connector_count=body.get("connector_count"),
                   state_present=body.get("controller_state") is not None,
                   snapshot_bindings=(body.get("requirement_snapshot") or {}).get("binding_count"))
        print(json.dumps(out)); sys.exit(0)
    r = client.post("/api/source-ingest/persona-source-provisioning/reconcile", headers=headers, json=payload)
    out["status"] = r.status_code
    body = r.json()
    if r.status_code == 200:
        out.update(summary=body["summary"], bindings=len(body["bindings"]),
                   skipped_count=body.get("skipped_count"), retirement_actions=body["retirement_actions"])
        if mode == "write-with-state":
            from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
            state = ControllerState("ctrl-rollback", "test-controller", "test", "tenant-dev", {})
            synthetic = {**body, "skipped": [{"kind": "connector_record", "id": f"future-{i}", "reason": "adapter-not-defined: X"} for i in range(3)], "skipped_count": 3}
            state.record_success(desired_state=meta, reconcile=synthetic, schedule={}, actual_readback=body["post_readback"])
            ControllerStateStore(os.path.join(data_dir, "controller_state.json")).save(state)
    else:
        out["detail"] = body.get("detail")
        from services.source_ingestion.api_models import PersonaSourceProvisioningRequest
        try:
            module.runtime.reconcile_persona_source_provisioning(
                PersonaSourceProvisioningRequest(**payload), authorization=headers["Authorization"])
        except Exception as exc:  # the raise site, as repo-relative file:line:function frames
            tree = os.environ["PREVIOUS_RELEASE_TREE"] + os.sep
            out["raised"] = type(exc).__name__
            out["frames"] = [f"{f.filename.replace(tree, '')}:{f.lineno}:{f.name}"
                             for f in traceback.extract_tb(exc.__traceback__) if f.filename.startswith(tree)]
print(json.dumps(out))
'''

_MODULES = ["services.source_ingestion.requirement_state"]


def _write_driver(tmp_path: Path) -> Path:
    path = tmp_path / "previous_release_reconcile_driver.py"
    path.write_text(_RECONCILE_DRIVER, encoding="utf-8")
    return path


def _through_harness(revision: str, driver: Path, data_dir: Path, mode: str) -> dict[str, Any]:
    result = harness.run_driver(revision, driver, modules=_MODULES, driver_args=[str(data_dir), mode], check=False)
    assert result.returncode == 0, result.stderr[-2000:]
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_reproduce_first_baseline_release_gets_http_400_from_state_written_by_dev(tmp_path: Path) -> None:
    """Incident 2026-11-17 06:47Z: e7ec353d6 reconciling state the newer release left on the volume."""

    driver = _write_driver(tmp_path)
    data_dir = tmp_path / "volume"
    data_dir.mkdir()
    written = _through_harness(PRE_CHANGE_REVISION, driver, data_dir, "write")
    assert written["status"] == 200 and written["bindings"] == written["requirement_count"] == 32
    connectors = {e["payload"]["connector"]["connector_id"] for e in _jsonl(data_dir / "connector_config.jsonl")}
    assert {"collector-tw-trading-holidays", "collector-tw-trading-suspension", "tw-finmind-monthly-revenue",
            "tw-finmind-quarterly-statements", "tw-finmind-valuation"} <= connectors

    old = _through_harness(BASELINE_REVISION, driver, data_dir, "reconcile")

    assert old["requirement_count"] == 27  # the baseline's own desired state
    assert old["status"] == 400
    assert old["detail"].startswith(
        "provider-owned adapter is not allowlisted: FinMindTaiwanFundamentalsAdapter.records_from_rows; allowed="
    )
    assert old["raised"] == "SourceEvidenceError"
    # Raise site at e7ec353d6: retirement of a connector the baseline does not define.
    assert any(f.startswith("services/source_ingestion/provider_adapters.py:") and f.endswith(":validate_provider_adapter_token") for f in old["frames"])
    assert any(f.startswith("services/source_ingestion/runtime.py:") and f.endswith(":_retire_removed_requirement_bindings") for f in old["frames"])
    assert any(f.startswith("services/source_ingestion/configured.py:") and f.endswith(":upsert_config") for f in old["frames"])


def test_rollback_proof_previous_releases_read_state_written_by_this_change(tmp_path: Path) -> None:
    """e7ec353d6 and the commit before this change read what the new code wrote, without error."""

    driver = _write_driver(tmp_path)
    data_dir = tmp_path / "volume"
    data_dir.mkdir()
    written = _through_harness(CURRENT_REVISION, driver, data_dir, "write-with-state")
    assert written["status"] == 200 and written["bindings"] == 32 and written["skipped_count"] == 0
    raw_state = json.loads((data_dir / "controller_state.json").read_text(encoding="utf-8"))["state"]
    assert raw_state["reconcile"]["skipped_count"] == 3 and len(raw_state["reconcile"]["skipped_ids"]) == 3

    for revision in (BASELINE_REVISION, PRE_CHANGE_REVISION):
        snapshot = tmp_path / f"volume-{revision}"
        shutil.copytree(data_dir, snapshot)
        # Reads controller_state.json, the connector store, the schedule store and the requirement log.
        read = _through_harness(revision, driver, snapshot, "readback")
        assert read["status"] == 200, revision
        assert read["connector_count"] == 32 and read["state_present"] is True and read["snapshot_bindings"] == 32
        # Difference, named: these releases do not know reconcile.skipped_count / skipped_ids; their
        # ControllerState.from_dict ignores unknown keys (checksum is over the payload as written), so
        # the read is safe; the next save by them rewrites the file without the two fields.


def test_harness_is_reusable_for_another_stateful_service_requirement_state_reader(tmp_path: Path) -> None:
    """Example for another module: the e7ec353d6 requirement_state reader against a log the current code wrote."""

    driver = tmp_path / "requirement_reader.py"
    driver.write_text(
        "import json, sys\n"
        "from services.source_ingestion.requirement_state import RequirementSnapshotStore\n"
        "snapshot = RequirementSnapshotStore(sys.argv[1]).reload()\n"
        "print(json.dumps({'sequence': snapshot.sequence, 'binding_count': snapshot.binding_count}))\n",
        encoding="utf-8",
    )
    from services.source_ingestion.requirement_state import RequirementSnapshotStore

    log = tmp_path / "requirement_snapshots.jsonl"
    current = RequirementSnapshotStore(log)
    current.append(
        desired_state_sha256="b" * 64, bindings={"r1": "c1", "r2": "c2"}, persona_count=1,
        authority="test://harness-example", authoritative=True,
    )
    result = harness.run_driver(
        BASELINE_REVISION, driver,
        modules=["services.source_ingestion.requirement_state"], driver_args=[str(log)],
    )
    assert result.json() == {"sequence": 1, "binding_count": 2}
    cli = subprocess.run(
        [sys.executable, str(Path(harness.__file__)), "--revision", BASELINE_REVISION,
         "--path", "services", "--module", "services.source_ingestion.requirement_state",
         "--driver", str(driver), "--", str(log)],
        capture_output=True, text=True, check=False,
    )
    assert cli.returncode == 0 and json.loads(cli.stdout) == {"sequence": 1, "binding_count": 2}
