from __future__ import annotations

import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from read_store import ReadSurfaceStore


def test_bff_reads_source_connector_registry_through_service_client() -> None:
    calls = []

    def fake_get(base_url, path, *, headers=None):
        calls.append((base_url, path, headers))
        assert base_url == "http://source-ingest:8097"
        assert path == "/api/source-ingest/registry"
        return True, {
            "schema_version": "source_connector_registry.v1",
            "connectors": [
                {
                    "connector_id": "conn-openalex-api",
                    "provider": "OpenAlex",
                    "source_type": "paper",
                    "status": "enabled",
                    "policy": {
                        "auth": {
                            "auth_type": "api_key",
                            "secret_ref": {"secret_ref_id": "env://OPENALEX_API_KEY"},
                        },
                        "rate_limit": {"requests_per_minute": 60},
                        "license": {"license_scope": "open", "allowed_use": ["research", "search_index"]},
                        "source_metadata": {"display_name": "OpenAlex works"},
                    },
                    "fetch_policy": {"mode": "external_feed", "max_records": 25},
                }
            ],
            "provider_examples": [
                {
                    "connector": {"connector_id": "example-openalex-feed"},
                    "fetch_policy": {"mode": "external_feed"},
                }
            ],
        }

    with tempfile.TemporaryDirectory() as td:
        with mock.patch.dict(os.environ, {"PANTHEON_SOURCE_INGEST_API_URL": "http://source-ingest:8097"}):
            with mock.patch("read_store._http_json_get", side_effect=fake_get):
                store = ReadSurfaceStore(
                    os.path.join(td, "read_surfaces.json"),
                    allow_local_snapshot_fallback=False,
                )
                registry = store.get_source_connector_registry()

    assert calls == [("http://source-ingest:8097", "/api/source-ingest/registry", None)]
    assert registry["source"] == "service_client"
    assert registry["connectors"][0]["connector_id"] == "conn-openalex-api"
    assert registry["connectors"][0]["policy"]["auth"]["secret_ref"]["secret_ref_id"] == "env://OPENALEX_API_KEY"
    assert registry["provider_examples"][0]["fetch_policy"]["mode"] == "external_feed"
