"""Export real owner source events as canonical memory entries (read-only on hosted).

* Hosted is only ever read: the client below can only issue GET.
* Each source event is converted by the PRODUCTION writer
  (services.memory.learn_feedback_writeback.write_learn_feedback) into a
  temporary local store; nothing is written to hosted.
* Events that cannot be mapped are listed with a reason, never fabricated.
* The manifest records endpoints (no credentials), counts, time, git sha, sha256.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

MANIFEST_NAME = "manifest.json"
ENTRIES_NAME = "memory_entries.jsonl"
DECISIONS_NAME = "decisions.jsonl"
UNMAPPED_NAME = "unmapped.json"
REQUIRED_FILES = (ENTRIES_NAME, DECISIONS_NAME, UNMAPPED_NAME)
DECISION_TYPES = ("trial_admission", "member_removal", "weight_change",
                  "research_demand", "evolution_proposal")


class Unmappable(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class ReadOnlyClient:
    """GET-only JSON client.  Token comes from the environment, never from output."""

    def __init__(self, base_url: str, token: str = "", tenant_id: str = "", timeout: float = 20.0):
        self.base_url, self.token, self.tenant_id, self.timeout = base_url.rstrip("/"), token, tenant_id, timeout

    def get(self, path: str, params: Optional[Mapping[str, Any]] = None) -> Any:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        headers = {"Accept": "application/json"}
        if self.tenant_id:
            headers["X-Tenant-Id"] = self.tenant_id
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")

    def describe(self, path: str) -> str:
        return self.base_url + path


# ---- mappers: owner record -> learn-feedback payload (production writer input) ----

def _first(d: Mapping, *keys: str) -> Any:
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return None


def _anchors(rec: Mapping) -> Dict[str, Any]:
    subj = rec.get("subject") if isinstance(rec.get("subject"), Mapping) else {}
    src = {**rec, **subj}
    families = _first(src, "strategy_families", "families", "tried_families") or []
    family = _first(src, "strategy_family", "family") or (families[0] if families else None)
    return {
        "strategy_family": family,
        "strategy_families": list(families) if families else ([family] if family else []),
        "underlyings": list(_first(src, "underlyings") or []),
        "instrument_class": _first(src, "instrument_class") or "",
        "regime_tag": _first(src, "regime_tag", "regime") or "",
        "missing_datasets": list(_first(src, "missing_datasets", "dataset_refs") or []),
        "strategy_id": _first(src, "strategy_id", "parent_strategy_id"),
    }


def _persona_of(rec: Mapping, ctx: Mapping) -> str:
    pid = _first(rec, "persona_id", "sponsor_persona_id") or ctx.get("persona_id")
    if not pid:
        raise Unmappable("persona_id_absent")
    return str(pid)


def _common(rec: Mapping, ctx: Mapping, *, event_type: str, authority: str, id_keys, summary_keys) -> Dict[str, Any]:
    eid = _first(rec, *id_keys)
    if not eid:
        raise Unmappable("source_event_id_absent")
    summary = _first(rec, *summary_keys)
    if not isinstance(summary, str):
        raise Unmappable("summary_text_absent")
    pid = _persona_of(rec, ctx)
    evidence = _first(rec, "evidence_refs") or [{"type": "owner_record", "ref": str(eid)}]
    a = _anchors(rec)
    return {
        "source_event_type": event_type, "source_event_id": str(eid), "write_authority": authority,
        "sponsor_persona_id": pid, "contributing_persona_ids": [pid], "summary": summary, "headline": summary[:120],
        "evidence_refs": evidence, "tenant_id": ctx.get("tenant_id"),
        "written_at": _first(rec, "closed_at", "decided_at", "created_at", "published_at", "updated_at"),
        "scope": "strategy_family" if a["strategy_family"] else "system_wide",
        "scope_filter": a["strategy_family"],
        "_anchors": a,
    }


def map_postmortem(rec, ctx):
    # Real Postmortem rows (services/incident/incident.py) carry no persona or strategy
    # family; such rows are listed as unmapped rather than guessed.
    return _common(rec, ctx, event_type="postmortem_published", authority="incident-svc",
                   id_keys=("postmortem_id",), summary_keys=("root_cause", "title"))


APPROVED_STATES = ("approved", "executed")


def map_evolution(rec, ctx):
    # Production DecisionResponse.decision_state; never default or manufacture an approval.
    state = str(rec.get("decision_state") or "").lower()
    if not state:
        raise Unmappable("decision_state_absent")
    if state not in APPROVED_STATES:
        raise Unmappable(f"evolution_decision_not_approved:{state}")
    rec = {**rec, **(rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {})}
    return _common(rec, ctx, event_type="evolution_decision_approved", authority="evolution-svc",
                   id_keys=("decision_id",), summary_keys=("rationale",))


def map_consultation_memo(rec, ctx):
    # Only published persona-authored memos have an owner-attributed persona.
    if str(rec.get("status") or "").lower() != "published":
        raise Unmappable("consultation_memo_not_published")
    if str(rec.get("author_type") or "").lower() != "persona":
        raise Unmappable("consultation_memo_author_not_persona")
    rec = {**rec, "persona_id": rec.get("author_ref"), "closed_at": rec.get("published_at")}
    return _common(rec, ctx, event_type="consultation_closed", authority="consultation-svc",
                   id_keys=("memo_id",), summary_keys=("summary",))


MAPPERS: Dict[str, Callable] = {
    "postmortems": map_postmortem, "consultation_memos": map_consultation_memo,
    "evolution_proposals": map_evolution,
}
# Sources with no owner GET route: listed in unmapped, never fetched.
NO_OWNER_READ_ROUTE = {"research_findings": "no_owner_read_endpoint:research service has no findings route"}


def _rows(payload: Any) -> List[Mapping]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, Mapping)]
    if isinstance(payload, Mapping):
        for k in ("data", "items", "demands", "versions"):
            if isinstance(payload.get(k), list):
                return [r for r in payload[k] if isinstance(r, Mapping)]
    return []


# source name -> (client key, path, params)
SOURCES = {
    "postmortems": ("incident", "/api/postmortems", {"page_size": 200}),
    "evolution_proposals": ("evolution", "/api/evolution/proposals", {}),
    "consultation_memos": ("consultation", "/api/consult/memos", {}),
}


def _decisions_from_pool_versions(versions: List[Mapping], ctx: Mapping, family_of: Mapping[str, Mapping],
                                  unmapped: List[Dict]) -> List[Dict[str, Any]]:
    """Diff consecutive owner pool versions (members + singular decision) into decision subjects."""
    out: List[Dict[str, Any]] = []
    prev: Dict[str, Mapping] = {}
    for ver in sorted(versions, key=lambda v: v.get("pool_version") or 0):
        pid, pv = ver.get("persona_id") or ctx.get("persona_id"), ver.get("pool_version")
        cur = {m.get("artifact_ref"): m for m in ver.get("members") or [] if isinstance(m, Mapping)}
        for ref, m in cur.items():
            old = prev.get(ref)
            if old is None:
                dtype = "trial_admission"
            elif m.get("removed_at") and not old.get("removed_at"):
                dtype = "member_removal"
            elif m.get("risk_weight") != old.get("risk_weight"):
                dtype = "weight_change"
            else:
                continue
            demand = family_of.get(str(m.get("demand_ref") or ""), {})
            rec = {**demand, "member": m, "pool_decision": ver.get("decision")}
            try:
                out.append(_decision(rec, ctx, dtype, f"pool:{pid}:{pv}:{ref}", pid))
            except Unmappable as exc:
                unmapped.append({"source": f"decisions:strategy_pool:{pid}", "source_event_id": f"{pv}:{ref}",
                                 "reason": exc.reason})
        prev = cur
    return out


def _decision(rec: Mapping, ctx: Mapping, dtype: Optional[str], did: str, persona_id) -> Dict[str, Any]:
    if dtype not in DECISION_TYPES:
        raise Unmappable("decision_type_unrecognised")
    a = _anchors({**rec, **(rec.get("metadata") if isinstance(rec.get("metadata"), Mapping) else {})})
    if not a["strategy_family"]:
        raise Unmappable("strategy_family_absent")
    if not persona_id:
        raise Unmappable("persona_id_absent")
    return {"decision_id": did, "decision_type": dtype, "persona_id": str(persona_id),
            "tenant_id": ctx.get("tenant_id"), **{k: a[k] for k in (
                "strategy_family", "strategy_families", "underlyings", "instrument_class",
                "regime_tag", "missing_datasets")}, "structured": dict(rec)}


# ---- conversion through the production writer ----

class _NullBackend:
    """Stops the stores' search projection from touching any service during export."""

    def project(self, entry):  # pragma: no cover - trivial
        return {"projected": 1}


