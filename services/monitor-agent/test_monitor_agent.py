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


def test_lost_response_still_consumes_budget_across_restarts(tmp_path):
    """A POST that committed but whose response was lost must count toward the hourly cap."""
    stored = 0
    for run in range(6):
        fetch, _p = _fetch([_finding(run * 5 + i) for i in range(5)])
        inner = fetch

        def lossy(url, data=None, headers=None, timeout=20, inner=inner):
            nonlocal stored
            resp = inner(url, data=data, headers=headers, timeout=timeout)
            if "consume-agent-finding" in url:
                stored += 1
                raise TimeoutError("response lost")
            return resp

        _run(tmp_path, lossy, now=lambda run=run: 1000.0 + run * 60)  # fresh limiter each run
    assert stored == 20


def test_confirmed_update_releases_reservation(tmp_path):
    fetch, _p = _fetch([_finding(i) for i in range(5)], statuses={f"fp{i}": 200 for i in range(5)})
    for _ in range(6):
        assert _run(tmp_path, fetch)["updated"] == 5
    assert ma.RateLimiter(tmp_path / "s.json").reserve(1000.0)


def test_read_api_down_is_degraded_without_incident(tmp_path):
    fetch, posts = _fetch([_finding(1)], fail="http://perf")
    record = _run(tmp_path, fetch)
    assert record["status"] == "degraded" and record["created"] == 0 and posts == []


def test_agent_down_is_degraded_without_incident(tmp_path):
    fetch, posts = _fetch([_finding(1)], agent_fail=True)
    record = _run(tmp_path, fetch)
    assert record["status"] == "degraded" and posts == []


def _compose_env(key, default=""):
    """Compose defaults for the monitor-agent service, not test-local values."""
    import re
    text = (Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text()
    block = text[text.index("\n  monitor-agent:"):]
    match = re.search(rf"{key}: \$\{{[A-Z_]+:-(?:\$\{{[A-Z_]+:-)?([^}}]+)\}}", block)
    return match.group(1) if match else default


def test_source_credentials_pass_real_route_auth_contracts(monkeypatch):
    """The headers the job sends must satisfy the protected routes' real auth guards."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from flask import Flask, jsonify
    from services.runtime_auth_inbound import require_authn
    from services.telemetry.auth import require_telemetry_authority, request_tenant_id

    app = Flask(__name__)
    app.add_url_rule("/rt", "rt", require_authn(roles=("operator", "admin", "approver", "reviewer", "risk_owner"))(lambda: jsonify(ok=1)))
    app.add_url_rule(
        "/tel", "tel",
        require_telemetry_authority(("service", "operator", "reviewer", "admin"))(lambda: jsonify(tenant=request_tenant_id())),
    )
    # Telemetry validates against its own service token/tenants (compose defaults).
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", _compose_env("PANTHEON_TELEMETRY_SERVICE_TOKEN"))
    monkeypatch.setenv("PANTHEON_TELEMETRY_SERVICE_TENANTS", _compose_env("PANTHEON_TENANT_ID"))
    headers = ma.source_headers(_compose_env)
    client = app.test_client()
    assert client.get("/rt").status_code == 401  # unauthenticated is really rejected
    assert client.get("/rt", headers=headers["runtime_status"]).status_code == 200
    resp = client.get("/tel", headers=headers["performance"])
    assert resp.status_code == 200 and resp.get_json()["tenant"] == _compose_env("PANTHEON_TENANT_ID")


def test_collect_snapshot_sends_source_specific_headers():
    seen = {}
    ma.collect_snapshot(SOURCES, lambda url, headers=None: seen.setdefault(url, headers) or {}, {"performance": {"X-Tenant-Id": "t"}})
    assert seen["http://perf"] == {"X-Tenant-Id": "t"} and seen["http://rt"] is None


def test_build_prompt_bounds_prompt_with_50_large_incidents():
    # AC 1: The prompt monitor-agent sends to the structured route stays at or below 120000 characters
    # no matter how many open incidents exist or how long their summaries are; a test with 50 open incidents
    # each carrying a 200000 character summary proves it.
    # AC 2: Each incident in the monitor prompt keeps its title and cluster and a summary cut to a fixed
    # per-incident limit with a visible truncation marker.
    large_summary = "Drift breach threshold: " + ("z" * 200000)
    incidents = [
        {
            "title": f"Incident title {i}",
            "incident_cluster_id": f"cluster-{i % 5}",
            "evidence_summary": large_summary,
        }
        for i in range(50)
    ]
    snapshot = {"s1": "snap1", "s2": "snap2"}
    prompt = ma.build_prompt(snapshot, incidents)

    assert len(prompt) <= 120000
    assert "... [truncated]" in prompt
    for i in range(50):
        assert f"Incident title {i}" in prompt
        assert f"cluster-{i % 5}" in prompt
    assert "OMITTED_INCIDENTS" not in prompt  # all 50 fit when summaries are cut


def test_build_prompt_counts_omitted_incidents_when_total_limit_exceeded():
    # AC 2: incidents left out because of the total limit are counted in the prompt and not silently dropped
    incidents = [
        {
            "title": f"Breach {i} with long descriptive title that takes space in json payload",
            "incident_cluster_id": f"cluster-{i}",
            "evidence_summary": f"Summary {i} " + ("k" * 1500),
        }
        for i in range(300)
    ]
    snapshot = {f"source_{k}": "data" * 1000 for k in range(4)}
    prompt = ma.build_prompt(snapshot, incidents)

    assert len(prompt) <= 120000
    assert "OMITTED_INCIDENTS=" in prompt
    assert "open incidents omitted" in prompt
    # Check that prompt contains an omitted count > 0
    import re
    match = re.search(r"OMITTED_INCIDENTS=(\d+)", prompt)
    assert match is not None
    omitted_count = int(match.group(1))
    assert omitted_count > 0
    assert omitted_count < 300


def test_run_once_sends_bounded_prompt_with_50_large_open_incidents(tmp_path):
    large_summary = "Drift breach: " + ("w" * 200000)
    open_incidents = [
        {
            "title": f"Title {i}",
            "incident_cluster_id": f"cluster-{i}",
            "evidence_summary": large_summary,
        }
        for i in range(50)
    ]

    def fetch(url, data=None, headers=None, timeout=20):
        if "/structured" in url:
            fetch.agent_request = data
            return {"data": {"output": {"structured_data": {"findings": []}}}}
        if "open_only" in url:
            return open_incidents
        return {"ok": True}

    record = _run(tmp_path, fetch)
    assert record["status"] == "ok"
    prompt = fetch.agent_request["prompt"]
    assert len(prompt) <= 120000
    assert "... [truncated]" in prompt

