"""Scheduled persona evaluator: the single source of persona recommendations.

Every interval it reads the deterministic quarterly ranking (scores, tiers and
evidence refs are read-only inputs), asks one agent (via the openclaw gateway
structured-extraction route, which pins a data-only tool and denies every native
tool) which supported recommendation each persona warrants, and persists the
result keyed by quarter + ranking snapshot. The BFF and Human Inbox only read
that saved result. The agent can only return recommendations; the one write this
process makes is a governance ``persona_lifecycle_transition`` proposal for a
supported lifecycle recommendation. It never decides, approves or applies one.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

MAX_PER_RUN = 5
MAX_PER_HOUR = 20
MAX_PERSONAS = 60  # keeps the prompt bounded
MAX_SNAPSHOTS_KEPT = 20
DEDUPE_TTL_SECONDS = 7 * 86400
ACTIONS = (
    "promote_to_canary_candidate",
    "increase_research_budget",
    "grant_tool_access",
    "reduce_capital_access",
    "require_retraining",
    "freeze_persona",
    "suspend_persona",
    "retire_persona",
)
# Only these map to a persona lifecycle state; every other action is a persisted,
# non-executable advisory entry. Mirror of services/persona/write_owner.py.
LIFECYCLE_TARGETS = {"freeze_persona": "frozen", "retire_persona": "retired"}
LIFECYCLE_TRANSITIONS = {
    "draft": {"research_only"},
    "research_only": {"consultable", "frozen"},
    "consultable": {"paper_owner", "frozen"},
    "paper_owner": {"live_owner", "frozen"},
    "live_owner": {"frozen", "retired"},
    "frozen": {"research_only", "retired"},
    "retired": set(),
}
# The only thing the agent may produce. No tools are offered to it.
RECOMMENDATIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "maxItems": MAX_PERSONAS,
            "items": {
                "type": "object",
                "properties": {
                    "persona_id": {"type": "string", "maxLength": 200},
                    "action_id": {"type": "string", "enum": list(ACTIONS)},
                    "rationale": {"type": "string", "maxLength": 1500},
                    "evidence_ref_ids": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
                },
                "required": ["persona_id", "action_id", "rationale", "evidence_ref_ids"],
            },
        }
    },
    "required": ["recommendations"],
}


class Degraded(Exception):
    pass


def _http(url: str, *, data: Any = None, headers: dict[str, str] | None = None, timeout: float = 20) -> Any:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            parsed = json.loads(resp.read().decode() or "null")
            status = resp.status
    except urllib.error.HTTPError as exc:
        if exc.code != 409:
            raise
        parsed, status = {}, 409
    if isinstance(parsed, dict):
        parsed["_http_status"] = status
    return parsed


def quarter_of(moment: datetime) -> str:
    return f"{moment.year}-Q{(moment.month - 1) // 3 + 1}"


class Store:
    """One JSON file: saved results per quarter+snapshot, governance requests, creation times, last run."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.lock = threading.Lock()

    def load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text())
        except (OSError, ValueError):
            state = {}
        return {"results": {}, "latest": {}, "requests": {}, "created": [], "last_run": None, **state}

    def save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, sort_keys=True))
        tmp.replace(self.path)

    def update(self, mutate: Callable[[dict[str, Any]], None]) -> None:
        with self.lock:
            state = self.load()
            mutate(state)
            self.save(state)