def convert_with_production_writer(payloads: Iterable[Mapping], workdir: Path):
    from services.memory import search_retrieval
    from services.memory.institutional_memory_store import InstitutionalMemoryStore
    from services.memory.persona_memory_store import PersonaMemoryStore
    from services.memory.learn_feedback_writeback import write_learn_feedback

    prior = search_retrieval._GLOBAL_SEARCH_BACKEND
    search_retrieval.set_search_retrieval_backend(_NullBackend())
    try:
        return _convert(payloads, workdir, PersonaMemoryStore, InstitutionalMemoryStore, write_learn_feedback)
    finally:
        search_retrieval.set_search_retrieval_backend(prior)


def _convert(payloads, workdir, PersonaMemoryStore, InstitutionalMemoryStore, write_learn_feedback):
    pstore = PersonaMemoryStore(workdir / "persona_store.json")
    istore = InstitutionalMemoryStore(workdir / "institutional_store.json")
    anchors: Dict[str, Dict] = {}
    unmapped: List[Dict] = []
    for p in payloads:
        p = dict(p)
        a = p.pop("_anchors", {})
        p["tags"] = _anchor_tags(a)
        if not p.get("written_at"):
            p.pop("written_at", None)
        try:
            res = write_learn_feedback(p, persona_store=pstore, institutional_store=istore)
        except Exception as exc:  # production validation refused: record why
            unmapped.append({"source_event_id": p.get("source_event_id"),
                             "reason": f"writer_refused:{type(exc).__name__}:{str(exc)[:120]}"})
            continue
        for e in (*pstore.list(active_only=False), *istore.list(active_only=False)):
            if e.source_event_id == p["source_event_id"]:
                anchors[getattr(e, "memory_id", None) or e.entry_id] = a
    entries = []
    for e in pstore.list(active_only=False):
        entries.append({"layer": "persona", "anchors": anchors.get(e.memory_id, {}), "entry": e.to_dict()})
    for e in istore.list(active_only=False):
        entries.append({"layer": "institutional", "anchors": anchors.get(e.entry_id, {}), "entry": e.to_dict()})
    return entries, unmapped


