"""Read-only D13 projections. Owner failures never become successful empty data."""
from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import HTTPException

from ..agora.committee.client import CommitteeOwnerClient
from ..agora.memory_review.client import MemoryReviewClient
from ..command_adapters.base import get_base_url, http_request_json
from ..core.owner_reads import authorization as caller_auth, remaining_management_read_budget, selected_tenant


class CycleReadUnavailable(ValueError):
    pass


def read_cycle_records(read_store, tenant_id):
    """Use the controller's existing decoder and the application's injected owner."""
    port = read_store.ooda_management.ooda
    state = port.get_surface_status()
    if state.get("source") == "missing" and any(os.getenv(k, "").strip() for k in (
        "PANTHEON_BFF_OODA_PACKET_STORE", "PANTHEON_OODA_DATA_DIR", "PANTHEON_CONTROL_PLANE_DATA_DIR",
    )):
        from services.persona.cron_ooda_closure import resolve_ooda_packet_store_path
        from services.persona.persona_cycle_controller import read_packets
        path = Path(resolve_ooda_packet_store_path())
        if not path.is_file():
            raise CycleReadUnavailable("cycle_owner_store_missing")
        records = list(read_packets(path).values())
    else:
        if state.get("status") not in {"ok", "degraded"}:
            raise CycleReadUnavailable("cycle_owner_unavailable")
        token = selected_tenant.set(tenant_id)
        try:
            records = port.list_ooda_packets()
        finally:
            selected_tenant.reset(token)
    return [p for p in records if p.get("tenant_id") == tenant_id
            and p.get("environment") == "paper" and isinstance(p.get("persona_cycle"), dict)]


def persona_cycle_health(records, persona_id):
    from services.persona.persona_cycle_controller import cycle_health
    matched = [p for p in records if persona_id in (p.get("persona_ids") or [])]
    if not matched:
        raise CycleReadUnavailable("no_daily_cycle_packet")
    latest = max(matched, key=lambda p: ((p.get("producer") or {}).get("cron_run_at_ms") or 0,
                                         p.get("updated_at") or ""))
    if (latest.get("status") not in {"open", "observing", "oriented", "decided", "acted", "evolving", "closed", "failed"}
            or latest["persona_cycle"].get("phase") not in {"claimed", "terminal"}):
        raise CycleReadUnavailable("cycle_record_invalid")
    return cycle_health(latest)


def unavailable_health(reason):
    return {"status": "unavailable", "last_packet_id": None, "stage": None,
            "failure_reason": reason}


def read_fleet_cycle_health(read_store, tenant_id, persona_ids):
    try:
        records = read_cycle_records(read_store, tenant_id)
    except Exception:
        return {pid: unavailable_health("cycle_owner_unavailable") for pid in persona_ids}
    result = {}
    for pid in persona_ids:
        try:
            result[pid] = persona_cycle_health(records, pid)
        except Exception:
            result[pid] = unavailable_health("no_valid_daily_cycle_packet")
    return result


