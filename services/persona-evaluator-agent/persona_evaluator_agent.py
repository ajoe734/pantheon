"""Scheduled persona evaluator: the single source of persona recommendations.

Every interval it reads the deterministic quarterly ranking (scores, tiers and
evidence refs are read-only inputs), applies the fixed governance mapping, and
optionally asks the agent to explain the selected actions. It persists the
result keyed by quarter + ranking snapshot. The BFF and Human Inbox only read
that saved result. The agent writes rationale only. This
process admits snapshots to Rankings, submits existing lifecycle proposals, and
uses fixed quarter-close rules for record-only ``paper_canary_candidacy``
proposals. It never decides, approves or applies a proposal.
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
from urllib.parse import parse_qs, quote, urlparse, urlencode

from services.rankings.snapshots import snapshot_record, admit_snapshot
from services.rankings.store import build_rankings_store

MAX_PER_RUN = 5
MAX_PER_HOUR = 20
MAX_PERSONAS = 60  # keeps the prompt bounded
MAX_SNAPSHOTS_KEPT = 20
MAX_PATTERN_PERSONAS = 200
DEDUPE_TTL_SECONDS = 7 * 86400
ACTIONS = (
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
LIFECYCLE_TARGETS = {"freeze_persona": "frozen", "suspend_persona": "frozen", "retire_persona": "retired"}
MAPPING_VERSION = "promotion-governance-2026-07-05-v1"
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
            "maxItems": MAX_PERSONAS * len(ACTIONS),
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
        try:
            parsed = json.loads(exc.read().decode() or "{}")
        except Exception:
            parsed = {}
        status = 409
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
) -> tuple[list[dict[str, Any]], Any]:
    """Read the deterministic ranking; scores and refs are evidence, never recomputed here."""
    try:
        raw_items, seen = [], set()
        snapshot_id, page_token = "", ""
        while True:
            query = urlencode({"quarter": quarter, "page_size": 200, "page_token": page_token})
            resp = fetch(f"{bff_url}/bff/management/quarterly-ranking?{query}", headers=headers)
            data = resp["data"]
            current_id = str(data["ranking_snapshot_id"])
            if snapshot_id and current_id != snapshot_id:
                raise Degraded("ranking changed during pagination")
            snapshot_id = current_id
            raw_items.extend(data["items"])
            surfaces = (resp.get("meta") or {}).get("surfaces") or {}
            if any(v.get("status") == "unavailable" for v in surfaces.values() if isinstance(v, dict)):
                raise Degraded("ranking evidence surface unavailable")
            page_token = (resp.get("page_info") or {}).get("next_page_token")
            if not page_token:
                break
            if page_token in seen or len(seen) >= 100:
                raise Degraded("ranking pagination did not terminate")
            seen.add(page_token)
        snapshot = snapshot_record(raw_items, surface="quarterly", period=quarter)
        if snapshot.ranking_snapshot_id != snapshot_id:
            raise Degraded("ranking content incomplete, redacted or changed; snapshot not admitted")
    except Exception as exc:
        raise Degraded(f"ranking read API unavailable: {exc}") from exc
    if not raw_items or not snapshot_id:
        raise Degraded("ranking evidence is empty")
    items = []
    for raw in raw_items:
        if len(items) >= MAX_PERSONAS:  # bound applies to evidence-admitted rows
            break
        refs = [str(r.get("refId") or r.get("ref_id") or r.get("id")) for r in raw.get("evidence_refs") or []
                if isinstance(r, dict) and (r.get("refId") or r.get("ref_id") or r.get("id"))]
        if raw.get("source_confidence") == "unavailable" or raw.get("telemetry_resolution") == "missing" or not refs:
            if raw.get("eligible") is False:  # explicit ineligible + missing evidence: skip only this row
                continue
            raise Degraded(f"evidence unavailable for persona {raw['persona_id']}")
        items.append({
            "persona_id": str(raw["persona_id"]), "name": raw.get("name"), "state": raw.get("owner_lifecycle_state"),
            "stage": raw.get("stage"), "score": raw.get("score"), "tier": raw.get("tier"),
            "session_id": raw.get("session_id"), "formula_version": raw.get("formula_version"),
            "eligible": raw.get("eligible"), "exclusion_codes": raw.get("exclusion_codes"),
            "components": raw.get("components"), "evidence_ref_ids": refs[:10],
        })
    if not items:
        raise Degraded("no persona has admissible evidence")
    return items, snapshot


def build_prompt(items: list[dict[str, Any]]) -> str:
    return (
        "Write a rationale for each already selected action in fixed_actions. Do not choose, add, "
        "remove or change actions. Cite only the persona's evidence_ref_ids.\n"
        f"PERSONAS={json.dumps(items, sort_keys=True, default=str)}"
    )


def fixed_actions(item: dict[str, Any]) -> list[str]:
    """Every matching condition in the fixed governance table contributes advice."""
    components = item.get("components")
    if not isinstance(components, dict):
        raise Degraded("missing_or_invalid_ranking_metrics")
    values = [item.get("score"), *(components.get(k)
              for k in ("risk_score", "execution_score", "activity_score"))]
    if any(_fraction(v) is None or not 0 <= v <= 100 for v in values):
        raise Degraded("missing_or_invalid_ranking_metrics")
    overall, risk, execution, activity = values
    actions = []
    if overall >= 70 and risk >= 60:
        actions += ["increase_research_budget", "grant_tool_access"]
    if risk < 55 or overall < 55:
        actions.append("reduce_capital_access")
    if execution < 55 or activity < 45 or overall < 55:
        actions.append("require_retraining")
    if overall < 25:
        actions.append("retire_persona")
    if overall < 35:
        actions.append("suspend_persona")
    if overall < 45:
        actions.append("freeze_persona")
    return sorted(actions, key=lambda a: (a not in LIFECYCLE_TARGETS, ACTIONS.index(a)))


def ask_agent(
    items: list[dict[str, Any]], adapter_url: str, token: str, fetch: Callable[..., Any] = _http
) -> list[dict[str, Any]]:
    selected = {i["persona_id"]: fixed_actions(i) for i in items}
    items = [{**i, "fixed_actions": selected[i["persona_id"]]} for i in items]
    kept = {(i["persona_id"], action): {
        "persona_id": i["persona_id"], "action_id": action,
        "rationale": f"{MAPPING_VERSION}: {action}; score={i['score']}; components={json.dumps(i['components'], sort_keys=True)}",
        "evidence_ref_ids": i["evidence_ref_ids"], "from_state": str(i.get("state") or "").strip().lower(),
        "stage": i.get("stage"), "session_id": i.get("session_id"), "mapping_version": MAPPING_VERSION,
    } for i in items for action in selected[i["persona_id"]]}
    if not kept:
        return []
    try:
        resp = fetch(
            f"{adapter_url}/api/openclaw-adapter/assistant/providers/openclaw/structured",
            data={"prompt": build_prompt(items), "extraction_schema": RECOMMENDATIONS_SCHEMA},
            headers={"X-Operator-Id": "persona-evaluator-agent", "X-Pantheon-Service-Token": token},
            timeout=float(os.getenv("PERSONA_EVALUATOR_TIMEOUT_SECONDS", "180")),
        )
        raw = resp["data"]["output"]["structured_data"]["recommendations"]
    except Exception:
        return list(kept.values())  # a rationale writer cannot veto the fixed mapping
    if not isinstance(raw, list):
        return list(kept.values())
    by_persona = {i["persona_id"]: i for i in items}
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        if not isinstance(rec.get("persona_id"), str) or not isinstance(rec.get("action_id"), str):
            continue
        item = by_persona.get(rec.get("persona_id"))
        rationale = str(rec.get("rationale") or "").strip()
        refs = [r for r in rec.get("evidence_ref_ids") or [] if r in (item or {}).get("evidence_ref_ids", [])]
        if item is None or (item["persona_id"], rec.get("action_id")) not in kept or not rationale or not refs:
            continue  # unsupported, unsourced or unexplained advice is dropped, never repaired
        kept[(item["persona_id"], rec["action_id"])].update(rationale=rationale[:1500], evidence_ref_ids=refs)
    return list(kept.values())


def lifecycle_target(rec: dict[str, Any]) -> str | None:
    to_state = LIFECYCLE_TARGETS.get(rec["action_id"])
    return to_state if to_state in LIFECYCLE_TRANSITIONS.get(rec["from_state"], set()) else None


GOVERNANCE_APPROVALS_PATH = "/api/governance/approvals"


def proposal_digest(rec: dict[str, Any]) -> str:
    content = {
        "persona_id": str(rec.get("persona_id") or ""),
        "action_id": str(rec.get("action_id") or ""),
        "from_state": str(rec.get("from_state") or ""),
        "rationale": str(rec.get("rationale") or "").strip(),
        "evidence_ref_ids": sorted(rec.get("evidence_ref_ids") or []),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def lifecycle_request(
    rec: dict[str, Any], to_state: str, *, snapshot_id: str, tenant: str, actor: str
) -> dict[str, Any]:
    """Deterministic proposal identity bound to persona, target, content, and snapshot."""
    digest = proposal_digest(rec)
    key = hashlib.sha256(f"{rec['persona_id']}|{rec['from_state']}|{to_state}|{snapshot_id}|{digest}".encode()).hexdigest()
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
    """Submit a governance proposal from its persisted request identity."""
    return fetch(
        f"{governance_url}{GOVERNANCE_APPROVALS_PATH}", data=request["body"],
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": request["key"]},
    )


def readback_lifecycle(decision_id: str, *, governance_url: str, token: str, fetch: Callable[..., Any] = _http) -> Any:
    """Readback verification for conflict resolution: read-only confirmation of existing proposal."""
    try:
        return fetch(f"{governance_url}{GOVERNANCE_APPROVALS_PATH}/{decision_id}", headers={"Authorization": f"Bearer {token}"})
    except Exception:
        return None


def route_paper_advice(rec: dict[str, Any], *, tenant: str, persona_url: str, persona_token: str,
                       fetch: Callable[..., Any]) -> dict[str, Any]:
    """Prepare an owner request, then replay exactly that request until acknowledged.

    No permission activation or research execution is exposed here. Pool member
    actions use the same Evolution proposal contract as the pool scheduler.
    """
    if not tenant or rec.get("stage") != "paper_running":
        raise Degraded("routing_requires_paper_stage_and_tenant")
    pid = quote(rec["persona_id"], safe="")
    headers = {"Authorization": f"Bearer {persona_token}", "X-Tenant-Id": tenant}
    if rec["action_id"] in {"increase_research_budget", "grant_tool_access"}:
        if not persona_url or not persona_token:
            raise Degraded("harness_owner_credential_missing")
        harness = fetch(f"{persona_url}/api/personas/{pid}/harness", headers=headers)
        base = harness.get("active_version")
        version = next((v for v in harness.get("versions", []) if v.get("version") == base), None)
        if not version or not rec.get("session_id"):
            raise Degraded("harness_base_or_persona_session_missing")
        guidance = dict(version["surface"].get("tool_guidance") or {})
        if not guidance:
            raise Degraded("harness_tool_profile_missing")
        # This is a draft request for review within the existing tool ceiling.
        # No allowed_tools are invented or granted by the evaluator.
        guidance = {k: v + "\n" + rec["rationale"] for k, v in guidance.items()}
        key = hashlib.sha256(f"{tenant}|{rec['ranking_snapshot_id']}|{rec['recommendation_id']}".encode()).hexdigest()
        body = {"schema_version": "persona_harness_patch.v1", "patch_id": f"pm12-{key[:32]}", "persona_id": rec["persona_id"], "session_id": rec["session_id"],
                "base_version": base, "rollback_target": base, "patch_type": "tool_profile",
                "proposed_change": {"tool_guidance": guidance}, "expected_effect": rec["action_id"] + "; paper cap applies",
                "evidence_refs": [{"kind": "packet", "ref": r, "moment": rec["ranking_snapshot_id"]}
                                  for r in rec["evidence_ref_ids"]]}
        return {"url": f"{persona_url}/api/personas/{pid}/harness/patches", "body": body, "key": key,
                "owner": "persona_harness", "headers": headers}
    if rec["action_id"] == "require_retraining":
        # Same owner HTTP contracts as Evolution's PoolOwner, kept stdlib-only
        # so the scheduled evaluator image carries no research executor.
        def read(owner, path):
            url, token = os.getenv(f"AUTO_CONSUMER_{owner}_URL", ""), os.getenv(f"AUTO_CONSUMER_{owner}_TOKEN", "")
            if not url or not token:
                raise Degraded(f"{owner.lower()}_owner_credential_missing")
            return fetch(url.rstrip("/") + path,
                         headers={"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant})

        if not persona_url or not persona_token:
            raise Degraded("persona_owner_credential_missing")
        pool = fetch(f"{persona_url}/api/personas/{pid}/strategy-pool", headers=headers)
        if pool.get("tenant_id") != tenant or pool.get("persona_id") != rec["persona_id"]:
            raise Degraded("pool_owner_identity_mismatch")
        bindings = [b for b in read("CAPITAL", "/api/bindings") if b.get("persona_id") == rec["persona_id"]
                    and b.get("tenant_id") == tenant and b.get("status") == "active"]
        if len(bindings) != 1:
            raise Degraded("unique_active_paper_binding_required")
        payload = read("TELEMETRY", "/api/telemetry/runtime-summaries")
        rows = payload if isinstance(payload, list) else payload.get("summaries", [])
        rows = [r for r in rows if r.get("persona_capital_binding_id") == bindings[0]["binding_id"]
                and r.get("deployment_stage") == "paper" and r.get("tenant_id") == tenant]
        if len(rows) != 1:
            raise Degraded("unique_paper_runtime_summary_required")
        raw_books = rows[0].get("member_virtual_books") or []
        books = {b.get("member_id") or b.get("artifact_id"): {**b, **b.get("net_of_costs", {})} for b in raw_books}
        if len(books) != len(raw_books):
            raise Degraded("ambiguous_member_books")
        members = [m for m in pool["members"] if m["stage"] == "active"]
        if not members:
            raise Degraded("no_active_pool_member")
        for member in members:
            book = books.get(member["artifact_ref"], {})
            dates = book.get("dates") or []
            from datetime import date
            observed = [date.fromisoformat(d) for d in dates]
            joined = date.fromisoformat(member["joined_at"][:10])
            if (_fraction(book.get("sharpe_ratio")) is None or not book.get("source_ref")
                    or book.get("checksum") != member["checksum"] or not observed
                    or observed != sorted(set(observed)) or observed[-1] > datetime.now(timezone.utc).date()
                    or not any(d > joined for d in observed)):
                raise Degraded("pool_member_ranking_metric_missing")
        member = min(members, key=lambda m: (books[m["artifact_ref"]]["sharpe_ratio"], m["artifact_ref"]))
        entry = read("REGISTRY", "/api/registry/strategy-artifacts/" + quote(member["artifact_ref"], safe=""))["entry"]
        if (entry.get("artifact_state") != "approved" or entry.get("checksum") != member["checksum"]
                or entry.get("owner_tenant") != tenant or entry.get("registry_id") != member["artifact_ref"]
                or not entry.get("version")):
            raise Degraded("pool_member_registry_binding_mismatch")
        identity = [tenant, rec["persona_id"], member["artifact_ref"], member["checksum"],
                    member["joined_at"], "retrain", "quarter:" + rec["quarter"]]
        body = {"decision_id": "evd-pool-" + hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24],
                "tenant_id": tenant, "target_type": "candidate_artifact", "target_id": member["artifact_ref"],
                "target_version": entry["version"], "target_stage": "paper", "risk_level": "low",
                "persona_id": rec["persona_id"], "action_type": "retrain",
                "created_by_role": "evolution_controller", "created_by_id": "persona-evaluator-agent",
                "metadata": {"discovery_auto_scope": True, "variants": 50, "pool_member": {
                    "persona_id": rec["persona_id"], "pool_version": pool["pool_version"],
                    **{k: member[k] for k in ("artifact_ref", "checksum", "joined_at")}}}}
        body.update(rationale=rec["rationale"], evidence_refs=[
            {"ref_type": "telemetry_summary", "ref_id": r, "storage_ref": {}, "note": None}
            for r in rec["evidence_ref_ids"]])
        body["metadata"].update(ranking_snapshot_id=rec["ranking_snapshot_id"],
                                member_book_source_ref=books[member["artifact_ref"]]["source_ref"])
        url, token = os.getenv("AUTO_CONSUMER_EVOLUTION_URL", ""), os.getenv("AUTO_CONSUMER_EVOLUTION_TOKEN", "")
        if not url or not token:
            raise Degraded("evolution_owner_credential_missing")
        return {"url": url.rstrip("/") + "/api/evolution/proposals", "body": body, "key": body["decision_id"],
                "owner": "pool_member_evolution", "headers": {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}}
    raise Degraded("unsupported_owner_route")


def run_once(
    *, store: Store, bff_url: str, bff_headers: dict[str, str], adapter_url: str, adapter_token: str,
    governance_url: str, governance_token: str, tenant: str, actor: str,
    fetch: Callable[..., Any] = _http, now: Callable[[], float] = time.time,
    ranking_store: Any = None,
    persona_url: str = "", persona_token: str = "",
) -> dict[str, Any]:
    """Admit evidence before evaluation; failed admission creates no recommendation or proposal."""
    moment = datetime.fromtimestamp(now(), timezone.utc)
    quarter = quarter_of(moment)
    try:
        items, snapshot = collect_evidence(bff_url, quarter, {**bff_headers, "X-Tenant-Id": tenant}, fetch)
        try:
            admitted = admit_snapshot(ranking_store if ranking_store is not None else build_rankings_store(), snapshot)
        except Exception as exc:
            raise Degraded(f"ranking snapshot admission unavailable: {exc}") from exc
        snapshot_id = admitted.ranking_snapshot_id
        saved = store.load()["results"].get(f"{quarter}|{snapshot_id}")
        if saved and saved.get("mapping_version") == MAPPING_VERSION:
            recs, reused = [r for r in saved["items"] if r.get("action_id") in ACTIONS], True  # refresh/retry/restart reads the same recommendation
        else:
            def remember_admission(state):
                state["latest"][quarter] = snapshot_id
                state["results"][f"{quarter}|{snapshot_id}"] = {
                    "ranking_snapshot_id": snapshot_id, "items": [], "agent_status": "unavailable",
                    "evaluated_at": moment.isoformat(), "run_id": None,
                    "paper_persona_ids": sorted(i["persona_id"] for i in items if i.get("stage") == "paper_running"),
                }
            store.update(remember_admission)
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
    created = deduped = skipped = attempts = conflicts = 0

    def apply(state: dict[str, Any]) -> None:
        nonlocal created, deduped, skipped, attempts, conflicts
        entry_result = {"ranking_snapshot_id": snapshot_id, "run_id": run_id, "evaluated_at": moment.isoformat(),
                        "provider": "fixed_rule", "mapping_version": MAPPING_VERSION, "items": recs,
                        "paper_persona_ids": sorted(i["persona_id"] for i in items if str(i.get("stage") or "").lower() == "paper_running")}
        if reused:
            entry_result.update({k: saved[k] for k in ("run_id", "evaluated_at")})
            entry_result["paper_persona_ids"] = saved.get("paper_persona_ids", entry_result["paper_persona_ids"])
        state["results"][f"{quarter}|{snapshot_id}"] = entry_result
        state["latest"][quarter] = snapshot_id
        stale = sorted((v["evaluated_at"], k) for k, v in state["results"].items() if k.startswith(f"{quarter}|"))
        for _, key in stale[:-MAX_SNAPSHOTS_KEPT]:  # admitted snapshots stay readable for replayed submits
            del state["results"][key]
        state["created"] = [t for t in state["created"] if now() - t < 3600]
        state["requests"] = {k: v for k, v in state["requests"].items() if v.get("pending") or now() - v["at"] < DEDUPE_TTL_SECONDS}
        for rec in recs:
            action = rec["action_id"]
            if action == "reduce_capital_access":
                rec["routing"] = {"owner": "paper_allocation", "status": "pending_quarter_close",
                                  "ranking_snapshot_id": snapshot_id, "reason": "allocation_owner_recomputes_from_closed_snapshot"}
                continue
            if action in {"increase_research_budget", "grant_tool_access", "require_retraining"}:
                key = f"{tenant}|{quarter}|{snapshot_id}|{rec['recommendation_id']}"
                route = state.setdefault("owner_routes", {}).setdefault(key, {})
                rec["routing"] = route
                if route.get("status") == "proposed":
                    continue
                try:
                    request = route.get("request")
                    if request is None:
                        request = route_paper_advice(rec, tenant=tenant, persona_url=persona_url,
                                                    persona_token=persona_token, fetch=fetch)
                        # Credentials are resolved at dispatch, never persisted.
                        route.update(request={k: v for k, v in request.items() if k != "headers"}, status="pending")
                        store.save(state)
                    else:
                        token = persona_token if request["owner"] == "persona_harness" else os.getenv("AUTO_CONSUMER_EVOLUTION_TOKEN", "")
                        request = {**request, "headers": {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}}
                    if not request["headers"]["Authorization"].removeprefix("Bearer "):
                        raise Degraded("owner_route_credential_missing")
                    if attempts >= MAX_PER_RUN or len(state["created"]) >= MAX_PER_HOUR:
                        raise Degraded("owner_route_rate_limited")
                    attempts += 1
                    state["created"].append(now())
                    store.save(state)
                    response = fetch(request["url"], data=request["body"],
                                     headers={**request["headers"], "Idempotency-Key": request["key"]})
                    body = request["body"]
                    if response.get("_http_status") == 409 and request["owner"] == "pool_member_evolution":
                        response = fetch(request["url"] + "/" + quote(body["decision_id"], safe=""), headers=request["headers"])
                    if request["owner"] == "persona_harness":
                        valid = response.get("proposal") == body and response.get("status") == "draft"
                    else:
                        valid = all(response.get(k) == body[k] for k in
                                    ("decision_id", "tenant_id", "target_id", "target_version", "action_type", "metadata", "evidence_refs", "rationale"))
                    if not valid or response.get("_http_status", 201) not in (200, 201):
                        raise Degraded("owner_route_readback_mismatch")
                    route.update(status="proposed", owner=request["owner"])
                    created += 1
                    route.pop("reason", None)
                except Exception as exc:
                    route.update(status="degraded", reason=str(exc))
                continue
            to_state = lifecycle_target(rec)
            if to_state is None:
                rec["routing"] = {"owner": "operator_lifecycle", "status": "degraded", "reason": "lifecycle_transition_unavailable"}
                continue  # advisory entry: persisted, never executable
            digest = proposal_digest(rec)
            rec["routing"] = {"owner": "operator_lifecycle", "status": "pending"}
            dedupe_key = f"{rec['persona_id']}|{to_state}" + ("|suspend" if action == "suspend_persona" else "")
            entry = state["requests"].get(dedupe_key)
            if entry and not entry.get("pending"):
                if entry.get("content_digest") == digest:
                    deduped += 1
                    rec["governance_request"] = {"decision_id": entry["decision_id"], "to_state": to_state}
                    rec["routing"]["status"] = "proposed"
                    continue
                entry = None
            if attempts >= MAX_PER_RUN or len(state["created"]) >= MAX_PER_HOUR:
                skipped += 1
                rec["routing"].update(status="degraded", reason="owner_route_rate_limited")
                continue
            if entry is None:  # identity persisted before the possibly-creating POST; unknown outcomes replay it
                request = lifecycle_request(rec, to_state, snapshot_id=snapshot_id, tenant=tenant, actor=actor)
                entry = {"decision_id": request["body"]["decision_id"], "at": now(), "pending": request,
                         "content_digest": digest, "to_state": to_state}
                state["requests"][dedupe_key] = entry
            attempts += 1
            state["created"].append(now())
            store.save(state)
            try:
                resp = propose_lifecycle(entry["pending"], governance_url=governance_url, token=governance_token, fetch=fetch)
            except Exception:
                skipped += 1  # outcome unknown: slot and pending identity stay reserved
                rec["routing"].update(status="degraded", reason="lifecycle_owner_outcome_unknown")
                continue
            req_body = entry["pending"]["body"]
            req_digest = req_body["proposal_content_digest"]
            dec_id, pid = entry["decision_id"], rec["persona_id"]
            status = resp.get("_http_status", 201) if isinstance(resp, dict) else 201
            match = lambda d: (isinstance(d, dict) and d.get("decision_id") == dec_id and d.get("tenant_id") == tenant
                               and d.get("target_id") == pid and d.get("proposal_content_digest") == req_digest
                               and d.get("target_type") == req_body["target_type"]
                               and d.get("target_version") == req_body["target_version"])
            if match(resp) and status in (200, 201):
                is_created, verified = (status == 201), True
            else:
                rb = readback_lifecycle(dec_id, governance_url=governance_url, token=governance_token, fetch=fetch)
                is_created, verified = (status == 201), match(rb) and rb.get("_http_status", 200) in (200, 201)
            if verified:
                if is_created:
                    created += 1
                else:
                    deduped += 1
                    state["created"].pop()
                entry.pop("pending", None)
                entry["at"] = now()
                entry["content_digest"] = req_digest
                gov_req = {"decision_id": dec_id, "to_state": to_state}
                if digest == req_digest:
                    rec["governance_request"] = gov_req
                    rec["routing"]["status"] = "proposed"
                for res in state["results"].values():
                    for item in res.get("items", []):
                        if item.get("persona_id") == pid and lifecycle_target(item) == to_state and proposal_digest(item) == req_digest:
                            item["governance_request"] = gov_req
            else:
                conflicts += 1
                skipped += 1
                rec["routing"].update(status="degraded", reason="lifecycle_owner_readback_mismatch")
                if status != 201 and status < 500:
                    state["created"].pop()
                entry["conflict"] = {"status": status, "detail": (resp.get("detail") if isinstance(resp, dict) else str(resp)), "at": now()}

    store.update(apply)
    status_label = "degraded" if conflicts > 0 else "ok"
    record = {"status": status_label, "quarter": quarter, "ranking_snapshot_id": snapshot_id, "run_id": run_id,
              "reused": reused, "recommendations": len(recs), "created": created, "deduped": deduped,
              "skipped": skipped, "at": moment.isoformat()}
    record["routing_failures"] = sum((r.get("routing") or {}).get("status") == "degraded" for r in recs)
    if conflicts > 0:
        record["conflicts"] = conflicts
    store.update(lambda s: s.update(last_run=record))
    return record


# The governed paper cycle rides the existing allocation-evaluation route with this authority_mode; the Capital owner
# (operator role, paper pool and paper environment only) runs the cycle and is idempotent per tenant+quarter+pool.
PAPER_CYCLE_PATH = "/bff/management/allocation-policy/evaluate"
PAPER_CYCLE_MODE = "governed_paper_quarterly_cycle"


def _fraction(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def pool_health_factors(health: Any, policy: Any) -> dict[str, Any]:
    """Derive the allocation factors from a persona's recorded strategy-pool health, never from a default.

    risk_budget_factor = min(1, gross risk-budget utilisation / budget_full): an under-used budget earns less.
    capacity_factor needs the O-21 capacity fact (budget full and admitted candidates waiting). The pool health
    record carries no such observation, so it stays 1.0 with the reason recorded and the status is never "known".
    Idle-day share is not a capacity fact and is never substituted for it.
    """
    health = health if isinstance(health, dict) else {}
    out: dict[str, Any] = {"risk_budget_factor": 1.0, "capacity_factor": 1.0, "source": "strategy_pool_health", "reasons": {}}

    def known(key: str) -> float | None:
        entry = health.get(key)
        return _fraction(entry.get("value")) if isinstance(entry, dict) and entry.get("status") == "known" else None

    gross = known("gross_utilisation")
    budget_full = _fraction((policy or {}).get("budget_full")) if isinstance(policy, dict) else None
    if gross is not None and budget_full and gross >= 0:
        out["risk_budget_factor"] = round(min(1.0, gross / budget_full), 8)
    else:
        out["reasons"]["risk_budget_factor"] = "gross_utilisation_or_budget_full_unknown"
    out["reasons"]["capacity_factor"] = "capacity_observation_unavailable"
    out["status"] = "known" if not out["reasons"] else "partial" if len(out["reasons"]) < 2 else "unavailable"
    return out


def collect_pool_health(persona_ids: list[str], *, persona_url: str, token: str, tenant: str,
                        fetch: Callable[..., Any] = _http) -> dict[str, dict[str, Any]]:
    """Per persona: factors from the current pool version's recorded health, else 1.0 plus the reason."""
    result: dict[str, dict[str, Any]] = {}
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}
    for persona_id in persona_ids:
        if not token or not tenant or not persona_url:
            result[persona_id] = {"risk_budget_factor": 1.0, "capacity_factor": 1.0, "status": "unavailable",
                                  "reason": "persona service credential or tenant missing"}
            continue
        try:
            pool = fetch(f"{persona_url}/api/personas/{quote(persona_id, safe='')}/strategy-pool", headers=headers)
            decision = pool.get("decision") if isinstance(pool, dict) else None
            health = (decision or {}).get("health")
            if not health:
                raise ValueError("no recorded pool health")
            result[persona_id] = pool_health_factors(health, (decision or {}).get("policy"))
        except Exception as exc:  # absent or unreadable pool data: factors stay 1 and the reason is recorded
            result[persona_id] = {"risk_budget_factor": 1.0, "capacity_factor": 1.0, "status": "unavailable",
                                  "reason": f"pool health unavailable: {exc}"}
    return result


