"""Decision case file composer (SD-MEMORY-KNOWLEDGE-INTEGRATION M3).

A library of the knowledge plane, not a service and not a store.  Given one
decision subject it returns a bounded, cited case file with four groups:

* ``facts``            structured facts read from OWNER APIs only (registry,
                       research backtest/gate verdicts, strategy pool, member
                       virtual books).  Never read from the search index.
* ``similar_history``  retired/rejected/removed strategies and negative memory.
* ``lessons``          memory retrieved with a query and tag filters built from
                       the subject, scoped to what the caller may read.
* ``evidence``         excerpts through the governed search gateway.

The caller persists the returned dict in its own decision record.  Execution
never reads it.  A missing owner is recorded as an unavailable group/source
with a reason; nothing is invented or silently skipped.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

SCHEMA_VERSION = "decision_case_file.v1"
GROUPS = ("facts", "similar_history", "lessons", "evidence")
DECISION_TYPES = frozenset({
    "trial_admission", "member_removal", "weight_change",
    "evolution_proposal", "research_demand", "consult",
})
_NEEDS_MEMBER_REFS = DECISION_TYPES - {"research_demand", "consult"}
_COMMITTEE_SCOPE = "persona_and_committee"
_PRIVATE_SCOPE = "persona_private"


class CaseFileError(ValueError):
    """Fail-closed: the subject cannot yield a valid case file."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass(frozen=True)
class Budgets:
    # Coordinator starting values (policy proposals, not operator decisions).
    max_items_per_group: int = 8
    max_excerpt_chars: int = 400
    max_total_chars: int = 12000


@dataclass(frozen=True)
class Subject:
    decision_type: str
    persona_id: str
    tenant_id: str
    strategy_family: str
    underlyings: tuple = ()
    instrument_class: str = ""
    member_artifact_refs: tuple = ()
    backtest_run_refs: tuple = ()
    persona_capital_binding_id: str = ""
    regime_tag: str = ""
    strategy_families: tuple = ()
    missing_datasets: tuple = ()


@dataclass(frozen=True)
class Caller:
    actor_id: str
    actor_roles: tuple
    session_id: str
    persona_id: str = ""  # the persona the caller IS; "" for operators/services


@dataclass(frozen=True)
class OwnerEndpoint:
    base_url: str
    token: str = ""


@dataclass(frozen=True)
class Owners:
    registry: Optional[OwnerEndpoint] = None
    research: Optional[OwnerEndpoint] = None
    pool: Optional[OwnerEndpoint] = None
    telemetry: Optional[OwnerEndpoint] = None
    memory: Optional[OwnerEndpoint] = None
    search: Optional[OwnerEndpoint] = None

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "Owners":
        env = os.environ if env is None else env

        def ep(url_key: str, token_key: str) -> Optional[OwnerEndpoint]:
            url = (env.get(url_key) or "").strip()
            return OwnerEndpoint(url.rstrip("/"), (env.get(token_key) or "").strip()) if url else None

        return cls(
            registry=ep("PANTHEON_REGISTRY_API_URL", "PANTHEON_REGISTRY_SERVICE_TOKEN"),
            research=ep("PANTHEON_RESEARCH_API_URL", "PANTHEON_RESEARCH_SERVICE_TOKEN"),
            pool=ep("PANTHEON_PERSONA_API_URL", "PANTHEON_PERSONA_SERVICE_TOKEN"),
            telemetry=ep("PANTHEON_TELEMETRY_API_URL", "PANTHEON_TELEMETRY_SERVICE_TOKEN"),
            memory=ep("PANTHEON_MEMORY_API_URL", "PANTHEON_MEMORY_SERVICE_TOKEN"),
            search=ep("PANTHEON_SEARCH_API_URL", "PANTHEON_SEARCH_SERVICE_TOKEN"),
        )