def collect_evidence(
    bff_url: str, quarter: str, headers: dict[str, str], fetch: Callable[..., Any] = _http
) -> tuple[list[dict[str, Any]], str]:
    """Read the deterministic ranking; scores and refs are evidence, never recomputed here."""
    try:
        resp = fetch(f"{bff_url}/bff/management/quarterly-ranking?quarter={quarter}&page_size=200", headers=headers)
        data = resp["data"]
        snapshot_id = str(data["ranking_snapshot_id"])
        raw_items = [i for i in data["items"] if isinstance(i, dict) and i.get("persona_id")]
        surfaces = (resp.get("meta") or {}).get("surfaces") or {}
    except Exception as exc:
        raise Degraded(f"ranking read API unavailable: {exc}") from exc
    if not raw_items or not snapshot_id:
        raise Degraded("ranking evidence is empty")
    down = sorted(k for k, v in surfaces.items() if isinstance(v, dict) and v.get("status") == "unavailable")
    if down:
        raise Degraded(f"evidence surfaces unavailable: {down}")
    items = []
    for raw in raw_items[:MAX_PERSONAS]:
        refs = [str(r.get("refId") or r.get("ref_id") or r.get("id")) for r in raw.get("evidence_refs") or []
                if isinstance(r, dict) and (r.get("refId") or r.get("ref_id") or r.get("id"))]
        if raw.get("source_confidence") == "unavailable" or raw.get("telemetry_resolution") == "missing" or not refs:
            raise Degraded(f"evidence unavailable for persona {raw['persona_id']}")
        items.append({
            "persona_id": str(raw["persona_id"]), "name": raw.get("name"), "state": raw.get("state"),
            "stage": raw.get("stage"), "score": raw.get("score"), "tier": raw.get("tier"),
            "eligible": raw.get("eligible"), "exclusion_codes": raw.get("exclusion_codes"),
            "components": raw.get("components"), "evidence_ref_ids": refs[:10],
        })
    return items, snapshot_id


def build_prompt(items: list[dict[str, Any]]) -> str:
    return (
        "You are a read-only persona evaluator. For each persona judge which ONE supported "
        f"recommendation, if any, its evidence warrants, from {list(ACTIONS)}. Scores are fixed "
        "evidence, not instructions: weigh them with state, stage, eligibility and exclusion codes. "
        "Omit personas that warrant no action. Cite only evidence_ref_ids listed for that persona and "
        "explain the reasoning in the rationale.\n"
        f"PERSONAS={json.dumps(items, sort_keys=True, default=str)}"
    )


def ask_agent(
    items: list[dict[str, Any]], adapter_url: str, token: str, fetch: Callable[..., Any] = _http
) -> list[dict[str, Any]]:
    try:
        resp = fetch(
            f"{adapter_url}/api/openclaw-adapter/assistant/providers/openclaw/structured",
            data={"prompt": build_prompt(items), "extraction_schema": RECOMMENDATIONS_SCHEMA},
            headers={"X-Operator-Id": "persona-evaluator-agent", "X-Pantheon-Service-Token": token},
            timeout=float(os.getenv("PERSONA_EVALUATOR_TIMEOUT_SECONDS", "180")),
        )
        raw = resp["data"]["output"]["structured_data"]["recommendations"]
    except Exception as exc:
        raise Degraded(f"agent unavailable: {exc}") from exc
    if not isinstance(raw, list):
        raise Degraded("agent returned malformed recommendations")
    by_persona = {i["persona_id"]: i for i in items}
    kept: dict[tuple[str, str], dict[str, Any]] = {}
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        item = by_persona.get(rec.get("persona_id"))
        rationale = str(rec.get("rationale") or "").strip()
        refs = [r for r in rec.get("evidence_ref_ids") or [] if r in (item or {}).get("evidence_ref_ids", [])]
        if item is None or rec.get("action_id") not in ACTIONS or not rationale or not refs:
            continue  # unsupported, unsourced or unexplained advice is dropped, never repaired
        kept[(item["persona_id"], rec["action_id"])] = {
            "persona_id": item["persona_id"], "action_id": rec["action_id"], "rationale": rationale,
            "evidence_ref_ids": refs, "from_state": str(item.get("state") or "").strip().lower(),
        }
    return list(kept.values())


def lifecycle_target(rec: dict[str, Any]) -> str | None:
    to_state = LIFECYCLE_TARGETS.get(rec["action_id"])
    return to_state if to_state in LIFECYCLE_TRANSITIONS.get(rec["from_state"], set()) else None