def closed_quarter_snapshot(state: dict[str, Any], current_quarter: str) -> tuple[str, str] | None:
    """Most recent quarter strictly before the current one with a persisted ranking snapshot."""
    def key(q: str) -> tuple[int, int]:
        year, _, number = q.partition("-Q")
        return int(year), int(number)
    closed = [q for q in state["latest"] if q != current_quarter and key(q) < key(current_quarter)]
    if not closed:
        return None
    quarter = max(closed, key=key)
    return quarter, state["latest"][quarter]


def paper_cycle_once(
    *, store: Store, bff_url: str, bff_headers: dict[str, str], persona_url: str, persona_token: str,
    tenant: str, paper_pool_id: str, fetch: Callable[..., Any] = _http, now: Callable[[], float] = time.time,
) -> dict[str, Any]:
    """At quarter close ask the BFF/Capital owner for the governed paper allocation cycle (once per quarter).

    The owner recomputes everything from the persisted snapshot and is idempotent per tenant+quarter+pool,
    so a rerun or catch-up never creates a second proposal. Nothing is synthesised here: no configured pool,
    no closed-quarter snapshot or an owner refusal leaves a recorded status and no allocation.
    """
    moment = datetime.fromtimestamp(now(), timezone.utc)
    at = moment.isoformat()
    if not paper_pool_id:
        return {"status": "not_configured", "reason": "PERSONA_EVALUATOR_PAPER_POOL_ID is not set", "at": at}
    state = store.load()
    target = closed_quarter_snapshot(state, quarter_of(moment))
    if target is None:
        return {"status": "waiting_for_quarter_close", "at": at}
    quarter, snapshot_id = target
    cycle_key = f"{quarter}|{paper_pool_id}"
    done = state.get("paper_cycles", {}).get(cycle_key)
    if done and done.get("status") == "applied":
        return {**done, "reused": True}
    saved = state["results"].get(f"{quarter}|{snapshot_id}") or {}
    health = collect_pool_health(saved.get("paper_persona_ids") or [], persona_url=persona_url, token=persona_token,
                                 tenant=tenant, fetch=fetch)
    for rec in saved.get("items") or []:
        if rec.get("action_id") == "reduce_capital_access" and rec.get("stage") == "paper_running":
            health.setdefault(rec["persona_id"], {}).setdefault("recommendation_inputs", []).append({
                "action_id": rec["action_id"], "recommendation_id": rec["recommendation_id"],
                "ranking_snapshot_id": snapshot_id, "evidence_ref_ids": rec["evidence_ref_ids"]})
    body = {"authority_mode": PAPER_CYCLE_MODE, "quarter": quarter, "ranking_snapshot_id": snapshot_id,
            "capital_pool_id": paper_pool_id, "pool_health": health}
    headers = {**bff_headers, "X-Tenant-Id": tenant, "Idempotency-Key": f"paper-cycle:{tenant}:{quarter}:{paper_pool_id}"}
    try:
        resp = fetch(f"{bff_url}{PAPER_CYCLE_PATH}", data=body, headers=headers)
        data = resp["data"]
        record = {"status": "applied" if data.get("applied") else "pending", "quarter": quarter, "ranking_snapshot_id": snapshot_id,
                  "rebalance_id": data["rebalance"]["rebalance_id"], "replayed": bool(data.get("replayed")), "at": at}
        reductions = [r for r in saved.get("items") or [] if r.get("action_id") == "reduce_capital_access"]
        record["recommendation_ids"] = [r["recommendation_id"] for r in reductions]
    except Exception as exc:
        record = {"status": "degraded", "reason": f"paper allocation cycle failed: {exc}", "quarter": quarter,
                  "ranking_snapshot_id": snapshot_id, "at": at}
    def remember(state):
        state.setdefault("paper_cycles", {})[cycle_key] = record
        for rec in (state["results"].get(f"{quarter}|{snapshot_id}") or {}).get("items") or []:
            if rec.get("action_id") == "reduce_capital_access":
                rec["routing"] = {"owner": "paper_allocation", "status": record["status"],
                                  "ranking_snapshot_id": snapshot_id,
                                  **{k: record[k] for k in ("rebalance_id", "reason") if k in record}}
    store.update(remember)
    return record



