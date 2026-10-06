"""Scheduled pattern review: the production source of ``scheduled_pattern`` reflections.

Closed episodes for one persona and tenant are read from the telemetry owner
with service authentication.  Only a set of at least two distinct closed
episodes is reviewed, through the existing :class:`TradeReflectionPipeline`.
Window rule (PERSONA_TRADE_JOURNAL_GAP.md section 7C): a review covers only
closed episodes no earlier scheduled_pattern reflection covers, needs at least
MIN_EPISODES of them, and takes at most MAX_REVIEW_EPISODES (oldest first).
Each reflection records ``covered_episode_ids`` so patterns never overlap and
idempotency keys on covered ids, not on volatile projection fields.  The
reflection is identified by its episode set, so it can never collide with a
per-episode reflection, and it carries no mutation authority.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from services.persona.trade_reflection_pipeline import (
    ReflectionRequest,
    TradeReflectionPipeline,
    facts_snapshot,
)

CLOSED_STATUSES = frozenset({"closed", "reflected", "force_closed"})
MIN_EPISODES = 2
MAX_REVIEW_EPISODES = 50
MAX_PAGES = 20
PAGE_SIZE = 100
PATTERN_NAMESPACE = uuid.UUID("5d1c0a0e-6b0f-5c53-9a62-7f0c3b0f6d11")

EpisodeLister = Callable[[str, str], list[dict[str, Any]]]


def pattern_identity(episode_ids: Iterable[str]) -> str:
    """Stable uuid over the whole episode set; schema-valid ``trade_episode_id``."""
    return str(uuid.uuid5(PATTERN_NAMESPACE, "\n".join(sorted(set(episode_ids)))))


def qualifying_episodes(rows: list[Mapping[str, Any]], persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
    """Distinct closed episodes owned by exactly this persona and tenant."""
    kept: dict[str, dict[str, Any]] = {}
    for row in rows:
        episode_id = row.get("trade_episode_id")
        if (
            isinstance(episode_id, str) and episode_id
            and row.get("persona_id") == persona_id
            and row.get("tenant_id") == tenant_id
            and row.get("status") in CLOSED_STATUSES
        ):
            kept[episode_id] = dict(row)
    return [kept[key] for key in sorted(kept)]


def telemetry_episode_lister(
    telemetry_url: str | None = None,
    token: str | None = None,
    urlopen_fn: Callable[..., Any] = urlopen,
) -> EpisodeLister:
    """List one persona's episode projections from telemetry with the service token."""
    base = (telemetry_url or os.getenv("PANTHEON_TELEMETRY_API_URL") or "").rstrip("/")
    secret = token if token is not None else os.getenv("PANTHEON_TELEMETRY_SERVICE_TOKEN", "")

    def lister(persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        if not base or not secret.strip():
            raise RuntimeError("telemetry service credential is not configured")
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(MAX_PAGES):
            query = {"persona_id": persona_id, "limit": PAGE_SIZE, **({"cursor": cursor} if cursor else {})}
            request = Request(
                f"{base}/api/telemetry/trade-episodes?{urlencode(query)}",
                headers={"Authorization": f"Bearer {secret.strip()}", "X-Tenant-Id": tenant_id},
                method="GET",
            )
            with urlopen_fn(request, timeout=10) as response:
                page = json.loads(response.read().decode("utf-8"))
            rows.extend(page.get("projections") or [])
            cursor = page.get("next_cursor")
            if not cursor:
                return rows
        raise RuntimeError("telemetry episode pagination did not terminate")

    return lister


def review_pattern(
    *,
    persona_id: str,
    tenant_id: str,
    existing: list[Mapping[str, Any]],
    list_episodes: EpisodeLister,
    pipeline: TradeReflectionPipeline,
) -> dict[str, Any]:
    """Plan one pattern review.

    Returns ``{"status": "no_op" | "unchanged", ...}`` without any provider call,
    or ``{"status": "reviewed", "artifact": ...}`` for the caller to persist.
    """
    covered = {
        episode_id for row in existing if row.get("trigger") == "scheduled_pattern"
        for episode_id in row.get("covered_episode_ids") or ()
    }
    pending = [
        row for row in qualifying_episodes(list_episodes(persona_id, tenant_id), persona_id, tenant_id)
        if row["trade_episode_id"] not in covered
    ]
    if len(pending) < MIN_EPISODES:
        return {
            "status": "unchanged" if covered else "no_op",
            "reason": "insufficient_closed_episodes",
            "qualifying_episodes": len(pending),
        }
    pending.sort(key=lambda row: (str(row.get("opened_at") or ""), row["trade_episode_id"]))
    episodes = pending[:MAX_REVIEW_EPISODES]
    episode_ids = tuple(sorted(row["trade_episode_id"] for row in episodes))
    identity = pattern_identity(episode_ids)
    facts = {"persona_id": persona_id, "tenant_id": tenant_id, "episodes": episodes}
    snapshot_ref, snapshot_hash, _ = facts_snapshot(facts)
    base = {"pattern_id": identity, "qualifying_episodes": len(episodes), "facts_snapshot_ref": snapshot_ref}
    missing = tuple(ref for row in episodes for ref in row.get("missing_refs") or ())
    artifact = pipeline.process(ReflectionRequest(
        request_id=f"pattern-{identity}-{snapshot_hash[-12:]}", persona_id=persona_id,
        trade_episode_ids=episode_ids, trigger="scheduled_pattern", facts=facts, missing_refs=missing,
    ))
    artifact["trade_episode_id"] = identity
    artifact["covered_episode_ids"] = list(episode_ids)
    return {"status": "reviewed", "artifact": artifact, **base}
