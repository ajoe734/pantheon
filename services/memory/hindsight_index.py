"""Hindsight memory index behind the canonical memory service (H3-H7, H9, H11).

Canonical institutional and persona stores stay the only truth. Hindsight is a
rebuildable index that only this module talks to:

* every canonical create / supersede / archive / expiry is mirrored after the
  canonical write persisted (one Hindsight document per canonical id);
* mirror failures are retried, recorded with a reason and never raise into the
  canonical write path;
* ``rebuild`` drops each bank and re-retains every active canonical entry;
* recall returns canonical ids only; the route hydrates and authorises every id
  from the canonical stores before anything is returned;
* with the LLM layers enabled (the OpenClaw bridge is configured) each bank is
  switched to verbatim retain and background consolidation before its first retain.
  Standing questions (mental models) live in ``mental_models.py``.

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
LLM_LAYERS_ENV = "PANTHEON_MEMORY_HINDSIGHT_LLM_LAYERS_ENABLED"
MIRROR_WORKERS_ENV = "PANTHEON_MEMORY_MIRROR_WORKERS"

MIRROR_ATTEMPTS = 3
MIRROR_BACKOFF_SECONDS = 0.5
MAX_RETRY_AFTER_SECONDS = 5.0
REQUEST_TIMEOUT_SECONDS = 60.0
DEFAULT_FLUSH_TIMEOUT_SECONDS = 30.0
FAILURE_LOG_LIMIT = 500
DEFAULT_MIRROR_WORKERS = 1
MAX_PENDING_MIRRORS = 256
MAX_PENDING_RESYNC_BYTES = 256 * 1024
RESYNC_INTERVAL_SECONDS = 60.0
MAX_RESYNC_BACKOFF_SECONDS = 300.0


def _env_mirror_workers(default: int = DEFAULT_MIRROR_WORKERS) -> int:
    raw = os.getenv(MIRROR_WORKERS_ENV, "").strip()
    if not raw:
        return max(1, int(default))
    try:
        return max(1, int(raw))
    except ValueError:
        return max(1, int(default))


MAX_MIRROR_WORKERS = _env_mirror_workers(DEFAULT_MIRROR_WORKERS)
_INITIAL_MAX_MIRROR_WORKERS = MAX_MIRROR_WORKERS


def _configured_mirror_workers() -> int:
    if MAX_MIRROR_WORKERS != _INITIAL_MAX_MIRROR_WORKERS:
        return max(1, int(MAX_MIRROR_WORKERS))
    return _env_mirror_workers(MAX_MIRROR_WORKERS)

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

    def __init__(self, reason: str, detail: str = "", *, retry_after: Optional[float] = None) -> None:
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail
        self.retry_after = retry_after


def _parse_retry_after(headers: Any, *, max_seconds: float = MAX_RETRY_AFTER_SECONDS) -> Optional[float]:
    if headers is None or not hasattr(headers, "get"):
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(str(raw).strip())
    except ValueError:
        return None
    if value <= 0:
        return None
    return min(value, max(0.0, float(max_seconds)))


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
            retry_after = _parse_retry_after(getattr(exc, "headers", None)) if exc.code == 429 else None
            raise HindsightError(f"hindsight_http_{exc.code}", detail, retry_after=retry_after) from exc
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
        forget_layers(bank)

    def recall(self, bank: str, body: dict) -> dict:
        return self._call("POST", self._bank(bank) + "/memories/recall", body) or {}

    def memory_unit(self, bank: str, unit_id: str) -> Optional[dict]:
        return self._call("GET", f"{self._bank(bank)}/memories/{urllib.parse.quote(unit_id, safe='')}", ok_missing=True)

    def configure_bank(self, bank: str, updates: dict) -> None:
        self._call("PATCH", self._bank(bank) + "/config", {"updates": updates})

    def list_mental_models(self, bank: str, *, detail: str = "full") -> List[dict]:
        found: List[dict] = []
        offset = 0
        while True:
            query = urllib.parse.urlencode({"detail": detail, "limit": 100, "offset": offset})
            page = self._call("GET", self._bank(bank) + "/mental-models?" + query, ok_missing=True) or {}
            items = page.get("items") or []
            found.extend(items)
            if len(items) < 100:
                return found
            offset += len(items)

    def create_mental_model(self, bank: str, body: dict) -> Any:
        return self._call("POST", self._bank(bank) + "/mental-models", body)

    def update_mental_model(self, bank: str, model_id: str, body: dict) -> Any:
        return self._call("PATCH", f"{self._bank(bank)}/mental-models/{urllib.parse.quote(model_id, safe='')}", body)

    def delete_mental_model(self, bank: str, model_id: str) -> None:
        self._call("DELETE", f"{self._bank(bank)}/mental-models/{urllib.parse.quote(model_id, safe='')}", ok_missing=True)


def hindsight_client_from_env() -> Optional[HindsightClient]:
    url = os.getenv(URL_ENV, "").strip()
    key = os.getenv(KEY_ENV, "").strip()
    if not url or not key:
        return None
    return HindsightClient(url, key)


_GLOBAL_CLIENT: Any = None
_REBUILD_CLIENT: Any = None
_RESYNC_CLIENT: Any = None


def set_hindsight_client(client: Any) -> None:
    """Test / composition hook; ``None`` restores environment configuration."""
    global _GLOBAL_CLIENT
    _GLOBAL_CLIENT = client


def get_hindsight_client() -> Optional[HindsightClient]:
    if _GLOBAL_CLIENT is not None:
        return _GLOBAL_CLIENT
    if _REBUILD_CLIENT is not None:
        return _REBUILD_CLIENT
    if _RESYNC_CLIENT is not None:
        return _RESYNC_CLIENT
    return hindsight_client_from_env()


def llm_layers_enabled() -> bool:
    return os.getenv(LLM_LAYERS_ENV, "").strip().lower() in {"1", "true", "yes"}


def llm_layers() -> str:
    """H5: ``disabled`` until the OpenClaw bridge is configured (deploy sets the flag with it)."""
    return "enabled" if llm_layers_enabled() else "disabled"


# Per-bank switches applied before the first retain when the LLM layers are enabled.
# Unset, nothing is sent and Hindsight keeps its deployed chunks mode.
LAYER_BANK_CONFIG = {"retain_extraction_mode": "verbatim", "enable_auto_consolidation": True}
_LAYERED_BANKS: set = set()
_LAYER_LOCK = threading.Lock()


def ensure_layers(client: Any, bank: str) -> None:
    """Apply verbatim retain + background consolidation to ``bank`` once per process.

    Raises ``HindsightError`` on failure so the caller's retry/record path handles it;
    a bridge refusal never reaches here (it only defers Hindsight's own background work).
    """
    if not llm_layers_enabled():
        return
    with _LAYER_LOCK:
        if (id(client), bank) in _LAYERED_BANKS:
            return
        client.configure_bank(bank, dict(LAYER_BANK_CONFIG))
        _LAYERED_BANKS.add((id(client), bank))


def forget_layers(bank: str) -> None:
    with _LAYER_LOCK:
        for key in [k for k in _LAYERED_BANKS if k[1] == bank]:
            _LAYERED_BANKS.discard(key)


# ---------------------------------------------------------------------------
# Mirror (H3 / H11): canonical mutation -> Hindsight, never blocking
# ---------------------------------------------------------------------------

# Post-persistence dispatch: canonical writers hand the persisted state over here and return;
# index I/O + retries run outside the canonical critical section.  One bounded FIFO per
# (bank, canonical_id) keeps per-document lifecycle order (retain -> invalidate) while a
# bounded worker pool limits background thread / queue resource usage during outages.
# Runtime budget: zero persistent files/tables/queues; _PENDING_RESYNC is strictly capped at
# MAX_PENDING_MIRRORS=256 entries and MAX_PENDING_RESYNC_BYTES=256 KB (262144 bytes) with
# FIFO oldest-first eviction and cleanup on mirror completion/shutdown; no retained-doc cache.
_PENDING: Dict[Tuple[str, str], deque] = {}
_IN_FLIGHT: set[Tuple[str, str]] = set()
_SEEN_RETAIN: Dict[Tuple[str, str], None] = {}
_INVALIDATED: Dict[Tuple[str, str], Any] = {}
_TOMBSTONES: Dict[Tuple[str, str], Any] = {}
_PENDING_RESYNC: Dict[Tuple[str, str], Any] = {}
_PENDING_RESYNC_BYTES: Dict[Tuple[str, str], int] = {}
_RESYNC_WATCH: set[Tuple[str, str]] = set()
_RESYNC_OUTCOMES: Dict[Tuple[str, str], str] = {}
_RESYNC_IN_PROGRESS = False
_RESYNC_TIMER: Optional[threading.Timer] = None
_RESYNC_FAILURE_STREAK = 0
_LAST_RESYNC_AT = 0.0
_CATCHUP_CONTINUATION: Optional[Tuple[Any, ...]] = None
_CATCHUP_RESCANNING = False
_CATCHUP_THREAD: Optional[threading.Thread] = None
_ACTIVE_WORKERS = 0
_REBUILDING = 0
_REBUILD_LOCK = threading.Lock()
_IDLE = threading.Condition(threading.Lock())


def _cancel_resync_timer_unlocked() -> None:
    global _RESYNC_TIMER, _RESYNC_FAILURE_STREAK
    if _RESYNC_TIMER is not None:
        _RESYNC_TIMER.cancel()
        _RESYNC_TIMER = None
    _RESYNC_FAILURE_STREAK = 0


def stop_resync_timer() -> None:
    """Cancel any active background resync timer (shutdown / test teardown)."""
    with _IDLE:
        _cancel_resync_timer_unlocked()


def pending_resync_count() -> int:
    """Number of failed canonical entries/tombstones awaiting background resync."""
    with _IDLE:
        return len(_PENDING_RESYNC)


def pending_resync_bytes() -> int:
    """Estimated UTF-8 JSON payload bytes held in the bounded in-memory resync map."""
    with _IDLE:
        return sum(_PENDING_RESYNC_BYTES.values())


def _estimate_resync_entry_bytes(entry: Any) -> int:
    try:
        payload = entry.to_dict() if hasattr(entry, "to_dict") else retain_item(entry)
        return len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) + 128
    except Exception:
        return 1024


def _resync_delay_seconds_unlocked() -> float:
    base = max(0.001, float(RESYNC_INTERVAL_SECONDS))
    cap = max(base, float(MAX_RESYNC_BACKOFF_SECONDS))
    backoff_delay = min(cap, base * (2 ** min(max(0, _RESYNC_FAILURE_STREAK), 4)))
    if _LAST_RESYNC_AT > 0.0:
        since_last = max(0.0, time.monotonic() - _LAST_RESYNC_AT)
        min_remaining = max(0.0, base - since_last)
        return max(backoff_delay, min_remaining)
    return backoff_delay


def _arm_resync_timer_unlocked() -> None:
    global _RESYNC_TIMER
    if not _PENDING_RESYNC:
        _cancel_resync_timer_unlocked()
        return
    if _RESYNC_IN_PROGRESS:
        return
    if _RESYNC_TIMER is not None and _RESYNC_TIMER.is_alive():
        return
    delay = _resync_delay_seconds_unlocked()
    timer = threading.Timer(delay, _resync_timer_tick)
    timer.name = "memory-mirror-resync"
    timer.daemon = True
    _RESYNC_TIMER = timer
    try:
        timer.start()
    except Exception:
        _RESYNC_TIMER = None


def _set_pending_resync_unlocked(key: Tuple[str, str], entry: Any) -> None:
    entry_bytes = _estimate_resync_entry_bytes(entry)
    max_bytes = max(1024, int(MAX_PENDING_RESYNC_BYTES))
    cap = max(1, int(MAX_PENDING_MIRRORS))
    _PENDING_RESYNC.pop(key, None)
    _PENDING_RESYNC_BYTES.pop(key, None)
    if entry_bytes > max_bytes:
        if not _PENDING_RESYNC:
            _cancel_resync_timer_unlocked()
        return
    _PENDING_RESYNC[key] = entry
    _PENDING_RESYNC_BYTES[key] = entry_bytes
    while len(_PENDING_RESYNC) > cap or sum(_PENDING_RESYNC_BYTES.values()) > max_bytes:
        oldest = next(iter(_PENDING_RESYNC))
        _PENDING_RESYNC.pop(oldest, None)
        _PENDING_RESYNC_BYTES.pop(oldest, None)
    _arm_resync_timer_unlocked()


def _record_pending_resync(entry: Any) -> None:
    key = (bank_for(entry), canonical_id(entry))
    indexable = is_indexable(entry)
    with _IDLE:
        if indexable:
            tombstone = _TOMBSTONES.get(key)
            if tombstone is None:
                queued = _PENDING.get(key, ())
                tombstone = next((item for item in reversed(queued) if not is_indexable(item)), None)
            if tombstone is None:
                existing_resync = _PENDING_RESYNC.get(key)
                if existing_resync is not None and not is_indexable(existing_resync):
                    tombstone = existing_resync
            if tombstone is not None:
                _set_pending_resync_unlocked(key, tombstone)
                return
        else:
            _TOMBSTONES[key] = entry
            _bound_map_unlocked(_TOMBSTONES)
        _set_pending_resync_unlocked(key, entry)


def _clear_pending_resync(key: Tuple[str, str], *, for_retain: bool = False) -> None:
    with _IDLE:
        if for_retain:
            tombstone = _TOMBSTONES.get(key)
            existing = _PENDING_RESYNC.get(key)
            if tombstone is not None or (existing is not None and not is_indexable(existing)):
                return
        _PENDING_RESYNC.pop(key, None)
        _PENDING_RESYNC_BYTES.pop(key, None)
        if not _PENDING_RESYNC:
            _cancel_resync_timer_unlocked()


def _resync_timer_tick() -> None:
    global _RESYNC_TIMER, _LAST_RESYNC_AT
    with _IDLE:
        _RESYNC_TIMER = None
        _LAST_RESYNC_AT = time.monotonic()
    resync_pending_mirrors()


class MirrorDiagnostics:
    """Counts and recent failures reported by the mirror (bounded, thread-safe)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        global _LAST_RESYNC_AT, _CATCHUP_CONTINUATION, _CATCHUP_RESCANNING, _CATCHUP_THREAD
        with self._lock:
            self.counts = {"retained": 0, "invalidated": 0, "failed": 0, "anchor_none": 0}
            self.failures: deque = deque(maxlen=FAILURE_LOG_LIMIT)
        with _IDLE:
            _SEEN_RETAIN.clear()
            _INVALIDATED.clear()
            _TOMBSTONES.clear()
            _PENDING_RESYNC.clear()
            _PENDING_RESYNC_BYTES.clear()
            _RESYNC_WATCH.clear()
            _RESYNC_OUTCOMES.clear()
            _CATCHUP_CONTINUATION = None
            _CATCHUP_RESCANNING = False
            _CATCHUP_THREAD = None
            _LAST_RESYNC_AT = 0.0
            _cancel_resync_timer_unlocked()

    def record(self, key: str, **extra: Any) -> None:
        with self._lock:
            self.counts[key] = self.counts.get(key, 0) + 1

    def failure(self, entry_id: str, action: str, reason: str) -> None:
        with self._lock:
            self.counts["failed"] += 1
            self.failures.append({"entry_id": entry_id, "action": action, "reason": reason})

    def snapshot(self) -> dict:
        with self._lock:
            counts = dict(self.counts)
            failures = list(self.failures)
        return {
            "counts": counts,
            "failures": failures,
            "pending_resync_count": pending_resync_count(),
            "pending_resync_bytes": pending_resync_bytes(),
        }


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
        key = (bank, entry_id)
        reason = "hindsight_unreachable"
        for attempt in range(1, max(1, attempts) + 1):
            try:
                if action == "retain":
                    item = retain_item(entry)
                    ensure_layers(client, bank)
                    client.retain(bank, [item])
                    _clear_pending_resync(key, for_retain=True)
                    DIAGNOSTICS.record("retained")
                    if item["tags"][0] == ANCHOR_NONE_TAG:
                        DIAGNOSTICS.record("anchor_none")
                else:
                    client.invalidate_document(bank, entry_id, _invalidation_reason(entry))
                    _clear_pending_resync(key, for_retain=False)
                    DIAGNOSTICS.record("invalidated")
                return {"status": "mirrored", "entry_id": entry_id, "action": action}
            except HindsightError as exc:
                reason = exc.reason
                if exc.reason == "hindsight_unconfigured" or attempt >= attempts:
                    break
                delay = backoff * attempt
                if exc.retry_after is not None and exc.retry_after > 0:
                    delay = max(delay, min(float(exc.retry_after), MAX_RETRY_AFTER_SECONDS))
                time.sleep(delay)
        raise HindsightError(reason)
    except Exception as exc:  # canonical writes must survive index outages
        reason = exc.reason if isinstance(exc, HindsightError) else "hindsight_mirror_failed"
        if reason != "hindsight_unconfigured":
            _record_pending_resync(entry)
        DIAGNOSTICS.failure(entry_id, action, reason)
        _LOG.warning("memory_mirror_failed entry_id=%s action=%s reason=%s", entry_id, action, reason)
        return {"status": "failed", "entry_id": entry_id, "action": action, "reason": reason}


def _pending_keys_unlocked() -> int:
    return sum(1 for k, q in _PENDING.items() if k not in _IN_FLIGHT and q)


def _bound_map_unlocked(mapping: Dict[Tuple[str, str], Any]) -> None:
    while len(mapping) > FAILURE_LOG_LIMIT:
        mapping.pop(next(iter(mapping)), None)


def _enqueue_entry_unlocked(key: Tuple[str, str], entry: Any, *, from_resync: bool = False) -> bool:
    queue = _PENDING.get(key)
    indexable = is_indexable(entry)
    if not indexable:
        _TOMBSTONES[key] = entry
        _bound_map_unlocked(_TOMBSTONES)
        if key in _PENDING_RESYNC:
            _set_pending_resync_unlocked(key, entry)
    elif from_resync:
        if key in _TOMBSTONES:
            return False
        existing_resync = _PENDING_RESYNC.get(key)
        if existing_resync is not None and not is_indexable(existing_resync):
            return False
    else:
        _TOMBSTONES.pop(key, None)
    if queue is None and _pending_keys_unlocked() >= max(1, MAX_PENDING_MIRRORS):
        return False
    if indexable:
        _SEEN_RETAIN[key] = None
        _bound_map_unlocked(_SEEN_RETAIN)
    elif key not in _SEEN_RETAIN and not any(is_indexable(item) for item in (queue or ())):
        _INVALIDATED[key] = entry
        _bound_map_unlocked(_INVALIDATED)
    if queue is not None:
        for idx, existing in enumerate(queue):
            if is_indexable(existing) == indexable:
                queue[idx] = entry
                if indexable and any(not is_indexable(item) for item in queue):
                    _INVALIDATED.pop(key, None)
                return True
        if indexable and any(not is_indexable(item) for item in queue):
            idx = next(i for i, item in enumerate(queue) if not is_indexable(item))
            queue.insert(idx, entry)
            _INVALIDATED.pop(key, None)
        else:
            queue.append(entry)
        return True
    _PENDING[key] = deque([entry])
    return True


def _ensure_workers_unlocked() -> List[Any]:
    global _ACTIVE_WORKERS
    if _REBUILDING:
        return []
    unclaimed = sum(1 for k, q in _PENDING.items() if k not in _IN_FLIGHT and q)
    free_workers = max(0, _ACTIVE_WORKERS - len(_IN_FLIGHT))
    needed = min(unclaimed - free_workers, _configured_mirror_workers() - _ACTIVE_WORKERS)
    for _ in range(max(0, needed)):
        _ACTIVE_WORKERS += 1
        try:
            threading.Thread(target=_worker_loop, name="memory-mirror", daemon=True).start()
        except Exception:  # thread exhaustion must not fail the canonical write
            _ACTIVE_WORKERS -= 1
            if _ACTIVE_WORKERS == 0:
                failed: List[Any] = []
                for key in [k for k in list(_PENDING) if k not in _IN_FLIGHT]:
                    failed.extend(_PENDING.pop(key, ()))
                _IDLE.notify_all()
                return failed
            break
    return []


def _ensure_catchup_worker_unlocked() -> None:
    global _CATCHUP_RESCANNING, _CATCHUP_THREAD, _CATCHUP_CONTINUATION
    if _REBUILDING or _CATCHUP_RESCANNING or _CATCHUP_CONTINUATION is None:
        return
    is_continuation = bool(_CATCHUP_CONTINUATION[4]) if len(_CATCHUP_CONTINUATION) > 4 else True
    if is_continuation and (_PENDING or _IN_FLIGHT):
        return
    _CATCHUP_RESCANNING = True
    try:
        thread = threading.Thread(target=_catchup_worker_loop, name="memory-catchup", daemon=True)
        _CATCHUP_THREAD = thread
        thread.start()
    except Exception:
        _CATCHUP_RESCANNING = False
        _CATCHUP_THREAD = None
        _CATCHUP_CONTINUATION = None
        _IDLE.notify_all()


def schedule_mirror(entry: Any) -> bool:
    """Queue a mirror of an already-persisted canonical entry; never blocks, never raises."""
    key = (bank_for(entry), canonical_id(entry))
    accepted = False
    failed_to_start: List[Any] = []
    with _IDLE:
        accepted = _enqueue_entry_unlocked(key, entry)
        if accepted:
            failed_to_start = _ensure_workers_unlocked()
    if not accepted:
        action = "retain" if is_indexable(entry) else "invalidate"
        entry_id = canonical_id(entry)
        DIAGNOSTICS.failure(entry_id, action, "hindsight_mirror_queue_full")
        _LOG.warning("memory_mirror_failed entry_id=%s action=%s reason=hindsight_mirror_queue_full", entry_id, action)
        return False
    for item in failed_to_start:
        action = "retain" if is_indexable(item) else "invalidate"
        if get_hindsight_client() is not None:
            _record_pending_resync(item)
        DIAGNOSTICS.failure(canonical_id(item), action, "hindsight_mirror_failed")
    return not failed_to_start


def resync_pending_mirrors(*, client: Any = None) -> dict:
    """Retry entries in the bounded in-memory resync map through the shared bounded worker pool."""
    global _RESYNC_FAILURE_STREAK, _RESYNC_IN_PROGRESS, _RESYNC_CLIENT
    batch_keys: List[Tuple[str, str]] = []
    failed_to_start: List[Any] = []
    with _IDLE:
        if _REBUILDING or _RESYNC_IN_PROGRESS:
            if _PENDING_RESYNC and not _RESYNC_IN_PROGRESS:
                _arm_resync_timer_unlocked()
            return {"retried": 0, "mirrored": 0, "failed": 0, "pending_resync_count": len(_PENDING_RESYNC)}
        _RESYNC_IN_PROGRESS = True
        _RESYNC_CLIENT = client
        for key in list(_PENDING_RESYNC):
            if key in _IN_FLIGHT or _PENDING.get(key):
                continue
            entry = _PENDING_RESYNC.get(key)
            if entry is None:
                continue
            if _enqueue_entry_unlocked(key, entry, from_resync=True):
                batch_keys.append(key)
                _RESYNC_OUTCOMES.pop(key, None)
                _RESYNC_WATCH.add(key)
        if batch_keys:
            failed_to_start = _ensure_workers_unlocked()
    try:
        for item in failed_to_start:
            action = "retain" if is_indexable(item) else "invalidate"
            if (client or get_hindsight_client()) is not None:
                _record_pending_resync(item)
            DIAGNOSTICS.failure(canonical_id(item), action, "hindsight_mirror_failed")
        if batch_keys and not failed_to_start:
            deadline = time.monotonic() + DEFAULT_FLUSH_TIMEOUT_SECONDS
            with _IDLE:
                while any(k in _IN_FLIGHT or _PENDING.get(k) for k in batch_keys):
                    if _REBUILDING:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    _IDLE.wait(remaining)
                    _ensure_workers_unlocked()
    finally:
        with _IDLE:
            retried = len(batch_keys)
            mirrored = 0
            failed = 0
            for key in batch_keys:
                _RESYNC_WATCH.discard(key)
                outcome = _RESYNC_OUTCOMES.pop(key, None)
                if outcome == "mirrored" and key not in _PENDING_RESYNC:
                    mirrored += 1
                else:
                    failed += 1
            if failed > 0 and _PENDING_RESYNC:
                _RESYNC_FAILURE_STREAK += 1
            elif mirrored > 0:
                _RESYNC_FAILURE_STREAK = 0
            _RESYNC_CLIENT = None
            _RESYNC_IN_PROGRESS = False
            if _PENDING_RESYNC:
                _arm_resync_timer_unlocked()
            else:
                _cancel_resync_timer_unlocked()
            remaining_count = len(_PENDING_RESYNC)
            _IDLE.notify_all()
    return {"retried": retried, "mirrored": mirrored, "failed": failed, "pending_resync_count": remaining_count}


def _is_entry_mirrored(client: Any, bank: str, doc_id: str) -> bool:
    key = (bank, doc_id)
    with _IDLE:
        if key in _PENDING_RESYNC:
            return False
    memory_units_fn = getattr(client, "memory_units", None)
    if callable(memory_units_fn):
        try:
            return bool(memory_units_fn(bank, doc_id))
        except Exception:
            return False
    return False


def _has_tombstone_unlocked(key: Tuple[str, str]) -> bool:
    if key in _TOMBSTONES or key in _INVALIDATED:
        return True
    if any(not is_indexable(item) for item in _PENDING.get(key, ())):
        return True
    existing_resync = _PENDING_RESYNC.get(key)
    return existing_resync is not None and not is_indexable(existing_resync)


def _record_tombstone_unlocked(key: Tuple[str, str], entry: Any) -> None:
    _TOMBSTONES[key] = entry
    _bound_map_unlocked(_TOMBSTONES)
    existing_resync = _PENDING_RESYNC.get(key)
    if existing_resync is not None and is_indexable(existing_resync):
        _PENDING_RESYNC.pop(key, None)
        _PENDING_RESYNC_BYTES.pop(key, None)
        if not _PENDING_RESYNC:
            _cancel_resync_timer_unlocked()


def _revalidate_canonical_entry(store: Any, doc_id: str, snapshot_entry: Any) -> Optional[Any]:
    path = getattr(store, "_path", None)
    load_fn = getattr(store, "_load", None)
    lock = getattr(store, "_lock", None)
    if path is not None and callable(load_fn):
        try:
            if path.exists():
                if lock is not None:
                    with lock:
                        load_fn(path)
                else:
                    load_fn(path)
        except Exception:
            pass
    get_fn = getattr(store, "get", None)
    if callable(get_fn):
        try:
            return get_fn(doc_id)
        except Exception:
            return snapshot_entry
    list_fn = getattr(store, "list", None)
    if callable(list_fn):
        try:
            for item in list_fn(active_only=False):
                if canonical_id(item) == doc_id:
                    return item
            return None
        except Exception:
            return snapshot_entry
    return snapshot_entry


def _try_schedule_catchup_entry(entry: Any) -> Tuple[str, List[Any]]:
    key = (bank_for(entry), canonical_id(entry))
    with _IDLE:
        if _has_tombstone_unlocked(key):
            return "tombstoned", []
        if key in _IN_FLIGHT or any(is_indexable(item) for item in _PENDING.get(key, ())):
            return "already_queued", []
        if not _enqueue_entry_unlocked(key, entry, from_resync=True):
            return "queue_full", []
        return "scheduled", _ensure_workers_unlocked()


CATCHUP_STORE_RESOLVE_ATTEMPTS = 3
CATCHUP_STORE_RESOLVE_BACKOFF_SECONDS = 0.05


def _resolve_catchup_store(store_or_factory: Any) -> Any:
    """Resolve a lazily built canonical store; a transient build/read failure must not drop the catch-up."""
    if not callable(store_or_factory):
        return store_or_factory
    for attempt in range(1, CATCHUP_STORE_RESOLVE_ATTEMPTS + 1):
        try:
            return store_or_factory()
        except Exception:
            if attempt >= CATCHUP_STORE_RESOLVE_ATTEMPTS:
                raise
            time.sleep(CATCHUP_STORE_RESOLVE_BACKOFF_SECONDS * attempt)
    return None


def _scan_catchup_batch(
    institutional_store: Any,
    persona_store: Any,
    resolved_client: Any,
    *,
    attempted_keys: set[Tuple[str, str]],
    is_continuation: bool = False,
) -> dict:
    global _CATCHUP_CONTINUATION
    resolved_institutional = _resolve_catchup_store(institutional_store)
    resolved_persona = _resolve_catchup_store(persona_store)
    scheduled = 0
    deferred = 0
    already_mirrored = 0
    inactive_skipped = 0
    for store in (resolved_institutional, resolved_persona):
        if store is None:
            continue
        if not is_continuation:
            archive_expired_fn = getattr(store, "archive_expired", None)
            if callable(archive_expired_fn):
                try:
                    archive_expired_fn()
                except Exception:
                    pass
        try:
            entries = list(store.list(active_only=False))
        except Exception:
            continue
        for entry in entries:
            bank = bank_for(entry)
            doc_id = canonical_id(entry)
            key = (bank, doc_id)
            if not is_indexable(entry):
                inactive_skipped += 1
                with _IDLE:
                    _record_tombstone_unlocked(key, entry)
                continue
            if is_continuation and key in attempted_keys:
                continue
            with _IDLE:
                if _has_tombstone_unlocked(key):
                    inactive_skipped += 1
                    continue
                already_queued = (
                    key in _IN_FLIGHT
                    or any(is_indexable(item) for item in _PENDING.get(key, ()))
                    or (is_continuation and key in _PENDING_RESYNC)
                )
                queue_full = (
                    deferred > 0
                    or (_PENDING.get(key) is None and _pending_keys_unlocked() >= max(1, MAX_PENDING_MIRRORS))
                )
            if already_queued:
                already_mirrored += 1
                continue
            if queue_full:
                deferred += 1
                continue
            if _is_entry_mirrored(resolved_client, bank, doc_id):
                already_mirrored += 1
                continue
            current = _revalidate_canonical_entry(store, doc_id, entry)
            if current is None or not is_indexable(current):
                inactive_skipped += 1
                if current is not None:
                    with _IDLE:
                        _record_tombstone_unlocked(key, current)
                continue
            outcome, failed_to_start = _try_schedule_catchup_entry(current)
            for item in failed_to_start:
                action = "retain" if is_indexable(item) else "invalidate"
                if (resolved_client or get_hindsight_client()) is not None:
                    _record_pending_resync(item)
                DIAGNOSTICS.failure(canonical_id(item), action, "hindsight_mirror_failed")
            if outcome == "scheduled" and not failed_to_start:
                scheduled += 1
                attempted_keys.add(key)
            elif outcome == "already_queued":
                already_mirrored += 1
            elif outcome == "tombstoned":
                inactive_skipped += 1
            else:
                deferred += 1
    failed_continuation_start: List[Any] = []
    with _IDLE:
        if deferred > 0:
            _CATCHUP_CONTINUATION = (resolved_institutional, resolved_persona, resolved_client, attempted_keys, True)
            failed_continuation_start = _ensure_workers_unlocked()
            _ensure_catchup_worker_unlocked()
        elif _CATCHUP_CONTINUATION is not None and bool(_CATCHUP_CONTINUATION[4] if len(_CATCHUP_CONTINUATION) > 4 else True):
            _CATCHUP_CONTINUATION = None
    for item in failed_continuation_start:
        action = "retain" if is_indexable(item) else "invalidate"
        if (resolved_client or get_hindsight_client()) is not None:
            _record_pending_resync(item)
        DIAGNOSTICS.failure(canonical_id(item), action, "hindsight_mirror_failed")
    return {
        "status": "ok",
        "scheduled": scheduled,
        "deferred": deferred,
        "already_mirrored": already_mirrored,
        "inactive_skipped": inactive_skipped,
        "pending_resync_count": pending_resync_count(),
    }


def schedule_catch_up_mirrors(institutional_store: Any, persona_store: Any, *, client: Any = None) -> dict:
    """Schedule an idempotent non-destructive startup catch-up scan in bounded background work."""
    global _CATCHUP_CONTINUATION
    resolved_client = client or get_hindsight_client()
    if resolved_client is None:
        return {
            "status": "skipped",
            "reason": "hindsight_unconfigured",
            "scheduled": 0,
            "deferred": 0,
            "already_mirrored": 0,
            "inactive_skipped": 0,
            "pending_resync_count": pending_resync_count(),
        }
    with _IDLE:
        _CATCHUP_CONTINUATION = (institutional_store, persona_store, resolved_client, set(), False)
        _ensure_catchup_worker_unlocked()
    return {
        "status": "scheduled",
        "scheduled": 0,
        "deferred": 0,
        "already_mirrored": 0,
        "inactive_skipped": 0,
        "pending_resync_count": pending_resync_count(),
    }


def catch_up_mirrors(institutional_store: Any, persona_store: Any, *, client: Any = None) -> dict:
    """Idempotent non-destructive startup catch-up: schedule active canonical entries missing or unmirrored.

    Never calls ``delete_bank``. Safe to invoke repeatedly.
    """
    resolved_client = client or get_hindsight_client()
    if resolved_client is None:
        return {
            "status": "skipped",
            "reason": "hindsight_unconfigured",
            "scheduled": 0,
            "deferred": 0,
            "already_mirrored": 0,
            "inactive_skipped": 0,
            "pending_resync_count": pending_resync_count(),
        }
    return _scan_catchup_batch(
        institutional_store,
        persona_store,
        resolved_client,
        attempted_keys=set(),
        is_continuation=False,
    )


resync_missing_mirrors = catch_up_mirrors
catch_up_unmirrored = catch_up_mirrors


def _catchup_worker_loop() -> None:
    global _CATCHUP_RESCANNING, _CATCHUP_THREAD, _CATCHUP_CONTINUATION
    current_thread = threading.current_thread()
    try:
        while True:
            with _IDLE:
                if _REBUILDING or _CATCHUP_CONTINUATION is None:
                    return
                is_cont = bool(_CATCHUP_CONTINUATION[4]) if len(_CATCHUP_CONTINUATION) > 4 else True
                if is_cont and (_PENDING or _IN_FLIGHT):
                    return
                continuation = _CATCHUP_CONTINUATION
                _CATCHUP_CONTINUATION = None
            try:
                istore, pstore, cclient, attempted = continuation[:4]
                is_cont = bool(continuation[4]) if len(continuation) > 4 else True
                _scan_catchup_batch(
                    istore,
                    pstore,
                    cclient,
                    attempted_keys=attempted,
                    is_continuation=is_cont,
                )
            except Exception:
                with _IDLE:
                    _CATCHUP_CONTINUATION = None
                return
    finally:
        with _IDLE:
            if _CATCHUP_THREAD is current_thread or _CATCHUP_THREAD is None:
                _CATCHUP_RESCANNING = False
                _CATCHUP_THREAD = None
            _ensure_workers_unlocked()
            _ensure_catchup_worker_unlocked()
            _IDLE.notify_all()


def _worker_loop() -> None:
    global _ACTIVE_WORKERS
    while True:
        with _IDLE:
            if _REBUILDING:
                _ACTIVE_WORKERS -= 1
                _IDLE.notify_all()
                return
            key = next((k for k, q in _PENDING.items() if k not in _IN_FLIGHT and q), None)
            if key is not None:
                _IN_FLIGHT.add(key)
            else:
                _ACTIVE_WORKERS -= 1
                _ensure_catchup_worker_unlocked()
                _IDLE.notify_all()
                return
        _drain(key)


def _drain(key: Tuple[str, str]) -> None:
    invalidated: Any = None
    try:
        while True:
            with _IDLE:
                if _REBUILDING:
                    if not _PENDING.get(key):
                        _PENDING.pop(key, None)
                    _IN_FLIGHT.discard(key)
                    _IDLE.notify_all()
                    return
                queue = _PENDING.get(key)
                if not queue:
                    _PENDING.pop(key, None)
                    _IN_FLIGHT.discard(key)
                    _IDLE.notify_all()
                    return
                entry = queue.popleft()
            res = mirror_entry(entry)
            with _IDLE:
                if key in _RESYNC_WATCH:
                    _RESYNC_OUTCOMES[key] = res.get("status", "failed")
            if not is_indexable(entry):
                invalidated = entry
            else:
                with _IDLE:
                    late_invalidated = _INVALIDATED.pop(key, None) or invalidated
                    has_queued_invalidation = any(not is_indexable(item) for item in _PENDING.get(key, ()))
                if late_invalidated is not None and not has_queued_invalidation:
                    res_inv = mirror_entry(late_invalidated)
                    with _IDLE:
                        if key in _RESYNC_WATCH:
                            _RESYNC_OUTCOMES[key] = res_inv.get("status", "failed")
    except Exception:
        with _IDLE:
            if key in _RESYNC_WATCH:
                _RESYNC_OUTCOMES[key] = "failed"
            _PENDING.pop(key, None)
            _IN_FLIGHT.discard(key)
            _IDLE.notify_all()


def _wait_drained(timeout: float = DEFAULT_FLUSH_TIMEOUT_SECONDS) -> bool:
    deadline = time.monotonic() + timeout
    current_thread = threading.current_thread()
    with _IDLE:
        on_catchup_thread = current_thread is _CATCHUP_THREAD
        _ensure_workers_unlocked()
        if not on_catchup_thread:
            _ensure_catchup_worker_unlocked()
        while (
            _PENDING
            or _IN_FLIGHT
            or (not on_catchup_thread and (_CATCHUP_CONTINUATION is not None or _CATCHUP_RESCANNING))
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            _IDLE.wait(remaining)
            on_catchup_thread = current_thread is _CATCHUP_THREAD
            _ensure_workers_unlocked()
            if not on_catchup_thread:
                _ensure_catchup_worker_unlocked()
    return True


def flush_mirrors(timeout: float = DEFAULT_FLUSH_TIMEOUT_SECONDS) -> bool:
    """Wait until every queued mirror finished (tests, rebuild, shutdown). True if drained."""
    return _wait_drained(timeout)


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
    global _REBUILDING, _REBUILD_CLIENT
    report: Dict[str, Any] = {"banks": 0, "retained": 0, "inactive_skipped": 0, "anchor_none": 0,
                              "failures": [], "bank_counts": {}}
    with _REBUILD_LOCK:
        if not flush_mirrors():  # queued post-persistence mirrors must not race the bank drop
            report["failures"].append({"reason": "hindsight_mirror_drain_timeout"})
            return report
        deadline = time.monotonic() + DEFAULT_FLUSH_TIMEOUT_SECONDS
        with _IDLE:
            _REBUILDING += 1
            while _IN_FLIGHT or _CATCHUP_RESCANNING:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _REBUILDING -= 1
                    _ensure_workers_unlocked()
                    _ensure_catchup_worker_unlocked()
                    _IDLE.notify_all()
                    report["failures"].append({"reason": "hindsight_mirror_drain_timeout"})
                    return report
                _IDLE.wait(remaining)
        resolved_client = client or get_hindsight_client()
        _REBUILD_CLIENT = resolved_client
        try:
            if resolved_client is None:
                report["failures"].append({"reason": "hindsight_unconfigured"})
                return report
            by_bank: Dict[str, List[Any]] = {}
            expired_keys: set[Tuple[str, str]] = set()
            for store in (institutional_store, persona_store):
                for expired_entry in store.archive_expired() or []:
                    expired_keys.add((bank_for(expired_entry), canonical_id(expired_entry)))
                for entry in store.list(active_only=False):
                    by_bank.setdefault(bank_for(entry), [])
                    if is_indexable(entry):
                        by_bank[bank_for(entry)].append(entry)
                    else:
                        report["inactive_skipped"] += 1
            if expired_keys:
                with _IDLE:
                    for key in expired_keys:
                        queue = _PENDING.get(key)
                        if queue is not None and all(not is_indexable(item) for item in queue):
                            _PENDING.pop(key, None)
            for bank, entries in sorted(by_bank.items()):
                try:
                    resolved_client.delete_bank(bank)
                    forget_layers(bank)
                except HindsightError as exc:
                    report["failures"].append({"bank": bank, "reason": exc.reason})
                    continue
                with _IDLE:
                    for key in [k for k in _INVALIDATED if k[0] == bank]:
                        _INVALIDATED.pop(key, None)
                    for key in [k for k in _TOMBSTONES if k[0] == bank]:
                        _TOMBSTONES.pop(key, None)
                report["banks"] += 1
                retained = 0
                for entry in entries:
                    entry_id = canonical_id(entry)
                    item = retain_item(entry)
                    try:
                        ensure_layers(resolved_client, bank)
                        resolved_client.retain(bank, [item])
                    except HindsightError as exc:
                        report["failures"].append({"bank": bank, "entry_id": entry_id, "reason": exc.reason})
                        continue
                    _clear_pending_resync((bank, entry_id))
                    retained += 1
                    report["anchor_none"] += int(item["tags"][0] == ANCHOR_NONE_TAG)
                report["retained"] += retained
                report["bank_counts"][bank] = retained
        finally:
            failed_to_start: List[Any] = []
            with _IDLE:
                _REBUILDING -= 1
                has_pending = bool(_PENDING or _IN_FLIGHT or _CATCHUP_CONTINUATION is not None or _CATCHUP_RESCANNING)
                if not _REBUILDING:
                    failed_to_start = _ensure_workers_unlocked()
                    _ensure_catchup_worker_unlocked()
                _IDLE.notify_all()
            for item in failed_to_start:
                action = "retain" if is_indexable(item) else "invalidate"
                DIAGNOSTICS.failure(canonical_id(item), action, "hindsight_mirror_failed")
            try:
                if has_pending and not _wait_drained(DEFAULT_FLUSH_TIMEOUT_SECONDS):
                    report["failures"].append({"reason": "hindsight_mirror_drain_timeout"})
            finally:
                _REBUILD_CLIENT = None
        return report


# ---------------------------------------------------------------------------
# Recall helpers (H6)
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
    """Canonical ids cited by a reflect-shaped payload (``based_on`` memories)."""
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