def read_cycle_views(read_store, persona_id, tenant_id, identity, authorization, snapshot_at):
    surfaces = {}

    def section(name, source, read):
        try:
            value = read()
        except Exception as exc:
            reason = str(exc) if isinstance(exc, CycleReadUnavailable) else "owner_read_unavailable"
            # Existing owner adapters produce structured safe reasons. Never echo raw bodies.
            if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
                reason = exc.detail.get("reason") or reason
            surfaces[name] = {"status": "unavailable", "source": source, "reason": reason,
                              "snapshot_at": snapshot_at}
            return None
        surfaces[name] = {"status": "ok", "source": source, "reason": None, "snapshot_at": snapshot_at}
        return deepcopy(value)

    records = section("persona_cycles", "persona_cycle_owner", lambda: read_cycle_records(read_store, tenant_id))

    def cycles():
        if records is None:
            raise CycleReadUnavailable("cycle_owner_unavailable")
        health = persona_cycle_health(records, persona_id)
        rows = sorted([p for p in records if persona_id in (p.get("persona_ids") or [])],
                      key=lambda p: ((p.get("producer") or {}).get("cron_run_at_ms") or 0,
                                     p.get("updated_at") or ""), reverse=True)[:20]
        # Deliberate summary allowlist: no prompts, case files or staged memory.
        return {"health": health, "packets": [
            {"packet_id": p["packet_id"], "stage": p.get("status"), "updated_at": p.get("updated_at"),
             "phase": p["persona_cycle"].get("phase"),
             "failure_reason": p["persona_cycle"].get("failure_reason"),
             "harness_version": p["persona_cycle"].get("harness_version")} for p in rows]}

    cycle = section("persona_cycles", "persona_cycle_owner", cycles)
    auth = authorization or caller_auth.get() or ""
    memory_client = MemoryReviewClient(auth, tenant_id)

    def memories(*, experiences_only=False):
        query = {
            "actor_id": identity.operator_id, "actor_roles": "", "scope": "persona", "limit": 100,
            "session_id": "bff-persona-cycle:" + persona_id, "persona_id": persona_id,
        }
        if experiences_only:
            query["tags"] = "harness_experience"
        payload = memory_client.call("GET", "/api/memory/retrieve?" + urlencode(query))
        if not isinstance(payload.get("authz"), dict) or payload["authz"].get("allowed") is not True:
            raise CycleReadUnavailable("memory_retrieval_not_authorized")
        hits = payload.get("hits")
        if not isinstance(hits, list):
            raise CycleReadUnavailable("memory_response_invalid")
        entries = []
        for h in hits:
            e = h.get("entry") if isinstance(h, dict) else None
            if (not isinstance(e, dict) or h.get("type") != "persona" or not e.get("memory_id")
                    or e.get("tenant_id") != tenant_id or e.get("persona_id") != persona_id
                    or not isinstance(e.get("content"), dict)):
                raise CycleReadUnavailable("memory_scope_or_identity_mismatch")
            entries.append(e)
        if not entries:
            raise CycleReadUnavailable("no_memory_records")
        return entries

    entries = section("persona_memory_summary", "memory_owner", memories)
    summary = None if entries is None else {"returned": len(entries), "limit": 100,
        "entries": [{k: e.get(k) for k in ("memory_id", "memory_type", "written_at", "source_event_id")}
                    | {"summary": (e.get("content") or {}).get("summary")} for e in entries]}

    def experiences():
        values = [e for e in memories(experiences_only=True) if e.get("source_event_type") == "harness_experience"
                  or e.get("memory_type") == "harness_experience"]
        if not values:
            raise CycleReadUnavailable("no_harness_experiences")
        return [{"memory_id": e["memory_id"], "source_event_id": e.get("source_event_id"),
                 "written_at": e.get("written_at"), "summary": e["content"].get("summary"),
                 "experience": {k: (e["content"].get("structured_payload") or {}).get(k)
                                for k in ("cue", "lesson", "outcome", "confidence", "evidence_refs",
                                          "harness_version", "proposal_ids")}} for e in values]

    experience = section("harness_experiences", "memory_owner", experiences)

    def memos():
        owner = CommitteeOwnerClient(tenant_id, identity.operator_id)
        owner.client._timeout_seconds = remaining_management_read_budget(2.0)
        panels = owner.panels()
        # Initiator's panels only; other personas' private consultations stay private.
        own = sorted([p for p in panels if p.get("from_persona_id") == persona_id],
                     key=lambda p: p.get("created_at") or "", reverse=True)[:20]
        result = []
        for panel in own:
            owner.client._timeout_seconds = remaining_management_read_budget(2.0)
            rows = owner.call("GET", "/api/consult/memos?" + urlencode({"request_id": panel["request_id"]}))
            if not isinstance(rows, list) or any(not isinstance(m, dict) or not m.get("memo_id")
                    or m.get("request_id") != panel["request_id"] for m in rows):
                raise CycleReadUnavailable("consult_memo_scope_or_identity_mismatch")
            result.extend({k: m.get(k) for k in ("memo_id", "request_id", "status", "summary",
                          "recommendation", "created_at", "evidence_refs")} for m in rows)
        if not result:
            raise CycleReadUnavailable("no_consultation_memos")
        return result

    memo = section("persona_consultation_memos", "consultation_owner", memos)

    def harness():
        base = get_base_url("PANTHEON_PERSONA_API_URL", "PANTHEON_PERSONA_SERVICE_URL")
        value = http_request_json(base + f"/api/personas/{quote(persona_id, safe='')}/harness",
            auth_token=auth, tenant_id=tenant_id, timeout=remaining_management_read_budget(2.0))
        if (not isinstance(value, dict) or value.get("status") == "unavailable"
                or type(value.get("active_version")) is not int or not value.get("versions")):
            raise CycleReadUnavailable("harness_not_initialized_or_invalid")
        from services.persona.harness import HarnessVersion
        versions = [HarnessVersion.model_validate(v).model_dump() for v in value["versions"]]
        if any(v["persona_id"] != persona_id for v in versions) or not any(
                v["version"] == value["active_version"] for v in versions):
            raise CycleReadUnavailable("harness_version_scope_mismatch")
        return {"active_version": value["active_version"], "versions": [
            {k: v[k] for k in ("schema_version", "persona_id", "version", "parent_version", "patch_id",
                              "created_at", "created_by", "content_digest")} for v in versions]}

    harness_view = section("persona_harness", "persona_owner", harness)
    return {"cycle": cycle, "memorySummary": summary, "consultationMemos": memo,
            "harness": harness_view, "harnessExperiences": experience}, surfaces
