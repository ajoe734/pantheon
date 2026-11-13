"""Hindsight as a REBUILDABLE retrieval index over canonical memory entries.

Canonical memory stays owner-written.  Hindsight only holds a derived index:
  * one document per canonical entry, document_id == canonical memory id;
  * tags and caller-supplied entities come from structured anchors only
    (never from LLM output);
  * persona_private entries -> one bank per persona; everything else -> one shared bank;
  * delete + re-retain rebuilds the index.
Every recall result is mapped back to a canonical id (document_id, or
source_fact_ids for observations); unmappable results are dropped and counted.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

API = "/v1/default/banks"
SHARED_BANK = "shared"


def canonical_id(item: Mapping) -> str:
    e = item["entry"]
    return e.get("memory_id") or e["entry_id"]


def is_private(item: Mapping) -> bool:
    return item["layer"] == "persona" and item["entry"].get("relevance_scope") == "persona_private"


def entry_text(item: Mapping) -> str:
    c = item["entry"]["content"]
    if item["layer"] == "persona":
        return c["summary"]
    return f"{c['headline']}\n{c['body']}"


def anchor_tags(a: Mapping) -> List[str]:
    tags = [f"family:{f}" for f in a.get("strategy_families") or ([a["strategy_family"]] if a.get("strategy_family") else [])]
    tags += [f"underlying:{u}" for u in a.get("underlyings") or []]
    tags += [f"dataset:{d}" for d in a.get("missing_datasets") or []]
    if a.get("instrument_class"):
        tags.append(f"instrument:{a['instrument_class']}")
    if a.get("regime_tag"):
        tags.append(f"regime:{a['regime_tag']}")
    if a.get("strategy_id"):
        tags.append(f"strategy:{a['strategy_id']}")
    if a.get("decision_type"):
        tags.append(f"decision:{a['decision_type']}")
    return tags


def anchor_entities(a: Mapping) -> List[Dict[str, str]]:
    out = []
    for key, typ in (("strategy_families", "strategy_family"), ("underlyings", "underlying"),
                     ("missing_datasets", "dataset")):
        out += [{"text": str(v), "type": typ} for v in a.get(key) or []]
    for key, typ in (("strategy_family", "strategy_family"), ("instrument_class", "instrument_class"),
                     ("regime_tag", "regime"), ("strategy_id", "strategy")):
        if a.get(key) and {"text": str(a[key]), "type": typ} not in out:
            out.append({"text": str(a[key]), "type": typ})
    return out


def bank_for(item: Mapping, arm_family: str) -> str:
    base = f"persona-{item['entry']['persona_id']}" if is_private(item) else SHARED_BANK
    return f"{arm_family}-{base}"


def build_document(item: Mapping, *, with_entities: bool) -> Dict[str, Any]:
    e = item["entry"]
    doc = {
        "content": entry_text(item),
        "document_id": canonical_id(item),
        "timestamp": e["written_at"],
        "tags": anchor_tags(item.get("anchors") or {}),
        "metadata": {
            "source_event_type": e["source_event_type"], "source_event_id": e["source_event_id"],
            "write_authority": e["write_authority"], "layer": item["layer"],
            "relevance_scope": e.get("relevance_scope") or e.get("scope", ""),
        },
    }
    if with_entities:
        doc["entities"] = anchor_entities(item.get("anchors") or {})
    return doc


Transport = Callable[[str, str, Optional[dict]], Any]


def http_transport(base_url: str, timeout: float = 600.0) -> Transport:
    def call(method: str, path: str, body: Optional[dict] = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base_url.rstrip("/") + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode() or "null"
        return json.loads(raw)
    return call


@dataclass
class RecallResult:
    canonical_ids: List[str]
    dropped: int
    raw_count: int
    latency_s: float


@dataclass
class HindsightIndex:
    transport: Transport
    arm_family: str                      # "llm" (default extraction) or "chunks" (no LLM)
    seen_banks: set = field(default_factory=set)
    fact_to_doc: Dict[str, str] = field(default_factory=dict)
    retain_stats: List[Dict[str, Any]] = field(default_factory=list)
    private_owner: Dict[str, str] = field(default_factory=dict)   # canonical id -> persona (private only)

    @property
    def chunks(self) -> bool:
        return self.arm_family == "chunks"

    def ensure_bank(self, bank: str) -> None:
        if bank in self.seen_banks:
            return
        self.transport("PUT", f"{API}/{urllib.parse.quote(bank, safe='')}", {"name": bank})
        if self.chunks:
            self.transport("PATCH", f"{API}/{urllib.parse.quote(bank, safe='')}/config",
                           {"updates": {"retain_extraction_mode": "chunks"}})
        self.seen_banks.add(bank)

    def retain(self, items: Iterable[Mapping]) -> Dict[str, Any]:
        n, secs = 0, 0.0
        for item in items:
            bank = bank_for(item, self.arm_family)
            self.ensure_bank(bank)
            doc = build_document(item, with_entities=self.chunks)
            t0 = time.monotonic()
            res = self.transport("POST", f"{API}/{urllib.parse.quote(bank, safe='')}/memories",
                                 {"items": [doc], "async": False})
            dt = time.monotonic() - t0
            usage = (res or {}).get("usage") or {}
            self.retain_stats.append({"id": doc["document_id"], "seconds": dt,
                                      "llm_calls": usage.get("llm_calls", 0 if self.chunks else None),
                                      "input_tokens": usage.get("input_tokens"),
                                      "output_tokens": usage.get("output_tokens")})
            if is_private(item):
                self.private_owner[doc["document_id"]] = item["entry"]["persona_id"]
            n += 1
            secs += dt
        return {"retained": n, "seconds": secs}

    def delete_all(self, ids_by_bank: Mapping[str, Iterable[str]]) -> None:
        for bank, ids in ids_by_bank.items():
            for did in ids:
                try:
                    self.transport("DELETE", f"{API}/{urllib.parse.quote(bank, safe='')}/documents/{urllib.parse.quote(did, safe='')}", None)
                except urllib.error.HTTPError as exc:
                    if exc.code != 404:      # absent on first build is fine
                        raise

    def rebuild(self, items: Sequence[Mapping]) -> Dict[str, Any]:
        by_bank: Dict[str, List[str]] = {}
        for it in items:
            by_bank.setdefault(bank_for(it, self.arm_family), []).append(canonical_id(it))
        self.delete_all(by_bank)
        self.retain_stats.clear()
        return self.retain(items)

    def banks_for_persona(self, persona_id: str) -> List[str]:
        return [f"{self.arm_family}-persona-{persona_id}", f"{self.arm_family}-{SHARED_BANK}"]

    def recall(self, persona_id: str, query: str, *, tags: Optional[Sequence[str]] = None,
               top_k: int = 8, known_ids: Optional[set] = None) -> RecallResult:
        """Recall for one persona over its own bank + the shared bank only."""
        merged: List[Dict[str, Any]] = []
        t0 = time.monotonic()
        for bank in self.banks_for_persona(persona_id):
            if bank not in self.seen_banks:
                continue
            body: Dict[str, Any] = {"query": query, "max_tokens": 4096, "trace": False}
            if tags:
                body["tag_groups"] = [{"tags": list(tags), "match": "any_strict"}]
            res = self.transport("POST", f"{API}/{urllib.parse.quote(bank, safe='')}/memories/recall", body)
            merged += list((res or {}).get("results") or [])
        latency = time.monotonic() - t0
        for r in merged:                                   # learn fact -> document links
            if r.get("id") and r.get("document_id"):
                self.fact_to_doc[str(r["id"])] = r["document_id"]
        out, dropped = [], 0
        for r in merged:
            cid = self._map(r, known_ids)
            if cid is None:
                dropped += 1
            elif cid not in out:
                out.append(cid)
        return RecallResult(out[:top_k], dropped, len(merged), latency)

    def _map(self, r: Mapping, known: Optional[set]) -> Optional[str]:
        cand = r.get("document_id")
        if not cand:
            for fid in r.get("source_fact_ids") or []:
                if str(fid) in self.fact_to_doc:
                    cand = self.fact_to_doc[str(fid)]
                    break
        if not cand or (known is not None and cand not in known):
            return None
        return cand
