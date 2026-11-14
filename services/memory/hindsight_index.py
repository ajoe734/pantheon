"""Hindsight memory index behind the canonical memory service (H3-H7, H9, H11).

Canonical institutional and persona stores stay the only truth. Hindsight is a
rebuildable index that only this module talks to:

* every canonical create / supersede / archive / expiry is mirrored after the
  canonical write persisted (one Hindsight document per canonical id);
* mirror failures are retried, recorded with a reason and never raise into the
  canonical write path;
* ``rebuild`` drops each bank and re-retains every active canonical entry;
* recall / reflect return canonical ids only; the route hydrates and authorises
  every id from the canonical stores before anything is returned.

No agent holds the Hindsight key and there is no write path other than the
mirror.  No synthetic data is created here.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

_LOG = logging.getLogger(__name__)

URL_ENV = "PANTHEON_MEMORY_HINDSIGHT_URL"
KEY_ENV = "PANTHEON_MEMORY_HINDSIGHT_API_KEY"
REFLECT_ENV = "PANTHEON_MEMORY_HINDSIGHT_REFLECT_ENABLED"

MIRROR_ATTEMPTS = 3
MIRROR_BACKOFF_SECONDS = 0.5
REQUEST_TIMEOUT_SECONDS = 60.0
FAILURE_LOG_LIMIT = 500

# Anchor kinds (H4).  The value is the tag prefix used by owner writers
# (``family:x``, ``underlying:TW`` ...) and by the case-file lesson query.
ANCHOR_KINDS = (
    "strategy", "parent_strategy", "family", "underlying",
    "instrument", "dataset", "decision", "regime",
)
ANCHOR_NONE_TAG = "anchor:none"

# canonical structured_payload keys -> anchor kind
_PAYLOAD_KEYS: Dict[str, str] = {
    "strategy_id": "strategy", "strategy": "strategy", "strategy_ids": "strategy",
    "parent_strategy_id": "parent_strategy", "parent_strategy": "parent_strategy",
    "strategy_family": "family", "strategy_families": "family", "family": "family",
    "underlying": "underlying", "underlyings": "underlying",
    "instrument_class": "instrument", "instrument": "instrument",
    "dataset_id": "dataset", "dataset_ids": "dataset", "dataset_refs": "dataset",
    "missing_datasets": "dataset", "dataset": "dataset",
    "decision_type": "decision", "decision": "decision",
    "regime": "regime", "regime_tag": "regime",
}
_TAG_PREFIXES: Dict[str, str] = {
    "strategy": "strategy", "parent_strategy": "parent_strategy", "family": "family",
    "underlying": "underlying", "instrument": "instrument", "dataset": "dataset",
    "decision": "decision", "regime": "regime",
}


class HindsightError(RuntimeError):
    """Raised for Hindsight transport or protocol failures (reason is stable)."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# Anchors (H4): one deterministic mapping, never produced by an LLM
# ---------------------------------------------------------------------------

def normalize_anchor(kind: str, value: Any) -> Optional[str]:
    text = str(value).strip().lower() if value is not None else ""
    if kind not in ANCHOR_KINDS or not text:
        return None
    return f"{kind}:{text}"


def _values(raw: Any) -> List[Any]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw.get("dataset_id") or raw.get("id") or raw.get("ref")]
    if isinstance(raw, (list, tuple, set)):
        out: List[Any] = []
        for item in raw:
            out.extend(_values(item))
        return out
    return [raw]


def parse_subject_anchor(raw: str) -> Optional[str]:
    """``kind:value`` request anchor -> normalized anchor, or ``None``."""
    kind, sep, value = str(raw).partition(":")
    return normalize_anchor(kind.strip().lower(), value) if sep else None


