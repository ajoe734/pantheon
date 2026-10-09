"""Scheduled monitor agent: opens incidents for anomalies no open incident covers.

Every interval it collects a bounded, read-only snapshot, asks one agent (via
the openclaw gateway structured-extraction route, which pins a data-only tool and
denies every native tool) to judge it against the open incidents, and posts each
finding to the incidents consume endpoint. The agent can only return findings.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_PER_RUN = 5
MAX_PER_HOUR = 20
SNAPSHOT_BYTES = 8000  # per source; keeps the prompt bounded
MAX_PROMPT_CHARS = 120000
MAX_INCIDENT_SUMMARY_CHARS = 1000
DEFAULT_SOURCES = {
    "runtime_status": "http://runtime-manager:8081/api/runtime-fleet/desired-state",
    "performance": "http://telemetry:8083/api/telemetry/runtime-summaries",
    "persona_health": "http://persona:8002/readyz",
    "control_loop_health": "http://paper-fleet-reconciler:8011/readyz",
}
SEVERITIES = ["low", "medium", "high", "critical"]
# The only thing the agent may produce. No tools are offered to it.
FINDINGS_SCHEMA = {
    "type": "object",
    "properties": {
        "findings": {
            "type": "array",
            "maxItems": MAX_PER_RUN,
            "items": {
                "type": "object",
                "properties": {
                    "fingerprint": {"type": "string", "maxLength": 120},
                    "title": {"type": "string", "maxLength": 200},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "rationale": {"type": "string", "maxLength": 1500},
                },
                "required": ["fingerprint", "title", "severity", "rationale"],
            },
        }
    },
    "required": ["findings"],
}


class Degraded(Exception):
    pass


def _http(url: str, *, data: Any = None, headers: dict[str, str] | None = None, timeout: float = 20) -> Any:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", **(headers or {})}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        parsed = json.loads(resp.read().decode() or "null")
        if isinstance(parsed, dict):
            parsed["_http_status"] = resp.status
        return parsed


def source_headers(env: Callable[[str, str], str]) -> dict[str, dict[str, str]]:
    """Read-only credentials per protected source (same contracts the reconciler uses)."""
    tenant = env("PANTHEON_TENANT_ID", "default")
    return {
        "runtime_status": {"Authorization": f"Bearer {env('PANTHEON_RUNTIME_MANAGER_TOKEN', '')}"},
        "performance": {
            "Authorization": f"Bearer {env('PANTHEON_TELEMETRY_SERVICE_TOKEN', '')}",
            "X-Tenant-Id": tenant,
        },
    }


def collect_snapshot(
    sources: dict[str, str],
    fetch: Callable[..., Any] = _http,
    headers: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for name, url in sources.items():
        try:
            snapshot[name] = json.dumps(
                fetch(url, headers=(headers or {}).get(name)), sort_keys=True, default=str
            )[:SNAPSHOT_BYTES]
        except Exception as exc:
            raise Degraded(f"read API {name} unavailable: {exc}") from exc
    return snapshot


def snapshot_ref(snapshot: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()[:16]
    return f"monitor-snapshot-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{digest}"


def _format_incident(inc: dict[str, Any]) -> dict[str, Any]:
    summary = inc.get("evidence_summary")
    if isinstance(summary, str) and len(summary) > MAX_INCIDENT_SUMMARY_CHARS:
        summary = summary[:MAX_INCIDENT_SUMMARY_CHARS] + "... [truncated]"
    return {"title": inc.get("title"), "cluster": inc.get("incident_cluster_id"), "summary": summary}


def build_prompt(snapshot: dict[str, Any], open_incidents: list[dict[str, Any]]) -> str:
    base = (
        "You are a read-only monitor. Compare the snapshot with the open incidents. Report only "
        "anomalies in runtime status, drawdown, fill rate, slippage, persona health or control-loop "
        "health that NO open incident already covers. Reuse the same fingerprint for the same "
        "underlying anomaly. Return an empty findings list when nothing is unexplained.\n"
    )
    snap = f"\nSNAPSHOT={json.dumps(snapshot)}"
    covered: list[dict[str, Any]] = []
    omitted = 0
    for inc in open_incidents:
        cand_covered = covered + [_format_incident(inc)]
        cand_omitted = len(open_incidents) - len(cand_covered)
        note = f"\nOMITTED_INCIDENTS={cand_omitted} ({cand_omitted} open incidents omitted / left out due to prompt limit)" if cand_omitted else ""
        if len(f"{base}OPEN_INCIDENTS={json.dumps(cand_covered)}{note}{snap}") > MAX_PROMPT_CHARS:
            omitted = len(open_incidents) - len(covered)
            break
        covered = cand_covered
    note = f"\nOMITTED_INCIDENTS={omitted} ({omitted} open incidents omitted / left out due to prompt limit)" if omitted else ""
    prompt = f"{base}OPEN_INCIDENTS={json.dumps(covered)}{note}{snap}"
    return prompt[:MAX_PROMPT_CHARS]


def ask_agent(prompt: str, adapter_url: str, token: str, fetch: Callable[..., Any] = _http) -> list[dict[str, Any]]:
    try:
        resp = fetch(
            f"{adapter_url}/api/openclaw-adapter/assistant/providers/openclaw/structured",
            data={"prompt": prompt, "extraction_schema": FINDINGS_SCHEMA},
            headers={"X-Operator-Id": "monitor-agent", "X-Pantheon-Service-Token": token},
            timeout=float(os.getenv("MONITOR_AGENT_TIMEOUT_SECONDS", "120")),
        )
        findings = resp["data"]["output"]["structured_data"]["findings"]
    except Exception as exc:
        raise Degraded(f"agent unavailable: {exc}") from exc
    if not isinstance(findings, list):
        raise Degraded("agent returned malformed findings")
    return [f for f in findings if isinstance(f, dict)]


class RateLimiter:
    """Persistent hour window of incident-creating posts."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _times(self, now: float) -> list[float]:
        try:
            times = json.loads(self.path.read_text())
        except (OSError, ValueError):
            times = []
        return [t for t in times if now - t < 3600]

    def _save(self, times: list[float]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(times))
        tmp.replace(self.path)

    def reserve(self, now: float) -> bool:
        """Durably reserve one creation slot before a possibly-creating POST."""
        times = self._times(now)
        if len(times) >= MAX_PER_HOUR:
            return False
        self._save(times + [now])
        return True

    def release(self, now: float, reserved_at: float) -> None:
        """Return a slot once the POST is confirmed not to have created an incident."""
        times = self._times(now)
        if reserved_at in times:
            times.remove(reserved_at)
            self._save(times)