class OwnerUnavailable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def http_json(endpoint: Optional[OwnerEndpoint], path: str, *, tenant_id: str,
              method: str = "GET", body: Optional[dict] = None,
              params: Optional[Sequence[tuple]] = None, timeout: float = 10.0) -> Any:
    """One fixed-owner JSON call.  URLs are never caller- or model-selected."""
    if endpoint is None or not endpoint.base_url:
        raise OwnerUnavailable("owner_unconfigured")
    url = endpoint.base_url + path
    if params:
        url += "?" + urllib.parse.urlencode(list(params))
    headers = {"X-Tenant-Id": tenant_id, "Accept": "application/json"}
    if endpoint.token:
        headers["Authorization"] = f"Bearer {endpoint.token}"
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
            d = payload.get("detail", payload) if isinstance(payload, dict) else payload
            detail = d.get("error") or d.get("reason") or "" if isinstance(d, dict) else str(d)
        except Exception:
            pass
        raise OwnerUnavailable(f"http_{exc.code}" + (f":{str(detail)[:80]}" if detail else "")) from exc
    except (OSError, ValueError) as exc:
        raise OwnerUnavailable(f"owner_unreachable:{type(exc).__name__}") from exc


def _clip(text: Any, limit: int) -> str:
    s = text if isinstance(text, str) else json.dumps(text, sort_keys=True, default=str, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: max(0, limit - 1)] + "…"


def _item(group: str, kind: str, owner: str, source_id: str, excerpt: Any, *,
          score: Optional[float], mode: str, budgets: Budgets,
          subject_fields: Optional[dict] = None, **extra: Any) -> dict:
    return {
        "item_id": f"{group}:{kind}:{source_id}",
        "group": group, "kind": kind, "source_owner": owner, "source_id": str(source_id),
        "excerpt": _clip(excerpt, budgets.max_excerpt_chars),
        "retrieval_score": score, "retrieval_mode": mode,
        "subject_fields": subject_fields or {}, **extra,
    }


class _Group:
    def __init__(self, name: str):
        self.name, self.items, self.unavailable = name, [], []

    def missing(self, owner: str, reason: str) -> None:
        self.unavailable.append({"owner": owner, "reason": reason})

    def render(self, budgets: Budgets) -> dict:
        items = self.items[: budgets.max_items_per_group]
        truncated = len(self.items) > len(items)
        if self.unavailable and not items:
            status = "unavailable"
        elif self.unavailable:
            status = "partial"
        else:
            status = "ok" if items else "empty"
        out = {"status": status, "items": items, "truncated": truncated,
               "unavailable": list(self.unavailable)}
        if status == "unavailable":
            out["reason"] = "; ".join(f"{u['owner']}:{u['reason']}" for u in self.unavailable)
        return out


def _validate(subject: Subject, caller: Caller) -> None:
    if subject.decision_type not in DECISION_TYPES:
        raise CaseFileError("unknown_decision_type", subject.decision_type)
    for name in ("persona_id", "tenant_id", "strategy_family"):
        if not str(getattr(subject, name) or "").strip():
            raise CaseFileError("subject_field_missing", name)
    if subject.decision_type in _NEEDS_MEMBER_REFS and not subject.member_artifact_refs:
        raise CaseFileError("subject_field_missing", "member_artifact_refs")
    if not (caller.actor_id and caller.session_id and caller.actor_roles):
        raise CaseFileError("caller_context_missing", "actor_id/actor_roles/session_id")


def lesson_query(subject: Subject) -> tuple:
    """Query text and tag filters built from the decision content (no fixed query)."""
    families = tuple(dict.fromkeys((subject.strategy_family, *subject.strategy_families)))
    terms = [*families, *subject.underlyings, *subject.missing_datasets, subject.instrument_class,
             subject.decision_type.replace("_", " "), subject.regime_tag]
    tags = [*(f"family:{f}" for f in families), f"decision:{subject.decision_type}"]
    tags += [f"dataset:{d}" for d in subject.missing_datasets]
    tags += [f"underlying:{u}" for u in subject.underlyings]
    if subject.instrument_class:
        tags.append(f"instrument:{subject.instrument_class}")
    if subject.regime_tag:
        tags.append(f"regime:{subject.regime_tag}")
    return " ".join(t for t in terms if t), tags