def anchors_for(entry: Any) -> List[str]:
    """Anchors from canonical structured fields and tags (sorted, unique)."""
    found: set = set()
    content = getattr(entry, "content", None) or {}
    payload = content.get("structured_payload") or {}
    if isinstance(payload, dict):
        for key, kind in _PAYLOAD_KEYS.items():
            for value in _values(payload.get(key)):
                anchor = normalize_anchor(kind, value)
                if anchor:
                    found.add(anchor)
    for tag in content.get("tags") or []:
        anchor = parse_subject_anchor(str(tag))
        if anchor:
            found.add(anchor)
    scope = getattr(entry, "scope", None)
    scope_filter = getattr(entry, "scope_filter", None)
    if scope_filter and scope == "strategy_family":
        anchor = normalize_anchor("family", scope_filter)
        if anchor:
            found.add(anchor)
    if scope_filter and scope == "instrument_class":
        anchor = normalize_anchor("instrument", scope_filter)
        if anchor:
            found.add(anchor)
    return sorted(found)


# ---------------------------------------------------------------------------
# Banks and canonical-entry mapping (H3)
# ---------------------------------------------------------------------------

def _slug(raw: str) -> str:
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", raw.lower()).strip("_") or "x"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{cleaned[:40]}-{digest}"


def facts_bank(tenant_id: Optional[str]) -> str:
    return f"pantheon-facts-{_slug(tenant_id or 'default')}"


def persona_bank(tenant_id: Optional[str], persona_id: str) -> str:
    return f"pantheon-persona-{_slug(tenant_id or 'default')}-{_slug(persona_id)}"


def is_persona_entry(entry: Any) -> bool:
    return hasattr(entry, "memory_id")


def canonical_id(entry: Any) -> str:
    return entry.memory_id if is_persona_entry(entry) else entry.entry_id


def bank_for(entry: Any) -> str:
    if is_persona_entry(entry):
        return persona_bank(entry.tenant_id, entry.persona_id)
    return facts_bank(entry.tenant_id)


def scope_tag(entry: Any) -> str:
    if is_persona_entry(entry):
        return "scope:committee" if entry.relevance_scope == "persona_and_committee" else "scope:private"
    return f"scope:{entry.scope}"


def memory_type_tag(entry: Any) -> str:
    return f"type:{entry.memory_type if is_persona_entry(entry) else entry.knowledge_type}"


def _entry_text(entry: Any) -> str:
    content = entry.content
    if is_persona_entry(entry):
        return str(content.get("summary", "")).strip()
    return f"{content.get('headline', '').strip()}\n{content.get('body', '').strip()}"


def is_indexable(entry: Any) -> bool:
    return bool(entry.is_active and not entry.is_expired())


