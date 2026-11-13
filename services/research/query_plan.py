"""Weekly, deterministic collector proposals. Application is a reviewed policy PR.

Only persona demand mandate/dataset fields enter vocabulary matching. Neither
fetched documents nor model output is an input. The collector has no write path
back to this store, and this module never writes fetch-policy.json.
"""
from __future__ import annotations

import difflib
import fcntl
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

COLLECTOR_DIR = Path(__file__).resolve().parents[2] / "deploy" / "collector"
QUERY_VOCABULARY_PATH = COLLECTOR_DIR / "query_vocabulary.json"
FETCH_POLICY_PATH = COLLECTOR_DIR / "fetch-policy.json"
QUERY_SOURCE_IDS = ("collector-gdelt", "collector-paper-arxiv", "collector-paper-openalex")


class QueryPlanError(ValueError):
    pass


def _read_inputs():
    try:
        vocabulary_bytes = QUERY_VOCABULARY_PATH.read_bytes()
        policy_bytes = FETCH_POLICY_PATH.read_bytes()
        vocabulary, policy = json.loads(vocabulary_bytes), json.loads(policy_bytes)
        terms = vocabulary["terms"]
        if vocabulary["schema_version"] != "collector_query_vocabulary.v1" or not isinstance(terms, dict) or not terms:
            raise ValueError("invalid fixed vocabulary")
        for term, aliases in terms.items():
            if (not isinstance(term, str) or not term or any(c in term for c in '\"():*')
                    or not isinstance(aliases, list) or not aliases
                    or not all(isinstance(alias, str) and alias for alias in aliases)):
                raise ValueError("invalid fixed vocabulary entry")
        for sid in QUERY_SOURCE_IDS:
            current = policy["sources"][sid]["query_terms"]
            if (not isinstance(current, list) or not 1 <= len(current) <= 12
                    or not all(isinstance(t, str) and t in terms for t in current)
                    or len(set(current)) != len(current)):
                raise ValueError(f"invalid query_terms for {sid}")
        return terms, policy, policy_bytes, vocabulary_bytes
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise QueryPlanError("query proposal unavailable: missing or invalid committed vocabulary/policy") from exc


def _matches(alias: str, text: str) -> bool:
    # Latin aliases match whole words ("value" must not match "devalue").
    # Chinese aliases can be embedded in a sentence without word separators.
    if alias.isascii():
        return re.search(r"(?<!\w)" + re.escape(alias.casefold()) + r"(?!\w)", text) is not None
    return alias.casefold() in text


def build_proposal(tenant_id: str, demands: list[Mapping[str, Any]], *, now: datetime) -> dict[str, Any] | None:
    open_demands = sorted((d for d in demands if d.get("tenant_id") == tenant_id and d.get("status") == "open"
                           and datetime.fromisoformat(d["expires_at"].replace("Z", "+00:00")) > now),
                          key=lambda d: d["demand_id"])
    if not open_demands:
        return None
    terms, policy, policy_bytes, vocabulary_bytes = _read_inputs()
    matches: dict[str, list[str]] = {}
    for demand in open_demands:
        text = " ".join([str(demand.get("mandate") or ""), *demand.get("missing_datasets", [])]).casefold()
        for term, aliases in terms.items():
            if any(_matches(alias, text) for alias in aliases):
                matches.setdefault(term, []).append(demand["demand_id"])
    ranked = sorted(matches, key=lambda term: (-len(matches[term]), term))
    changes, patch = {}, []
    # Deep copy the policy; only the three API query_terms fields may change.
    candidate = json.loads(policy_bytes)
    for sid in QUERY_SOURCE_IDS:
        before = policy["sources"][sid]["query_terms"]
        after = before + [term for term in ranked if term not in before][:12 - len(before)]
        if before != after:
            changes[sid] = {"before": before, "after": after}
            path = f"/sources/{sid}/query_terms"
            patch.extend([{"op": "test", "path": path, "value": before},
                          {"op": "replace", "path": path, "value": after}])
            candidate["sources"][sid]["query_terms"] = after
    candidate_text = json.dumps(candidate, ensure_ascii=False, indent=2) + "\n" if changes else policy_bytes.decode()
    diff = "".join(difflib.unified_diff(policy_bytes.decode().splitlines(keepends=True), candidate_text.splitlines(keepends=True),
                                      fromfile="a/deploy/collector/fetch-policy.json", tofile="b/deploy/collector/fetch-policy.json"))
    year, week, _ = now.isocalendar()
    return {
        "schema_version": "collector_query_plan.v1", "week": f"{year}-W{week:02d}",
        "tenant_id": tenant_id, "demand_ids": [d["demand_id"] for d in open_demands],
        "matched_terms": matches, "changes": changes, "json_patch": patch, "fetch_policy_diff": diff,
        "policy_sha256": hashlib.sha256(policy_bytes).hexdigest(),
        "vocabulary_sha256": hashlib.sha256(vocabulary_bytes).hexdigest(),
        "status": "proposed" if changes else "no_change", "paper_only": True,
        "application": "Coordinator reviews this diff, rechecks open demands and the policy/vocabulary hashes, opens a fetch-policy.json PR, then deploys the merged policy. No automatic application.",
    }


class QueryPlanStore:
    """One durable proposal per tenant/ISO week; refresh as that week's demands change.

    Identical inputs are idempotent across daily cycle runs/restarts. A changed
    proposal has a new content hash and revision and needs a fresh policy review.
    Read-only task projections cannot be dispatched as research executions.
    """

    def __init__(self, path: Path | str, *, now=None):
        self.path = Path(path)
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _load(self):
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def refresh(self, tenant_id: str, demands: list[Mapping[str, Any]]) -> dict[str, Any] | None:
        now = self._now()
        proposal = build_proposal(tenant_id, demands, now=now)
        if proposal is None:
            return None
        digest = hashlib.sha256(json.dumps(proposal, sort_keys=True).encode()).hexdigest()
        task_id = "collector-query-plan-" + hashlib.sha256(tenant_id.encode()).hexdigest()[:20] + "-" + proposal["week"]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self._load()
            old = records.get(task_id)
            if old and old["query_plan"]["content_sha256"] == digest:
                return old
            timestamp = now.isoformat()
            proposal.update(content_sha256=digest, revision=(old["query_plan"]["revision"] + 1 if old else 1))
            task = {
                "id": task_id, "task_id": task_id, "tenant_id": tenant_id, "task_type": "collector_query_plan",
                "title": f"Collector query proposal {proposal['week']}",
                "objective": "Review the exact collector API fetch-policy diff from open persona demands.",
                "status": proposal["status"], "created_by": "persona-demand-cycle",
                "created_at": old["created_at"] if old else timestamp, "updated_at": timestamp,
                "source_refs": [{"type": "research_demand", "id": did} for did in proposal["demand_ids"]],
                "query_plan": proposal,
            }
            records[task_id] = task
            fd, temporary = tempfile.mkstemp(dir=self.path.parent)
            try:
                with os.fdopen(fd, "w") as handle:
                    json.dump(records, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return task

    def list_tasks(self):
        return list(self._load().values())

    def get_task(self, task_id: str):
        return self._load().get(task_id)