def subject_anchors(subject: Subject) -> list:
    """Subject anchors for association-first memory recall (H8).

    Built only from fields the subject carries.  The decision type is a
    category, not a subject, so it is never an anchor; ``regime`` is added only
    when the subject carries ``regime_tag`` (nothing invents a regime).
    """
    families = dict.fromkeys((subject.strategy_family, *subject.strategy_families))
    pairs = [("family", f) for f in families]
    pairs += [("underlying", u) for u in subject.underlyings]
    pairs += [("dataset", d) for d in subject.missing_datasets]
    pairs.append(("instrument", subject.instrument_class))
    pairs.append(("regime", subject.regime_tag))
    out: list = []
    for kind, value in pairs:
        text = str(value or "").strip().lower()
        if text and f"{kind}:{text}" not in out:
            out.append(f"{kind}:{text}")
    return out


# ---- facts (owner APIs only) -------------------------------------------------

def _facts(subject: Subject, owners: Owners, budgets: Budgets, g: _Group) -> None:
    tenant = subject.tenant_id
    q = urllib.parse.quote
    for ref in subject.member_artifact_refs:
        try:
            e = http_json(owners.registry, f"/api/registry/entries/{q(ref, safe='')}", tenant_id=tenant)
            e = e.get("entry", e) if isinstance(e, dict) else {}
            g.items.append(_item("facts", "registry_entry", "registry", ref, {
                "artifact_type": e.get("artifact_type"), "strategy_id": e.get("strategy_id"),
                "version": e.get("version"), "artifact_state": e.get("artifact_state"),
                "evaluation_summary": e.get("evaluation_summary"),
                "replication_success": e.get("replication_success")},
                score=None, mode="owner_read", budgets=budgets,
                subject_fields={"strategy_id": e.get("strategy_id")}))
        except OwnerUnavailable as exc:
            g.missing(f"registry:{ref}", exc.reason)
    for run in subject.backtest_run_refs:
        try:
            r = http_json(owners.research, f"/api/research-orchestrator/backtest-runs/{q(run, safe='')}/owner-binding",
                          tenant_id=tenant)
            g.items.append(_item("facts", "backtest_gate_verdict", "research", run, {
                "status": r.get("status"), "gate_verdict": r.get("gate_verdict"),
                "evaluation_digest": r.get("evaluation_digest"), "dataset_id": r.get("dataset_id")},
                score=None, mode="owner_read", budgets=budgets))
        except OwnerUnavailable as exc:
            g.missing(f"research:{run}", exc.reason)
    try:
        pool = http_json(owners.pool, f"/api/personas/{q(subject.persona_id, safe='')}/strategy-pool", tenant_id=tenant)
        g.items.append(_item("facts", "strategy_pool", "persona",
                             f"{subject.persona_id}@{(pool or {}).get('pool_version', 'current')}",
                             pool, score=None, mode="owner_read", budgets=budgets))
    except OwnerUnavailable as exc:
        g.missing("persona_pool", exc.reason)
    if subject.persona_capital_binding_id:
        try:
            payload = http_json(owners.telemetry, "/api/telemetry/runtime-summaries", tenant_id=tenant)
            rows = [r for r in (payload or {}).get("summaries", [])
                    if r.get("persona_capital_binding_id") == subject.persona_capital_binding_id
                    and r.get("tenant_id") == tenant]
            if len(rows) != 1:
                g.missing("telemetry", "own_paper_telemetry_missing_or_ambiguous")
            else:
                wanted = set(subject.member_artifact_refs)
                for b in rows[0].get("member_virtual_books") or []:
                    mid = b.get("member_id") or b.get("artifact_id")
                    if wanted and mid not in wanted and b.get("artifact_id") not in wanted:
                        continue
                    g.items.append(_item("facts", "member_virtual_book", "telemetry", mid, b,
                                         score=None, mode="owner_read", budgets=budgets))
        except OwnerUnavailable as exc:
            g.missing("telemetry", exc.reason)
    else:
        g.missing("telemetry", "persona_capital_binding_id_not_supplied")


