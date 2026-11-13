"""Metrics for the Hindsight comparison.  Pure functions; no I/O except sheet/scorer helpers."""
from __future__ import annotations

import json
import math
import random
import re
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

_CJK = re.compile(r"[㐀-鿿]")
_LATIN = re.compile(r"[A-Za-z]")


def query_language(text: str) -> str:
    cjk, latin = bool(_CJK.search(text)), bool(_LATIN.search(text))
    return "mixed" if cjk and latin else "zh-TW" if cjk else "en"


def subject_anchor_set(subject: Mapping) -> set:
    s = {("family", f) for f in [subject.get("strategy_family"), *(subject.get("strategy_families") or [])] if f}
    s |= {("underlying", u) for u in subject.get("underlyings") or []}
    s |= {("dataset", d) for d in subject.get("missing_datasets") or []}
    for key, kind in (("instrument_class", "instrument"), ("regime_tag", "regime")):
        if subject.get(key):
            s.add((kind, subject[key]))
    return s


def item_anchor_set(anchors: Mapping) -> set:
    return subject_anchor_set(anchors)


def off_subject_rate(returned_anchors: Sequence[Mapping], subject: Mapping) -> Optional[float]:
    """Share of returned items that share no anchor with the decision subject (canonical fields)."""
    if not returned_anchors:
        return None
    want = subject_anchor_set(subject)
    return sum(1 for a in returned_anchors if not (item_anchor_set(a) & want)) / len(returned_anchors)


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


def duplicate_rate(entries: Sequence[Mapping], embeddings: Optional[Sequence[Sequence[float]]] = None,
                   threshold: float = 0.95) -> Optional[float]:
    """Fraction of returned items that duplicate an earlier one (same source event or cosine >= threshold)."""
    if not entries:
        return None
    dup = 0
    for i, e in enumerate(entries):
        for j in range(i):
            same = (e["source_event_type"], e["source_event_id"]) == (entries[j]["source_event_type"], entries[j]["source_event_id"])
            close = embeddings is not None and _cos(embeddings[i], embeddings[j]) >= threshold
            if same or close:
                dup += 1
                break
    return dup / len(entries)


def scope_violations(persona_id: str, entries: Iterable[Mapping]) -> int:
    """Items that are another persona's persona_private entry."""
    return sum(1 for e in entries
               if e.get("persona_id") and e["persona_id"] != persona_id
               and e.get("relevance_scope") == "persona_private")


def percentile(values: Sequence[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * p
    lo, hi = math.floor(k), math.ceil(k)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def latency_summary(values: Sequence[float]) -> Dict[str, Optional[float]]:
    return {"p50": percentile(values, 0.5), "p95": percentile(values, 0.95), "n": len(values)}


def retain_summary(stats: Sequence[Mapping]) -> Dict[str, Any]:
    n = len(stats)
    if not n:
        return {"entries": 0}
    def mean(key):
        vals = [s[key] for s in stats if s.get(key) is not None]
        return sum(vals) / len(vals) if vals else None
    return {"entries": n, "seconds_per_entry": mean("seconds"), "llm_calls_per_entry": mean("llm_calls"),
            "input_tokens_per_entry": mean("input_tokens"), "output_tokens_per_entry": mean("output_tokens")}


def jaccard(a: Sequence[str], b: Sequence[str]) -> float:
    sa, sb = set(a), set(b)
    return 1.0 if not sa and not sb else len(sa & sb) / len(sa | sb)


def rebuild_stability(run1: Mapping[str, Sequence[str]], run2: Mapping[str, Sequence[str]]) -> Optional[float]:
    keys = sorted(set(run1) & set(run2))
    return sum(jaccard(run1[k], run2[k]) for k in keys) / len(keys) if keys else None


# ---- blinded labelling sheet + scorer ----

def build_blind_sheet(results: Mapping[str, Mapping[str, Sequence[str]]], texts: Mapping[str, str],
                      decisions: Mapping[str, Mapping], *, max_decisions: int = 30, seed: int = 20261113):
    """results[decision_id][arm] -> ranked canonical ids.  Returns (sheet_rows, key).

    Rows hold pooled, shuffled items with NO arm names; `key` maps (decision_id,item_id) -> arms
    and must be kept separate from the sheet given to the labeller.
    """
    rng = random.Random(seed)
    ids = sorted(results)
    rng.shuffle(ids)
    rows, key = [], {}
    for did in ids[:max_decisions]:
        pooled = sorted({cid for arm in results[did].values() for cid in arm})
        rng.shuffle(pooled)
        subj = decisions[did]
        for n, cid in enumerate(pooled):
            rows.append({"decision_id": did, "decision_type": subj.get("decision_type"),
                         "decision_summary": {k: subj.get(k) for k in ("strategy_family", "underlyings", "instrument_class", "regime_tag", "missing_datasets")},
                         "item_id": cid, "position": n, "item_text": texts.get(cid, ""), "label": ""})
            key[f"{did}|{cid}"] = [a for a, ids_ in results[did].items() if cid in ids_]
    return rows, key


def score_precision_at_k(results: Mapping[str, Mapping[str, Sequence[str]]], key_labels: Mapping[str, str],
                         k: int = 8) -> Dict[str, Any]:
    """key_labels['decision|item'] in {'relevant','irrelevant'}; unlabelled items are skipped (and counted)."""
    out: Dict[str, Dict[str, float]] = {}
    for did, arms in results.items():
        for arm, ranked in arms.items():
            top = list(ranked)[:k]
            labelled = [key_labels.get(f"{did}|{c}") for c in top]
            labelled = [l for l in labelled if l in ("relevant", "irrelevant")]
            if not labelled:
                continue
            a = out.setdefault(arm, {"p_sum": 0.0, "n": 0, "unlabelled": 0})
            a["p_sum"] += sum(l == "relevant" for l in labelled) / k
            a["n"] += 1
            a["unlabelled"] += len(top) - len(labelled)
    return {arm: {"precision_at_k": v["p_sum"] / v["n"], "decisions": v["n"], "unlabelled_items": v["unlabelled"], "k": k}
            for arm, v in out.items()}