def _anchor_tags(a: Mapping) -> List[str]:
    tags = [f"family:{f}" for f in a.get("strategy_families", [])]
    tags += [f"underlying:{u}" for u in a.get("underlyings", [])]
    tags += [f"dataset:{d}" for d in a.get("missing_datasets", [])]
    if a.get("instrument_class"):
        tags.append(f"instrument:{a['instrument_class']}")
    if a.get("regime_tag"):
        tags.append(f"regime:{a['regime_tag']}")
    if a.get("strategy_id"):
        tags.append(f"strategy:{a['strategy_id']}")
    return tags


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True,
                                       cwd=Path(__file__).resolve().parent).strip()
    except Exception:
        return "unknown"


def export(clients: Mapping[str, ReadOnlyClient], out_dir: Path, *, persona_ids: Iterable[str] = (),
           tenant_id: str = "", now: Optional[Callable[[], datetime]] = None) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ctx = {"tenant_id": tenant_id or None}
    payloads, unmapped, endpoints, decisions = [], [], [], []
    counts = {}
    for name, reason in NO_OWNER_READ_ROUTE.items():
        unmapped.append({"source": name, "reason": reason})
    for name, (ckey, path, params) in SOURCES.items():
        client = clients.get(ckey)
        if client is None:
            unmapped.append({"source": name, "reason": "owner_endpoint_unconfigured"})
            continue
        endpoints.append(client.describe(path))
        try:
            rows = _rows(client.get(path, params))
        except Exception as exc:
            unmapped.append({"source": name, "reason": f"owner_unreachable:{type(exc).__name__}"})
            continue
        counts[name] = len(rows)
        for rec in rows:
            try:
                payloads.append(MAPPERS[name](rec, ctx))
            except Unmappable as exc:
                unmapped.append({"source": name, "source_event_id": str(_first(rec, "postmortem_id", "decision_id", "memo_id")),
                                 "reason": exc.reason})
        if name == "evolution_proposals":
            for rec in rows:
                try:
                    decisions.append(_decision(rec, ctx, "evolution_proposal",
                                               f"evolution:{_first(rec, 'decision_id')}",
                                               _first(rec, "persona_id", "sponsor_persona_id")))
                except Unmappable as exc:
                    unmapped.append({"source": "decisions:evolution", "reason": exc.reason})
    research = clients.get("research")
    family_of: Dict[str, Mapping] = {}
    if research is not None:
        path = "/api/research/persona-demands"
        endpoints.append(research.describe(path))
        try:
            rows = _rows(research.get(path))
            counts["research_demands"] = len(rows)
            for rec in rows:
                did = str(_first(rec, "demand_id") or "")
                family_of[did] = rec
                try:
                    decisions.append(_decision(rec, ctx, "research_demand", f"demand:{did}", rec.get("persona_id")))
                except Unmappable as exc:
                    unmapped.append({"source": "decisions:research_demand", "source_event_id": did, "reason": exc.reason})
        except Exception as exc:
            unmapped.append({"source": "research_demand", "reason": f"owner_unreachable:{type(exc).__name__}"})
    else:
        unmapped.append({"source": "research_demand", "reason": "owner_endpoint_unconfigured"})
    pool = clients.get("persona")
    for pid in persona_ids:
        if pool is None:
            unmapped.append({"source": "strategy_pool", "reason": "owner_endpoint_unconfigured"})
            break
        path = f"/api/personas/{urllib.parse.quote(pid, safe='')}/strategy-pool/versions"
        endpoints.append(pool.describe(path))
        try:
            rows = _rows(pool.get(path))
        except Exception as exc:
            unmapped.append({"source": f"strategy_pool:{pid}", "reason": f"owner_unreachable:{type(exc).__name__}"})
            continue
        counts[f"strategy_pool:{pid}"] = len(rows)
        decisions.extend(_decisions_from_pool_versions(rows, {**ctx, "persona_id": pid}, family_of, unmapped))

    with tempfile.TemporaryDirectory() as tmp:
        entries, refused = convert_with_production_writer(payloads, Path(tmp))
    unmapped += refused
    (out_dir / ENTRIES_NAME).write_text("".join(json.dumps(e, sort_keys=True, ensure_ascii=False) + "\n" for e in entries))
    (out_dir / DECISIONS_NAME).write_text("".join(json.dumps(d, sort_keys=True, ensure_ascii=False) + "\n" for d in decisions))
    (out_dir / UNMAPPED_NAME).write_text(json.dumps(unmapped, indent=2, ensure_ascii=False))
    manifest = {
        "schema": "hindsight_comparison_export.v1",
        "exported_at": (now or (lambda: datetime.now(timezone.utc)))().isoformat(),
        "git_sha": _git_sha(), "source_endpoints": sorted(set(endpoints)),
        "source_counts": counts,
        "counts": {"memory_entries": len(entries), "decisions": len(decisions), "unmapped": len(unmapped)},
        "sha256": {n: _sha256(out_dir / n) for n in REQUIRED_FILES},
        "hosted_written": False,
    }
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2))
    return manifest


