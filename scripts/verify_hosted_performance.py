#!/usr/bin/env python3
"""Measures latency on the hosted BFF endpoints to generate performance evidence.
"""
import os
import sys
import ssl
import json
import time
import urllib.request

def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def measure_call(base, path, token):
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    ctx = _ctx()
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            r.read()
            status = r.status
    except Exception as e:
        status = getattr(e, "code", 500)
    end = time.perf_counter()
    return (end - start) * 1000.0, status

def main():
    base = os.environ.get("BFF_BASE", "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io")
    token = "lupin:admin:pantheon-dev"

    endpoints = [
        "/bff/management/trade-journeys?tenant_id=pantheon-dev&environment=paper",
        "/bff/management/trade-journeys/tj-scenario-1?tenant_id=pantheon-dev&environment=paper",
        "/bff/management/trade-journeys/resolve?q=co-scen-9&tenant_id=pantheon-dev&environment=paper",
    ]

    print("=============================================================")
    print("HOSTED PERFORMANCE AUDIT: LATENCY MEASUREMENT")
    print(f"Target BFF: {base}")
    print("=============================================================")

    for path in endpoints:
        print(f"\nMeasuring: {path}")
        latencies = []
        # Warmup
        measure_call(base, path, token)

        # 10 iterations
        for i in range(10):
            lat, status = measure_call(base, path, token)
            latencies.append(lat)
            print(f"  Iteration {i+1}: status={status}, latency={lat:.2f}ms")
            time.sleep(0.1)

        latencies.sort()
        min_lat = latencies[0]
        max_lat = latencies[-1]
        avg_lat = sum(latencies) / len(latencies)
        p95_lat = latencies[int(len(latencies) * 0.95)]

        print(f"  --- Statistics ---")
        print(f"  Min: {min_lat:.2f}ms")
        print(f"  Max: {max_lat:.2f}ms")
        print(f"  Avg: {avg_lat:.2f}ms")
        print(f"  p95: {p95_lat:.2f}ms")

        # Assertions
        if avg_lat > 500.0:
            print("  FAIL: Average latency exceeds 500ms SLO threshold")
            return 1
        else:
            print("  ✓ PASS: Meets performance SLO constraints")

    print("\nHOSTED PERFORMANCE AUDIT COMPLETED SUCCESSFULLY!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