# Record-only candidacy is deliberately distinct from lifecycle transitions.
PROMOTION_TARGET = "paper_canary_candidacy"


def promotion_content(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: rec[k] for k in ("persona_id", "quarter", "ranking_snapshot_id", "from_stage", "to_stage",
                               "rule_summary", "rationale", "evidence_ref_ids", "panel_memo") if k in rec}


def candidacy_panel(candidate: dict[str, Any], *, tenant: str, quarter: str, snapshot_id: str,
                    fetch: Callable[..., Any]) -> dict[str, Any]:
    """Open the existing admission panel and require its published risk/red-team memo.

    This scheduled path never fabricates participant answers. A pending or
    unavailable consultation leaves the candidacy away from the operator queue.
    """
    from services.persona.consult_triggers import required_consultation, RISK_PERSONA_ID, RED_TEAM_PERSONA_ID
    url = (os.getenv("PANTHEON_CONSULTATION_API_URL") or "").rstrip("/")
    token = os.getenv("CONSULTATION_SERVICE_TOKEN", "")
    if not url or not token:
        raise Degraded("canary_panel_owner_unavailable")
    pid = candidate["persona_id"]
    requirement = required_consultation("trial_admission", pid, initiator_persona_id=pid,
                                       touches=[PROMOTION_TARGET], target_version=snapshot_id)
    identity = hashlib.sha256(f"{tenant}|{quarter}|{snapshot_id}|{pid}".encode()).hexdigest()
    request_id = "pm12-panel-" + identity[:32]
    headers = {"Authorization": f"Bearer {token}", "X-Pantheon-Tenant-Id": tenant,
               "X-Pantheon-Service-Actor": f"persona:{pid}"}
    body = {"request_id": request_id, "tenant_id": tenant, "request_type": "persona_policy",
            "requested_by": {"actor_type": "persona", "actor_id": pid}, "from_persona_id": pid,
            "target_type": "trial_admission", "target_id": pid, "consultation_type": "persona_panel",
            "task": "Review fixed paper canary candidacy for " + quarter,
            "policy_id": requirement.policy_id, "metadata": {"panel": requirement.to_panel_metadata()},
            "evidence_refs": [f"ranking-snapshot:{snapshot_id}"], "trace_id": request_id}
    request = fetch(url + "/api/consult/requests", data=body, headers=headers)
    if any(request.get(k) != body[k] for k in ("request_id", "tenant_id", "target_type", "target_id", "metadata")):
        # The owner adds its own create fingerprint to metadata.
        if (any(request.get(k) != body[k] for k in ("request_id", "tenant_id", "target_type", "target_id"))
                or (request.get("metadata") or {}).get("panel") != body["metadata"]["panel"]):
            raise Degraded("canary_panel_binding_mismatch")
    if request.get("status") == "draft":
        fetch(url + f"/api/consult/requests/{request_id}/submit", data={}, headers=headers)
    transcript = fetch(url + f"/api/consult/requests/{request_id}/transcript", headers=headers)
    if transcript.get("request_id") != request_id:
        raise Degraded("canary_panel_transcript_mismatch")
    conclusions = {}
    for event in transcript.get("events") or []:
        actor = event.get("actor") or {}
        if actor.get("actor_type") == "persona" and actor.get("actor_id") in {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}:
            content = event.get("content") or {}
            if content.get("recommendation") and event.get("evidence_refs"):
                conclusions[actor["actor_id"]] = content
    if set(conclusions) != {RISK_PERSONA_ID, RED_TEAM_PERSONA_ID}:
        raise Degraded("canary_panel_conclusions_pending")
    memos = fetch(url + "/api/consult/memos?" + urlencode({"request_id": request_id, "status": "published"}), headers=headers)
    memo = next((m for m in memos if m.get("request_id") == request_id and m.get("status") == "published"
                 and m.get("target_type") == "trial_admission" and m.get("target_id") == pid
                 and m.get("memo_type") == "committee_summary" and m.get("author_ref") == f"consult-panel:{request_id}"
                 and m.get("memo_id") and m.get("summary")), None)
    if memo is None:
        raise Degraded("canary_panel_memo_pending")
    return {"request_id": request_id, "memo_id": memo["memo_id"], "summary": memo["summary"],
            "recommendation": memo["recommendation"], "conclusions": conclusions,
            "ranking_snapshot_id": snapshot_id, "tenant_id": tenant,
            "evidence_ref": f"consultation://memos/{memo['memo_id']}"}