def validate_manifest(data_dir: Path) -> Dict[str, Any]:
    """Raise ValueError unless the dataset carries a valid, matching exporter manifest."""
    mp = Path(data_dir) / MANIFEST_NAME
    if not mp.is_file():
        raise ValueError("manifest_missing")
    m = json.loads(mp.read_text())
    if m.get("schema") != "hindsight_comparison_export.v1" or m.get("hosted_written") is not False:
        raise ValueError("manifest_invalid")
    for k in ("exported_at", "git_sha", "source_endpoints", "counts", "sha256"):
        if k not in m:
            raise ValueError(f"manifest_field_missing:{k}")
    if not isinstance(m["exported_at"], str) or not isinstance(m["git_sha"], str) \
            or not isinstance(m["source_endpoints"], list) or not isinstance(m["counts"], dict) \
            or not isinstance(m["sha256"], dict):
        raise ValueError("manifest_field_type_invalid")
    if set(m["sha256"]) != set(REQUIRED_FILES):
        raise ValueError("manifest_sha_set_mismatch")
    for name, digest in m["sha256"].items():
        p = Path(data_dir) / name
        if not isinstance(digest, str) or not p.is_file() or _sha256(p) != digest:
            raise ValueError(f"manifest_sha_mismatch:{name}")
    line_counts = {"memory_entries": ENTRIES_NAME, "decisions": DECISIONS_NAME}
    for key, fname in line_counts.items():
        n = sum(1 for l in (Path(data_dir) / fname).read_text().splitlines() if l.strip())
        if m["counts"].get(key) != n:
            raise ValueError(f"manifest_count_mismatch:{key}")
    if m["counts"].get("unmapped") != len(json.loads((Path(data_dir) / UNMAPPED_NAME).read_text())):
        raise ValueError("manifest_count_mismatch:unmapped")
    return m


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tenant-id", default=os.getenv("PANTHEON_TENANT_ID", ""))
    ap.add_argument("--persona-id", action="append", default=[])
    args = ap.parse_args(argv)
    env = os.environ
    def mk(url_key, tok_key):
        u = env.get(url_key, "").strip()
        return ReadOnlyClient(u, env.get(tok_key, ""), args.tenant_id) if u else None
    clients = {k: c for k, c in {
        "incident": mk("PANTHEON_POSTMORTEM_API_URL", "PANTHEON_POSTMORTEM_SERVICE_TOKEN"),
        "evolution": mk("PANTHEON_EVOLUTION_API_URL", "PANTHEON_EVOLUTION_SERVICE_TOKEN"),
        "research": mk("PANTHEON_RESEARCH_API_URL", "PANTHEON_RESEARCH_SERVICE_TOKEN"),
        "consultation": mk("PANTHEON_CONSULTATION_API_URL", "PANTHEON_CONSULTATION_SERVICE_TOKEN"),
        "persona": mk("PANTHEON_PERSONA_API_URL", "PANTHEON_PERSONA_SERVICE_TOKEN"),
    }.items() if c}
    m = export(clients, Path(args.out), persona_ids=args.persona_id, tenant_id=args.tenant_id)
    print(json.dumps({"counts": m["counts"], "out": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
