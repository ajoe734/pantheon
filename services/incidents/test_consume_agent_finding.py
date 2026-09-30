"""consume-agent-finding dedupes by fingerprint and merges into the open incident."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_URL", "http://127.0.0.1:9")
os.environ.setdefault("PANTHEON_RUNTIME_MANAGER_TOKEN", "incident-route-test-token")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import services.incidents.main as incidents_main  # noqa: E402
from services.incident.incident import IncidentStore  # noqa: E402

app = incidents_main.app

URL = "/api/incidents/consume-agent-finding"


@pytest.fixture(autouse=True)
def store(monkeypatch):
    fresh = IncidentStore()
    monkeypatch.setattr(incidents_main, "store", fresh)
    return fresh


def _body(**over):
    body = {
        "fingerprint": "drawdown:persona-a", "title": "Drawdown spike", "severity": "high",
        "rationale": "drawdown 9% with no incident", "snapshot_ref": "snap-1",
    }
    return {**body, **over}


def test_repeat_finding_updates_open_incident(store):
    client = TestClient(app)
    first = client.post(URL, json=_body(fingerprint="fp-repeat"))
    assert first.status_code == 201
    second = client.post(URL, json=_body(fingerprint="fp-repeat", snapshot_ref="snap-2", rationale="still"))
    assert second.status_code == 200
    assert second.json()["incident_id"] == first.json()["incident_id"]
    summary = store.get_incident(first.json()["incident_id"]).evidence_summary
    assert "snap-1" in summary and "snap-2" in summary and "rationale=" in summary


def test_different_fingerprint_opens_new_incident():
    client = TestClient(app)
    a = client.post(URL, json=_body(fingerprint="fp-a")).json()["incident_id"]
    b = client.post(URL, json=_body(fingerprint="fp-b")).json()["incident_id"]
    assert a != b


def test_missing_fields_rejected():
    assert TestClient(app).post(URL, json=_body(rationale="")).status_code == 422


def test_concurrent_same_fingerprint_creates_one_open_incident(store):
    import threading

    barrier = threading.Barrier(2)
    original = store.find_open_incidents

    def racing_find():
        found = original()
        try:
            barrier.wait(timeout=2)  # both threads see "no open incident" before either creates
        except threading.BrokenBarrierError:
            pass
        return found

    store.find_open_incidents = racing_find
    codes = []

    def post():
        codes.append(TestClient(app).post(URL, json=_body(fingerprint="fp-race")).status_code)

    threads = [threading.Thread(target=post) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(codes) == [200, 201]
    assert len([i for i in original() if "agent-" in i.incident_id]) == 1