def _probe_persona(persona: dict[str, Any]) -> bool:
    metadata = persona.get("metadata") or {}
    text = json.dumps({k: persona.get(k) for k in ("persona_id", "id", "name", "mandate")}).lower()
    text += " ".join(str(v).lower() for v in metadata.values() if isinstance(v, str))
    if any(str(source.get(k) or "").lower() in {"test", "acceptance", "fixture", "synthetic"}
           for source in (persona, metadata) for k in ("data_scope", "dataScope")):
        return True
    if any(str(source.get(k) or "").lower() in {"true", "1", "yes"}
           for source in (persona, metadata) for k in ("is_test", "test_data", "acceptance_run", "is_acceptance")):
        return True
    return ("probe" in text and any(word in text for word in ("release", "acceptance"))) or any(
        metadata.get(k) is True for k in ("is_probe", "release_probe", "acceptance_probe", "is_test"))


def nomination_rule(items: list[dict[str, Any]], quarter: str, *, min_days: int, min_orders: int) -> tuple[Any, dict[str, Any]]:
    """Only saved owner facts count. No elapsed-day, fill-count or rolling-metric proxies."""
    summary: dict[str, Any] = {"rule": "paper-canary-quarter-v1", "min_observed_trading_days": min_days,
                               "min_filled_orders": min_orders}
    def refuse(reason: str):
        return None, {**summary, "reason": reason}
    if min_days < 1 or min_orders < 1:
        return refuse("invalid_sample_thresholds")
    paper = [i for i in items if i.get("stage") == "paper_running"]
    if not paper:
        return refuse("no_paper_personas")
    if any(_fraction(i.get("score")) is None or i.get("rank") is None for i in paper):
        return refuse("unknown_ranking_metric")
    top = [i for i in paper if i.get("rank") == 1 and not isinstance(i.get("rank"), bool)]
    if len(top) != 1:
        return refuse("rank_one_tie" if len(top) > 1 else "no_rank_one_paper_persona")
    candidate = top[0]
    summary.update(persona_id=candidate["persona_id"], rank=1, score=candidate["score"])
    if any(i is not candidate and i["score"] >= candidate["score"] for i in paper):
        return refuse("rank_one_tie_or_inconsistent_order")
    if candidate.get("eligible") is not True or candidate.get("exclusion_codes") != []:
        return refuse("rank_one_excluded_or_eligibility_unknown")
    try:
        components = candidate.get("components") or {}
        fixed_actions(candidate)
        if candidate["score"] < 85 or components["risk_score"] < 70 or components["execution_score"] < 65:
            return refuse("fixed_mapping_canary_threshold_not_met")
    except Degraded:
        return refuse("missing_or_invalid_ranking_metrics")
    metrics = candidate.get("metrics") or {}
    if any(metrics.get(k + "_status") != "known" or _fraction(metrics.get(k)) is None
           for k in ("pnl_return", "drawdown")):
        return refuse("unknown_ranking_metric")
    fill = metrics.get("fill_rate_details") or {}
    if (fill.get("status") != "known" or _fraction(metrics.get("fill_rate")) is None
            or (fill.get("window") or {}).get("quarter") != quarter
            or (metrics.get("pnl_period") or {}).get("quarter") != quarter):
        return refuse("unknown_quarter_sample")
    dates = set()
    try:
        for ref in metrics.get("telemetry_evidence_refs") or []:
            if ref.get("source_type") != "quarter_equity" or not ref.get("ref_id"):
                continue
            at = datetime.fromisoformat(ref["observed_at"].replace("Z", "+00:00"))
            if at.tzinfo is None or quarter_of(at.astimezone(timezone.utc)) != quarter:
                return refuse("invalid_quarter_observation")
            dates.add(at.astimezone(timezone.utc).date().isoformat())
        orders = set()
        for order in fill.get("sources") or []:
            qty = _fraction(order.get("filled_quantity"))
            submitted = _fraction(order.get("submitted_quantity"))
            at = datetime.fromisoformat(order["submitted_at"].replace("Z", "+00:00"))
            if (qty is None or submitted is None or qty < 0 or submitted <= 0 or qty > submitted
                    or at.tzinfo is None or quarter_of(at.astimezone(timezone.utc)) != quarter
                    or not all(order.get(k) for k in ("runtime_id", "binding_id", "order_id", "event_ids"))):
                return refuse("invalid_quarter_order_sample")
            if qty > 0:
                orders.add((order["runtime_id"], order["binding_id"], order["order_id"]))
    except (ValueError, TypeError, KeyError, AttributeError):
        return refuse("invalid_quarter_sample")
    summary.update(observed_trading_days=len(dates), filled_orders=len(orders),
                   observation_basis="distinct_UTC_days_with_owner_quarter_equity_observations",
                   order_basis="distinct_submitted_orders_with_positive_quarter_filled_quantity")
    if len(dates) < min_days or len(orders) < min_orders:
        return refuse("insufficient_quarter_sample")
    return candidate, summary