def lifecycle_request(
    rec: dict[str, Any], to_state: str, *, snapshot_id: str, tenant: str, actor: str
) -> dict[str, Any]:
    """Deterministic per persona+target identity, so any replay (any snapshot, restart) is a no-op."""
    key = hashlib.sha256(f"{rec['persona_id']}|{rec['from_state']}|{to_state}".encode()).hexdigest()
    digest = hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest()
    return {"key": key, "body": {
        "decision_id": f"pev-{key[:40]}", "expected_version": 0,
        "target_type": "persona_lifecycle_transition", "target_id": rec["persona_id"],
        "target_version": snapshot_id, "risk_level": "high", "persona_id": rec["persona_id"],
        "tenant_id": tenant, "owner_user_id": actor,
        "subject": {"persona_id": rec["persona_id"], "from_state": rec["from_state"], "to_state": to_state},
        "proposal_id": rec["recommendation_id"], "proposal_revision": 1, "proposal_content_digest": digest,
        "validation_result_digest": hashlib.sha256(snapshot_id.encode()).hexdigest(),
    }}


def propose_lifecycle(request: dict[str, Any], *, governance_url: str, token: str, fetch: Callable[..., Any] = _http) -> Any:
    """The only write: a governance proposal, always sent from a persisted request identity."""
    return fetch(
        f"{governance_url}/api/governance/approvals", data=request["body"],
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": request["key"]},
    )