def retain_item(entry: Any) -> Dict[str, Any]:
    anchors = anchors_for(entry)
    persona = is_persona_entry(entry)
    metadata = {
        "canonical_id": canonical_id(entry),
        "canonical_kind": "persona" if persona else "institutional",
        "tenant_id": entry.tenant_id or "default",
        "source_event_type": entry.source_event_type,
        "source_event_id": entry.source_event_id,
        "write_authority": entry.write_authority,
        "memory_type": entry.memory_type if persona else entry.knowledge_type,
        "relevance_scope": entry.relevance_scope if persona else entry.scope,
    }
    if persona:
        metadata["persona_id"] = entry.persona_id
    tags = [f"anchor:{a}" for a in anchors] or [ANCHOR_NONE_TAG]
    tags += [scope_tag(entry), memory_type_tag(entry)]
    return {
        "content": _entry_text(entry),
        "document_id": canonical_id(entry),
        "timestamp": entry.written_at,
        "context": entry.source_event_type,
        "metadata": metadata,
        "tags": tags,
        "entities": [{"text": a, "type": a.split(":", 1)[0]} for a in anchors],
        "resolve_entities": False,
    }


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class HindsightClient:
    def __init__(self, url: str, api_key: str, *, timeout: float = REQUEST_TIMEOUT_SECONDS) -> None:
        self.url = url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _call(self, method: str, path: str, body: Optional[dict] = None, *, ok_missing: bool = False) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.url + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as exc:
            if ok_missing and exc.code == 404:
                return None
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise HindsightError(f"hindsight_http_{exc.code}", detail) from exc
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise HindsightError("hindsight_unreachable", type(exc).__name__) from exc

    @staticmethod
    def _bank(bank: str) -> str:
        return "/v1/default/banks/" + urllib.parse.quote(bank, safe="")

    def retain(self, bank: str, items: Sequence[dict]) -> Any:
        return self._call("POST", self._bank(bank) + "/memories", {"items": list(items), "async": False})

    def memory_units(self, bank: str, document_id: str) -> List[dict]:
        units: List[dict] = []
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {"document_id": document_id, "state": "valid", "limit": 100, "offset": offset})
            page = self._call("GET", self._bank(bank) + "/memories/list?" + query, ok_missing=True)
            items = (page or {}).get("items") or []
            units.extend(u for u in items if u.get("document_id") == document_id)
            if len(items) < 100:
                return units
            offset += len(items)

    def invalidate_document(self, bank: str, document_id: str, reason: str) -> int:
        count = 0
        for unit in self.memory_units(bank, document_id):
            self._call("PATCH", f"{self._bank(bank)}/memories/{urllib.parse.quote(unit['id'], safe='')}",
                       {"state": "invalidated", "reason": reason})
            count += 1
        return count

    def delete_bank(self, bank: str) -> None:
        self._call("DELETE", self._bank(bank), ok_missing=True)

    def recall(self, bank: str, body: dict) -> dict:
        return self._call("POST", self._bank(bank) + "/memories/recall", body) or {}

    def reflect(self, bank: str, body: dict) -> dict:
        return self._call("POST", self._bank(bank) + "/reflect", body) or {}


def hindsight_client_from_env() -> Optional[HindsightClient]:
    url = os.getenv(URL_ENV, "").strip()
    key = os.getenv(KEY_ENV, "").strip()
    if not url or not key:
        return None
    return HindsightClient(url, key)


_GLOBAL_CLIENT: Any = None


def set_hindsight_client(client: Any) -> None:
    """Test / composition hook; ``None`` restores environment configuration."""
    global _GLOBAL_CLIENT
    _GLOBAL_CLIENT = client


def get_hindsight_client() -> Optional[HindsightClient]:
    return _GLOBAL_CLIENT if _GLOBAL_CLIENT is not None else hindsight_client_from_env()


def reflect_configured() -> bool:
    return os.getenv(REFLECT_ENV, "").strip().lower() in {"1", "true", "yes"}


def llm_layers() -> str:
    """H5: ``disabled`` until the operator enables the LLM layers (reflect flag)."""
    return "enabled" if reflect_configured() else "disabled"


# ---------------------------------------------------------------------------
# Mirror (H3 / H11): canonical mutation -> Hindsight, never blocking
# ---------------------------------------------------------------------------