def promotion_once(
    *, store: Store, tenant: str, actor: str, governance_url: str, governance_token: str,
    persona_url: str, persona_token: str, adapter_url: str, adapter_token: str,
    ranking_store: Any = None, fetch: Callable[..., Any] = _http, now: Callable[[], float] = time.time,
    min_days: int | None = None, min_orders: int | None = None,
) -> dict[str, Any]:
    """Existing schedule's quarter-close trigger. Persist one tenant+quarter identity before POST."""
    moment = datetime.fromtimestamp(now(), timezone.utc)
    record: dict[str, Any] = {"status": "waiting_for_quarter_close", "at": moment.isoformat()}
    def apply(state: dict[str, Any]) -> None:
        nonlocal record
        target = closed_quarter_snapshot(state, quarter_of(moment))
        if target is None:
            state["last_promotion"] = record
            return
        quarter, snapshot_id = target
        key = f"{tenant}|{quarter}"
        nominations = state.setdefault("promotions", {})
        existing = nominations.get(key) or {}
        if existing.get("status") == "proposed":
            record = ({**existing, "reused": True} if (existing.get("recommendation") or {}).get("panel_memo") else
                      {**existing, "status": "degraded", "reason": "legacy_candidacy_missing_panel_memo"})
            state["last_promotion"] = record
            return
        record = {"quarter": quarter, "ranking_snapshot_id": snapshot_id, "tenant_id": tenant,
                  "at": moment.isoformat()}
        try:
            if not tenant or not actor or not governance_token:
                raise Degraded("promotion_identity_or_credential_missing")
            request = existing.get("request")
            if request and not (existing.get("recommendation") or {}).get("panel_memo"):
                raise Degraded("legacy_candidacy_missing_panel_memo")
            if request is None:
                owner = ranking_store if ranking_store is not None else build_rankings_store()
                snapshot = owner.get_ranking_snapshot(snapshot_id)
                if (snapshot is None or snapshot.ranking_snapshot_id != snapshot_id
                        or snapshot.period != quarter or snapshot.surface != "quarterly"):
                    raise Degraded("closed_quarter_snapshot_unavailable")
                from services.rankings.snapshots import snapshot_content_digest, SUPPORTED_FORMULA_VERSIONS
                snapshot_items = snapshot.to_canonical_dict()["items"]
                if snapshot.content_digest != snapshot_content_digest(snapshot_items, surface="quarterly", period=quarter):
                    raise Degraded("snapshot_content_digest_mismatch")
                if snapshot.formula_version not in SUPPORTED_FORMULA_VERSIONS or any(
                    i.get("formula_version") not in SUPPORTED_FORMULA_VERSIONS for i in snapshot_items
                ):
                    raise Degraded("unknown_ranking_formula")
                days = int(os.getenv("PERSONA_EVALUATOR_PROMOTION_MIN_TRADING_DAYS", "30")) if min_days is None else min_days
                orders = int(os.getenv("PERSONA_EVALUATOR_PROMOTION_MIN_FILLED_ORDERS", "20")) if min_orders is None else min_orders
                candidate, summary = nomination_rule(snapshot_items, quarter, min_days=days, min_orders=orders)
                record["rule_summary"] = summary
                if candidate is None:
                    record.update(status="no_nomination", reason=summary["reason"])
                    nominations[key] = record
                    state["last_promotion"] = record
                    return
                if not persona_token or not persona_url:
                    raise Degraded("persona_identity_unavailable")
                persona = fetch(f"{persona_url}/api/personas/{quote(candidate['persona_id'], safe='')}",
                                headers={"Authorization": f"Bearer {persona_token}", "X-Tenant-Id": tenant})
                if (persona.get("tenant_id") != tenant
                        or (persona.get("persona_id") or persona.get("id")) != candidate["persona_id"]):
                    raise Degraded("persona_identity_or_tenant_mismatch")
                if _probe_persona(persona) or _probe_persona(candidate):
                    record.update(status="no_nomination", reason="release_or_acceptance_probe")
                    nominations[key] = record
                    state["last_promotion"] = record
                    return
                panel_memo = candidacy_panel(candidate, tenant=tenant, quarter=quarter,
                                             snapshot_id=snapshot_id, fetch=fetch)
                factual = json.dumps(summary, sort_keys=True)
                rationale = factual
                provider = "fixed_rule"
                try:
                    response = fetch(f"{adapter_url}/api/openclaw-adapter/assistant/providers/openclaw/structured",
                        data={"prompt": "Write a rationale for this already fixed nomination. Do not choose an action. FACTS=" + factual,
                              "extraction_schema": {"type": "object", "properties": {"rationale": {"type": "string", "maxLength": 1500}},
                                                    "required": ["rationale"], "additionalProperties": False}},
                        headers={"X-Operator-Id": actor, "X-Pantheon-Service-Token": adapter_token},
                        timeout=float(os.getenv("PERSONA_EVALUATOR_TIMEOUT_SECONDS", "180")))
                    text = response["data"]["output"]["structured_data"]["rationale"]
                    if isinstance(text, str) and text.strip():
                        rationale += "\n" + text.strip()[:1500]
                        provider = "openclaw_rationale"
                except Exception:
                    pass  # the agent cannot veto a rule-selected nomination
                rec = {"persona_id": candidate["persona_id"], "quarter": quarter, "ranking_snapshot_id": snapshot_id,
                       "from_stage": "paper", "to_stage": "canary_candidate", "action_id": "promote_to_canary_candidate",
                       "rule_summary": summary, "rationale": rationale, "provider": provider,
                       "panel_memo": panel_memo,
                       "evidence_ref_ids": sorted(set(candidate.get("evidence_ref_ids") or []) | {panel_memo["evidence_ref"]}),
                       "recommendation_id": f"pm12-{quarter.lower()}-{candidate['persona_id']}-promote_to_canary_candidate"}
                digest = hashlib.sha256(json.dumps(promotion_content(rec), sort_keys=True).encode()).hexdigest()
                identity = hashlib.sha256(f"paper-canary-candidacy|{tenant}|{quarter}".encode()).hexdigest()
                subject = {k: rec[k] for k in ("persona_id", "from_stage", "to_stage", "quarter", "ranking_snapshot_id")}
                subject.update(proposal_content_digest=digest, rule_summary=factual, rationale=rationale,
                               panel_memo=json.dumps(panel_memo, sort_keys=True))
                request = {"key": identity, "body": {
                    "decision_id": f"pcc-{identity[:40]}", "expected_version": 0, "target_type": PROMOTION_TARGET,
                    "target_id": rec["persona_id"], "target_version": snapshot_id, "persona_id": rec["persona_id"],
                    "tenant_id": tenant, "owner_user_id": actor, "risk_level": "low", "subject": subject,
                    "proposal_id": rec["recommendation_id"], "proposal_revision": 1, "proposal_content_digest": digest,
                    "validation_result_digest": snapshot.content_digest}}
                rec["governance_request"] = {"decision_id": request["body"]["decision_id"], "target_type": PROMOTION_TARGET,
                                             "subject": subject, "proposal_content_digest": digest}
                record.update(request=request, recommendation=rec)
                nominations[key] = {**record, "status": "pending"}
                store.save(state)  # unknown HTTP outcomes must replay the original content and identity
            else:
                record = {**existing, "at": moment.isoformat()}
            response = propose_lifecycle(request, governance_url=governance_url, token=governance_token, fetch=fetch)
            if not isinstance(response, dict) or response.get("_http_status", 201) not in (200, 201):
                response = readback_lifecycle(request["body"]["decision_id"], governance_url=governance_url,
                                             token=governance_token, fetch=fetch)
            body = request["body"]
            if not isinstance(response, dict) or any(response.get(k) != body[k] for k in (
                "decision_id", "tenant_id", "target_type", "target_id", "target_version", "proposal_id", "proposal_content_digest"
            )) or (response.get("metadata") or {}).get("subject") != body["subject"]:
                raise Degraded("promotion_governance_readback_mismatch")
            record.update(status="proposed", decision_id=body["decision_id"])
            record.pop("reason", None)
        except Exception as exc:
            record.update(status="degraded", reason=str(exc))
        nominations[key] = record
        state["last_promotion"] = record
    store.update(apply)
    return record