# ---- memory ------------------------------------------------------------------

def _memory_hits(owners: Owners, subject: Subject, caller: Caller, *, scope: str, query: str,
                 tags: Sequence[str], limit: int, roles: Optional[Sequence[str]] = None,
                 memory_type: str = "") -> dict:
    params = [("actor_id", caller.actor_id), ("session_id", caller.session_id), ("scope", scope),
              ("query", query), ("limit", str(limit))]
    params += [("actor_roles", r) for r in (roles or caller.actor_roles)]
    if scope != "institutional":
        params.append(("persona_id", subject.persona_id))
        params.append(("session_persona_id", subject.persona_id))
    params += [("tags", t) for t in tags]
    params += [("subject_anchors", a) for a in subject_anchors(subject)]
    if memory_type:
        params.append(("memory_type", memory_type))
    return http_json(owners.memory, "/api/memory/retrieve", tenant_id=subject.tenant_id, params=params) or {}


def _memory_item(group: str, hit: dict, budgets: Budgets, layer: str, response: dict) -> dict:
    entry = hit.get("entry") or {}
    sid = entry.get("memory_id") or entry.get("entry_id") or ""
    content = entry.get("content") or {}
    text = (content.get("summary") or content.get("headline") or content.get("body") or content)
    # The route does not yet report retrieval mode on every deployment; say so
    # instead of guessing (MEMORY-INDEX-PROJECTION adds it).
    mode = str(hit.get("retrieval_mode") or response.get("retrieval_mode") or "unreported")
    return _item(group, f"memory_{layer}", "memory", sid, text,
                 score=hit.get("relevance_score"), mode=mode, budgets=budgets,
                 subject_fields={"tags": list(content.get("tags") or []),
                                 "memory_type": entry.get("memory_type") or entry.get("knowledge_type")},
                 memory_layer=layer, relevance_scope=entry.get("relevance_scope"),
                 source_event_id=entry.get("source_event_id"))


def _lessons(subject: Subject, caller: Caller, owners: Owners, budgets: Budgets, g: _Group) -> None:
    query, tags = lesson_query(subject)
    limit = budgets.max_items_per_group
    own = bool(caller.persona_id) and caller.persona_id == subject.persona_id
    seen: set = set()

    def add(hits: Iterable[dict], layer: str, response: dict, *, allow_private: bool) -> None:
        for hit in hits:
            entry = hit.get("entry") or {}
            if hit.get("type") == "persona":
                if entry.get("persona_id") != subject.persona_id:
                    continue  # never another persona's memory
                if entry.get("relevance_scope") != _COMMITTEE_SCOPE and not allow_private:
                    continue  # private items only for the persona itself
            it = _memory_item("lessons", hit, budgets, layer, response)
            if it["item_id"] not in seen:
                seen.add(it["item_id"])
                g.items.append(it)

    # Always send the caller's own verified roles (never invent one: the route
    # rejects roles beyond the verified token).  Committee visibility is
    # enforced here: non-persona callers only keep persona_and_committee items.
    plans = [("institutional", caller.actor_roles), ("persona", caller.actor_roles)]
    for scope, roles in plans:
        try:
            resp = _memory_hits(owners, subject, caller, scope=scope, query=query, tags=tags,
                                limit=limit, roles=roles)
            if scope == "institutional":
                add(resp.get("hits", []), "institutional", resp, allow_private=False)
            else:
                for hit in resp.get("hits", []):
                    shared = (hit.get("entry") or {}).get("relevance_scope") == _COMMITTEE_SCOPE
                    add([hit], "committee_shared" if shared else "persona_private", resp,
                        allow_private=own)
        except OwnerUnavailable as exc:
            g.missing(f"memory:{'persona' if scope == 'persona' else scope}", exc.reason)
    if not own:
        g.unavailable.append({"owner": "memory:persona_private", "reason": "caller_is_not_the_persona",
                              "expected": True})
    g.items.sort(key=lambda i: -(i["retrieval_score"] or 0.0))


