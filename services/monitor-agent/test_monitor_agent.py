from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import monitor_agent as ma  # noqa: E402

SOURCES = {"runtime_status": "http://rt", "performance": "http://perf"}


def _fetch(findings, *, fail=None, agent_fail=False, posts=None, statuses=None):
    posts = posts if posts is not None else []

    def fetch(url, data=None, headers=None, timeout=20):
        if fail and fail in url:
            raise OSError("down")
        if "/structured" in url:
            if agent_fail:
                raise OSError("agent down")
            fetch.agent_request = data
            return {"data": {"output": {"structured_data": {"findings": findings}}}}
        if "consume-agent-finding" in url:
            posts.append(data)
            return {"_http_status": (statuses or {}).get(data["fingerprint"], 201)}
        if "open_only" in url:
            return []
        return {"ok": True}

    return fetch, posts


def _run(tmp_path, fetch, now=lambda: 1000.0):
    return ma.run_once(
        sources=SOURCES, incidents_url="http://inc", adapter_url="http://ad", adapter_token="t",
        limiter=ma.RateLimiter(tmp_path / "s.json"), fetch=fetch, now=now,
    )


def _finding(i):
    return {"fingerprint": f"fp{i}", "title": f"t{i}", "severity": "high", "rationale": "r"}


def test_agent_is_given_no_write_tool(tmp_path):
    fetch, _ = _fetch([])
    _run(tmp_path, fetch)
    request = fetch.agent_request
    assert set(request) == {"prompt", "extraction_schema"}  # no tools / tool_choice
    assert set(request["extraction_schema"]["properties"]) == {"findings"}


def test_finding_posts_with_snapshot_ref_and_rationale(tmp_path):
    fetch, posts = _fetch([_finding(1)])
    record = _run(tmp_path, fetch)
    assert record["created"] == 1 and posts[0]["snapshot_ref"] == record["snapshot_ref"]
    assert posts[0]["rationale"] == "r"


def test_repeated_finding_counts_as_update_not_creation(tmp_path):
    fetch, _ = _fetch([_finding(1)], statuses={"fp1": 200})
    record = _run(tmp_path, fetch)
    assert (record["created"], record["updated"]) == (0, 1)


def test_at_most_five_per_run(tmp_path):
    fetch, posts = _fetch([_finding(i) for i in range(9)])
    assert _run(tmp_path, fetch)["created"] == 5 and len(posts) == 5


def test_at_most_twenty_per_hour(tmp_path):
    total = 0
    for _ in range(6):
        fetch, _p = _fetch([_finding(i) for i in range(5)])
        total += _run(tmp_path, fetch)["created"]
    assert total == 20
    later = _run(tmp_path, _fetch([_finding(1)])[0], now=lambda: 1000.0 + 3601)
    assert later["created"] == 1


def test_read_api_down_is_degraded_without_incident(tmp_path):
    fetch, posts = _fetch([_finding(1)], fail="http://perf")
    record = _run(tmp_path, fetch)
    assert record["status"] == "degraded" and record["created"] == 0 and posts == []


def test_agent_down_is_degraded_without_incident(tmp_path):
    fetch, posts = _fetch([_finding(1)], agent_fail=True)
    record = _run(tmp_path, fetch)
    assert record["status"] == "degraded" and posts == []
