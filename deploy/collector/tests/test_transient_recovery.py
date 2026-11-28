"""Regressions for missing GDELT exports and bounded rate-limit recovery."""
import json
from datetime import datetime, timezone

import pytest

from collector_stack.config import DEFAULT_POLICY_PATH, load_collector_config
from collector_stack.fetchers import FetchResponse, FixtureTransport
from collector_stack.runner import CollectorRunner, SourcePausedError
from collector_stack.state import CollectorStateStore


def test_missing_export_is_recorded_without_pausing(tmp_path, fixtures_dir, test_hasher):
    feed = fixtures_dir / "gdelt_feed_recorded"
    url = "https://data.gdeltproject.org/gdeltv2/20261113110000.export.CSV.zip"
    transport = FixtureTransport({
        "https://data.gdeltproject.org/gdeltv2/lastupdate.txt": (feed / "masterfilelist-excerpt.txt").read_bytes(),
        url: FetchResponse(url, url, 404, b"missing"),
    })
    runner = CollectorRunner(load_collector_config(DEFAULT_POLICY_PATH, env={}),
                             root_dir=tmp_path, hasher=test_hasher, transport=transport)
    for n in range(3):
        result = runner.run_source("collector-gdelt", run_id=f"missing-{n}")
        assert result.status == "partial"
        assert result.bundle.manifest["reason"].startswith("missing_export_404:")
        assert result.bundle.manifest["items"] == []
        assert result.state.paused is False and result.state.consecutive_blocks == 0
    transport.register(url, (feed / "20261113110000.export.CSV.zip").read_bytes())
    result = runner.run_source("collector-gdelt", run_id="recovered")
    assert result.status == "complete" and result.bundle.manifest["items"]


def test_429_one_request_then_daily_retry_survives_restart(tmp_path, test_hasher):
    url = "https://export.arxiv.org/api/query"
    transport = FixtureTransport({url: FetchResponse(url, url, 429, b"limited")})
    config = load_collector_config(DEFAULT_POLICY_PATH, env={})
    def runner():
        return CollectorRunner(config, root_dir=tmp_path, hasher=test_hasher, transport=transport)
    first = runner().run_source("collector-paper-arxiv", run_id="limited",
                               started_at="2026-11-28T00:00:00Z", finished_at="2026-11-28T00:00:01Z")
    assert first.status == "paused"
    assert first.state.retry_at == "2026-11-29T00:00:01Z"
    for hour in range(1, 24):
        with pytest.raises(SourcePausedError):
            runner().run_source("collector-paper-arxiv", run_id=f"tick-{hour}",
                                started_at=f"2026-11-28T{hour:02d}:00:00Z")
    assert len(transport.calls) == 1
    again = runner().run_source("collector-paper-arxiv", run_id="retry",
                               started_at="2026-11-29T00:00:01Z", finished_at="2026-11-29T00:00:02Z")
    assert len(transport.calls) == 2 and again.status == "paused"
    assert again.state.retry_at == "2026-11-30T00:00:02Z"


@pytest.mark.parametrize("retry_after", ["172800", "Mon, 30 Nov 2026 00:00:00 GMT"])
def test_provider_retry_after_is_never_shortened(tmp_path, retry_after):
    store = CollectorStateStore(tmp_path / "state.json")
    state = store.record_run_outcome(source_id="paper", run_id="run", finished_at="2026-11-28T00:00:00Z",
        policy_hash="same", block_reason="http_429:https://example.test", retry_after=retry_after)
    assert state.retry_at == "2026-11-30T00:00:00Z"


def test_legacy_pause_recovery_does_not_release_authorization_blocks(tmp_path):
    path = tmp_path / "state.json"
    sources = {}
    for source, reason in [("collector-gdelt", "gdelt_http_404:https://data.gdeltproject.org/gdeltv2/20261118111500.export.CSV.zip"),
                           ("arxiv", "http_429:https://export.arxiv.org/api/query"),
                           ("login", "login_redirect:https://example.test/login"),
                           ("forbidden", "http_403:https://example.test")]:
        sources[source] = {"paused": True, "block_history": [reason, reason], "last_run_at": "2026-11-18T00:00:00Z",
                           "paused_policy_hash": "same", "consecutive_blocks": 2}
    path.write_text(json.dumps({"sources": sources}))
    store = CollectorStateStore(path)
    now = datetime(2026, 11, 28, tzinfo=timezone.utc)
    for source in sources:
        assert store.get_source_state(source, current_policy_hash="same", now=now).paused == (source in {"login", "forbidden"})