# ---- similar history ---------------------------------------------------------

def _history(subject: Subject, caller: Caller, owners: Owners, budgets: Budgets, g: _Group) -> None:
    tenant, q = subject.tenant_id, urllib.parse.quote
    for family in dict.fromkeys((subject.strategy_family, *subject.strategy_families)):
        try:
            entries = http_json(owners.registry, f"/api/registry/strategies/{q(family, safe='')}/entries",
                                tenant_id=tenant) or []
            for e in entries:
                e = e.get("entry", e)
                if str(e.get("artifact_state")) != "retired" or e.get("registry_id") in subject.member_artifact_refs:
                    continue
                g.items.append(_item("similar_history", "retired_strategy", "registry", e.get("registry_id"), {
                    "strategy_id": e.get("strategy_id"), "version": e.get("version"),
                    "evaluation_summary": e.get("evaluation_summary")},
                    score=None, mode="owner_read", budgets=budgets,
                    subject_fields={"strategy_id": e.get("strategy_id")}))
        except OwnerUnavailable as exc:
            g.missing("registry:strategy_family", exc.reason)
    try:
        versions = http_json(owners.pool, f"/api/personas/{q(subject.persona_id, safe='')}/strategy-pool/versions",
                             tenant_id=tenant) or {}
        for i, ref in enumerate(versions.get("refusals") or []):
            g.items.append(_item("similar_history", "pool_refusal", "persona",
                                 (ref or {}).get("refusal_id") or f"{subject.persona_id}#refusal{i}", ref,
                                 score=None, mode="owner_read", budgets=budgets))
        removed = 0
        for ver in versions.get("versions") or []:
            for m in (ver or {}).get("members") or []:
                if str((m or {}).get("stage")) != "removed" or removed >= budgets.max_items_per_group:
                    continue
                removed += 1
                ref = m.get("artifact_ref") or m.get("artifact_id") or m.get("member_id") or f"removed{removed}"
                g.items.append(_item("similar_history", "removed_member", "persona",
                                     f"{subject.persona_id}@v{(ver or {}).get('pool_version', '?')}:{ref}", {
                                         "artifact_ref": ref, "removal_reason": m.get("removal_reason"),
                                         "pool_version": (ver or {}).get("pool_version"),
                                         "risk_weight": m.get("risk_weight")},
                                     score=None, mode="owner_read", budgets=budgets))
    except OwnerUnavailable as exc:
        g.missing("persona_pool_versions", exc.reason)
    query, tags = lesson_query(subject)
    try:  # negative memory: institutional entries tagged negative_memory
        resp = _memory_hits(owners, subject, caller, scope="institutional", query=query,
                            tags=[*tags, "negative_memory"], limit=budgets.max_items_per_group)
        subject_tags = {t.lower() for t in tags if t.startswith(("family:", "underlying:", "dataset:"))}
        for hit in resp.get("hits", []):
            if hit.get("type") == "persona":
                continue  # negative history only consumes the institutional read
            etags = {str(t).strip().lower() for t in ((hit.get("entry") or {}).get("content") or {}).get("tags") or []}
            # require an actual negative marker AND subject relevance (not just the OR-matched marker)
            if "negative_memory" not in etags or not (etags & subject_tags):
                continue
            g.items.append(_memory_item("similar_history", hit, budgets, "negative_memory", resp))
    except OwnerUnavailable as exc:
        g.missing("memory:negative_memory", exc.reason)


# ---- evidence ----------------------------------------------------------------