def saved_result(state: dict[str, Any], quarter: str, snapshot_id: str) -> Any:
    """Combine saved advice with the frozen quarter nomination for owner readback."""
    result = state["results"].get(f"{quarter}|{snapshot_id}")
    if result is None:
        return None
    result = {**result, "items": list(result.get("items") or [])}
    for record in state.get("promotions", {}).values():
        rec = record.get("recommendation")
        if rec and rec.get("quarter") == quarter:
            result["items"].append(rec)
    result["promotion_nomination"] = [
        {k: r[k] for k in ("status", "reason", "quarter", "ranking_snapshot_id", "rule_summary", "decision_id") if k in r}
        for r in state.get("promotions", {}).values() if r.get("quarter") == quarter]
    return result


def pattern_review_once(
    *, persona_url: str, token: str, tenant: str, fetch: Callable[..., Any] = _http,
) -> dict[str, Any]:
    """Ask the Persona owner for each persona's scheduled_pattern reflection.

    The owner reads closed episodes from telemetry, reviews them only when at
    least two qualify, and persists with its own CAS; this host only schedules.
    A missing credential or tenant fails closed before any call.
    """
    if not token or not tenant or not persona_url:
        return {"status": "degraded", "reason": "persona service credential or tenant missing", "reviewed": 0}
    headers = {"Authorization": f"Bearer {token}", "X-Tenant-Id": tenant}
    counts = {"reviewed": 0, "unchanged": 0, "no_op": 0, "failed": 0}
    try:
        personas = fetch(f"{persona_url}/api/personas", headers=headers)
    except Exception as exc:
        return {"status": "degraded", "reason": f"persona list unavailable: {exc}", **counts}
    for persona in (personas if isinstance(personas, list) else [])[:MAX_PATTERN_PERSONAS]:
        persona_id = str(persona.get("persona_id") or persona.get("id") or "")
        if not persona_id or persona.get("tenant_id") != tenant:
            continue
        try:
            data = fetch(
                f"{persona_url}/api/personas/{quote(persona_id, safe='')}/trade-reflections:pattern-review",
                data={"tenant_id": tenant}, headers=headers, timeout=float(os.getenv("PERSONA_EVALUATOR_TIMEOUT_SECONDS", "180")),
            )["data"]
            counts[data["status"]] += 1
        except Exception:
            counts["failed"] += 1  # same episode set is retried on the next run
    return {"status": "degraded" if counts["failed"] else "ok", **counts}


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
                code, body = 200, {"data": {"quarter": quarter, "result": saved_result(state, quarter, snapshot_id),
                                            "last_run": state["last_run"], "last_promotion": state.get("last_promotion")}}
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