def run_once(
    *,
    sources: dict[str, str],
    incidents_url: str,
    adapter_url: str,
    adapter_token: str,
    limiter: RateLimiter,
    headers: dict[str, dict[str, str]] | None = None,
    fetch: Callable[..., Any] = _http,
    now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """One run. Returns a record; a degraded run creates no incident."""
    try:
        snapshot = collect_snapshot(sources, fetch, headers)
        open_incidents = fetch(f"{incidents_url}/api/incidents?open_only=true")
        ref = snapshot_ref(snapshot)
        findings = ask_agent(build_prompt(snapshot, open_incidents), adapter_url, adapter_token, fetch)
    except Exception as exc:
        return {"status": "degraded", "reason": str(exc), "created": 0, "updated": 0}

    created = updated = skipped = 0
    for finding in findings[:MAX_PER_RUN]:
        reserved_at = now()
        if created >= MAX_PER_RUN or not limiter.reserve(reserved_at):
            skipped += 1
            continue
        try:
            resp = fetch(
                f"{incidents_url}/api/incidents/consume-agent-finding",
                data={**{k: finding.get(k) for k in ("fingerprint", "title", "severity", "rationale")}, "snapshot_ref": ref},
            )
        except Exception:
            skipped += 1  # outcome unknown: the reservation is kept so a lost 201 still counts
            continue
        if resp.get("_http_status") == 201:
            created += 1
        else:
            updated += 1
            limiter.release(now(), reserved_at)
    return {"status": "ok", "snapshot_ref": ref, "created": created, "updated": updated, "skipped": skipped}


def main() -> None:
    env = os.environ.get
    sources = json.loads(env("MONITOR_AGENT_SOURCES_JSON", "")) if env("MONITOR_AGENT_SOURCES_JSON") else DEFAULT_SOURCES
    limiter = RateLimiter(Path(env("MONITOR_AGENT_STATE_PATH", "/data/monitor-agent/creations.json")))
    interval = float(env("MONITOR_AGENT_INTERVAL_SECONDS", "900"))
    while True:
        record = run_once(
            sources=sources,
            incidents_url=env("PANTHEON_INCIDENTS_API_URL", "http://incidents:8090"),
            adapter_url=env("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-gateway-adapter:8104"),
            adapter_token=env("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", ""),
            limiter=limiter,
            headers=source_headers(lambda k, d: env(k) or d),
        )
        print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **record}), flush=True)
        if env("MONITOR_AGENT_ONCE"):
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
