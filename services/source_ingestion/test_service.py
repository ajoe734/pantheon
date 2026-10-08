from __future__ import annotations

import json
import importlib
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


def _read_headers(tenant: str = "tenant-a", roles: list[str] | None = None) -> dict[str, str]:
    from services.runtime_auth_inbound import encode_jwt_hs256

    token = encode_jwt_hs256(
        {"sub": "test-reader", "roles": roles or ["operator"], "tenant_id": tenant, "exp": int(__import__("time").time()) + 600},
        secret=os.environ.get("PANTHEON_RUNTIME_JWT_SECRET", "source-test-secret"),
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client():
    tempdir = tempfile.mkdtemp(prefix="source_ingest_service_")
    env_backup = {
        "SOURCE_INGEST_DATA_DIR": os.environ.get("SOURCE_INGEST_DATA_DIR"),
        "SOURCE_INGEST_STORE_PATH": os.environ.get("SOURCE_INGEST_STORE_PATH"),
        "SOURCE_INGEST_CONNECTOR_STORE_PATH": os.environ.get("SOURCE_INGEST_CONNECTOR_STORE_PATH"),
        "SOURCE_INGEST_EVIDENCE_STORE_PATH": os.environ.get("SOURCE_INGEST_EVIDENCE_STORE_PATH"),
        "SOURCE_INGEST_DLQ_PATH": os.environ.get("SOURCE_INGEST_DLQ_PATH"),
        "SOURCE_INGEST_AUDIT_PATH": os.environ.get("SOURCE_INGEST_AUDIT_PATH"),
        "SOURCE_INGEST_MAX_RECORDS": os.environ.get("SOURCE_INGEST_MAX_RECORDS"),
        "PANTHEON_RUNTIME_JWT_SECRET": os.environ.get("PANTHEON_RUNTIME_JWT_SECRET"),
        "SEARCH_INGEST_NOTIFY_URL": os.environ.get("SEARCH_INGEST_NOTIFY_URL"),
    }
    os.environ["SOURCE_INGEST_DATA_DIR"] = tempdir
    os.environ["SOURCE_INGEST_MAX_RECORDS"] = "3"
    os.environ["PANTHEON_RUNTIME_JWT_SECRET"] = "source-test-secret"
    os.environ["SEARCH_INGEST_NOTIFY_URL"] = ""

    sys.modules.pop("services.source_ingestion.main", None)
    module = importlib.import_module("services.source_ingestion.main")
    module = importlib.reload(module)

    try:
        yield TestClient(module.app, headers=_read_headers()), Path(tempdir), module
    finally:
        for key, value in env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _connector(**overrides):
    payload = {
        "connector_id": "conn-openalex",
        "source_type": "paper",
        "provider": "OpenAlex",
        "license_scope": "open",
    }
    payload.update(overrides)
    return payload


def _record(**overrides):
    payload = {
        "source_id": "src-paper-1",
        "connector_id": "conn-openalex",
        "source_type": "paper",
        "title": "Paper 1",
        "content_ref": "https://example.test/paper-1",
    }
    payload.update(overrides)
    payload["metadata"] = {"tenant_id": "tenant-a", **dict(payload.get("metadata") or {})}
    return payload



def _serve_json(payload: dict, *, robots_txt: str | None = None):
    body = json.dumps(payload).encode("utf-8")
    robots_body = robots_txt.encode("utf-8") if robots_txt is not None else None

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib callback name
            if self.path == "/robots.txt" and robots_body is not None:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(robots_body)))
                self.end_headers()
                self.wfile.write(robots_body)
                return
            if self.path != "/feed.json":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}/feed.json"