def read_token(name: str) -> str:
    """Token file (re-read every run so rotation needs no restart); env only without a file path."""
    path = os.environ.get(name + "_FILE")
    if not path:
        return os.environ.get(name, "")
    try:
        token = Path(path).read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise Degraded(f"{name} file unreadable") from exc
    if not token:
        raise Degraded(f"{name} file empty")
    return token


def main() -> None:
    env = os.environ.get
    store = Store(Path(env("PERSONA_EVALUATOR_STATE_PATH", "/data/persona-evaluator-agent/state.json")))
    serve(store, env("PERSONA_EVALUATOR_READ_TOKEN", ""), int(env("PERSONA_EVALUATOR_PORT", "8105")))
    interval = float(env("PERSONA_EVALUATOR_INTERVAL_SECONDS", "900"))
    while True:
        try:
            bff_token, governance_token = read_token("PERSONA_EVALUATOR_BFF_TOKEN"), read_token("PERSONA_EVALUATOR_GOVERNANCE_TOKEN")
        except Degraded as exc:
            record = {"status": "degraded", "reason": str(exc), "created": 0, "at": datetime.now(timezone.utc).isoformat()}
            store.update(lambda s: s.update(last_run=record))
            print(json.dumps(record), flush=True)
            if env("PERSONA_EVALUATOR_ONCE"):
                return
            time.sleep(interval)
            continue
        try:
            persona_token = read_token("PERSONA_EVALUATOR_PERSONA_TOKEN")
        except Degraded:
            persona_token = ""
        record = run_once(
            store=store,
            bff_url=env("PERSONA_EVALUATOR_BFF_URL", "http://operator-bff:8001"),
            bff_headers={"Authorization": f"Bearer {bff_token}"},
            adapter_url=env("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-gateway-adapter:8104"),
            adapter_token=env("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", ""),
            governance_url=env("PANTHEON_GOVERNANCE_API_URL", "http://governance:8082"),
            governance_token=governance_token,
            tenant=env("PANTHEON_TENANT_ID", "default"),
            actor=env("PERSONA_EVALUATOR_ACTOR_ID", "persona-evaluator-agent"),
            persona_url=env("PERSONA_EVALUATOR_PERSONA_URL", "http://persona:8002").rstrip("/"),
            persona_token=persona_token,
        )
        print(json.dumps(record), flush=True)
        paper_cycle = paper_cycle_once(
            store=store, bff_url=env("PERSONA_EVALUATOR_BFF_URL", "http://operator-bff:8001"),
            bff_headers={"Authorization": f"Bearer {bff_token}"},
            persona_url=env("PERSONA_EVALUATOR_PERSONA_URL", "http://persona:8002").rstrip("/"),
            persona_token=persona_token, tenant=env("PANTHEON_TENANT_ID", "default"),
            paper_pool_id=env("PERSONA_EVALUATOR_PAPER_POOL_ID", ""),
        )
        store.update(lambda s: s.update(last_paper_cycle=paper_cycle))
        print(json.dumps({"paper_cycle": paper_cycle}), flush=True)
        promotion = promotion_once(
            store=store, tenant=env("PANTHEON_TENANT_ID", "default"),
            actor=env("PERSONA_EVALUATOR_ACTOR_ID", "persona-evaluator-agent"),
            governance_url=env("PANTHEON_GOVERNANCE_API_URL", "http://governance:8082"), governance_token=governance_token,
            persona_url=env("PERSONA_EVALUATOR_PERSONA_URL", "http://persona:8002").rstrip("/"), persona_token=persona_token,
            adapter_url=env("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "http://openclaw-gateway-adapter:8104"),
            adapter_token=env("PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN", ""),
        )
        print(json.dumps({"promotion": {k: promotion[k] for k in ("status", "reason", "quarter", "ranking_snapshot_id", "decision_id") if k in promotion}}), flush=True)
        pattern = pattern_review_once(
            persona_url=env("PERSONA_EVALUATOR_PERSONA_URL", "http://persona:8002").rstrip("/"),
            token=persona_token, tenant=env("PANTHEON_TENANT_ID", ""),
        )
        store.update(lambda s: s.update(last_pattern_review={**pattern, "at": datetime.now(timezone.utc).isoformat()}))
        print(json.dumps({"pattern_review": pattern}), flush=True)
        if env("PERSONA_EVALUATOR_ONCE"):
            return
        time.sleep(interval)


if __name__ == "__main__":
    main()
