from __future__ import annotations

import json
from unittest import mock

from services.search.scheduler_worker import run_tick


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class _PostRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request, timeout=None):
        body = json.loads((request.data or b"{}").decode("utf-8"))
        self.calls.append((request.full_url, body))
        if request.full_url.endswith("/api/search/index/materialize"):
            return _FakeHttpResponse({"materialized_at": "2026-05-04T00:00:00Z"})
        return _FakeHttpResponse({"pipeline_snapshot": {"triggered_by": body["triggered_by"]}})


def test_search_index_scheduler_refreshes_and_materializes() -> None:
    recorder = _PostRecorder()
    with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", recorder):
        result = run_tick(
            api_url="http://search-svc:8098",
            force_full=True,
            materialize=True,
            trigger_ref="schedule-test-001",
        )

    assert recorder.calls == [
        (
            "http://search-svc:8098/api/search/index/refresh",
            {
                "triggered_by": "scheduled_refresh",
                "trigger_ref": "schedule-test-001",
                "force_full": True,
            },
        ),
        ("http://search-svc:8098/api/search/index/materialize", {}),
    ]
    assert result["refresh"]["pipeline_snapshot"]["triggered_by"] == "scheduled_refresh"
    assert result["materialize"]["materialized_at"] == "2026-05-04T00:00:00Z"


def test_search_index_scheduler_can_refresh_without_materialize() -> None:
    recorder = _PostRecorder()
    with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", recorder):
        result = run_tick(
            api_url="http://search-svc:8098",
            materialize=False,
            trigger_ref="schedule-test-002",
        )

    assert [url for url, _body in recorder.calls] == ["http://search-svc:8098/api/search/index/refresh"]
    assert result["materialize"] is None