class MirrorDiagnostics:
    """Counts and recent failures reported by the mirror (bounded, thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.counts = {"retained": 0, "invalidated": 0, "failed": 0, "anchor_none": 0}
            self.failures: deque = deque(maxlen=FAILURE_LOG_LIMIT)

    def record(self, key: str, **extra: Any) -> None:
        with self._lock:
            self.counts[key] = self.counts.get(key, 0) + 1

    def failure(self, entry_id: str, action: str, reason: str) -> None:
        with self._lock:
            self.counts["failed"] += 1
            self.failures.append({"entry_id": entry_id, "action": action, "reason": reason})

    def snapshot(self) -> dict:
        with self._lock:
            return {"counts": dict(self.counts), "failures": list(self.failures)}


DIAGNOSTICS = MirrorDiagnostics()


def mirror_entry(entry: Any, *, client: Any = None, attempts: int = MIRROR_ATTEMPTS,
                 backoff: float = MIRROR_BACKOFF_SECONDS) -> dict:
    """Mirror one canonical entry after it persisted. Never raises."""
    entry_id = canonical_id(entry)
    action = "retain" if is_indexable(entry) else "invalidate"
    try:
        client = client or get_hindsight_client()
        if client is None:
            raise HindsightError("hindsight_unconfigured")
        bank = bank_for(entry)
        reason = "hindsight_unreachable"
        for attempt in range(1, max(1, attempts) + 1):
            try:
                if action == "retain":
                    item = retain_item(entry)
                    client.retain(bank, [item])
                    DIAGNOSTICS.record("retained")
                    if item["tags"][0] == ANCHOR_NONE_TAG:
                        DIAGNOSTICS.record("anchor_none")
                else:
                    client.invalidate_document(bank, entry_id, _invalidation_reason(entry))
                    DIAGNOSTICS.record("invalidated")
                return {"status": "mirrored", "entry_id": entry_id, "action": action}
            except HindsightError as exc:
                reason = exc.reason
                if exc.reason == "hindsight_unconfigured" or attempt >= attempts:
                    break
                time.sleep(backoff * attempt)
        raise HindsightError(reason)
    except Exception as exc:  # canonical writes must survive index outages
        reason = exc.reason if isinstance(exc, HindsightError) else "hindsight_mirror_failed"
        DIAGNOSTICS.failure(entry_id, action, reason)
        _LOG.warning("memory_mirror_failed entry_id=%s action=%s reason=%s", entry_id, action, reason)
        return {"status": "failed", "entry_id": entry_id, "action": action, "reason": reason}


# Post-persistence dispatch: canonical writers hand the persisted state over here and return;
# index I/O + retries run outside the canonical critical section.  One FIFO per canonical id
# keeps per-document lifecycle order (retain -> invalidate); different ids run in parallel.
_PENDING: Dict[str, deque] = {}
_IDLE = threading.Condition(threading.Lock())


def schedule_mirror(entry: Any) -> None:
    """Queue a mirror of an already-persisted canonical entry; never blocks, never raises."""
    key = canonical_id(entry)
    with _IDLE:
        queue = _PENDING.get(key)
        if queue is not None:
            queue.append(entry)  # the drain thread for this id picks it up in order
            return
        _PENDING[key] = deque([entry])
    try:
        threading.Thread(target=_drain, args=(key,), name="memory-mirror", daemon=True).start()
    except Exception:  # thread exhaustion must not fail the canonical write
        with _IDLE:
            _PENDING.pop(key, None)
            _IDLE.notify_all()
        DIAGNOSTICS.failure(key, "retain", "hindsight_mirror_failed")


def _drain(key: str) -> None:
    while True:
        with _IDLE:
            queue = _PENDING.get(key)
            if not queue:
                _PENDING.pop(key, None)
                _IDLE.notify_all()
                return
            entry = queue.popleft()
        mirror_entry(entry)


def flush_mirrors(timeout: float = 30.0) -> bool:
    """Wait until every queued mirror finished (tests, rebuild, shutdown). True if drained."""
    deadline = time.monotonic() + timeout
    with _IDLE:
        while _PENDING:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _IDLE.wait(remaining)
    return True


def _invalidation_reason(entry: Any) -> str:
    if entry.superseded_by:
        return "superseded"
    if entry.archived_at:
        return str(entry.archived_reason or "archived")[:200]
    return "expired"


# ---------------------------------------------------------------------------
# Rebuild (H3)
# ---------------------------------------------------------------------------

def rebuild(institutional_store: Any, persona_store: Any, *, client: Any = None) -> dict:
    """Drop every bank known to canonical memory and re-retain active entries.

    Idempotent: a second run drops and re-retains the same documents.
    """
    flush_mirrors()  # queued post-persistence mirrors must not race the bank drop
    client = client or get_hindsight_client()
    report: Dict[str, Any] = {"banks": 0, "retained": 0, "inactive_skipped": 0, "anchor_none": 0,
                              "failures": [], "bank_counts": {}}
    if client is None:
        report["failures"].append({"reason": "hindsight_unconfigured"})
        return report
    by_bank: Dict[str, List[Any]] = {}
    for store in (institutional_store, persona_store):
        store.archive_expired()
        for entry in store.list(active_only=False):
            by_bank.setdefault(bank_for(entry), [])
            if is_indexable(entry):
                by_bank[bank_for(entry)].append(entry)
            else:
                report["inactive_skipped"] += 1
    for bank, entries in sorted(by_bank.items()):
        try:
            client.delete_bank(bank)
        except HindsightError as exc:
            report["failures"].append({"bank": bank, "reason": exc.reason})
            continue
        report["banks"] += 1
        retained = 0
        for entry in entries:
            item = retain_item(entry)
            try:
                client.retain(bank, [item])
            except HindsightError as exc:
                report["failures"].append({"bank": bank, "entry_id": canonical_id(entry), "reason": exc.reason})
                continue
            retained += 1
            report["anchor_none"] += int(item["tags"][0] == ANCHOR_NONE_TAG)
        report["retained"] += retained
        report["bank_counts"][bank] = retained
    return report


# ---------------------------------------------------------------------------
# Recall / reflect helpers (H6, H9)
# ---------------------------------------------------------------------------

def tag_groups(*, subject_anchors: Iterable[str] = (), scope_tags: Iterable[str] = (),
               type_tag: Optional[str] = None) -> List[dict]:
    groups: List[dict] = []
    anchors = [f"anchor:{a}" for a in dict.fromkeys(filter(None, map(parse_subject_anchor, subject_anchors)))]
    if anchors:
        groups.append({"tags": anchors, "match": "any_strict"})
    scopes = list(dict.fromkeys(scope_tags))
    if scopes:
        groups.append({"tags": scopes, "match": "any_strict"})
    if type_tag:
        groups.append({"tags": [type_tag], "match": "any_strict"})
    return groups


def recall_canonical_ids(client: Any, bank: str, *, query: str, groups: List[dict],
                         limit: int) -> List[Tuple[str, float]]:
    """Recall one bank; return ``(canonical_id, score)`` best-first, deduplicated.

    Documents map directly through ``document_id``; observations map through
    ``source_fact_ids`` resolved with ``include.source_facts``.
    """
    body: Dict[str, Any] = {
        "query": query, "budget": "mid", "max_tokens": min(32768, max(4096, limit * 1000)),
        "include": {"entities": None, "source_facts": {}},
    }
    if groups:
        body["tag_groups"] = groups
    response = client.recall(bank, body)
    source_facts = response.get("source_facts") or {}
    ordered: Dict[str, float] = {}
    total = len(response.get("results") or [])
    for rank, result in enumerate(response.get("results") or []):
        score = float(((result.get("scores") or {}).get("final")) or (total - rank) / max(total, 1))
        ids: List[str] = []
        if result.get("document_id"):
            ids.append(result["document_id"])
        for fact_id in result.get("source_fact_ids") or []:
            doc = (source_facts.get(fact_id) or {}).get("document_id")
            if doc:
                ids.append(doc)
        for doc_id in ids:
            if score > ordered.get(doc_id, float("-inf")):
                ordered[doc_id] = score
    return sorted(ordered.items(), key=lambda item: item[1], reverse=True)


def reflect_citations(response: dict) -> List[str]:
    """Canonical ids cited by a reflect response (``based_on`` memories)."""
    based_on = response.get("based_on") or {}
    ids: Dict[str, None] = {}
    for fact in based_on.get("memories") or []:
        if fact.get("document_id"):
            ids[fact["document_id"]] = None
    return list(ids)


def _main(argv: Sequence[str]) -> int:
    if list(argv) != ["rebuild"]:
        print("usage: python -m services.memory.hindsight_index rebuild")
        return 2
    from .main import _persona_store, _store
    report = rebuild(_store(), _persona_store())
    print(json.dumps(report, ensure_ascii=False))
    return 1 if report["failures"] else 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
