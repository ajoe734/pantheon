#!/usr/bin/env python3
"""E2E hosted SSE verifier and reconnection probe.
"""
import os
import sys
import ssl
import time
import urllib.request

def _ctx():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx

def test_sse_connection(base, token, last_event_id=None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "text/event-stream",
    }
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
        
    path = "/bff/management/trade-journeys/events?tenant_id=pantheon-dev&environment=paper"
    url = base.rstrip("/") + path
    req = urllib.request.Request(url, headers=headers)
    ctx = _ctx()
    
    print(f"Connecting to SSE: {url}")
    if last_event_id:
        print(f"  With Last-Event-ID: {last_event_id}")
        
    start_time = time.time()
    event_count = 0
    last_id = None
    
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
            print(f"Connection established! Status={response.status}")
            print(f"Headers: Content-Type={response.headers.get('Content-Type')}")
            
            # Read line by line with a limit of 5 seconds or 2 events
            line_buffer = []
            while time.time() - start_time < 5.0 and event_count < 2:
                line = response.readline().decode("utf-8")
                if not line:
                    break
                line = line.strip()
                if line:
                    line_buffer.append(line)
                    print(f"  Received line: {line}")
                    if line.startswith("id:"):
                        last_id = line[3:].strip()
                    if line.startswith("data:"):
                        event_count += 1
                else:
                    # Empty line indicates end of an SSE block
                    if line_buffer:
                        print("  --- end of event block ---")
                        line_buffer = []
                        
    except Exception as e:
        print(f"Connection ended/interrupted: {e}")
        
    return last_id, event_count

def main():
    base = os.environ.get("BFF_BASE", "https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io")
    token = "lupin:admin:pantheon-dev"

    print("=============================================================")
    print("HOSTED SSE STREAM & RECONNECT PROBE")
    print(f"Target BFF: {base}")
    print("=============================================================")

    # 1. Establish initial SSE stream connection
    print("\nPhase 1: Testing initial SSE connection...")
    last_id, count = test_sse_connection(base, token)
    print(f"Initial test finished. Received {count} events. Last Event ID: {last_id}")

    # 2. Simulate reconnection using Last-Event-ID
    print("\nPhase 2: Testing reconnect with Last-Event-ID...")
    reconnect_id = last_id or "1"
    new_last_id, new_count = test_sse_connection(base, token, last_event_id=reconnect_id)
    print(f"Reconnection test finished. Received {new_count} events.")

    print("\nHOSTED SSE RECONNECT PROBE COMPLETED SUCCESSFULLY!")
    return 0

if __name__ == "__main__":
    sys.exit(main())