def test_private_evidence_reads_authenticate_and_scope_the_existing_repository(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.runtime_auth_inbound import encode_jwt_hs256

    test_client, _, module = client
    secret = "source-read-test-secret"
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    body = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(connector_id="tenant-read"),
            "trace_id": "tenant-read-trace",
            "trigger_type": "manual",
            "records": [_record(connector_id="tenant-read", source_id="tenant-a-source", metadata={"tenant_id": "tenant-a", "body": "private"})],
        },
    )
    assert body.status_code == 201, body.text
    token = encode_jwt_hs256(
        {"sub": "reader", "roles": ["operator"], "organization": {"id": "tenant-a"}, "exp": int(__import__("time").time()) + 600},
        secret=secret,
    )
    headers = {"Authorization": f"Bearer {token}"}
    assert test_client.get("/api/source-ingest/source-records/tenant-a-source").status_code == 401
    source = test_client.get("/api/source-ingest/source-records/tenant-a-source", headers=headers)
    assert source.status_code == 200, source.text
    assert source.json()["source_record"]["source_id"] == "tenant-a-source"
    refs = body.json()["evidence_refs"]
    for path, key, wrapper in (
        ("/api/source-ingest/evidence/items", "evidence_item_ids", "items"),
        ("/api/source-ingest/evidence/bundles", "evidence_bundle_id", "bundles"),
        ("/api/source-ingest/evidence/knowledge-objects", "knowledge_object_ids", "knowledge_objects"),
    ):
        result = test_client.get(path, headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()[wrapper]
    assert test_client.get(f"/api/source-ingest/evidence/items/{refs['evidence_item_ids'][0]}", headers=headers).status_code == 200
    assert test_client.get(f"/api/source-ingest/evidence/bundles/{refs['evidence_bundle_id']}", headers=headers).status_code == 200
    assert test_client.get(f"/api/source-ingest/evidence/knowledge-objects/{refs['knowledge_object_ids'][0]}", headers=headers).status_code == 200
    assert test_client.get("/api/source-ingest/source-records/tenant-a-source", headers={**headers, "X-Tenant-Id": "tenant-b"}).status_code == 403
    assert test_client.get("/api/source-ingest/source-records/tenant-a-source", headers={"Authorization": "Bearer invalid"}).status_code == 401
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", "")
    assert test_client.get("/api/source-ingest/source-records", headers=headers).status_code == 503
    monkeypatch.setenv("PANTHEON_RUNTIME_JWT_SECRET", secret)
    multi = encode_jwt_hs256(
        {"sub": "reader", "roles": ["operator"], "tenant_ids": ["tenant-a", "tenant-b"], "exp": int(__import__("time").time()) + 600},
        secret=secret,
    )
    assert test_client.get("/api/source-ingest/source-records", headers={"Authorization": f"Bearer {multi}"}).status_code == 403
    other = encode_jwt_hs256(
        {"sub": "reader", "roles": ["operator"], "tenant_id": "tenant-b", "exp": int(__import__("time").time()) + 600},
        secret=secret,
    )
    assert test_client.get("/api/source-ingest/source-records/tenant-a-source", headers={"Authorization": f"Bearer {other}"}).status_code == 404

    class DownOwner:
        def list_source_records(self, **_kwargs):
            raise OSError("owner down")

    monkeypatch.setattr(module, "evidence_repository", DownOwner())
    assert test_client.get("/api/source-ingest/source-records", headers=headers).status_code == 503


def test_health_exposes_storage_contract(client) -> None:
    test_client, data_dir, _ = client

    response = test_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["service"] == "pantheon-source-ingest"
    assert body["store_path"] == str(data_dir / "ingest_schedule.jsonl")
    assert body["connector_store_path"] == str(data_dir / "connector_config.jsonl")
    assert body["source_evidence_path"] == str(data_dir / "source_evidence.jsonl")
    assert body["dlq_path"] == str(data_dir / "source_ingest_dlq.jsonl")
    assert body["audit_path"] == str(data_dir / "source_ingest_audit.jsonl")


def test_fleet_freshness_readiness_does_not_replay_journals_at_production_scale(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, module = client
    connector_count = 1_591
    calls = {"configs": 0, "schedules": 0, "snapshot": 0}

    class ConnectorStore:
        def list_configs(self):
            calls["configs"] += 1
            return [
                SimpleNamespace(
                    connector=SimpleNamespace(
                        connector_id=f"connector-{index}",
                        metadata={},
                    )
                )
                for index in range(connector_count)
            ]

        def get_config(self, _connector_id):
            raise AssertionError("fleet freshness must not perform point config reads")

    class ScheduleStore:
        def list_schedules(self):
            calls["schedules"] += 1
            return []

        def get_schedule(self, _connector_id):
            raise AssertionError("fleet freshness must not perform point schedule reads")

    class IngestStore:
        def read_freshness_snapshot(self):
            calls["snapshot"] += 1
            return {"runs": (), "receipts": (), "watermarks": {}}

        def list_runs(self):
            raise AssertionError("fleet freshness must not replay runs per connector")

        def list_receipts(self, **_kwargs):
            raise AssertionError("fleet freshness must not replay receipts per connector")

        def get_watermark(self, _connector_id):
            raise AssertionError("fleet freshness must not replay watermarks per connector")

    monkeypatch.setattr(module, "connector_store", ConnectorStore())
    monkeypatch.setattr(module, "schedule_config_store", ScheduleStore())
    monkeypatch.setattr(module, "store", IngestStore())
    result = module._source_freshness_readiness()

    assert calls == {"configs": 0, "schedules": 0, "snapshot": 0}
    assert result == {
        "status": "not_observed",
        "data_ready": False,
        "scheduled_connector_count": 0,
        "stale_connector_count": 0,
        "degraded_connector_count": 0,
        "reason": "controller_state_missing",
    }


def test_controller_readback_reads_each_store_once_at_production_scale(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, module = client
    connector_count = 1_591
    calls = {"configs": 0, "schedules": 0, "snapshot": 0, "records": 0}

    class ConnectorStore:
        def read_snapshot(self):
            calls["configs"] += 1
            configs = []
            fetch_states = {}
            for index in range(connector_count):
                connector_id = f"connector-{index}"
                connector = SimpleNamespace(
                    connector_id=connector_id,
                    metadata={},
                    to_dict=lambda connector_id=connector_id: {
                        "connector_id": connector_id
                    },
                )
                configs.append(SimpleNamespace(connector=connector))
                fetch_states[connector_id] = {
                    "connector_id": connector_id,
                    "attempts": 0,
                }
            return configs, fetch_states

        def list_configs(self):
            raise AssertionError("controller readback must not replay configs")

        def get_config(self, _connector_id):
            raise AssertionError("controller readback must not perform point config reads")

        def get_fetch_state(self, _connector_id):
            raise AssertionError("controller readback must not replay fetch state per connector")

    class ScheduleStore:
        def list_schedules(self):
            calls["schedules"] += 1
            return []

        def get_schedule(self, _connector_id):
            raise AssertionError("controller readback must not replay schedules per connector")

    class IngestStore:
        def read_freshness_snapshot(self):
            calls["snapshot"] += 1
            return {"runs": (), "receipts": (), "watermarks": {}}

        def list_runs(self):
            raise AssertionError("controller readback must not replay runs per connector")

        def list_receipts(self, **_kwargs):
            raise AssertionError("controller readback must not replay receipts per connector")

        def get_watermark(self, _connector_id):
            raise AssertionError("controller readback must not replay watermarks per connector")

    class EvidenceRepository:
        def list_source_records(self):
            calls["records"] += 1
            return []

    monkeypatch.setattr(module, "connector_store", ConnectorStore())
    monkeypatch.setattr(module, "schedule_config_store", ScheduleStore())
    monkeypatch.setattr(module, "store", IngestStore())
    monkeypatch.setattr(module, "evidence_repository", EvidenceRepository())

    result = module._controller_connector_readbacks()

    assert calls == {"configs": 1, "schedules": 1, "snapshot": 1, "records": 1}
    assert len(result) == connector_count


def test_registry_reads_each_persistent_store_once_at_production_scale(
    client,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, module = client
    connector_count = 1_591
    calls = {"configs": 0, "schedules": 0, "freshness": 0}
    configs = []
    fetch_states = {}
    for index in range(connector_count):
        connector_id = f"connector-{index}"
        configs.append(
            SimpleNamespace(
                connector=module.SourceConnector.from_dict(
                    _connector(connector_id=connector_id)
                ),
                fetch={"mode": "static_records", "records": []},
            )
        )
        fetch_states[connector_id] = {
            "connector_id": connector_id,
            "attempts": 0,
            "successful_attempts": 0,
            "failed_attempts": 0,
            "last_error": None,
            "updated_at": None,
        }

    class ConnectorStore:
        def read_snapshot(self):
            calls["configs"] += 1
            return configs, fetch_states

        def list_configs(self):
            raise AssertionError("registry must not replay configs")

        def get_config(self, _connector_id):
            raise AssertionError("registry must not perform point config reads")

        def get_fetch_state(self, _connector_id):
            raise AssertionError("registry must not replay fetch state per connector")

    class ScheduleStore:
        def list_schedules(self):
            calls["schedules"] += 1
            return []

        def get_schedule(self, _connector_id):
            raise AssertionError("registry must not replay schedules per connector")

    class IngestStore:
        def read_freshness_snapshot(self):
            calls["freshness"] += 1
            return {"runs": (), "receipts": (), "watermarks": {}}

        def list_runs(self):
            raise AssertionError("registry must not replay runs per connector")

        def list_receipts(self, **_kwargs):
            raise AssertionError("registry must not replay receipts per connector")

        def get_watermark(self, _connector_id):
            raise AssertionError("registry must not replay watermarks per connector")

    class Manager:
        def list_connectors(self):
            return []

        def get_connector(self, _connector_id):
            raise AssertionError("all scale-test connectors are configured")

    monkeypatch.setattr(module, "connector_store", ConnectorStore())
    monkeypatch.setattr(module, "schedule_config_store", ScheduleStore())
    monkeypatch.setattr(module, "store", IngestStore())
    monkeypatch.setattr(module, "manager", Manager())

    entries = module._source_connector_entries()
    policy = module._source_policy_registry_payload(entries)

    assert len(entries) == connector_count
    assert len(policy["connector_policies"]) == connector_count
    assert calls == {"configs": 1, "schedules": 1, "freshness": 1}


def test_trigger_success_persists_run_and_watermark_for_replay(client) -> None:
    test_client, data_dir, module = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(),
            "trace_id": "trace-source-ingest-success",
            "trigger_type": "manual",
            "next_watermark": "2026-04-28T18:00:00Z",
            "records": [_record()],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    run_id = body["run"]["ingest_run_id"]
    assert body["run"]["status"] == "completed"
    assert body["watermark"]["value"] == "2026-04-28T18:00:00Z"
    assert body["source_search_refresh"]["status"] == "not_configured"
    assert body["source_search_refresh"]["ingest_run_id"] == run_id
    assert body["run"]["events"][-1]["event_type"] == "SearchIndexRefreshObserved"
    assert (data_dir / "ingest_schedule.jsonl").exists()
    source_record = test_client.get("/api/source-ingest/source-records/src-paper-1", headers=_read_headers())
    assert source_record.status_code == 200
    assert source_record.json()["source_record"]["metadata"]["source_ingest_run_id"] == run_id

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    replayed_run = replay_client.get(f"/api/source-ingest/jobs/{run_id}")
    assert replayed_run.status_code == 200
    assert replayed_run.json()["run"]["status"] == "completed"
    replayed_events = replayed_run.json()["run"]["events"]
    assert replayed_events[-1]["event_type"] == "SearchIndexRefreshObserved"
    assert json.loads(replayed_events[-1]["message"])["status"] == "not_configured"
    replayed_watermark = replay_client.get("/api/source-ingest/watermarks/conn-openalex")
    assert replayed_watermark.status_code == 200
    assert replayed_watermark.json()["watermark"]["last_ingest_run_id"] == run_id


def test_provider_owned_job_parameters_are_accepted_by_jobs_api(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": {
                "connector_id": "us-fred-macro",
                "source_type": "macro",
                "provider": "FRED",
                "license_scope": "public_macro_reference",
            },
            "fetch": {
                "mode": "provider_owned_adapter",
                "adapter": "FredMacroSeriesAdapter.records_from_observations_payload",
                "adapter_config": {"max_records": 3},
                "request": {},
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "us-fred-macro",
            "trace_id": "trace-fred-job-parameters",
            "trigger_type": "srclive_api_regression",
            "job_parameters": {
                "series_id": "GDP",
                "csv_text": "observation_date,GDP\n2025-07-01,30623.1\n2025-10-01,30999.2\n",
            },
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["run"]["normalized_count"] == 2
    assert body["records"][0]["metadata"]["series_id"] == "GDP"
    assert body["records"][0]["metadata"]["fetch_mode"] == "public_csv_fallback"


def test_configured_connector_fetch_runs_without_inline_records_and_persists_evidence_refs(client) -> None:
    test_client, data_dir, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-autonomous-notes", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T20:00:00Z",
                "records": [
                    {
                        "source_id": "src-autonomous-note-1",
                        "title": "Autonomous note",
                        "content_ref": "memory://autonomous/note-1",
                        "metadata": {
                            "tenant_id": "tenant-a",
                            "body": "Autonomous source evidence persisted for downstream consumers.",
                            "access_scope": ["operator", "research"],
                            "keywords": ["autonomous", "evidence"],
                        },
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-autonomous-notes",
            "trace_id": "trace-source-ingest-autonomous",
            "trigger_type": "scheduled",
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["records"][0]["source_id"] == "src-autonomous-note-1"
    assert body["watermark"]["value"] == "2026-04-28T20:00:00Z"
    assert body["evidence_refs"]["source_ids"] == ["src-autonomous-note-1"]
    evidence_item_id = body["evidence_refs"]["evidence_item_ids"][0]
    evidence_bundle_id = body["evidence_refs"]["evidence_bundle_id"]
    assert evidence_bundle_id
    assert (data_dir / "connector_config.jsonl").exists()
    assert (data_dir / "source_evidence.jsonl").exists()

    source = test_client.get("/api/source-ingest/source-records/src-autonomous-note-1", headers=_read_headers())
    assert source.status_code == 200
    item = test_client.get(f"/api/source-ingest/evidence/items/{evidence_item_id}", headers=_read_headers())
    assert item.status_code == 200
    assert item.json()["item"]["source_id"] == "src-autonomous-note-1"
    bundle = test_client.get(f"/api/source-ingest/evidence/bundles/{evidence_bundle_id}", headers=_read_headers())
    assert bundle.status_code == 200
    assert bundle.json()["bundle"]["source_ids"] == ["src-autonomous-note-1"]

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    replayed_source = replay_client.get("/api/source-ingest/source-records/src-autonomous-note-1", headers=_read_headers())
    assert replayed_source.status_code == 200
    replayed_item = replay_client.get(f"/api/source-ingest/evidence/items/{evidence_item_id}", headers=_read_headers())
    assert replayed_item.status_code == 200


def test_source_evidence_normalization_sets_canonical_refs_and_dedupes_owner(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(connector_id="conn-doi-normalization", license_scope="open"),
            "trace_id": "trace-source-ingest-normalization",
            "trigger_type": "manual",
            "records": [
                _record(
                    source_id="src-paper-owner",
                    connector_id="conn-doi-normalization",
                    content_ref="https://doi.org/10.5555/Example.Paper",
                    metadata={
                        "body": "Canonical DOI evidence.",
                        "access_scope": ["research"],
                    },
                ),
                _record(
                    source_id="src-paper-duplicate",
                    connector_id="conn-doi-normalization",
                    content_ref="https://dx.doi.org/10.5555/example.paper",
                    metadata={
                        "body": "Canonical DOI evidence.",
                        "access_scope": ["research"],
                    },
                ),
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert set(body["evidence_refs"]["source_ids"]) == {"src-paper-owner"}

    sources = test_client.get("/api/source-ingest/source-records", headers=_read_headers())
    assert sources.status_code == 200
    persisted_sources = sources.json()["source_records"]
    assert [source["source_id"] for source in persisted_sources] == ["src-paper-owner"]
    metadata = persisted_sources[0]["metadata"]
    assert metadata["canonical_doi"] == "10.5555/example.paper"
    assert metadata["source_dedupe_key"] == "doi:10.5555/example.paper"
    assert metadata["content_hash"].startswith("sha256:")
    assert metadata["license_scope"] == "open"
    assert metadata["access_scope"] == ["research"]

    items = test_client.get("/api/source-ingest/evidence/items", headers=_read_headers())
    assert items.status_code == 200
    persisted_items = items.json()["items"]
    assert len(persisted_items) == 1
    assert persisted_items[0]["source_id"] == "src-paper-owner"
    assert persisted_items[0]["metadata"]["evidence_owner_id"] == persisted_items[0]["evidence_item_id"]


def test_registry_exposes_connector_status_policy_and_provider_examples(client) -> None:
    test_client, _, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(
                connector_id="conn-openalex-api",
                auth_type="api_key",
                secret_ref_id="env://OPENALEX_API_KEY",
                rate_limit_policy={
                    "requests_per_minute": 60,
                    "burst": 10,
                    "retry_after_seconds": 60,
                    "policy_ref": "source-ingest://policy/openalex",
                },
                license_policy={
                    "license_scope": "open",
                    "allowed_use": ["research", "search_index"],
                    "attribution_required": True,
                },
                source_metadata={
                    "display_name": "OpenAlex works",
                    "homepage_url": "https://openalex.org",
                    "tags": ["paper", "external_feed"],
                },
            ),
            "fetch": {
                "mode": "external_feed",
                "url": "https://api.openalex.org/works",
                "allowed_url_prefixes": ["https://api.openalex.org/"],
                "max_bytes": 4096,
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.get("/api/source-ingest/registry")

    assert response.status_code == 200, response.text
    body = response.json()
    entry = next(item for item in body["connectors"] if item["connector_id"] == "conn-openalex-api")
    assert entry["status"] == "enabled"
    assert entry["policy"]["auth"]["auth_type"] == "api_key"
    assert entry["policy"]["auth"]["secret_ref"]["secret_ref_id"] == "env://OPENALEX_API_KEY"
    assert entry["policy"]["rate_limit"]["requests_per_minute"] == 60
    assert entry["policy"]["license"]["allowed_use"] == ["research", "search_index"]
    assert entry["policy"]["source_metadata"]["display_name"] == "OpenAlex works"
    assert entry["fetch_policy"]["mode"] == "external_feed"
    assert entry["fetch_policy"]["url_host"] == "api.openalex.org"
    assert body["provider_examples"]
    assert {example["fetch_policy"]["mode"] for example in body["provider_examples"]} == {
        "static_records",
        "external_feed",
        "provider_owned_adapter",
    }
    catalog = body["financial_data_source_catalog"]
    assert catalog["schema_version"] == "financial_data_source_catalog.v1"
    assert catalog["summary"]["data_source_count"] == len(catalog["entries"])
    assert catalog["summary"]["data_source_count"] >= 10
    assert "FinMind" in catalog["summary"]["providers"]
    assert "TEJ" in catalog["summary"]["providers"]
    assert body["active_universe_policy"]["schema_version"] == "active_universe_scheduling_policy.v1"


def test_financial_data_source_catalog_endpoint_exposes_templates(client) -> None:
    test_client, _, _ = client

    response = test_client.get("/api/source-ingest/data-sources/financial-catalog")

    assert response.status_code == 200, response.text
    body = response.json()
    template_ids = {template["template_id"] for template in body["config_templates"]}
    templates_by_id = {template["template_id"]: template for template in body["config_templates"]}
    assert body["catalog_status"] == "template_only_not_live_ingestion_claim"
    assert "template-tw-tej-research-backfill" in template_ids
    assert "template-tw-mops-official-disclosures" in template_ids
    assert "template-us-sec-edgar-filings" in template_ids
    assert (
        templates_by_id["template-tw-twse-tpex-official-market"]["fetch"]["adapter"]
        == "TaiwanOfficialMarketDatasetAdapter.records_from_payload"
    )
    assert (
        templates_by_id["template-tw-mops-official-disclosures"]["fetch"]["adapter"]
        == "MopsSourceIngestAdapter.records_from_payload"
    )
    assert body["active_universe_policy"]["summary"]["archive_baseline_rule_count"] >= 3


def test_external_http_feed_is_allowlisted_bounded_and_preserves_license_access_scope(client) -> None:
    test_client, _, _ = client
    feed_payload = {
        "next_watermark": "2026-04-28T22:00:00Z",
        "records": [
            {
                "source_id": "src-http-feed-note-1",
                "title": "HTTP feed note",
                "content_ref": "https://feeds.example.test/source/http-note-1",
                "metadata": {
                    "body": "HTTP feed evidence is bounded and allowlisted.",
                    "keywords": ["http", "feed"],
                },
            }
        ],
    }
    server, feed_url = _serve_json(feed_payload)
    try:
        configured = test_client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": _connector(
                    connector_id="conn-http-feed-notes",
                    source_type="internal_note",
                    license_scope="internal",
                ),
                "fetch": {
                    "mode": "external_feed",
                    "network_scope": "internal_service",
                    "url": feed_url,
                    "allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"],
                    "timeout_seconds": 2,
                    "max_bytes": 4096,
                    "max_records": 3,
                    "default_access_scope": ["operator", "research"],
                },
            },
        )
        assert configured.status_code == 201, configured.text

        response = test_client.post(
            "/api/source-ingest/jobs",
            json={
                "connector_id": "conn-http-feed-notes",
                "trace_id": "trace-source-ingest-http-feed",
                "trigger_type": "scheduled",
            },
        )
        assert response.status_code == 201, response.text
    finally:
        server.shutdown()
        server.server_close()

    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["watermark"]["value"] == "2026-04-28T22:00:00Z"
    metadata = body["records"][0]["metadata"]
    assert metadata["license_scope"] == "internal"
    assert metadata["access_scope"] == ["operator", "research"]
    assert metadata["source_feed_url"] == feed_url
    assert body["evidence_refs"]["knowledge_object_ids"]


def test_news_connector_ingest_preserves_entitlement_pit_on_bundle(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(
                connector_id="conn-news-vendor",
                source_type="news",
                license_scope="vendor",
                license_policy={
                    "license_scope": "vendor",
                    "allowed_use": ["research", "search_index"],
                    "policy_ref": "source-ingest://license/news-vendor",
                },
                metadata={
                    "entitlement_tags": ["news-vendor-research"],
                    "access_scope": ["research"],
                },
            ),
            "trace_id": "trace-source-ingest-news",
            "trigger_type": "manual",
            "records": [
                _record(
                    source_id="src-news-vendor-1",
                    connector_id="conn-news-vendor",
                    source_type="news",
                    title="ACME earnings surprise",
                    content_ref="https://news.example.test/acme-earnings",
                    metadata={
                        "publisher": "Example News",
                        "published_at": "2026-05-01T12:00:00Z",
                        "event_time": "2026-05-01T12:00:00Z",
                        "available_time": "2026-05-01T12:01:00Z",
                        "body": "ACME reported an earnings surprise.",
                        "keywords": ["ACME", "earnings"],
                    },
                )
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    source = test_client.get("/api/source-ingest/source-records/src-news-vendor-1", headers=_read_headers())
    assert source.status_code == 200
    source_metadata = source.json()["source_record"]["metadata"]
    assert source_metadata["entitlement_tags"] == ["news-vendor-research"]
    assert source_metadata["available_time"] == "2026-05-01T12:01:00Z"
    assert source_metadata["pit"]["validated"] is True
    assert source_metadata["governance"]["direct_execution_allowed"] is False

    bundle = test_client.get(f"/api/source-ingest/evidence/bundles/{body['evidence_refs']['evidence_bundle_id']}", headers=_read_headers())
    assert bundle.status_code == 200
    bundle_payload = bundle.json()["bundle"]
    assert bundle_payload["available_time"] == "2026-05-01T12:01:00Z"
    assert bundle_payload["entitlement_tags"] == ["news-vendor-research"]
    assert bundle_payload["license_scope"] == "vendor"


def test_external_file_feed_runs_under_same_allowlist_contract(client) -> None:
    test_client, data_dir, _ = client
    feed_path = data_dir / "allowlisted-feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "next_watermark": "2026-04-28T22:30:00Z",
                "records": [
                    {
                        "source_id": "src-file-feed-note-1",
                        "title": "File feed note",
                        "content_ref": "file-feed://note-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    feed_url = feed_path.as_uri()

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-file-feed-notes", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": feed_url,
                "allowed_url_prefixes": [(data_dir / "").as_uri()],
                "max_bytes": 4096,
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-file-feed-notes",
            "trace_id": "trace-source-ingest-file-feed",
            "trigger_type": "scheduled",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["run"]["status"] == "completed"
    assert response.json()["watermark"]["value"] == "2026-04-28T22:30:00Z"


def test_external_feed_rejects_unallowlisted_url_at_configuration(client) -> None:
    test_client, _, _ = client

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-rejected-feed", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": "https://not-allowed.example.test/feed.json",
                "allowed_url_prefixes": ["https://allowed.example.test/"],
                "max_records": 3,
            },
        },
    )

    assert configured.status_code == 400
    assert "outside allowed_url_prefixes" in configured.json()["detail"]


def test_external_http_feed_respects_robots_disallow_and_routes_to_dlq(client) -> None:
    test_client, _, _ = client
    feed_payload = {
        "next_watermark": "2026-04-28T22:45:00Z",
        "records": [
            {
                "source_id": "src-robots-denied-note-1",
                "title": "Robots denied note",
                "content_ref": "https://feeds.example.test/source/robots-denied",
            }
        ],
    }
    server, feed_url = _serve_json(
        feed_payload,
        robots_txt="User-agent: pantheon-source-ingest\nDisallow: /feed.json\n",
    )
    try:
        configured = test_client.post(
            "/api/source-ingest/connectors",
            json={
                "connector": _connector(connector_id="conn-robots-denied", source_type="internal_note"),
                "fetch": {
                    "mode": "external_feed",
                    "network_scope": "internal_service",
                    "url": feed_url,
                    "allowed_url_prefixes": [feed_url.rsplit("/", 1)[0] + "/"],
                    "timeout_seconds": 2,
                    "max_bytes": 4096,
                    "max_records": 3,
                },
            },
        )
        assert configured.status_code == 201, configured.text

        failed = test_client.post(
            "/api/source-ingest/jobs",
            json={
                "connector_id": "conn-robots-denied",
                "trace_id": "trace-source-ingest-robots-denied",
                "trigger_type": "scheduled",
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert "robots.txt disallows" in body["dlq_entries"][0]["reason"]
    watermark = test_client.get("/api/source-ingest/watermarks/conn-robots-denied")
    assert watermark.status_code == 404


def test_external_feed_size_failure_routes_to_dlq_without_advancing_watermark(client) -> None:
    test_client, data_dir, _ = client
    feed_path = data_dir / "oversized-feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "next_watermark": "2026-04-28T23:00:00Z",
                "records": [
                    {
                        "source_id": "src-oversized-feed-note-1",
                        "title": "Oversized feed note",
                        "content_ref": "file-feed://oversized-note-1",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-oversized-feed", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": feed_path.as_uri(),
                "allowed_url_prefixes": [(data_dir / "").as_uri()],
                "max_bytes": 16,
                "max_records": 3,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-oversized-feed",
            "trace_id": "trace-source-ingest-oversized-feed",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert body["watermark"] is None
    assert "exceeds fetch.max_bytes=16" in body["dlq_entries"][0]["reason"]
    watermark = test_client.get("/api/source-ingest/watermarks/conn-oversized-feed")
    assert watermark.status_code == 404


def test_external_feed_record_count_failure_routes_to_dlq_without_advancing_watermark(client) -> None:
    test_client, data_dir, _ = client
    feed_path = data_dir / "too-many-records-feed.json"
    feed_path.write_text(
        json.dumps(
            {
                "next_watermark": "2026-04-28T23:10:00Z",
                "records": [
                    {
                        "source_id": "src-too-many-feed-note-1",
                        "title": "Too many feed note 1",
                        "content_ref": "file-feed://too-many-note-1",
                    },
                    {
                        "source_id": "src-too-many-feed-note-2",
                        "title": "Too many feed note 2",
                        "content_ref": "file-feed://too-many-note-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-too-many-feed", source_type="internal_note"),
            "fetch": {
                "mode": "external_feed",
                "url": feed_path.as_uri(),
                "allowed_url_prefixes": [(data_dir / "").as_uri()],
                "max_bytes": 4096,
                "max_records": 1,
            },
        },
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-too-many-feed",
            "trace_id": "trace-source-ingest-too-many-feed",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert "exceeds fetch.max_records=1" in body["dlq_entries"][0]["reason"]
    watermark = test_client.get("/api/source-ingest/watermarks/conn-too-many-feed")
    assert watermark.status_code == 404


def test_configured_failure_dlq_audit_preserves_existing_watermark(client) -> None:
    test_client, _, _ = client
    connector = _connector(connector_id="conn-watermarked-failure", source_type="internal_note")
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": connector,
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T20:00:00Z",
                "records": [
                    {
                        "source_id": "src-watermark-baseline",
                        "title": "Watermark baseline",
                        "content_ref": "memory://watermark/baseline",
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text
    completed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-watermarked-failure",
            "trace_id": "trace-watermark-baseline",
            "trigger_type": "scheduled",
        },
    )
    assert completed.status_code == 201, completed.text
    first_run_id = completed.json()["run"]["ingest_run_id"]

    reconfigured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": connector,
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T21:00:00Z",
                "fail_until_attempt": 3,
                "failure_reason": "bounded feed temporarily unavailable",
                "records": [
                    {
                        "source_id": "src-watermark-newer",
                        "title": "Watermark newer",
                        "content_ref": "memory://watermark/newer",
                    }
                ],
            },
        },
    )
    assert reconfigured.status_code == 201, reconfigured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-watermarked-failure",
            "trace_id": "trace-watermark-failure",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    body = failed.json()
    assert body["run"]["status"] == "failed"
    assert body["watermark"]["value"] == "2026-04-28T20:00:00Z"
    assert body["watermark"]["last_ingest_run_id"] == first_run_id
    assert body["dlq_entries"][0]["event"]["payload"]["watermark"] == "2026-04-28T20:00:00Z"
    assert body["audit_actions"][0]["action_type"] == "source_ingestion.scheduled_run.dead_lettered"
    assert body["audit_actions"][0]["metadata"]["watermark"] == "2026-04-28T20:00:00Z"

    replayed_watermark = test_client.get("/api/source-ingest/watermarks/conn-watermarked-failure")
    assert replayed_watermark.status_code == 200
    assert replayed_watermark.json()["watermark"]["value"] == "2026-04-28T20:00:00Z"
    assert replayed_watermark.json()["watermark"]["last_ingest_run_id"] == first_run_id


def test_configured_connector_preserves_per_record_access_scope_for_search_index(client) -> None:
    test_client, data_dir, _ = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(
                connector_id="conn-mixed-scope-notes",
                source_type="internal_note",
                license_scope="internal",
            ),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T20:30:00Z",
                "records": [
                    {
                        "source_id": "src-public-momentum-note",
                        "title": "Public momentum note",
                        "content_ref": "memory://autonomous/public-momentum-note",
                        "metadata": {
                            "tenant_id": "tenant-a",
                            "body": "Momentum volatility evidence visible to operator research.",
                            "access_scope": ["operator", "research"],
                            "keywords": ["momentum", "volatility"],
                        },
                    },
                    {
                        "source_id": "src-private-momentum-note",
                        "title": "Private momentum note",
                        "content_ref": "memory://autonomous/private-momentum-note",
                        "metadata": {
                            "tenant_id": "tenant-a",
                            "body": "Momentum volatility evidence limited to risk committee.",
                            "access_scope": ["risk-committee"],
                            "keywords": ["momentum", "volatility"],
                        },
                    },
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-mixed-scope-notes",
            "trace_id": "trace-source-ingest-mixed-scope",
            "trigger_type": "scheduled",
        },
    )
    assert response.status_code == 201, response.text
    knowledge_object_ids = response.json()["evidence_refs"]["knowledge_object_ids"]
    assert len(knowledge_object_ids) == 2

    listed = test_client.get("/api/source-ingest/evidence/knowledge-objects", headers=_read_headers())
    assert listed.status_code == 200
    by_id = {item["knowledge_object_id"]: item for item in listed.json()["knowledge_objects"]}
    assert by_id[knowledge_object_ids[0]]["access_scope"] == ["operator", "research"]
    assert by_id[knowledge_object_ids[1]]["access_scope"] == ["risk-committee"]

    from services.search.main import create_app

    search_client = TestClient(create_app(data_dir / "search-index.jsonl", data_dir / "source_evidence.jsonl"))
    search_response = search_client.post(
        "/api/search/query",
        json={
            "request_id": "search-mixed-scope",
            "trace_id": "trace-search-mixed-scope",
            "query": "momentum volatility",
            "persona_id": "operator-workbench",
            "workspace_id": "research-workbench",
            "tenant_id": "tenant-a",
            "source_types": ["internal_note"],
            "access_context": {
                "persona_id": "operator-workbench",
                "workspace_id": "research-workbench",
                "environment": "paper",
                "tenant_id": "tenant-a",
                "access_scopes": ["operator", "research"],
                "license_scopes": ["internal"],
            },
        },
    )
    assert search_response.status_code == 200, search_response.text
    search_payload = search_response.json()
    assert [item["result_id"] for item in search_payload["results"]] == [knowledge_object_ids[0]]
    assert search_payload["results"][0]["citations"] == ["Public momentum note"]
    assert search_payload["rejected_items_count"] == 1


def test_dlq_replay_retries_configured_failure_and_persists_status(client) -> None:
    test_client, _, module = client
    configured = test_client.post(
        "/api/source-ingest/connectors",
        json={
            "connector": _connector(connector_id="conn-replay-notes", source_type="internal_note"),
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-04-28T21:00:00Z",
                "fail_until_attempt": 2,
                "failure_reason": "upstream fixture unavailable",
                "records": [
                    {
                        "source_id": "src-replayed-note-1",
                        "title": "Replayed note",
                        "content_ref": "memory://autonomous/replayed-note-1",
                            "metadata": {"tenant_id": "tenant-a"},
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    failed = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-replay-notes",
            "trace_id": "trace-source-ingest-replay",
            "trigger_type": "scheduled",
        },
    )
    assert failed.status_code == 201, failed.text
    assert failed.json()["run"]["status"] == "failed"
    assert failed.json()["dlq_entries"][0]["status"] == "pending"

    replay = test_client.post(
        "/api/source-ingest/dlq/replay",
        json={"tag": "retry_exhausted", "reason": "test replay after configured recovery"},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["summary"]["applied"] == 1

    watermark = test_client.get("/api/source-ingest/watermarks/conn-replay-notes")
    assert watermark.status_code == 200
    assert watermark.json()["watermark"]["value"] == "2026-04-28T21:00:00Z"
    source = test_client.get("/api/source-ingest/source-records/src-replayed-note-1", headers=_read_headers())
    assert source.status_code == 200
    replayed_dlq = test_client.get("/api/source-ingest/dlq?status=replayed")
    assert replayed_dlq.status_code == 200
    assert len(replayed_dlq.json()["entries"]) == 1

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    durable_replayed_dlq = replay_client.get("/api/source-ingest/dlq?status=replayed")
    assert durable_replayed_dlq.status_code == 200
    assert len(durable_replayed_dlq.json()["entries"]) == 1


def test_rejected_records_route_to_replayable_dlq_and_audit(client) -> None:
    test_client, data_dir, module = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(),
            "trace_id": "trace-source-ingest-rejected",
            "trigger_type": "manual",
            "next_watermark": "2026-04-28T19:00:00Z",
            "records": [
                _record(
                    source_id="src-rejected-paper",
                    status="rejected",
                    metadata={"reject_reason": "license scope denied"},
                )
            ],
        },
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "rejected"
    assert body["watermark"] is None
    assert body["dlq_entries"][0]["reason"] == "license scope denied"
    assert body["audit_actions"][0]["action_type"] == "source_ingestion.source_record.dead_lettered"
    assert (data_dir / "source_ingest_dlq.jsonl").exists()
    assert (data_dir / "source_ingest_audit.jsonl").exists()

    reloaded = importlib.reload(module)
    replay_client = TestClient(reloaded.app)
    replayed_dlq = replay_client.get("/api/source-ingest/dlq")
    assert replayed_dlq.status_code == 200
    assert replayed_dlq.json()["entries"][0]["reason"] == "license scope denied"
    replayed_audit = replay_client.get("/api/source-ingest/audit")
    assert replayed_audit.status_code == 200
    assert replayed_audit.json()["actions"][0]["action_type"] == "source_ingestion.source_record.dead_lettered"


def test_trigger_enforces_bounded_batch_size(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector": _connector(),
            "trace_id": "trace-source-ingest-too-large",
            "records": [
                _record(source_id="src-1"),
                _record(source_id="src-2"),
                _record(source_id="src-3"),
                _record(source_id="src-4"),
            ],
        },
    )

    assert response.status_code == 413
    assert "SOURCE_INGEST_MAX_RECORDS=3" in response.json()["detail"]


def test_trigger_jobs_payload_forbids_extra_fields(client) -> None:
    test_client, _, _ = client
    response = test_client.post(
        "/api/source-ingest/jobs",
        json={
            "connector_id": "conn-openalex",
            "mode": "bounded_pull",
            "trace_id": "trace-extra-field",
        },
    )

    assert response.status_code == 422
    body = response.json()
    assert "extra_forbidden" in json.dumps(body) or "extra fields not permitted" in json.dumps(body).lower()


def test_controller_readback_thread_safety(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, _, module = client
    lock_acquired = False

    original_payload_func = module._controller_readback_payload

    def mock_payload():
        nonlocal lock_acquired
        # Since RLock is reentrant, acquiring non-blocking from within the lock holder returns True:
        acquired_again = module.authoritative_reconcile_lock.acquire(blocking=False)
        if acquired_again:
            lock_acquired = True
            module.authoritative_reconcile_lock.release()
        return original_payload_func()

    monkeypatch.setattr(module, "_controller_readback_payload", mock_payload)

    response = test_client.get("/api/source-ingest/controller/readback")
    assert response.status_code == 200
    assert lock_acquired is True


def test_controller_provisioned_official_connector_stamps_requirement_tenant_and_enforces_strict_tenant_reads(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _, module = client
    monkeypatch.setenv("PANTHEON_ENV", "dev")

    from services.source_ingestion.controller_state import ControllerState, ControllerStateStore

    state = ControllerState(
        controller_id="ctrl-test-state-1",
        controller_name="test-controller",
        environment="test",
        tenant_id="tenant-dev",
        deployment={},
    )
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(state)

    # 1. Controller provisions connector from persona requirement snapshot
    persona = {
        "persona_id": "persona-tw-momentum",
        "name": "TW Momentum",
        "mandate": "Trade TW daily momentum",
        "lifecycle_state": "research_only",
        "created_at": "2026-06-01T00:00:00Z",
        "required_data_sources": [
            {
                "dataset": "tw_price_daily",
                "market": "TW",
                "cadence": "daily",
                "source_class": "live_pull",
                "connector_candidates": ["tw-twse-tpex-official-market"],
                "policy_gates": ["require_connector_approved", "require_schedule_active"],
            }
        ],
    }
    controller_headers = {"Authorization": f"Bearer {module.controller_token}"}
    reconcile_res = test_client.post(
        "/api/source-ingest/persona-source-provisioning/reconcile",
        headers=controller_headers,
        json={"persona": persona},
    )
    assert reconcile_res.status_code == 200, reconcile_res.text

    # Provisioned connector carries requirement tenant
    config = module.connector_store.get_config("tw-twse-tpex-official-market")
    assert config is not None

    # 2. Trigger ingest job for the provisioned connector
    job_res = test_client.post(
        "/api/source-ingest/jobs",
        headers=controller_headers,
        json={
            "connector_id": "tw-twse-tpex-official-market",
            "trace_id": "trace-official-tw-run-test",
            "trigger_type": "scheduled",
            "records": [
                {
                    "source_id": "tw-official:tw_price_daily:TWSE:2330:2026-10-07",
                    "connector_id": "tw-twse-tpex-official-market",
                    "source_type": "market",
                    "title": "2330 Daily Close",
                    "content_ref": "tw-official://tw_price_daily/TWSE/2330/2026-10-07",
                    "status": "normalized",
                    "metadata": {
                        "provider": "TWSE OpenAPI",
                        "dataset": "tw_price_daily",
                        "market": "TW",
                        "venue": "TWSE",
                        "symbol": "2330",
                        "available_time": "2026-10-07T05:30:00Z",
                        "event_time": "2026-10-07T05:30:00Z",
                    },
                }
            ],
        },
    )
    assert job_res.status_code == 201, job_res.text
    body = job_res.json()
    refs = body["evidence_refs"]
    bundle_id = refs["evidence_bundle_id"]
    item_id = refs["evidence_item_ids"][0]

    # 3. Controller readback reflects requirement tenant in latest_source_record
    readback = test_client.get("/api/source-ingest/controller/readback")
    assert readback.status_code == 200
    conn_readback = next(c for c in readback.json()["connectors"] if c["connector_id"] == "tw-twse-tpex-official-market")
    assert conn_readback["latest_source_record"]["metadata"]["tenant_id"] == "tenant-dev"
    assert conn_readback["latest_source_record"]["provenance"]["tenant_id"] == "tenant-dev"

    # 4. Strict tenant-scoped reads: admitted tenant (tenant-dev) can read
    dev_headers = _read_headers(tenant="tenant-dev")

    # Source record read
    rec_dev = test_client.get("/api/source-ingest/source-records/tw-official:tw_price_daily:TWSE:2330:2026-10-07", headers=dev_headers)
    assert rec_dev.status_code == 200
    assert rec_dev.json()["source_record"]["metadata"]["tenant_id"] == "tenant-dev"

    # Evidence bundle read & list
    bundle_dev = test_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}", headers=dev_headers)
    assert bundle_dev.status_code == 200
    assert bundle_dev.json()["bundle"]["evidence_bundle_id"] == bundle_id
    assert bundle_dev.json()["bundle"]["metadata"]["tenant_id"] == "tenant-dev"

    bundles_dev = test_client.get("/api/source-ingest/evidence/bundles", headers=dev_headers)
    assert bundles_dev.status_code == 200
    assert bundle_id in [b["evidence_bundle_id"] for b in bundles_dev.json()["bundles"]]

    # Evidence item read
    item_dev = test_client.get(f"/api/source-ingest/evidence/items/{item_id}", headers=dev_headers)
    assert item_dev.status_code == 200
    assert item_dev.json()["item"]["metadata"]["tenant_id"] == "tenant-dev"

    # 5. Strict tenant isolation: other tenant (tenant-other) gets 404 / empty list
    other_headers = _read_headers(tenant="tenant-other")
    assert test_client.get("/api/source-ingest/source-records/tw-official:tw_price_daily:TWSE:2330:2026-10-07", headers=other_headers).status_code == 404
    assert test_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}", headers=other_headers).status_code == 404
    assert test_client.get(f"/api/source-ingest/evidence/items/{item_id}", headers=other_headers).status_code == 404
    bundles_other = test_client.get("/api/source-ingest/evidence/bundles", headers=other_headers)
    assert bundles_other.status_code == 200
    assert bundle_id not in [b["evidence_bundle_id"] for b in bundles_other.json()["bundles"]]

    # 6. Strict tenant isolation: untenanted / anonymous requests rejected
    anon_client = TestClient(module.app)
    assert anon_client.get("/api/source-ingest/source-records/tw-official:tw_price_daily:TWSE:2330:2026-10-07").status_code == 401
    assert anon_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}").status_code == 401
    assert anon_client.get(f"/api/source-ingest/evidence/items/{item_id}").status_code == 401
    assert anon_client.get("/api/source-ingest/evidence/bundles").status_code == 401


def test_postgres_backend_cross_process_evidence_visibility_via_api():
    """API server observes committed scheduler evidence from Postgres without restart."""
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn

    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("No accessible PostgreSQL instance found for real PG tests")
    from unittest import mock
    import psycopg
    import uuid
    from services.knowledge.evidence import EvidenceBundle, EvidenceItem
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    schema = f"test_api_cross_{uuid.uuid4().hex[:12]}"
    table = f"{schema}.source_evidence"
    tempdir = tempfile.mkdtemp(prefix="source_ingest_pg_api_")

    env_overrides = {
        "SOURCE_INGEST_DATA_DIR": tempdir,
        "SOURCE_INGEST_MAX_RECORDS": "3",
        "PANTHEON_RUNTIME_JWT_SECRET": "source-test-secret",
        "SOURCE_INGEST_EVIDENCE_BACKEND": "postgres",
        "SOURCE_INGEST_EVIDENCE_DSN": dsn,
        "SOURCE_INGEST_EVIDENCE_TABLE": table,
        "SOURCE_INGEST_EVIDENCE_BOOTSTRAP": "1",
    }
    with mock.patch.dict(os.environ, env_overrides):
        sys.modules.pop("services.source_ingestion.main", None)
        module = importlib.import_module("services.source_ingestion.main")
        module = importlib.reload(module)
        api_client = TestClient(module.app)

        try:
            # Scheduler process: separate PostgresSourceEvidenceRepository instance
            scheduler_repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=False)
            tenant_dev = "tenant-dev"

            source = SourceRecord(
                source_id="src-api-cross-1",
                connector_id="tw-twse-tpex-official-market",
                source_type="market",
                title="2330 Daily Close",
                content_ref="tw-official://tw_price_daily/TWSE/2330/2026-10-07",
                status="normalized",
                metadata={"tenant_id": tenant_dev, "source_dedupe_key": "dk-api-src-1"},
            )
            scheduler_repo.add_source_record(source)

            item = EvidenceItem(
                evidence_item_id="item-api-cross-1",
                source_id=source.source_id,
                item_type="metric_series",
                content_ref=source.content_ref,
                citation_label="TWSE:2330",
                body="2330 daily close evidence from scheduler.",
                metadata={"tenant_id": tenant_dev, "evidence_dedupe_key": "dk-api-item-1"},
            )
            scheduler_repo.add_evidence_item(item)

            bundle = EvidenceBundle(
                evidence_bundle_id="bundle-api-cross-1",
                source_ids=[source.source_id],
                evidence_item_ids=[item.evidence_item_id],
                summary="Scheduler committed bundle",
                citation_refs=["TWSE:2330"],
                confidence=1.0,
                license_scope="official",
                access_scope=["internal"],
                created_by="source-ingest-scheduler",
                metadata={
                    "connector_id": "tw-twse-tpex-official-market",
                    "ingest_run_id": "ingest-scheduled-cross-1",
                    "tenant_id": tenant_dev,
                },
            )
            scheduler_repo.add_bundle(bundle)

            # API server observes the bundle committed by scheduler without restart
            headers_dev = _read_headers(tenant=tenant_dev)
            res = api_client.get("/api/source-ingest/evidence/bundles", headers=headers_dev)
            assert res.status_code == 200
            bundle_ids = [b["evidence_bundle_id"] for b in res.json()["bundles"]]
            assert "bundle-api-cross-1" in bundle_ids

            single_res = api_client.get("/api/source-ingest/evidence/bundles/bundle-api-cross-1", headers=headers_dev)
            assert single_res.status_code == 200
            assert single_res.json()["bundle"]["metadata"]["ingest_run_id"] == "ingest-scheduled-cross-1"

            # Strict tenant isolation
            headers_other = _read_headers(tenant="tenant-other")
            other_res = api_client.get("/api/source-ingest/evidence/bundles", headers=headers_other)
            assert other_res.status_code == 200
            assert "bundle-api-cross-1" not in [b["evidence_bundle_id"] for b in other_res.json()["bundles"]]
            assert api_client.get("/api/source-ingest/evidence/bundles/bundle-api-cross-1", headers=headers_other).status_code == 404
        finally:
            with psycopg.connect(dsn) as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_postgres_backend_adversarial_db_failure_cannot_return_stale_cache_via_api():
    """Adversarial proof: loaded bootcache then DB failure cannot HTTP 200 with stale evidence."""
    from services.source_ingestion.test_pg_store import _get_test_pg_dsn

    dsn = _get_test_pg_dsn()
    if not dsn:
        pytest.skip("No accessible PostgreSQL instance found for real PG tests")
    from unittest import mock
    import psycopg
    import uuid
    from services.knowledge.evidence import EvidenceBundle, EvidenceItem, KnowledgeObject
    from services.source_ingestion.connectors.base import SourceRecord
    from services.source_ingestion.pg_store import PostgresSourceEvidenceRepository

    schema = f"test_api_adv_{uuid.uuid4().hex[:12]}"
    table = f"{schema}.source_evidence"
    tempdir = tempfile.mkdtemp(prefix="source_ingest_adv_")

    env_overrides = {
        "SOURCE_INGEST_DATA_DIR": tempdir,
        "SOURCE_INGEST_MAX_RECORDS": "3",
        "PANTHEON_RUNTIME_JWT_SECRET": "source-test-secret",
        "SOURCE_INGEST_EVIDENCE_BACKEND": "postgres",
        "SOURCE_INGEST_EVIDENCE_DSN": dsn,
        "SOURCE_INGEST_EVIDENCE_TABLE": table,
        "SOURCE_INGEST_EVIDENCE_BOOTSTRAP": "1",
    }
    with mock.patch.dict(os.environ, env_overrides):
        tenant_dev = "tenant-adv"
        headers = _read_headers(tenant=tenant_dev)

        # Step 1: Pre-populate data in Postgres before server boots
        bootstrap_repo = PostgresSourceEvidenceRepository(dsn=dsn, table=table, bootstrap=True)
        source = SourceRecord(
            source_id="src-adv-001",
            connector_id="tw-twse-tpex-official-market",
            source_type="market",
            title="Adversarial Source",
            content_ref="tw-official://tw_price_daily/TWSE/2330/2026-10-07",
            status="normalized",
            metadata={"tenant_id": tenant_dev, "source_dedupe_key": "dk-adv-src"},
        )
        bootstrap_repo.add_source_record(source)

        item = EvidenceItem(
            evidence_item_id="item-adv-001",
            source_id=source.source_id,
            item_type="metric_series",
            content_ref=source.content_ref,
            citation_label="TWSE:2330",
            body="Adversarial item body.",
            metadata={"tenant_id": tenant_dev, "evidence_dedupe_key": "dk-adv-item"},
        )
        bootstrap_repo.add_evidence_item(item)

        bundle = EvidenceBundle(
            evidence_bundle_id="bundle-adv-001",
            source_ids=[source.source_id],
            evidence_item_ids=[item.evidence_item_id],
            summary="Adversarial bundle summary",
            citation_refs=["TWSE:2330"],
            confidence=1.0,
            license_scope="official",
            access_scope=["internal"],
            created_by="source-ingest-tester",
            metadata={"tenant_id": tenant_dev},
        )
        bootstrap_repo.add_bundle(bundle)

        ko = KnowledgeObject(
            knowledge_object_id="ko-adv-001",
            source_id=source.source_id,
            evidence_item_id=item.evidence_item_id,
            evidence_bundle_id=bundle.evidence_bundle_id,
            title=source.title,
            text=item.body,
            source_type="market",
            license_scope="official",
            access_scope=["internal"],
            metadata={"tenant_id": tenant_dev},
        )
        bootstrap_repo.add_knowledge_object(ko)

        # Step 2: Boot API server. During startup, reload() populates the in-memory boot cache.
        sys.modules.pop("services.source_ingestion.main", None)
        module = importlib.import_module("services.source_ingestion.main")
        module = importlib.reload(module)
        api_client = TestClient(module.app)

        try:
            # Healthy verification: API returns 200 with persisted record
            healthy_res = api_client.get(f"/api/source-ingest/source-records/{source.source_id}", headers=headers)
            assert healthy_res.status_code == 200

            # Step 3: Adversarial situation: drop the table in Postgres to cause genuine DB query failure
            with psycopg.connect(dsn) as conn:
                conn.execute(f'DROP TABLE "{schema}"."source_evidence"')

            # Step 4: Assert API fails closed (503), CANNOT return HTTP 200 with stale bootcache
            endpoints = [
                f"/api/source-ingest/source-records/{source.source_id}",
                "/api/source-ingest/source-records",
                f"/api/source-ingest/evidence/items/{item.evidence_item_id}",
                "/api/source-ingest/evidence/items",
                f"/api/source-ingest/evidence/bundles/{bundle.evidence_bundle_id}",
                "/api/source-ingest/evidence/bundles",
                f"/api/source-ingest/evidence/knowledge-objects/{ko.knowledge_object_id}",
                "/api/source-ingest/evidence/knowledge-objects",
            ]
            for ep in endpoints:
                res = api_client.get(ep, headers=headers)
                assert res.status_code != 200, f"Endpoint {ep} unexpectedly returned 200 with stale cache"
                assert res.status_code == 503, f"Endpoint {ep} returned {res.status_code}, expected 503"
                assert "stale" not in res.text.lower()
        finally:
            with psycopg.connect(dsn) as conn:
                conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def test_controller_owned_connector_resolves_tenant_from_controller_state(client, monkeypatch: pytest.MonkeyPatch) -> None:
    test_client, data_dir, module = client
    monkeypatch.delenv("PANTHEON_TENANT_ID", raising=False)
    monkeypatch.delenv("PANTHEON_BFF_TENANT_ID", raising=False)

    from services.source_ingestion.controller_state import ControllerState, ControllerStateStore

    state = ControllerState(
        controller_id="ctrl-test-state-1",
        controller_name="test-controller",
        environment="test",
        tenant_id="tenant-dev",
        deployment={},
    )
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(state)

    from services.source_ingestion.persona_source_reconciler import RECONCILIATION_METADATA_KEY

    controller_headers = {"Authorization": f"Bearer {module.controller_token}"}
    configured = test_client.post(
        "/api/source-ingest/connectors",
        headers=controller_headers,
        json={
            "connector": {
                "connector_id": "conn-reconciled-managed",
                "source_type": "market",
                "provider": "TW_OFFICIAL",
                "license_scope": "official",
                "metadata": {
                    RECONCILIATION_METADATA_KEY: {
                        "managed_by": "persona_source_provisioning_reconciler",
                    },
                },
            },
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-10-07T12:00:00Z",
                "records": [
                    {
                        "source_id": "src-managed-reconciled-1",
                        "title": "Managed test record",
                        "content_ref": "memory://managed/1",
                        "metadata": {
                            "body": "Reconciled market payload",
                            "access_scope": ["internal"],
                        },
                    }
                ],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    response = test_client.post(
        "/api/source-ingest/jobs",
        headers=controller_headers,
        json={
            "connector_id": "conn-reconciled-managed",
            "trace_id": "trace-controller-state-tenant",
            "trigger_type": "scheduled",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run"]["status"] == "completed"
    assert body["records"][0]["metadata"]["tenant_id"] == "tenant-dev"
    bundle_id = body["evidence_refs"]["evidence_bundle_id"]
    assert bundle_id

    dev_headers = _read_headers("tenant-dev")
    dev_headers["X-Tenant-Id"] = "tenant-dev"
    bundle_res = test_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}", headers=dev_headers)
    assert bundle_res.status_code == 200
    assert bundle_res.json()["bundle"]["metadata"]["tenant_id"] == "tenant-dev"

    list_res = test_client.get("/api/source-ingest/evidence/bundles", headers=dev_headers)
    assert list_res.status_code == 200
    bundle_ids = [b["evidence_bundle_id"] for b in list_res.json()["bundles"]]
    assert bundle_id in bundle_ids

    wrong_headers = _read_headers("tenant-wrong")
    wrong_headers["X-Tenant-Id"] = "tenant-wrong"
    wrong_res = test_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}", headers=wrong_headers)
    assert wrong_res.status_code == 404

    wrong_list_res = test_client.get("/api/source-ingest/evidence/bundles", headers=wrong_headers)
    assert wrong_list_res.status_code == 200
    assert bundle_id not in [b["evidence_bundle_id"] for b in wrong_list_res.json()["bundles"]]


def test_controller_owned_connector_without_controller_state_rejects_job_and_persists_nothing(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _, module = client
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-env")
    monkeypatch.setenv("PANTHEON_BFF_TENANT_ID", "tenant-bff-env")

    # Ensure no controller state exists
    state_path = module.runtime.CONTROLLER_STATE_PATH
    if state_path.exists():
        state_path.unlink()

    from services.source_ingestion.persona_source_reconciler import RECONCILIATION_METADATA_KEY

    controller_headers = {"Authorization": f"Bearer {module.controller_token}"}
    configured = test_client.post(
        "/api/source-ingest/connectors",
        headers=controller_headers,
        json={
            "connector": {
                "connector_id": "conn-ctrl-no-state",
                "source_type": "market",
                "provider": "TW_OFFICIAL",
                "license_scope": "official",
                "metadata": {
                    RECONCILIATION_METADATA_KEY: {
                        "managed_by": "persona_source_provisioning_reconciler",
                    },
                },
            },
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-10-08T00:00:00Z",
                "records": [],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    record_id = "tw-official:tw_price_daily:TWSE:2330:2026-10-08-no-state"
    job_res = test_client.post(
        "/api/source-ingest/jobs",
        headers=controller_headers,
        json={
            "connector_id": "conn-ctrl-no-state",
            "trace_id": "trace-test-no-state",
            "trigger_type": "scheduled",
            "records": [
                {
                    "source_id": record_id,
                    "connector_id": "conn-ctrl-no-state",
                    "source_type": "market",
                    "title": "2330 Daily Close",
                    "content_ref": "tw-official://tw_price_daily/TWSE/2330/2026-10-08",
                    "status": "normalized",
                    "metadata": {
                        "provider": "TWSE OpenAPI",
                        "dataset": "tw_price_daily",
                        "market": "TW",
                        "venue": "TWSE",
                        "symbol": "2330",
                        "available_time": "2026-10-08T05:30:00Z",
                        "event_time": "2026-10-08T05:30:00Z",
                    },
                }
            ],
        },
    )
    assert job_res.status_code >= 400
    assert "controller tenant identity is unavailable" in job_res.text

    # Verify no source record, evidence bundle, or distillation admission was persisted
    assert module.evidence_repository.get_source_record(record_id) is None
    assert len(module.evidence_repository.list_bundles()) == 0
    assert module.distillation_job_queue.count() == 0


def test_controller_owned_connector_resolves_only_controller_state_ignoring_env_and_stale_metadata(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_client, _, module = client
    monkeypatch.setenv("PANTHEON_TENANT_ID", "tenant-env")

    from services.source_ingestion.controller_state import ControllerState, ControllerStateStore
    from services.source_ingestion.persona_source_reconciler import RECONCILIATION_METADATA_KEY

    state = ControllerState(
        controller_id="ctrl-test-state-single-path",
        controller_name="test-controller",
        environment="test",
        tenant_id="tenant-dev",
        deployment={},
    )
    ControllerStateStore(module.runtime.CONTROLLER_STATE_PATH).save(state)

    controller_headers = {"Authorization": f"Bearer {module.controller_token}"}
    configured = test_client.post(
        "/api/source-ingest/connectors",
        headers=controller_headers,
        json={
            "connector": {
                "connector_id": "conn-ctrl-stale-meta",
                "source_type": "market",
                "provider": "TW_OFFICIAL",
                "license_scope": "official",
                "metadata": {
                    "tenant_id": "tenant-stale",
                    RECONCILIATION_METADATA_KEY: {
                        "managed_by": "persona_source_provisioning_reconciler",
                    },
                },
            },
            "fetch": {
                "mode": "static_records",
                "next_watermark": "2026-10-08T00:00:00Z",
                "records": [],
            },
        },
    )
    assert configured.status_code == 201, configured.text

    record_id = "tw-official:tw_price_daily:TWSE:2330:2026-10-08-stale-meta"
    job_res = test_client.post(
        "/api/source-ingest/jobs",
        headers=controller_headers,
        json={
            "connector_id": "conn-ctrl-stale-meta",
            "trace_id": "trace-test-stale-meta",
            "trigger_type": "scheduled",
            "records": [
                {
                    "source_id": record_id,
                    "connector_id": "conn-ctrl-stale-meta",
                    "source_type": "market",
                    "title": "2330 Daily Close",
                    "content_ref": "tw-official://tw_price_daily/TWSE/2330/2026-10-08",
                    "status": "normalized",
                    "metadata": {
                        "provider": "TWSE OpenAPI",
                        "dataset": "tw_price_daily",
                        "market": "TW",
                        "venue": "TWSE",
                        "symbol": "2330",
                        "available_time": "2026-10-08T05:30:00Z",
                        "event_time": "2026-10-08T05:30:00Z",
                    },
                }
            ],
        },
    )
    assert job_res.status_code == 201, job_res.text
    body = job_res.json()
    assert body["run"]["status"] == "completed"
    assert body["records"][0]["metadata"]["tenant_id"] == "tenant-dev"
    bundle_id = body["evidence_refs"]["evidence_bundle_id"]
    assert bundle_id

    persisted_record = module.evidence_repository.get_source_record(record_id, tenant_id="tenant-dev")
    assert persisted_record is not None
    assert persisted_record.tenant_id == "tenant-dev"
    assert persisted_record.metadata.get("tenant_id") == "tenant-dev"

    bundle = module.evidence_repository.get_bundle(bundle_id, tenant_id="tenant-dev")
    assert bundle is not None
    assert bundle.metadata.get("tenant_id") == "tenant-dev"

    # Readers admitted for tenant-env or tenant-stale get 404 for bundle and do not see it in bundle list
    for other_tenant in ("tenant-env", "tenant-stale"):
        headers_other = _read_headers(tenant=other_tenant)
        headers_other["X-Tenant-Id"] = other_tenant
        res_other = test_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}", headers=headers_other)
        assert res_other.status_code == 404
        list_other = test_client.get("/api/source-ingest/evidence/bundles", headers=headers_other)
        assert list_other.status_code == 200
        assert bundle_id not in [b["evidence_bundle_id"] for b in list_other.json()["bundles"]]

    # Reader admitted for tenant-dev sees it
    headers_dev = _read_headers(tenant="tenant-dev")
    headers_dev["X-Tenant-Id"] = "tenant-dev"
    res_dev = test_client.get(f"/api/source-ingest/evidence/bundles/{bundle_id}", headers=headers_dev)
    assert res_dev.status_code == 200
    assert res_dev.json()["bundle"]["evidence_bundle_id"] == bundle_id
    list_dev = test_client.get("/api/source-ingest/evidence/bundles", headers=headers_dev)
    assert list_dev.status_code == 200
    assert bundle_id in [b["evidence_bundle_id"] for b in list_dev.json()["bundles"]]


def test_non_controller_connector_requires_write_capable_auth_and_binds_tenant(client) -> None:
    test_client, _, module = client
    fetch_payload = {"mode": "static_records", "records": []}

    # AC1 & AC2: Unauthenticated POST rejected with 401
    anon_client = TestClient(module.app)
    unauth = anon_client.post(
        "/api/source-ingest/connectors",
        json={"connector": _connector(connector_id="conn-unauth-test"), "fetch": fetch_payload},
    )
    assert unauth.status_code == 401
    assert module.connector_store.get_config("conn-unauth-test") is None

    # AC1: Viewer-only role rejected with 403 AUTH_FORBIDDEN
    viewer_headers = _read_headers(tenant="tenant-dev", roles=["viewer"])
    viewer_res = test_client.post(
        "/api/source-ingest/connectors",
        headers=viewer_headers,
        json={"connector": _connector(connector_id="conn-viewer-test"), "fetch": fetch_payload},
    )
    assert viewer_res.status_code == 403
    assert viewer_res.json()["detail"]["code"] == "AUTH_FORBIDDEN"
    assert module.connector_store.get_config("conn-viewer-test") is None

    # AC2: Token without tenant identity rejected with 403 TENANT_SCOPE_DENIED
    from services.runtime_auth_inbound import encode_jwt_hs256

    tenantless_token = encode_jwt_hs256(
        {"sub": "non-tenant-operator", "roles": ["operator"], "exp": int(__import__("time").time()) + 600},
        secret=os.environ["PANTHEON_RUNTIME_JWT_SECRET"],
    )
    no_tenant_res = test_client.post(
        "/api/source-ingest/connectors",
        headers={"Authorization": f"Bearer {tenantless_token}"},
        json={"connector": _connector(connector_id="conn-notenant-test"), "fetch": fetch_payload},
    )
    assert no_tenant_res.status_code == 403
    assert no_tenant_res.json()["detail"]["code"] == "TENANT_SCOPE_DENIED"
    assert module.connector_store.get_config("conn-notenant-test") is None

    # AC2: Mismatched explicit metadata.tenant_id rejected with 403 TENANT_SCOPE_DENIED
    mismatch_headers = _read_headers(tenant="tenant-dev")
    mismatch_res = test_client.post(
        "/api/source-ingest/connectors",
        headers=mismatch_headers,
        json={
            "connector": _connector(connector_id="conn-mismatch-test", metadata={"tenant_id": "tenant-other"}),
            "fetch": fetch_payload,
        },
    )
    assert mismatch_res.status_code == 403
    assert mismatch_res.json()["detail"]["code"] == "TENANT_SCOPE_DENIED"
    assert module.connector_store.get_config("conn-mismatch-test") is None

    # AC1: Success with write-capable principal binds resolved tenant into metadata.tenant_id
    success_headers = _read_headers(tenant="tenant-dev", roles=["operator"])
    success_res = test_client.post(
        "/api/source-ingest/connectors",
        headers=success_headers,
        json={"connector": _connector(connector_id="conn-bound-test", metadata={}), "fetch": fetch_payload},
    )
    assert success_res.status_code == 201
    body = success_res.json()
    assert body["connector"]["metadata"]["tenant_id"] == "tenant-dev"
    stored = module.connector_store.get_config("conn-bound-test")
    assert stored is not None
    assert stored.connector.metadata["tenant_id"] == "tenant-dev"


def test_non_controller_connector_lifecycle_and_schedule_mutation_requires_admitted_tenant(client) -> None:
    test_client, _, module = client
    fetch_payload = {"mode": "static_records", "records": []}

    # Configure a connector bound to tenant-dev
    conn_id = "conn-lifecycle-sched-test"
    headers_dev = _read_headers(tenant="tenant-dev", roles=["operator"])
    res = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers_dev,
        json={"connector": _connector(connector_id=conn_id), "fetch": fetch_payload},
    )
    assert res.status_code == 201

    headers_other = _read_headers(tenant="tenant-other", roles=["operator"])

    # AC3: Lifecycle mutation with different tenant rejected with 403 TENANT_SCOPE_DENIED
    life_bad = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/lifecycle",
        headers=headers_other,
        json={"status": "disabled", "reason": "maintenance"},
    )
    assert life_bad.status_code == 403
    assert life_bad.json()["detail"]["code"] == "TENANT_SCOPE_DENIED"

    # AC3: Lifecycle mutation unauthenticated rejected
    anon_client = TestClient(module.app)
    life_unauth = anon_client.put(
        f"/api/source-ingest/connectors/{conn_id}/lifecycle",
        json={"status": "disabled", "reason": "maintenance"},
    )
    assert life_unauth.status_code == 401

    # AC3: Schedule mutation with different tenant rejected with 403 TENANT_SCOPE_DENIED
    sched_bad = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        headers=headers_other,
        json={"interval_seconds": 60, "enabled": True},
    )
    assert sched_bad.status_code == 403
    assert sched_bad.json()["detail"]["code"] == "TENANT_SCOPE_DENIED"

    # AC3: Admitted tenant succeeds for schedule and lifecycle mutations
    sched_good = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/schedule",
        headers=headers_dev,
        json={"interval_seconds": 120, "enabled": True},
    )
    assert sched_good.status_code == 200
    assert sched_good.json()["schedule"]["interval_seconds"] == 120

    life_good = test_client.put(
        f"/api/source-ingest/connectors/{conn_id}/lifecycle",
        headers=headers_dev,
        json={"status": "disabled", "reason": "maintenance"},
    )
    assert life_good.status_code == 200
    assert life_good.json()["connector"]["status"] == "disabled"


def test_controller_ownership_determined_solely_by_reconciliation_marker(client) -> None:
    test_client, _, module = client
    fetch_payload = {"mode": "static_records", "records": []}

    # AC1: Verify CONTROLLER_OWNED_CONNECTOR_IDS is removed
    assert not hasattr(module.SourceIngestionRuntime, "CONTROLLER_OWNED_CONNECTOR_IDS")
    assert not hasattr(module.runtime, "CONTROLLER_OWNED_CONNECTOR_IDS")

    # Connector with ID 'tw-twse-tpex-official-market' configured without the reconciliation marker
    # is treated as a standard tenant connector and does NOT require controller auth
    headers_dev = _read_headers(tenant="tenant-dev", roles=["operator"])
    res = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers_dev,
        json={
            "connector": _connector(
                connector_id="tw-twse-tpex-official-market",
                metadata={"dataset": "tw_price_daily"},
            ),
            "fetch": fetch_payload,
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["connector"]["metadata"]["tenant_id"] == "tenant-dev"
    assert "persona_source_reconciliation" not in body["connector"]["metadata"]

    # Mutation by admitted tenant succeeds
    sched_res = test_client.put(
        "/api/source-ingest/connectors/tw-twse-tpex-official-market/schedule",
        headers=headers_dev,
        json={"interval_seconds": 3600, "enabled": True},
    )
    assert sched_res.status_code == 200

    # AC2: A connector that carries the reconciliation marker requires controller token
    from services.source_ingestion.persona_source_reconciler import RECONCILIATION_METADATA_KEY

    reconciled_connector_id = "conn-custom-reconciled"
    reconciled_payload = {
        "connector": _connector(
            connector_id=reconciled_connector_id,
            metadata={
                RECONCILIATION_METADATA_KEY: {
                    "managed_by": "persona_source_provisioning_reconciler",
                },
            },
        ),
        "fetch": fetch_payload,
    }

    # Tenant token rejected with 403 (service authorization invalid)
    res_tenant = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers_dev,
        json=reconciled_payload,
    )
    assert res_tenant.status_code == 403

    # Controller token succeeds
    controller_headers = {"Authorization": f"Bearer {module.controller_token}"}
    res_ctrl = test_client.post(
        "/api/source-ingest/connectors",
        headers=controller_headers,
        json=reconciled_payload,
    )
    assert res_ctrl.status_code == 201

    # Lifecycle and schedule mutations for marker-bearing connector also require controller auth
    life_bad = test_client.put(
        f"/api/source-ingest/connectors/{reconciled_connector_id}/lifecycle",
        headers=headers_dev,
        json={"status": "disabled", "reason": "test"},
    )
    assert life_bad.status_code == 403

    sched_bad = test_client.put(
        f"/api/source-ingest/connectors/{reconciled_connector_id}/schedule",
        headers=headers_dev,
        json={"interval_seconds": 60, "enabled": True},
    )
    assert sched_bad.status_code == 403


def test_configure_connector_refuses_adopting_tenantless_existing_connector(client) -> None:
    test_client, _, module = client
    fetch_payload = {"mode": "static_records", "records": []}

    # AC3: Seed an existing non-controller connector without tenant_id in metadata
    from services.source_ingestion.connectors import SourceConnector

    legacy_id = "conn-legacy-untenanted"
    legacy_conn = SourceConnector.from_dict(
        _connector(connector_id=legacy_id, metadata={"legacy_flag": "true"})
    )
    assert not legacy_conn.metadata.get("tenant_id")
    module.connector_store.upsert_config(legacy_conn, fetch_payload)
    assert module.connector_store.get_config(legacy_id) is not None

    # Write-capable tenant caller attempts to update the existing tenantless connector
    headers_dev = _read_headers(tenant="tenant-dev", roles=["operator"])
    res = test_client.post(
        "/api/source-ingest/connectors",
        headers=headers_dev,
        json={
            "connector": _connector(connector_id=legacy_id, metadata={"updated": "true"}),
            "fetch": fetch_payload,
        },
    )
    # AC3: Must fail closed with 403 TENANT_SCOPE_DENIED rather than adopting
    assert res.status_code == 403
    detail = res.json().get("detail", {})
    assert detail.get("code") == "TENANT_SCOPE_DENIED"
    assert detail.get("message") == "Connector has no bound tenant"

    # Verify connector metadata in store was not modified / adopted
    stored = module.connector_store.get_config(legacy_id)
    assert stored is not None
    assert "tenant_id" not in stored.connector.metadata
