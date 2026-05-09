import os
import sys
import pytest
import asyncio
from fastapi.testclient import TestClient

# Add current directory to path to import main
sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main

AUTH = "Bearer test-operator:operator,admin"

@pytest.fixture
def client():
    return TestClient(bff_main.app)

def test_sse_compatibility_routes_exposed(client):
    # Route, expected_channel_header (if applicable), query_params
    routes = [
        ("/bff/events/stream", "artifact", {"channel": "artifact"}),
        ("/bff/sse/notifications", "inbox", {}),
        ("/bff/sse/command-center/kpi", "ranking", {}),
        ("/bff/sse/command-center/events", "loop", {}),
        ("/bff/sse/jobs/job-1/progress", "tool", {}),
        ("/bff/sse/alerts", "sentinel", {}),
        ("/bff/sse/incidents/inc-1/timeline", "journal", {}),
        ("/bff/sse/deployment/events", "artifact", {}),
        ("/bff/sse/review/updates", "approval", {}),
        ("/bff/sse/agora/signals", "signal", {}),
        ("/bff/sse/agora/sessions/sess-1", "ask", {}),
    ]

    for route, expected_channel, params in routes:
        # Note: StreamingResponse doesn't work well with TestClient for actual streaming,
        # but we can check the headers and initial response.
        # We use a context manager to handle the stream or just check the first byte.
        with client.get(route, headers={"Authorization": AUTH}, params=params, stream=True) as response:
            print(f"Route {route}: {response.status_code}")
            assert response.status_code == 200
            assert response.headers["content-type"] == "text/event-stream"
            assert response.headers["X-SSE-Channel"] == expected_channel

def test_sse_replay_error_compatibility(client):
    # Test that aliases also return 409 for missing replay history
    route = "/bff/sse/notifications"
    response = client.get(route, headers={"Authorization": AUTH}, params={"last_event_id": "evt-missing"})
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "SSE_REPLAY_UNAVAILABLE"

if __name__ == "__main__":
    # Manual check
    client_obj = TestClient(bff_main.app)
    routes = [
        ("/bff/events/stream", {"channel": "artifact"}),
        ("/bff/sse/notifications", {}),
        ("/bff/sse/command-center/kpi", {}),
        ("/bff/sse/command-center/events", {}),
        ("/bff/sse/jobs/job-1/progress", {}),
        ("/bff/sse/alerts", {}),
        ("/bff/sse/incidents/inc-1/timeline", {}),
        ("/bff/sse/deployment/events", {}),
        ("/bff/sse/review/updates", {}),
        ("/bff/sse/agora/signals", {}),
        ("/bff/sse/agora/sessions/sess-1", {}),
    ]
    for r, params in routes:
        res = client_obj.get(r, headers={"Authorization": AUTH}, params=params)
        print(f"{r}: {res.status_code} ({res.headers.get('X-SSE-Channel')})")
