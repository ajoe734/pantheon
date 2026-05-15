import os
import sys
from fastapi.testclient import TestClient

# Add the directory to sys.path to import main
sys.path.insert(0, "/home/lupin/code/pantheon/services/control-plane/bff")

import main as bff_main

# Set AUTH_STUB to true to bypass JWT validation
os.environ["PANTHEON_BFF_AUTH_STUB"] = "true"

AUTH = "Bearer test-operator:operator,admin"

def reproduce_sse_gap():
    client = TestClient(bff_main.app)
    routes = [
        "/bff/events/stream",
        "/bff/sse/notifications",
        "/bff/sse/command-center/kpi",
        "/bff/sse/command-center/events",
        "/bff/sse/jobs/job-1/progress",
        "/bff/sse/alerts",
        "/bff/sse/incidents/inc-1/timeline",
        "/bff/sse/deployment/events",
        "/bff/sse/review/updates",
        "/bff/sse/agora/signals",
        "/bff/sse/agora/sessions/sess-1",
    ]

    print("Running SSE Gap Reproduction...")
    for route in routes:
        try:
            response = client.get(route, headers={"Authorization": AUTH})
            print(f"Route {route}: {response.status_code}")
        except Exception as e:
            print(f"Route {route}: Error {e}")

    print("\nChecking existing /api/v1 routes for comparison...")
    existing_routes = [
        "/api/v1/runtime/rt-1/events/stream",
        "/api/v1/incidents/stream",
        "/api/v1/kill-switch/updates",
        "/api/v1/approvals/stream",
        "/api/v1/agora/ask/stream",
    ]
    for route in existing_routes:
        try:
            response = client.get(route, headers={"Authorization": AUTH})
            print(f"Route {route}: {response.status_code}")
        except Exception as e:
            print(f"Route {route}: Error {e}")

if __name__ == "__main__":
    reproduce_sse_gap()