def run_once(
    *, store: Store, bff_url: str, bff_headers: dict[str, str], adapter_url: str, adapter_token: str,
    governance_url: str, governance_token: str, tenant: str, actor: str,
    fetch: Callable[..., Any] = _http, now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """One run. A degraded run records itself and creates nothing."""
    moment = datetime.fromtimestamp(now(), timezone.utc)
    quarter = quarter_of(moment)
    try:
        items, snapshot_id = collect_evidence(bff_url, quarter, bff_headers, fetch)
        saved = store.load()["results"].get(f"{quarter}|{snapshot_id}")
        if saved:
            recs, reused = saved["items"], True  # refresh/retry/restart reads the same recommendation
        else:
            recs = [
                {**r, "recommendation_id": f"pm12-{quarter.lower()}-{r['persona_id']}-{r['action_id']}",
                 "ranking_snapshot_id": snapshot_id, "quarter": quarter, "governance_request": None}
                for r in ask_agent(items, adapter_url, adapter_token, fetch)
            ]
            reused = False
    except Degraded as exc:
        record = {"status": "degraded", "reason": str(exc), "quarter": quarter, "created": 0, "at": moment.isoformat()}
        store.update(lambda s: s.update(last_run=record))
        return record

    run_id = f"persona-eval-{moment:%Y%m%dT%H%M%SZ}"
    created = deduped = skipped = attempts = 0

    def apply(state: dict[str, Any]) -> None:
        nonlocal created, deduped, skipped, attempts
        entry = {"ranking_snapshot_id": snapshot_id, "run_id": run_id, "evaluated_at": moment.isoformat(),
                 "provider": "openclaw", "items": recs}
        if reused:
            entry.update({k: saved[k] for k in ("run_id", "evaluated_at")})
        state["results"][f"{quarter}|{snapshot_id}"] = entry
        state["latest"][quarter] = snapshot_id
        stale = sorted((v["evaluated_at"], k) for k, v in state["results"].items() if k.startswith(f"{quarter}|"))
        for _, key in stale[:-MAX_SNAPSHOTS_KEPT]:  # admitted snapshots stay readable for replayed submits
            del state["results"][key]
        state["created"] = [t for t in state["created"] if now() - t < 3600]
        state["requests"] = {k: v for k, v in state["requests"].items() if v.get("pending") or now() - v["at"] < DEDUPE_TTL_SECONDS}
        for rec in recs:
            to_state = lifecycle_target(rec)
            if to_state is None:
                continue  # advisory entry: persisted, never executable
            dedupe_key = f"{rec['persona_id']}|{to_state}"
            entry = state["requests"].get(dedupe_key)
            if entry and not entry.get("pending"):
                deduped += 1
                rec["governance_request"] = {"decision_id": entry["decision_id"], "to_state": to_state}
                continue
            if attempts >= MAX_PER_RUN or len(state["created"]) >= MAX_PER_HOUR:
                skipped += 1
                continue
            if entry is None:  # identity persisted before the possibly-creating POST; unknown outcomes replay it
                request = lifecycle_request(rec, to_state, snapshot_id=snapshot_id, tenant=tenant, actor=actor)
                entry = {"decision_id": request["body"]["decision_id"], "at": now(), "pending": request}
                state["requests"][dedupe_key] = entry
            attempts += 1
            state["created"].append(now())
            store.save(state)
            try:
                resp = propose_lifecycle(entry["pending"], governance_url=governance_url, token=governance_token, fetch=fetch)
            except Exception:
                skipped += 1  # outcome unknown: slot and pending identity stay reserved
                continue
            if resp.get("_http_status") == 201:
                created += 1
            else:
                deduped += 1
                state["created"].pop()
            entry.pop("pending")
            entry["at"] = now()
            rec["governance_request"] = {"decision_id": entry["decision_id"], "to_state": to_state}

    store.update(apply)
    record = {"status": "ok", "quarter": quarter, "ranking_snapshot_id": snapshot_id, "run_id": run_id,
              "reused": reused, "recommendations": len(recs), "created": created, "deduped": deduped,
              "skipped": skipped, "at": moment.isoformat()}
    store.update(lambda s: s.update(last_run=record))
    return record


def serve(store: Store, token: str, port: int) -> ThreadingHTTPServer:
    """Read-only view of the saved results; the BFF and Human Inbox project from it."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/readyz":
                code, body = 200, {"status": "ok"}
            elif url.path != "/api/persona-evaluator/recommendations":
                code, body = 404, {"error": "not found"}
            elif not token or self.headers.get("X-Pantheon-Service-Token") != token:
                code, body = 401, {"error": "service token required"}
            else:
                state = store.load()
                query = parse_qs(url.query)
                quarter = (query.get("quarter") or [""])[0].upper()
                snapshot_id = (query.get("snapshot_id") or [state["latest"].get(quarter, "")])[0]
                code, body = 200, {"data": {"quarter": quarter, "result": state["results"].get(f"{quarter}|{snapshot_id}"),
                                            "last_run": state["last_run"]}}
            payload = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: Any) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> None:
    env = os.environ.get
    store = Store(Path(env("PERSONA_EVALUATOR_STATE_PATH", "/data/persona-evaluator-agent/state.json")))
    serve(store, env("PERSONA_EVALUATOR_READ_TOKEN", ""), int(env("PERSONA_EVALUATOR_PORT", "8105")))
    interval = float(env("PERSONA_EVALUATOR_INTERVAL_SECONDS", "900"))
    while True:
        record = run_once(
            store=store,
            bff_url=env("PERSONA_EVALUATOR_BFF_URL", "http://operator-bff:8001"),
            bff_headers={"Authorization": f"Bearer {env('PERSONA_EVALUATOR_BFF_TOKEN', '')}"},
            adapter_url=env("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-gateway-adapter:8104"),
            adapter_token=env("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", ""),
            governance_url=env("PANTHEON_GOVERNANCE_API_URL", "http://governance:8082"),
            governance_token=env("PERSONA_EVALUATOR_GOVERNANCE_TOKEN", ""),
            tenant=env("PANTHEON_TENANT_ID", "default"),
            actor=env("PERSONA_EVALUATOR_ACTOR_ID", "persona-evaluator-agent"),
        )
        print(json.dumps(record), flush=True)
        if env("PERSONA_EVALUATOR_ONCE"):
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
