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


def test_search_index_scheduler_main_touches_alive_path(tmp_path) -> None:
    alive_file = tmp_path / "search_scheduler_alive"
    recorder = _PostRecorder()
    with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", recorder), \
         mock.patch.dict("os.environ", {"SEARCH_INDEX_SCHEDULER_ALIVE_PATH": str(alive_file), "SEARCH_INDEX_SCHEDULER_MAX_TICKS": "1"}):
        from services.search.scheduler_worker import main
        res = main()
        assert res == 0
        assert alive_file.exists()


def test_search_index_scheduler_survives_failed_tick_and_recovers(tmp_path, capsys) -> None:
    import urllib.error

    alive_file = tmp_path / "alive"
    recorder = _PostRecorder()
    calls = {"n": 0}

    def flaky(request, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("timed out")
        if calls["n"] == 2:
            raise urllib.error.HTTPError(request.full_url, 503, "Service Unavailable", {}, None)
        return recorder(request, timeout=timeout)

    env = {
        "SEARCH_INDEX_SCHEDULER_ALIVE_PATH": str(alive_file),
        "SEARCH_INDEX_SCHEDULER_MAX_TICKS": "3",
        "SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS": "1",
        "SEARCH_INDEX_SCHEDULER_MATERIALIZE": "false",
    }
    with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", flaky), \
         mock.patch("services.search.scheduler_worker.time.sleep"), \
         mock.patch.dict("os.environ", env):
        from services.search.scheduler_worker import main

        assert main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert lines[0]["status"] == "failed" and lines[0]["error_type"] == "TimeoutError"
    assert lines[1]["status"] == "failed" and "HTTP 503" in lines[1]["error"]
    assert "result" in lines[2]
    assert alive_file.exists()  # only touched by the successful tick


def test_search_index_scheduler_failed_tick_never_marks_alive(tmp_path) -> None:
    alive_file = tmp_path / "alive"
    env = {
        "SEARCH_INDEX_SCHEDULER_ALIVE_PATH": str(alive_file),
        "SEARCH_INDEX_SCHEDULER_MAX_TICKS": "2",
    }
    with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", side_effect=TimeoutError("x")), \
         mock.patch("services.search.scheduler_worker.time.sleep"), \
         mock.patch.dict("os.environ", env):
        from services.search.scheduler_worker import main

        assert main() == 0
    assert not alive_file.exists()


def _drain_env(alive_file, ticks: int, max_stalled: int = 3) -> dict:
    return {
        "SEARCH_INDEX_SCHEDULER_ALIVE_PATH": str(alive_file),
        "SEARCH_INDEX_SCHEDULER_MAX_TICKS": str(ticks),
        "SEARCH_INDEX_SCHEDULER_INTERVAL_SECONDS": "1",
        "SEARCH_INDEX_SCHEDULER_MATERIALIZE": "false",
        "SEARCH_INDEX_SCHEDULER_MAX_STALLED_TICKS": str(max_stalled),
    }


def _run_main_with_backlogs(tmp_path, capsys, backlogs: list[int], max_stalled: int = 3) -> list[dict]:
    alive_file = tmp_path / "alive"
    seq = iter(backlogs)

    def urlopen(request, timeout=None):
        return _FakeHttpResponse(
            {"status": "in_progress", "pipeline_snapshot": {"backlog_remaining": next(seq)}}
        )

    with mock.patch("services.search.scheduler_worker.urllib.request.urlopen", urlopen), \
         mock.patch("services.search.scheduler_worker.time.sleep"), \
         mock.patch.dict("os.environ", _drain_env(alive_file, len(backlogs), max_stalled)):
        from services.search.scheduler_worker import main

        assert main() == 0
    return [json.loads(x) for x in capsys.readouterr().out.splitlines()]


def test_in_progress_ticks_with_falling_backlog_stay_healthy(tmp_path, capsys) -> None:
    # 2026-11-20 08:16Z-09:11Z shape: every tick answers in_progress while backlog 1095 -> 140.
    backlogs = [1095, 900, 700, 520, 300, 140, 100, 60]
    lines = _run_main_with_backlogs(tmp_path, capsys, backlogs, max_stalled=3)
    assert all(line["health"] == "healthy" for line in lines)
    assert (tmp_path / "alive").exists()


def test_in_progress_ticks_without_backlog_decrease_turn_unhealthy(tmp_path, capsys) -> None:
    lines = _run_main_with_backlogs(tmp_path, capsys, [500, 400, 400, 400, 400, 400], max_stalled=3)
    assert [line["health"] for line in lines[:4]] == ["healthy"] * 4
    assert lines[4]["health"] == "unhealthy"
    assert lines[4]["stalled_ticks"] == 3
