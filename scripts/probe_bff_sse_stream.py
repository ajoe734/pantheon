#!/usr/bin/env python3
"""BFF-CONSOL-011 /bff/events/stream replay smoke probe.

The probe exercises the real BFF SSE route over HTTP. It models the two
browser transports that matter for execute-plans:

- cookie session: native EventSource withCredentials=true, no Authorization
- bearer session: EventSource polyfill/fetch stream with Authorization

Secrets and bearer material are never written to the evidence file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


TASK_ID = "BFF-CONSOL-011"
DEFAULT_BASE_URL = os.getenv("PANTHEON_BFF_BASE_URL", "http://127.0.0.1:8000")
DEFAULT_OUTPUT = "support/evidence/BFF-CONSOL-011-sse-replay-smoke.json"
DEFAULT_CHANNEL = "approval"
DEFAULT_COOKIE_NAME = "pantheon_session"
SSE_HEADER_KEYS = (
    "Content-Type",
    "Cache-Control",
    "X-SSE-Channel",
    "X-SSE-Replay-Supported",
    "X-SSE-Replay-Window-Events",
    "X-SSE-Buffer-Size",
    "X-SSE-Replay-Store",
    "X-SSE-Resync-Routes",
    "X-BFF-Session-Kind",
)


@dataclass(frozen=True)
class AuthMode:
    name: str
    transport: str
    browser_client: str
    authorization_header: bool
    cookie_session: bool
    with_credentials: bool


COOKIE_MODE = AuthMode(
    name="cookie_session",
    transport="Cookie header only",
    browser_client="native EventSource(..., { withCredentials: true })",
    authorization_header=False,
    cookie_session=True,
    with_credentials=True,
)
BEARER_MODE = AuthMode(
    name="bearer_polyfill",
    transport="Authorization header",
    browser_client="EventSource polyfill/fetch stream",
    authorization_header=True,
    cookie_session=False,
    with_credentials=False,
)


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_12(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def make_token(args: argparse.Namespace) -> tuple[str, dict[str, Any]]:
    provided = (
        os.getenv("PANTHEON_BFF_SMOKE_BEARER_TOKEN", "").strip()
        or os.getenv("PANTHEON_BFF_BEARER_TOKEN", "").strip()
    )
    if provided:
        token = provided.removeprefix("Bearer ").strip()
        return token, {
            "kind": "provided_bearer",
            "token_sha256_12": sha256_12(token),
        }

    secret = (
        os.getenv("PANTHEON_BFF_SMOKE_JWT_SECRET", "").strip()
        or os.getenv("PANTHEON_BFF_JWT_SECRET", "").strip()
    )
    if not secret:
        raise SystemExit(
            "Set PANTHEON_BFF_SMOKE_BEARER_TOKEN or "
            "PANTHEON_BFF_SMOKE_JWT_SECRET/PANTHEON_BFF_JWT_SECRET"
        )

    from services.runtime_auth_inbound import encode_jwt_hs256

    now = int(time.time())
    roles = [role.strip() for role in args.roles.split(",") if role.strip()]
    payload = {
        "sub": args.subject,
        "iss": args.issuer,
        "aud": args.audience,
        "iat": now,
        "exp": now + args.ttl_seconds,
        "roles": roles,
        "amr": ["pwd", "mfa"],
        "mfa_verified": True,
    }
    token = encode_jwt_hs256(payload, secret=secret)
    return token, {
        "kind": "minted_hs256_jwt",
        "subject": args.subject,
        "issuer": args.issuer,
        "audience": args.audience,
        "roles": roles,
        "ttl_seconds": args.ttl_seconds,
        "secret_sha256_12": sha256_12(secret),
    }


def header_value(headers: dict[str, str], key: str) -> str | None:
    wanted = key.lower()
    for current_key, value in headers.items():
        if current_key.lower() == wanted:
            return value
    return None


def selected_headers(headers: dict[str, str]) -> dict[str, str]:
    selected: dict[str, str] = {}
    for key in SSE_HEADER_KEYS:
        value = header_value(headers, key)
        if value is not None:
            selected[key] = value
    return selected


def auth_headers(
    *,
    mode: AuthMode,
    token: str,
    cookie_name: str,
    last_event_id: str | None = None,
) -> dict[str, str]:
    headers = {
        "Accept": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Correlation-Id": f"cid-{TASK_ID.lower()}-{int(time.time())}",
    }
    if mode.authorization_header:
        headers["Authorization"] = f"Bearer {token}"
        headers["X-MFA-Token"] = "000000"
    if mode.cookie_session:
        headers["Cookie"] = f"{cookie_name}={token}"
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    return headers


def redacted_request_headers(headers: dict[str, str]) -> dict[str, Any]:
    return {
        "Authorization": "present" if "Authorization" in headers else "absent",
        "Cookie": "present" if "Cookie" in headers else "absent",
        "Last-Event-ID": headers.get("Last-Event-ID"),
        "Accept": headers.get("Accept"),
    }


def request_json(
    *,
    method: str,
    base_url: str,
    path: str,
    token: str,
    timeout: float,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-MFA-Token": "000000",
    }
    body_bytes = json.dumps(body or {}).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body_bytes,
        headers=headers,
        method=method,
    )
    started = time.time()
    raw = b""
    status = 0
    response_headers: dict[str, str] = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            status = int(response.status)
            response_headers = dict(response.headers.items())
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        response_headers = dict(exc.headers.items())
        raw = exc.read()
    except Exception as exc:  # noqa: BLE001
        return {
            "status": 0,
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }

    text = raw.decode("utf-8", errors="replace")
    try:
        parsed: Any = json.loads(text) if text else None
    except json.JSONDecodeError:
        parsed = None
    return {
        "status": status,
        "ok": 200 <= status < 300,
        "duration_ms": round((time.time() - started) * 1000),
        "headers": selected_headers(response_headers),
        "body": parsed,
        "body_prefix": None if 200 <= status < 300 else text[:500],
    }


def publish_event(
    *,
    base_url: str,
    token: str,
    timeout: float,
    channel: str,
    event_type: str,
    run_id: str,
    label: str,
) -> dict[str, Any]:
    path = "/api/v1/internal/sse/publish?" + urllib.parse.urlencode(
        {"channel": channel, "event_type": event_type}
    )
    payload = {
        "approval_id": f"appr-{TASK_ID.lower()}-{run_id}-{label}",
        "target_type": "ApprovalDecision",
        "target_id": f"decision-{run_id}-{label}",
        "requester_id": "codex2-sse-probe",
        "metadata": {"task_id": TASK_ID, "label": label, "run_id": run_id},
    }
    result = request_json(
        method="POST",
        base_url=base_url,
        path=path,
        token=token,
        timeout=timeout,
        body=payload,
    )
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    return {
        "label": label,
        "channel": channel,
        "event_type": event_type,
        "path": path,
        "status": result.get("status"),
        "ok": bool(result.get("ok")) and bool(body.get("event_id")),
        "event_id": body.get("event_id"),
        "payload_summary": {
            "approval_id": payload["approval_id"],
            "target_id": payload["target_id"],
            "metadata": payload["metadata"],
        },
        "error": result.get("error") or result.get("body_prefix"),
    }


def parse_sse_block(lines: list[str]) -> dict[str, Any]:
    fields: dict[str, list[str]] = {}
    comments: list[str] = []
    for line in lines:
        if not line:
            continue
        if line.startswith(":"):
            comments.append(line)
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        fields.setdefault(key.strip(), []).append(value.lstrip())

    raw_data = "\n".join(fields.get("data", []))
    try:
        data: Any = json.loads(raw_data) if raw_data else None
    except json.JSONDecodeError:
        data = None
    return {
        "raw_lines": lines,
        "comments": comments,
        "id": fields.get("id", [None])[-1],
        "event": fields.get("event", [None])[-1],
        "data": data,
        "data_parse_ok": raw_data == "" or data is not None,
    }


def read_first_sse_event(response: Any) -> dict[str, Any]:
    lines: list[str] = []
    while True:
        raw = response.readline()
        if raw == b"":
            break
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if lines:
                parsed = parse_sse_block(lines)
                if parsed["comments"] and not parsed["id"] and not parsed["data"]:
                    lines = []
                    continue
                return parsed
            continue
        lines.append(line)
    return {
        "raw_lines": lines,
        "id": None,
        "event": None,
        "data": None,
        "data_parse_ok": False,
        "error": "stream ended before an SSE event block was received",
    }


def event_shape_checks(parsed_event: dict[str, Any]) -> dict[str, bool]:
    data = parsed_event.get("data")
    return {
        "sse_id_line_present": bool(parsed_event.get("id")),
        "sse_event_type_line_present": bool(parsed_event.get("event")),
        "data_json_parse_ok": bool(parsed_event.get("data_parse_ok")),
        "data_id_present": isinstance(data, dict) and bool(data.get("id")),
        "data_type_present": isinstance(data, dict) and bool(data.get("type")),
        "data_timestamp_present": isinstance(data, dict) and bool(data.get("timestamp")),
        "id_line_matches_data_id": (
            isinstance(data, dict)
            and bool(parsed_event.get("id"))
            and parsed_event.get("id") == data.get("id")
        ),
        "event_line_matches_data_type": (
            isinstance(data, dict)
            and bool(parsed_event.get("event"))
            and parsed_event.get("event") == data.get("type")
        ),
    }


def summarize_event(parsed_event: dict[str, Any]) -> dict[str, Any]:
    data = parsed_event.get("data")
    data_summary: dict[str, Any] = {"type": type(data).__name__}
    if isinstance(data, dict):
        data_summary = {
            "id": data.get("id"),
            "type": data.get("type"),
            "timestamp": data.get("timestamp"),
            "data_keys": sorted(data.get("data", {}).keys()) if isinstance(data.get("data"), dict) else [],
        }
    return {
        "id": parsed_event.get("id"),
        "event": parsed_event.get("event"),
        "data": data_summary,
        "shape_checks": event_shape_checks(parsed_event),
        "raw_block": "\n".join(parsed_event.get("raw_lines", []))[:1000],
    }


def stream_first_event(
    *,
    base_url: str,
    mode: AuthMode,
    token: str,
    timeout: float,
    channel: str,
    cookie_name: str,
    last_event_id: str | None = None,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({"channel": channel})
    url = f"{base_url.rstrip('/')}/bff/events/stream?{query}"
    headers = auth_headers(
        mode=mode,
        token=token,
        cookie_name=cookie_name,
        last_event_id=last_event_id,
    )
    req = urllib.request.Request(url, headers=headers, method="GET")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            response_headers = dict(response.headers.items())
            parsed_event = read_first_sse_event(response)
            event_summary = summarize_event(parsed_event)
            checks = event_summary["shape_checks"]
            ok = (
                int(response.status) == 200
                and all(checks.values())
                and header_value(response_headers, "X-SSE-Channel") == channel
                and header_value(response_headers, "X-SSE-Replay-Supported") == "true"
            )
            return {
                "mode": mode.name,
                "browser_client": mode.browser_client,
                "transport": mode.transport,
                "with_credentials": mode.with_credentials,
                "status": int(response.status),
                "ok": ok,
                "duration_ms": round((time.time() - started) * 1000),
                "url_path": f"/bff/events/stream?{query}",
                "request_headers": redacted_request_headers(headers),
                "response_headers": selected_headers(response_headers),
                "first_event": event_summary,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        return {
            "mode": mode.name,
            "browser_client": mode.browser_client,
            "transport": mode.transport,
            "with_credentials": mode.with_credentials,
            "status": int(exc.code),
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "url_path": f"/bff/events/stream?{query}",
            "request_headers": redacted_request_headers(headers),
            "response_headers": selected_headers(dict(exc.headers.items())),
            "error_body_prefix": raw[:500],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "mode": mode.name,
            "browser_client": mode.browser_client,
            "transport": mode.transport,
            "with_credentials": mode.with_credentials,
            "status": 0,
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "url_path": f"/bff/events/stream?{query}",
            "request_headers": redacted_request_headers(headers),
            "error": f"{type(exc).__name__}: {exc}",
        }


def replay_unavailable(
    *,
    base_url: str,
    mode: AuthMode,
    token: str,
    timeout: float,
    channel: str,
    cookie_name: str,
    missing_event_id: str,
) -> dict[str, Any]:
    query = urllib.parse.urlencode({"channel": channel})
    url = f"{base_url.rstrip('/')}/bff/events/stream?{query}"
    headers = auth_headers(
        mode=mode,
        token=token,
        cookie_name=cookie_name,
        last_event_id=missing_event_id,
    )
    req = urllib.request.Request(url, headers=headers, method="GET")
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(500).decode("utf-8", errors="replace")
            return {
                "mode": mode.name,
                "status": int(response.status),
                "ok": False,
                "duration_ms": round((time.time() - started) * 1000),
                "request_headers": redacted_request_headers(headers),
                "response_headers": selected_headers(dict(response.headers.items())),
                "body_prefix": raw,
                "error": "expected HTTP 409",
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        response_headers = dict(exc.headers.items())
        try:
            body: Any = json.loads(raw) if raw else None
        except json.JSONDecodeError:
            body = None
        error = body.get("detail", {}).get("error", {}) if isinstance(body, dict) else {}
        details = error.get("details", {}) if isinstance(error, dict) else {}
        resync_header = header_value(response_headers, "X-SSE-Resync-Routes")
        ok = (
            int(exc.code) == 409
            and isinstance(error, dict)
            and error.get("code") == "SSE_REPLAY_UNAVAILABLE"
            and details.get("lastEventId") == missing_event_id
            and bool(resync_header)
        )
        return {
            "mode": mode.name,
            "status": int(exc.code),
            "ok": ok,
            "duration_ms": round((time.time() - started) * 1000),
            "request_headers": redacted_request_headers(headers),
            "response_headers": selected_headers(response_headers),
            "error_code": error.get("code") if isinstance(error, dict) else None,
            "details": {
                "reason": details.get("reason"),
                "channel": details.get("channel"),
                "lastEventId": details.get("lastEventId"),
                "replaySupported": details.get("replaySupported"),
                "replayWindowEvents": details.get("replayWindowEvents"),
                "replayStore": details.get("replayStore"),
                "resyncRoutes": details.get("resyncRoutes"),
            },
            "body_prefix": raw[:500] if not ok else None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "mode": mode.name,
            "status": 0,
            "ok": False,
            "duration_ms": round((time.time() - started) * 1000),
            "request_headers": redacted_request_headers(headers),
            "error": f"{type(exc).__name__}: {exc}",
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--channel", default=DEFAULT_CHANNEL)
    parser.add_argument("--cookie-name", default=DEFAULT_COOKIE_NAME)
    parser.add_argument("--subject", default="op-bff-consol-011")
    parser.add_argument("--roles", default="operator,admin,reviewer,approver")
    parser.add_argument("--issuer", default=os.getenv("PANTHEON_BFF_JWT_ISSUER", "pantheon-dev") or "pantheon-dev")
    parser.add_argument("--audience", default=os.getenv("PANTHEON_BFF_JWT_AUDIENCE", "bff-operators") or "bff-operators")
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    token, auth_source = make_token(args)
    generated_at = utc_now()
    run_id = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    missing_event_id = f"evt-{TASK_ID.lower()}-missing-{run_id}"

    first_publish = publish_event(
        base_url=base_url,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        event_type="approval.created",
        run_id=run_id,
        label="first",
    )
    second_publish = publish_event(
        base_url=base_url,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        event_type="approval.decided",
        run_id=run_id,
        label="second",
    )

    publish_ok = first_publish["ok"] and second_publish["ok"]
    cookie_open = stream_first_event(
        base_url=base_url,
        mode=COOKIE_MODE,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        cookie_name=args.cookie_name,
    )
    bearer_open = stream_first_event(
        base_url=base_url,
        mode=BEARER_MODE,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        cookie_name=args.cookie_name,
    )

    replay_last_event_id = str(first_publish.get("event_id") or "")
    cookie_replay = stream_first_event(
        base_url=base_url,
        mode=COOKIE_MODE,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        cookie_name=args.cookie_name,
        last_event_id=replay_last_event_id or None,
    )
    bearer_replay = stream_first_event(
        base_url=base_url,
        mode=BEARER_MODE,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        cookie_name=args.cookie_name,
        last_event_id=replay_last_event_id or None,
    )
    expected_replay_event_id = second_publish.get("event_id")
    for replay in (cookie_replay, bearer_replay):
        replay["expected_replayed_event_id"] = expected_replay_event_id
        observed_id = (
            replay.get("first_event", {})
            .get("data", {})
            .get("id")
            if isinstance(replay.get("first_event"), dict)
            else None
        )
        replay["replayed_expected_event"] = observed_id == expected_replay_event_id
        replay["ok"] = bool(replay.get("ok")) and observed_id == expected_replay_event_id

    unavailable = replay_unavailable(
        base_url=base_url,
        mode=BEARER_MODE,
        token=token,
        timeout=args.timeout,
        channel=args.channel,
        cookie_name=args.cookie_name,
        missing_event_id=missing_event_id,
    )

    mock_generator_check = {
        "enabled": False,
        "live_mode_closed": True,
        "assertion": (
            "Probe seeded the BFF in-memory SSE replay buffer through the authenticated "
            "internal publish route and consumed /bff/events/stream; no client-side mock "
            "event generator or seed fallback was used."
        ),
        "passed": (
            cookie_open.get("response_headers", {}).get("X-SSE-Replay-Store") == "in-memory"
            and bearer_open.get("response_headers", {}).get("X-SSE-Replay-Store") == "in-memory"
        ),
    }

    assertions = {
        "publish_seed_events": publish_ok,
        "cookie_session_probe_opened_native_eventsource_shape": bool(cookie_open.get("ok")),
        "bearer_probe_opened_polyfill_authorization_shape": bool(bearer_open.get("ok")),
        "first_event_has_id_type_timestamp": bool(cookie_open.get("ok") and bearer_open.get("ok")),
        "cookie_last_event_id_replay_returns_event_after_cursor": bool(cookie_replay.get("ok")),
        "bearer_last_event_id_replay_returns_event_after_cursor": bool(bearer_replay.get("ok")),
        "missing_last_event_id_returns_409": bool(unavailable.get("ok")),
        "missing_replay_409_has_resync_routes_header": bool(
            unavailable.get("response_headers", {}).get("X-SSE-Resync-Routes")
        ),
        "mock_generator_closed_in_live_mode": bool(mock_generator_check.get("passed")),
    }
    failed_assertions = [name for name, ok in assertions.items() if not ok]

    evidence = {
        "task_id": TASK_ID,
        "generated_at": generated_at,
        "target_url": base_url,
        "channel": args.channel,
        "auth_source": auth_source,
        "client_modes": {
            COOKIE_MODE.name: {
                "browser_client": COOKIE_MODE.browser_client,
                "with_credentials": COOKIE_MODE.with_credentials,
                "authorization_header": COOKIE_MODE.authorization_header,
                "cookie_name": args.cookie_name,
            },
            BEARER_MODE.name: {
                "browser_client": BEARER_MODE.browser_client,
                "with_credentials": BEARER_MODE.with_credentials,
                "authorization_header": BEARER_MODE.authorization_header,
            },
        },
        "commands": [
            "PANTHEON_BFF_SMOKE_JWT_SECRET=<redacted> "
            "PANTHEON_BFF_JWT_SECRET=<redacted> "
            "scripts/probe_bff_sse_stream.py --base-url <bff-url>"
        ],
        "publish": [first_publish, second_publish],
        "open_transcripts": {
            COOKIE_MODE.name: cookie_open,
            BEARER_MODE.name: bearer_open,
        },
        "last_event_id_replay": {
            "cursor_event_id": replay_last_event_id,
            COOKIE_MODE.name: cookie_replay,
            BEARER_MODE.name: bearer_replay,
        },
        "replay_unavailable": unavailable,
        "mock_generator_live_mode": mock_generator_check,
        "assertions": assertions,
        "summary": {
            "passed": not failed_assertions,
            "failed_assertions": failed_assertions,
            "acceptance": {
                "cookie_session_probe_opened": bool(cookie_open.get("ok")),
                "bearer_probe_opened": bool(bearer_open.get("ok")),
                "first_event_contains_id_type_timestamp": assertions["first_event_has_id_type_timestamp"],
                "last_event_id_replay": bool(cookie_replay.get("ok") and bearer_replay.get("ok")),
                "replay_unavailable_409_with_resync_routes": bool(unavailable.get("ok")),
                "mock_generator_live_mode_closed": bool(mock_generator_check.get("passed")),
            },
        },
    }

    out_path = REPO_ROOT / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(json.dumps(evidence["summary"], sort_keys=True))
    print(str(out_path))
    if failed_assertions:
        for assertion in failed_assertions:
            print(f"FAIL {assertion}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
