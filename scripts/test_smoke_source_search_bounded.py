from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import smoke_source_search_bounded as smoke


class _FakeFeedServer:
    def shutdown(self) -> None:
        return

    def server_close(self) -> None:
        return


def _persistent_api() -> tuple[dict[str, Any], Any]:
    state: dict[str, Any] = {
        "configured_connector_ids": [],
        "registry_entries": [],
        "feed_run_id": None,
    }

    def request(
        method: str,
        url: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> tuple[int, dict[str, Any]]:
        assert timeout > 0
        payload = body or {}
        if url.endswith("/readyz"):
            return 200, {"status": "ok"}
        if url.endswith("/api/source-ingest/registry"):
            return 200, {
                "connectors": list(state["registry_entries"]),
                "provider_examples": [],
            }
        if method == "POST" and url.endswith("/api/source-ingest/connectors"):
            connector_id = payload["connector"]["connector_id"]
            fetch = dict(payload["fetch"])
            state["configured_connector_ids"].append(connector_id)
            fetch_policy = {"mode": fetch["mode"]}
            if fetch["mode"] == "external_feed":
                fetch_policy["allowed_url_prefix_count"] = len(
                    fetch["allowed_url_prefixes"]
                )
            state["registry_entries"].append({"fetch_policy": fetch_policy})
            return 201, {"fetch": fetch}
        if method == "POST" and url.endswith("/api/source-ingest/jobs"):
            connector_id = payload["connector_id"]
            if payload.get("trigger_type") == "bounded_smoke_failure":
                return 201, {
                    "run": {"status": "failed"},
                    "dlq_entries": [{"entry_id": f"dlq-{connector_id}"}],
                }
            ingest_run_id = f"ingest-{connector_id}"
            response: dict[str, Any] = {
                "run": {
                    "status": "completed",
                    "ingest_run_id": ingest_run_id,
                }
            }
            if "-feed-" in connector_id:
                state["feed_run_id"] = ingest_run_id
                response["source_search_refresh"] = {
                    "status": "refreshed",
                    "search_service": {
                        "materialized_matches_completion": True,
                        "pipeline_run_id": f"pipeline-{connector_id}",
                    },
                }
            return 201, response
        if method == "POST" and url.endswith("/api/source-ingest/dlq/replay"):
            return 200, {"summary": {"applied": 1}}
        if method == "PUT" and url.endswith("/schedule"):
            return 200, {"schedule": {"enabled": True}}
        if method == "POST" and url.endswith("/api/source-ingest/run-scheduled"):
            return 200, {
                "summary": {"total_ran": 1},
                "ran": [{"frontier": {"frontier_id": "frontier-bounded"}}],
            }
        if "/api/source-ingest/source-records/" in url:
            source_id = url.rsplit("/", 1)[-1]
            return 200, {"source_record": {"source_id": source_id}}
        if url.endswith("/api/source-ingest/audit"):
            return 200, {
                "actions": [
                    {"action_type": "source_ingestion.scheduled_run.dead_lettered"},
                    {"action_type": "foundation.dlq.replay.applied"},
                ]
            }
        if "/api/search/index/source-completions/" in url:
            return 200, {
                "truth": {
                    "index_refreshed": True,
                    "materialized_matches_completion": True,
                }
            }
        if url.endswith("/api/search/index/pipeline-runs?limit=20"):
            return 200, {"runs": [{"trigger_ref": state["feed_run_id"]}]}
        if method == "POST" and url.endswith("/api/search/index/refresh"):
            return 200, {
                "pipeline_snapshot": {
                    "schema_version": "index_pipeline_snapshot.v1"
                }
            }
        if method == "POST" and url.endswith("/api/search/query"):
            token = payload["query"].split()[-1]
            suffix = token.removeprefix("bounded-smoke-")
            return 200, {
                "results": [
                    {
                        "matched_items": [
                            {"source_id": f"src-bounded-feed-{suffix}"}
                        ]
                    }
                ]
            }
        raise AssertionError(f"unexpected request: {method} {url} {payload}")

    return state, request


def test_repeated_persistent_state_runs_complete_with_disjoint_ids(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state, request = _persistent_api()
    run_ids = iter(
        [
            SimpleNamespace(hex="fresh-state"),
            SimpleNamespace(hex="repeated-state"),
        ]
    )
    monkeypatch.setattr(smoke, "_request_json", request)
    monkeypatch.setattr(
        smoke,
        "_serve_feed",
        lambda _payload: (_FakeFeedServer(), "http://source-search-bounded-smoke/feed.json"),
    )
    monkeypatch.setattr(smoke.uuid, "uuid4", lambda: next(run_ids))
    monkeypatch.setenv("SOURCE_SEARCH_SMOKE_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("SOURCE_SEARCH_SMOKE_REQUEST_TIMEOUT_SECONDS", "1")

    assert smoke.main() == 0
    assert smoke.main() == 0

    connector_ids = state["configured_connector_ids"]
    assert len(connector_ids) == 8
    assert len(set(connector_ids)) == 8
    assert any(connector_id.endswith("-fresh-state") for connector_id in connector_ids)
    assert any(connector_id.endswith("-repeated-state") for connector_id in connector_ids)
    output = capsys.readouterr().out
    assert output.count("source/search bounded smoke passed") == 2
    assert "checkpoint=registry_policy_checked" in output
    assert "budget_seconds=5.000" in output


def test_request_timeout_is_bounded_and_names_phase_connector_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, float] = {}

    def fail_with_timeout(
        _method: str,
        _url: str,
        *,
        body: dict[str, Any] | None = None,
        timeout: float = 10,
    ) -> tuple[int, dict[str, Any]]:
        del body
        observed["timeout"] = timeout
        raise TimeoutError("simulated persistent-state timeout")

    monkeypatch.setattr(smoke, "_request_json", fail_with_timeout)
    budget = smoke._SmokeBudget(
        timeout_seconds=0.05,
        request_timeout_seconds=5,
    )
    budget.checkpoint("search-svc_ready", "search-svc ready")
    started = time.monotonic()

    with pytest.raises(smoke._SmokeTimeout) as exc_info:
        smoke._request_for_phase(
            budget,
            "registry_policy_check",
            "registry",
            "GET",
            "http://source-ingest:8097/api/source-ingest/registry",
        )

    assert time.monotonic() - started < 0.5
    assert 0 < observed["timeout"] <= 0.05
    message = str(exc_info.value)
    assert "phase=registry_policy_check" in message
    assert "connector=registry" in message
    assert "last_successful_checkpoint=search-svc_ready" in message
    assert "budget_seconds=0.050" in message
    assert "simulated persistent-state timeout" in message