def _evidence(subject: Subject, caller: Caller, owners: Owners, budgets: Budgets, g: _Group) -> None:
    query, _tags = lesson_query(subject)
    body = {"query": query, "retrieval_mode": "hybrid", "actor_ref": caller.actor_id,
            "role_refs": list(caller.actor_roles), "purpose": "decision_case_file",
            "persona_id": subject.persona_id, "workspace_id": subject.tenant_id,
            "request_id": f"case-file-{caller.session_id}", "trace_id": f"case-file-{caller.session_id}",
            "top_k": budgets.max_items_per_group, "require_citations": True,
            "source_types": ["internal_note", "research_finding", "source_document"]}
    try:
        resp = http_json(owners.search, "/api/search/v2/query", tenant_id=subject.tenant_id,
                         method="POST", body=body) or {}
    except OwnerUnavailable as exc:
        g.missing("search_gateway", exc.reason)
        return
    mode = str(resp.get("retrieval_mode") or "unreported")
    for res in resp.get("results") or []:
        for m in res.get("matched_items") or []:
            sid = m.get("object_id") or m.get("evidence_id") or m.get("id") or res.get("result_id")
            g.items.append(_item("evidence", "search_excerpt", "search", sid,
                                 m.get("excerpt") or m.get("text") or m.get("summary") or res.get("answer_context"),
                                 score=res.get("relevance_score"), mode=mode, budgets=budgets,
                                 citations=list(res.get("citations") or []),
                                 evidence_bundle_id=res.get("evidence_bundle_id")))


# ---- composer ----------------------------------------------------------------

def _digest(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def compose_case_file(subject: Subject, caller: Caller, owners: Owners, *,
                      budgets: Budgets = Budgets(),
                      now: Optional[Callable[[], datetime]] = None) -> dict:
    """Compose a schema-valid case file; raises CaseFileError when the subject is unusable."""
    _validate(subject, caller)
    groups = {n: _Group(n) for n in GROUPS}
    _facts(subject, owners, budgets, groups["facts"])
    _history(subject, caller, owners, budgets, groups["similar_history"])
    _lessons(subject, caller, owners, budgets, groups["lessons"])
    _evidence(subject, caller, owners, budgets, groups["evidence"])
    rendered = {n: g.render(budgets) for n, g in groups.items()}
    # total character budget: trim tail items from the largest group until it fits
    def size() -> int:
        return sum(len(i["excerpt"]) for g in rendered.values() for i in g["items"])
    while size() > budgets.max_total_chars:
        biggest = max((g for g in rendered.values() if g["items"]), key=lambda g: len(g["items"]))
        biggest["items"].pop()
        biggest["truncated"] = True
    for g in rendered.values():  # re-derive status after trimming
        if not g["items"] and g["status"] in {"ok", "partial"}:
            g["status"] = "unavailable" if g["unavailable"] else "empty"
    query, tags = lesson_query(subject)
    body = {
        "schema_version": SCHEMA_VERSION,
        "composed_at": (now or (lambda: datetime.now(timezone.utc)))().replace(microsecond=0).isoformat(),
        "subject": {"decision_type": subject.decision_type, "persona_id": subject.persona_id,
                    "tenant_id": subject.tenant_id, "strategy_family": subject.strategy_family,
                    "underlyings": list(subject.underlyings), "instrument_class": subject.instrument_class,
                    "member_artifact_refs": list(subject.member_artifact_refs),
                    "backtest_run_refs": list(subject.backtest_run_refs),
                    "regime_tag": subject.regime_tag},
        "caller": {"actor_id": caller.actor_id, "is_subject_persona": caller.persona_id == subject.persona_id},
        "lesson_query": {"query": query, "tags": tags},
        "budgets": {"max_items_per_group": budgets.max_items_per_group,
                    "max_excerpt_chars": budgets.max_excerpt_chars,
                    "max_total_chars": budgets.max_total_chars},
        "groups": rendered,
        "execution_may_read": False,
    }
    if subject.strategy_families:
        body["subject"]["strategy_families"] = list(subject.strategy_families)
    if subject.missing_datasets:
        body["subject"]["missing_datasets"] = list(subject.missing_datasets)
    digest = _digest(body)
    return {"case_file_id": f"casefile-{digest[:24]}", "digest": digest, **body}
