"""Run the four-arm comparison at top 8 over an exporter dataset.

Arms:  current | hs_default | hs_filtered_rerank | hs_chunks_rerank
Refuses any dataset without a valid exporter manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import metrics
from .export_real_events import DECISIONS_NAME, ENTRIES_NAME, validate_manifest
from .hindsight_index import HindsightIndex, canonical_id, entry_text, http_transport

ARMS = ("current", "hs_default", "hs_filtered_rerank", "hs_chunks_rerank")
TOP_K = 8


def load_dataset(data_dir: Path) -> Dict[str, Any]:
    manifest = validate_manifest(data_dir)      # raises ValueError if missing/invalid
    rd = lambda n: [json.loads(l) for l in (Path(data_dir) / n).read_text().splitlines() if l.strip()]
    return {"manifest": manifest, "entries": rd(ENTRIES_NAME), "decisions": rd(DECISIONS_NAME)}


def _subject(decision: Mapping):
    from services.knowledge.case_file import Subject
    return Subject(
        decision_type=decision["decision_type"], persona_id=decision["persona_id"],
        tenant_id=decision.get("tenant_id") or "", strategy_family=decision["strategy_family"],
        underlyings=tuple(decision.get("underlyings") or ()), instrument_class=decision.get("instrument_class") or "",
        regime_tag=decision.get("regime_tag") or "", strategy_families=tuple(decision.get("strategy_families") or ()),
        missing_datasets=tuple(decision.get("missing_datasets") or ()),
        member_artifact_refs=("eval",))


def decision_query(decision: Mapping):
    """Production lesson_query: (query text, tag list)."""
    from services.knowledge.case_file import lesson_query
    return lesson_query(_subject(decision))


class CurrentRetrieval:
    """The current keyword-fallback retrieval over rebuilt local stores (hybrid when a backend is configured)."""

    def __init__(self, entries: List[Mapping], workdir: Path):
        from services.memory import search_retrieval
        from services.memory.institutional_memory_store import InstitutionalMemoryEntry, InstitutionalMemoryStore
        from services.memory.persona_memory_store import PersonaMemoryEntry, PersonaMemoryStore

        class _Null:
            def project(self, e):
                return {"projected": 1}
        search_retrieval.set_search_retrieval_backend(_Null())
        self.p, self.i = PersonaMemoryStore(workdir / "p.json"), InstitutionalMemoryStore(workdir / "i.json")
        for it in entries:
            if it["layer"] == "persona":
                self.p.create(PersonaMemoryEntry.from_dict(it["entry"]))
            else:
                self.i.create(InstitutionalMemoryEntry.from_dict(it["entry"]))
        self.hybrid = bool(os.getenv("PANTHEON_SEARCH_API_URL"))

    def retrieve(self, persona_id: str, query: str, tags: List[str], k: int) -> List[str]:
        hits = [(h.relevance_score, h.entry.written_at, h.entry.memory_id)
                for h in self.p.retrieve(persona_id=persona_id, query=query, tags=tags, limit=k)]
        hits += [(h.relevance_score, h.entry.written_at, h.entry.entry_id)
                 for h in self.i.retrieve(query=query, tags=tags, limit=k)]
        hits.sort(reverse=True)
        return [h[2] for h in hits[:k]]


def usefulness_of(item: Mapping) -> float:
    from services.memory.usefulness import usefulness_score

    class _E:
        usefulness_marks = item["entry"].get("usefulness_marks") or {}
    return usefulness_score(_E)


def rerank_by_usefulness(ids: List[str], by_id: Mapping[str, Mapping]) -> List[str]:
    """Final step: stable sort by the existing usefulness_score (hindsight order kept on ties)."""
    return [c for _, _, c in sorted(((-usefulness_of(by_id[c]), n, c) for n, c in enumerate(ids)))]


def run(data_dir: Path, out_dir: Path, *, transport: Callable, llm_info: Optional[Mapping] = None,
        embed: Optional[Callable[[List[str]], List[List[float]]]] = None, rebuild_twice: bool = True,
        smoke: bool = False, max_decisions: Optional[int] = None,
        llm_enabled: bool = True) -> Dict[str, Any]:
    ds = load_dataset(Path(data_dir))
    entries, decisions = ds["entries"], ds["decisions"][:max_decisions]
    by_id = {canonical_id(e): e for e in entries}
    known = set(by_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    flat = lambda cid: {**by_id[cid]["entry"], "persona_id": by_id[cid]["entry"].get("persona_id")}

    with tempfile.TemporaryDirectory() as tmp:
        current = CurrentRetrieval(entries, Path(tmp))
    arms = ARMS if llm_enabled else tuple(a for a in ARMS if a not in ("hs_default", "hs_filtered_rerank"))
    indexes = {"chunks": HindsightIndex(transport, "chunks")}
    if llm_enabled:
        indexes["llm"] = HindsightIndex(transport, "llm")
    retain = {name: ix.rebuild(entries) for name, ix in indexes.items()}

    def one_pass() -> Dict[str, Dict[str, Dict[str, Any]]]:
        res: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for d in decisions:
            q, tags = decision_query(d)
            pid = d["persona_id"]
            r: Dict[str, Dict[str, Any]] = {}
            import time
            t0 = time.monotonic()
            ids = current.retrieve(pid, q, tags, TOP_K)
            r["current"] = {"ids": ids, "latency": time.monotonic() - t0, "dropped": 0}
            if llm_enabled:
                rr = indexes["llm"].recall(pid, q, top_k=TOP_K, known_ids=known)
                r["hs_default"] = {"ids": rr.canonical_ids, "latency": rr.latency_s, "dropped": rr.dropped}
                rr = indexes["llm"].recall(pid, q, tags=tags, top_k=TOP_K, known_ids=known)
                r["hs_filtered_rerank"] = {"ids": rerank_by_usefulness(rr.canonical_ids, by_id), "latency": rr.latency_s, "dropped": rr.dropped}
            rr = indexes["chunks"].recall(pid, q, tags=tags, top_k=TOP_K, known_ids=known)
            r["hs_chunks_rerank"] = {"ids": rerank_by_usefulness(rr.canonical_ids, by_id), "latency": rr.latency_s, "dropped": rr.dropped}
            res[d["decision_id"]] = r
        return res

    first = one_pass()
    stability: Dict[str, Optional[float]] = {}
    if rebuild_twice:
        for ix in indexes.values():
            ix.rebuild(entries)
        second = one_pass()
        for arm in arms[1:]:
            stability[arm] = metrics.rebuild_stability(
                {d: first[d][arm]["ids"] for d in first}, {d: second[d][arm]["ids"] for d in second})
    by_decision = {d["decision_id"]: d for d in decisions}
    per_arm: Dict[str, Any] = {}
    for arm in arms:
        offs, dups, viol, lat, dropped = [], [], 0, [], 0
        by_lang: Dict[str, List[float]] = {}
        for did, r in first.items():
            d, ids = by_decision[did], r[arm]["ids"]
            q, _ = decision_query(d)
            anchors = [by_id[c].get("anchors") or {} for c in ids]
            off = metrics.off_subject_rate(anchors, d)
            if off is not None:
                offs.append(off)
                by_lang.setdefault(metrics.query_language(q), []).append(off)
            vecs = embed([entry_text(by_id[c]) for c in ids]) if (embed and ids) else None
            dup = metrics.duplicate_rate([flat(c) for c in ids], vecs)
            if dup is not None:
                dups.append(dup)
            viol += metrics.scope_violations(d["persona_id"], [flat(c) for c in ids])
            lat.append(r[arm]["latency"])
            dropped += r[arm]["dropped"]
        mean = lambda xs: sum(xs) / len(xs) if xs else None
        per_arm[arm] = {
            "off_subject_rate": mean(offs), "duplicate_rate": mean(dups),
            "duplicate_basis": "source_event+cosine" if embed else "source_event_only",
            "scope_violations": viol, "unmappable_dropped": dropped,
            "off_subject_by_language": {k: {"mean": mean(v), "n": len(v)} for k, v in by_lang.items()},
            "recall_latency_s": metrics.latency_summary(lat), "decisions": len(first),
            "rebuild_top8_jaccard": stability.get(arm),
        }
    texts = {c: entry_text(e) for c, e in by_id.items()}
    sheet, key = metrics.build_blind_sheet({d: {a: first[d][a]["ids"] for a in arms} for d in first}, texts, by_decision)
    (out_dir / "blind_sheet.json").write_text(json.dumps(sheet, indent=2, ensure_ascii=False))
    (out_dir / "blind_key.json").write_text(json.dumps(key, indent=2))
    (out_dir / "ranked_results.json").write_text(json.dumps({d: {a: first[d][a]["ids"] for a in arms} for d in first}, indent=2))
    result = {
        "kind": "smoke" if smoke else "comparison",
        "note": "smoke: pipeline check only, not a comparison result" if smoke else "",
        "llm": dict(llm_info or {}), "manifest_sha256": ds["manifest"]["sha256"],
        "retain": {name: {**retain[name], **metrics.retain_summary(indexes[name].retain_stats)} for name in indexes},
        "arms": per_arm, "llm_arms_run": llm_enabled, "hosted_behavior_claimed": False,
    }
    (out_dir / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--api", default=f"http://127.0.0.1:{os.getenv('HINDSIGHT_EVAL_API_PORT', '58888')}")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-llm", action="store_true", help="skip arms that need an LLM")
    ap.add_argument("--max-decisions", type=int)
    a = ap.parse_args(argv)
    llm = {"provider": os.getenv("HINDSIGHT_EVAL_LLM_PROVIDER", ""), "model": os.getenv("HINDSIGHT_EVAL_LLM_MODEL", ""),
           "embeddings": os.getenv("HINDSIGHT_EVAL_EMBED_MODEL", "intfloat/multilingual-e5-large"),
           "reranker": os.getenv("HINDSIGHT_EVAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")}
    try:
        r = run(Path(a.data), Path(a.out), transport=http_transport(a.api), llm_info=llm, smoke=a.smoke, llm_enabled=not a.no_llm,
                max_decisions=a.max_decisions)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 2
    print(json.dumps({"kind": r["kind"], "arms": list(r["arms"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
