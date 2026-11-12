"""Persona strategy pool record (``persona_strategy_pool.v1``).

Design: SD_PERSONA_STRATEGY_POOL_AUTONOMY_2026-11-12 §4.1.

One append-only, versioned record per persona.  Every change appends a new
version; earlier versions stay readable and removed members stay in history.
Removing a member never touches the persona lifecycle (this module has no
dependency on the persona lifecycle writer at all).

Execution does not read members from this store; it reads the envelope
artifact (reviewer decision R6).  This store is the persona's book of
membership.

Fail-closed rules:
  * a non-removed member must be an approved registry entry whose checksum
    matches the supplied checksum, otherwise the change is refused and the
    refusal reason is recorded;
  * an unreachable / unconfigured registry is an availability failure, not a
    verdict -- nothing is written;
  * the first member is derived only from owner records (registry entries
    bound to the persona); zero or several candidates is a refusal, never a
    guess; no persona or artifact id is hard-coded.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request as UrllibRequest, urlopen

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "persona_strategy_pool.v1"
STAGES = ("trial", "active", "removed")
_WEIGHT_EPSILON = 1e-9


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_digest(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class PoolError(ValueError):
    status_code = 422


class PoolNotFound(PoolError):
    status_code = 404


class PoolVersionConflict(PoolError):
    status_code = 409


class PoolMemberRefused(PoolError):
    """A member change was refused; ``reason`` has been recorded."""

    def __init__(self, reason: str, *, artifact_ref: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.artifact_ref = artifact_ref


class RegistryUnavailable(PoolError):
    status_code = 503


class PoolMember(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_ref: str = Field(min_length=1)
    checksum: str = Field(min_length=1)
    stage: str
    risk_weight: float
    baseline_ref: str | None = None
    joined_at: str = Field(min_length=1)
    parent_ref: str | None = None
    demand_ref: str | None = None
    removed_at: str | None = None
    removal_reason: str | None = None

    @field_validator("stage")
    @classmethod
    def _stage(cls, value: str) -> str:
        if value not in STAGES:
            raise ValueError(f"stage must be one of {list(STAGES)}")
        return value

    @field_validator("risk_weight")
    @classmethod
    def _weight(cls, value: float) -> float:
        if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise ValueError("risk_weight must be a finite number in [0, 1]")
        return float(value)

    @model_validator(mode="after")
    def _consistency(self) -> "PoolMember":
        if self.stage == "active" and not (self.baseline_ref or "").strip():
            raise ValueError("an active member requires baseline_ref")
        if self.stage == "removed" and not (self.removal_reason or "").strip():
            raise ValueError("a removed member requires removal_reason")
        return self


class AppendPoolVersionRequest(BaseModel):
    """The complete membership of the new version.

    ``expected_version`` is the version the caller read (0 when creating); a
    stale value is refused so concurrent writers cannot overwrite each other.
    """

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=0)
    members: list[PoolMember]
    change_reason: str = Field(min_length=1)
    actor_id: str | None = None


class PoolCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    artifact_ref: str = Field(min_length=1)
    checksum: str = Field(min_length=1)
    demand_ref: str | None = None
    parent_ref: str | None = None


class PoolDecisionRequest(BaseModel):
    """Authenticated paper owner supplies observed books and governed backtest inputs.

    Gate verdicts, calculated metrics, trial counters and thresholds are never
    accepted from the caller. Repeated attempts accumulate in pool history.
    """
    model_config = ConfigDict(extra="forbid")
    expected_version: int = Field(ge=1)
    execution_context: Literal["paper"]
    action: Literal["admit", "reweight", "evolution_trial", "evolution_compete"]
    candidate: PoolCandidate | None = None
    books: dict[str, dict[str, Any]] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    dataset: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _candidate_required(self):
        if self.action not in {"admit", "evolution_trial", "evolution_compete"} and self.candidate is not None:
            raise ValueError("only admission accepts a candidate")
        return self


class RegistryEntryReader(Protocol):
    def __call__(self, registry_id: str, tenant_id: str) -> Mapping[str, Any]: ...


class OwnerArtifactLister(Protocol):
    """Registry entries bound to a persona (owner records), approved only."""

    def __call__(self, persona_id: str, tenant_id: str) -> list[Mapping[str, Any]]: ...


def _http_json(path: str, tenant_id: str) -> Any:
    from services.service_token_file import configured_service_token

    url = (os.getenv("PERSONA_REGISTRY_BASE_URL") or "").rstrip("/")
    try:
        token = configured_service_token("PERSONA_REGISTRY_SERVICE_TOKEN")
    except RuntimeError as exc:
        raise RegistryUnavailable("registry read principal unavailable") from exc
    if not url or not token:
        raise RegistryUnavailable("registry owner URL and read principal are required")
    request = UrllibRequest(
        url + path,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "X-Tenant-Id": tenant_id},
        method="GET",
    )
    timeout = float(os.getenv("PERSONA_REGISTRY_TIMEOUT_SECONDS", "5"))
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator-configured URL
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise RegistryUnavailable(f"registry read failed: HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise RegistryUnavailable("registry read unavailable or malformed") from exc


def http_registry_entry_reader(registry_id: str, tenant_id: str) -> Mapping[str, Any]:
    payload = _http_json("/api/registry/entries/" + quote(registry_id, safe=""), tenant_id)
    if payload is None:
        return {}
    entry = payload.get("entry") if isinstance(payload, Mapping) else None
    if not isinstance(entry, Mapping) or entry.get("registry_id") != registry_id:
        raise RegistryUnavailable("registry returned a malformed or mismatched entry")
    return entry


def http_owner_artifact_lister(persona_id: str, tenant_id: str) -> list[Mapping[str, Any]]:
    """Approved paper execution artifacts the registry binds to this persona."""
    found: list[Mapping[str, Any]] = []
    offset = 0
    while True:
        payload = _http_json(
            f"/api/registry/strategy-artifacts?artifact_state=approved&limit=200&offset={offset}",
            tenant_id,
        )
        items = payload.get("items") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            raise RegistryUnavailable("registry catalog response is malformed")
        for row in items:
            registry_id = str((row or {}).get("registry_id") or "")
            if not registry_id:
                continue
            entry = http_registry_entry_reader(registry_id, tenant_id)
            metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
            if (
                metadata.get("persona_id") == persona_id
                and metadata.get("execution_context") == "paper"
                and entry.get("owner_tenant") in (tenant_id, None)
            ):
                found.append(entry)
        nxt = payload.get("next_page_token")
        if not nxt:
            return select_running_artifacts(found)
        offset = int(nxt)


def select_running_artifacts(entries: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Keep only the artifact(s) the persona actually runs.

    The provisioning owner approves a zero-capital fail-closed baseline and
    the running artifact under the same persona; the baseline is the running
    artifact's rollback target, not a running member.  Excluded:
      * entries flagged ``metadata.fail_closed_baseline``;
      * entries that another candidate names as its rollback target;
      * entries without the capital-binding owner reference;
      * entries the registry marks superseded.
    """
    rollback_ids = {
        str(ref)
        for e in entries
        for ref in (
            e.get("rollback_target"),
            (e.get("metadata") or {}).get("rollback_target_registry_id"),
        )
        if ref
    }
    kept: list[Mapping[str, Any]] = []
    for entry in entries:
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), Mapping) else {}
        if metadata.get("fail_closed_baseline") is True:
            continue
        if str(entry.get("registry_id")) in rollback_ids:
            continue
        if not metadata.get("persona_capital_binding_id"):
            continue
        if entry.get("superseded_by") or metadata.get("superseded_by"):
            continue
        kept.append(entry)
    return kept


def _verify_member(
    member: PoolMember, tenant_id: str, reader: RegistryEntryReader
) -> None:
    entry = reader(member.artifact_ref, tenant_id)
    if not entry:
        raise PoolMemberRefused("artifact is not a registry entry", artifact_ref=member.artifact_ref)
    state = str(getattr(entry.get("artifact_state"), "value", entry.get("artifact_state")) or "")
    if state != "approved":
        raise PoolMemberRefused(
            f"registry entry is not approved (state={state or 'unknown'})", artifact_ref=member.artifact_ref
        )
    if str(entry.get("checksum") or "") != member.checksum:
        raise PoolMemberRefused("checksum does not match the registry entry", artifact_ref=member.artifact_ref)


class PersonaStrategyPoolStore:
    """Append-only pool records over a compare-and-set record store."""

    def __init__(
        self,
        records: Any,
        *,
        registry_reader: RegistryEntryReader | None = None,
        owner_lister: OwnerArtifactLister | None = None,
        now: Callable[[], str] = _utc_now,
    ) -> None:
        self._records = records
        self._reader = registry_reader or http_registry_entry_reader
        self._lister = owner_lister or http_owner_artifact_lister
        self._now = now

    # -- reads -------------------------------------------------------------
    def _record(self, persona_id: str, tenant_id: str) -> dict[str, Any]:
        record = self._records.get(persona_id)
        if not record or record.get("tenant_id") != tenant_id or not record.get("versions"):
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        return record

    def current(self, persona_id: str, tenant_id: str) -> dict[str, Any]:
        return dict(self._record(persona_id, tenant_id)["versions"][-1])

    def versions(self, persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        record = self._records.get(persona_id)
        if not record or record.get("tenant_id") != tenant_id:
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        return [dict(v) for v in record["versions"]]

    def version(self, persona_id: str, tenant_id: str, number: int) -> dict[str, Any]:
        for item in self._record(persona_id, tenant_id)["versions"]:
            if item["pool_version"] == number:
                return dict(item)
        raise PoolNotFound(f"pool version {number} not found")

    def refusals(self, persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        record = self._records.get(persona_id)
        if not record or record.get("tenant_id") != tenant_id:
            return []
        return [dict(r) for r in record.get("refusals", [])]

    def member_baselines(self, persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        """Each non-removed member's own approved baseline (or explicit missing reason).

        Read-only; keyed by the immutable pool version so the association with
        the member's artifact/checksum is preserved.
        """
        from services.evolution.approved_baselines import resolve_member_baseline

        version = self.current(persona_id, tenant_id)
        out = []
        for member in version["members"]:
            if member["stage"] == "removed":
                continue
            entry = self._reader(member["artifact_ref"], tenant_id)
            out.append({
                "pool_version": version["pool_version"], "stage": member["stage"],
                "baseline": resolve_member_baseline(
                    tenant_id=tenant_id, persona_id=persona_id,
                    artifact_ref=member["artifact_ref"], checksum=member["checksum"], entry=entry),
            })
        return out

    # -- writes ------------------------------------------------------------
    def append(
        self,
        persona_id: str,
        tenant_id: str,
        request: AppendPoolVersionRequest,
        *,
        actor_id: str,
        decision: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        existing = self._records.get(persona_id)
        if existing is not None and existing.get("tenant_id") != tenant_id:
            # Never reveal or touch another tenant's record.
            raise PoolNotFound(f"no strategy pool for persona {persona_id!r}")
        versions = list(existing["versions"]) if existing else []
        current_number = len(versions)
        if request.expected_version != current_number:
            raise PoolVersionConflict(
                f"expected_version {request.expected_version} does not match current {current_number}"
            )
        previous = versions[-1] if versions else None
        try:
            self._validate_membership(previous, request.members, tenant_id,
                policy_authorized=bool(decision and decision.get("status") == "applied"))
        except PoolMemberRefused as exc:
            self._record_refusal(persona_id, tenant_id, request, actor_id, exc)
            raise
        body = {
            "schema_version": SCHEMA_VERSION,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "pool_version": current_number + 1,
            "members": [m.model_dump() for m in request.members],
            "change_reason": request.change_reason,
            "created_at": self._now(),
            "created_by": actor_id,
            "previous_digest": previous["digest"] if previous else None,
            "decision": decision,
        }
        body["digest"] = canonical_digest(body)
        for _ in range(8):
            record = {
                "persona_id": persona_id,
                "tenant_id": tenant_id,
                "versions": versions + [body],
                "refusals": list(existing.get("refusals", [])) if existing else [],
            }
            ok, _ = self._records.compare_and_set(persona_id, existing, record)
            if ok:
                break
            # Only a refusal journal entry may have landed meanwhile; a new
            # version means a real conflict.
            latest = self._records.get(persona_id)
            if latest is None or latest.get("tenant_id") != tenant_id or len(latest.get("versions", [])) != len(versions):
                raise PoolVersionConflict("pool changed concurrently; re-read and retry")
            existing = latest
        else:
            raise PoolVersionConflict("pool changed concurrently; re-read and retry")
        return dict(body)

    def _validate_membership(
        self, previous: Mapping[str, Any] | None, members: list[PoolMember], tenant_id: str,
        *, policy_authorized: bool = False,
    ) -> None:
        from pool_policy import load_policy

        policy = load_policy()
        if sum(m.stage != "removed" for m in members) > policy.member_ceiling:
            raise PoolMemberRefused("pool member safety ceiling exceeded")
        refs = [m.artifact_ref for m in members]
        if len(set(refs)) != len(refs):
            raise PoolMemberRefused("duplicate artifact_ref in membership")
        prior = {m["artifact_ref"]: m for m in (previous or {}).get("members", [])}
        by_ref = {m.artifact_ref: m for m in members}
        for ref in prior:
            if ref not in by_ref:
                raise PoolMemberRefused(
                    "a member cannot be dropped from history; mark it removed", artifact_ref=ref
                )
        total = 0.0
        for member in members:
            before = prior.get(member.artifact_ref)
            if before is not None:
                if before["stage"] == "removed" and member.stage != "removed":
                    raise PoolMemberRefused(
                        "a removed member cannot be re-activated; add a new member",
                        artifact_ref=member.artifact_ref,
                    )
                if before["checksum"] != member.checksum or before["joined_at"] != member.joined_at:
                    raise PoolMemberRefused(
                        "a member's checksum and joined_at are immutable", artifact_ref=member.artifact_ref
                    )
            if member.stage == "removed":
                continue
            total += member.risk_weight
            _verify_member(member, tenant_id, self._reader)
            if not policy_authorized:
                # The record API remains available for bootstrap, risk
                # reductions and removals. It cannot bypass automated policy
                # by inserting a member or increasing an allocation directly.
                if previous and (before is None or member.risk_weight > before["risk_weight"] + _WEIGHT_EPSILON
                                 or (before["stage"] == "trial" and member.stage == "active")):
                    raise PoolMemberRefused("admission or increased risk requires a paper pool policy decision",
                                            artifact_ref=member.artifact_ref)
                if member.stage == "trial" and member.risk_weight > policy.trial_risk_cap + _WEIGHT_EPSILON:
                    # An unchanged matured trial is already policy-attested.
                    if before is None or member.risk_weight != before["risk_weight"]:
                        raise PoolMemberRefused("unproven trial allocation exceeds policy cap", artifact_ref=member.artifact_ref)
        if total > 1.0 + _WEIGHT_EPSILON:
            raise PoolMemberRefused(f"risk weights of live members sum to {total:.6f}, above 1.0")

    def health(self, persona_id, tenant_id, books, observations):
        from pool_policy import load_policy, pool_health

        current = self.current(persona_id, tenant_id)
        return {"pool_version": current["pool_version"],
                **pool_health(current["members"], books, observations, load_policy())}

    def decide(self, persona_id, tenant_id, request: PoolDecisionRequest, *, actor_id):
        """Append even a refused decision, preserving membership and unused budget.

        This never publishes or applies an envelope: R6 still requires registry,
        Governance, DeploymentPlan and RuntimeBinding for execution of a version.
        """
        from pool_policy import load_policy, member_statistics, pool_health, reweight
        from services.evolution.approved_baselines import resolve_member_baseline
        from services.research.pool_admission import evaluate_admission

        current = self.current(persona_id, tenant_id)
        if current["pool_version"] != request.expected_version:
            raise PoolVersionConflict("pool changed; re-read before deciding")
        policy = load_policy()
        members = [dict(m) for m in current["members"]]
        count = max((int((v.get("decision") or {}).get("persona_trial_count", 0))
                     for v in self.versions(persona_id, tenant_id)), default=0)
        if request.action == "admit" and request.candidate is not None:
            count += 1
        decision = {"action": request.action, "status": "refused", "reasons": [],
                    "persona_trial_count": count, "policy": policy.model_dump(),
                    "candidate": request.candidate.model_dump() if request.candidate else None,
                    "policy_digest": canonical_digest(policy.model_dump()),
                    "health": pool_health(members, request.books, request.observations, policy),
                    "input_digest": canonical_digest({"books": request.books, "observations": request.observations,
                                                      "dataset": request.dataset}),
                    "book_sources": {ref: {key: book.get(key) for key in ("checksum", "source_ref", "basis", "dates")}
                                     for ref, book in request.books.items()},
                    "execution_context": "paper", "execution_status": "requires_approved_envelope"}
        now = self._now()
        try:
            if request.action in {"evolution_trial", "evolution_compete"}:
                candidate = request.candidate
                if candidate is None or not candidate.parent_ref:
                    raise ValueError("evolution_parent_required")
                parent = next((m for m in members if m["artifact_ref"] == candidate.parent_ref and m["stage"] == "active"), None)
                if parent is None:
                    raise ValueError("evolution_parent_not_active")
                entry = self._reader(candidate.artifact_ref, tenant_id)
                child_baseline = resolve_member_baseline(tenant_id=tenant_id, persona_id=persona_id,
                    artifact_ref=candidate.artifact_ref, checksum=candidate.checksum, entry=entry)
                if child_baseline["status"] != "approved":
                    raise ValueError("evolution_child_original_oos_not_approved:" + child_baseline["reason"])
                artifact = (entry.get("metadata") or {}).get("strategy_artifact") or {}
                if candidate.parent_ref not in (artifact.get("lineage") or {}).get("parent_registry_ids", []):
                    raise ValueError("evolution_child_lineage_mismatch")
                parent_entry = self._reader(parent["artifact_ref"], tenant_id)
                parent_baseline = resolve_member_baseline(tenant_id=tenant_id, persona_id=persona_id,
                    artifact_ref=parent["artifact_ref"], checksum=parent["checksum"], entry=parent_entry)
                if parent_baseline["status"] != "approved":
                    raise ValueError("evolution_parent_baseline_missing")
                if not set(child_baseline["validated_universe"]) <= set(parent_baseline["validated_universe"]):
                    raise ValueError("evolution_child_universe_widened")
                if request.action == "evolution_trial":
                    if any(m["artifact_ref"] == candidate.artifact_ref for m in members):
                        raise ValueError("candidate_already_in_pool_history")
                    members.append(PoolMember(artifact_ref=candidate.artifact_ref, checksum=candidate.checksum,
                        parent_ref=parent["artifact_ref"], stage="trial", risk_weight=0,
                        joined_at=now, baseline_ref=entry.get("approval_decision_id")).model_dump())
                else:
                    from services.evolution.pool_evolution_scheduler import member_days
                    from pool_policy import dated_series, sharpe
                    child = next((m for m in members if m["artifact_ref"] == candidate.artifact_ref
                                  and m["stage"] == "trial" and m.get("parent_ref") == parent["artifact_ref"]
                                  and m["checksum"] == candidate.checksum), None)
                    if child is None:
                        raise ValueError("evolution_child_not_trial")
                    child_book = request.books.get(child["artifact_ref"], {})
                    parent_book = request.books.get(parent["artifact_ref"], {})
                    days = member_days(child, child_book)
                    if len(days) < 60:
                        raise ValueError("evolution_child_needs_60_new_trading_days")
                    child_series = dated_series(child_book, child["checksum"])
                    parent_series = dated_series(parent_book, parent["checksum"])
                    if not set(days) <= parent_series.keys() or not set(days) <= child_series.keys():
                        raise ValueError("evolution_comparison_window_incomplete")
                    child_wins = sharpe([child_series[d] for d in days], policy.periods_per_year) > sharpe(
                        [parent_series[d] for d in days], policy.periods_per_year)
                    loser = parent if child_wins else child
                    if child_wins:
                        child.update(stage="active", risk_weight=parent["risk_weight"])
                    loser.update(stage="removed", risk_weight=0, removed_at=now,
                                 removal_reason="evolution_competition")
                    decision.update(removed_artifact_ref=loser["artifact_ref"], post_admission_trading_days=len(days))
                decision["status"] = "applied"
            elif request.action == "reweight":
                previous_stats = next(((v.get("decision") or {})["member_statistics"]
                    for v in reversed(self.versions(persona_id, tenant_id))
                    if "member_statistics" in (v.get("decision") or {})), {})
                members, stats = reweight(members, request.books, policy, now, previous_stats)
                decision.update(status="applied", member_statistics=stats)
            else:
                candidate = request.candidate
                if candidate is None:
                    raise ValueError("no_qualifying_candidate_budget_left_unused")
                live = [m for m in members if m["stage"] != "removed"]
                if candidate.artifact_ref in {m["artifact_ref"] for m in members}:
                    raise ValueError("candidate_already_in_pool_history")
                entries, baselines = {}, {}
                for ref, checksum in [(m["artifact_ref"], m["checksum"]) for m in live] + [(candidate.artifact_ref, candidate.checksum)]:
                    entries[ref] = self._reader(ref, tenant_id)
                    baseline = resolve_member_baseline(tenant_id=tenant_id, persona_id=persona_id,
                        artifact_ref=ref, checksum=checksum, entry=entries[ref])
                    if baseline["status"] != "approved":
                        raise ValueError(f"{ref}: {baseline['reason']}")
                    baselines[ref] = baseline
                # Until a separate permitted-universe owner is available, do
                # not infer authority to widen P: use the pool's approved universe.
                symbols = {s.split(".")[0] for m in live for s in baselines[m["artifact_ref"]]["validated_universe"]}
                if not {s.split(".")[0] for s in baselines[candidate.artifact_ref]["validated_universe"]} <= symbols:
                    raise ValueError("candidate_outside_pool_approved_universe")
                trial = PoolMember(artifact_ref=candidate.artifact_ref, checksum=candidate.checksum,
                    stage="trial", risk_weight=0, baseline_ref=entries[candidate.artifact_ref].get("approval_decision_id"),
                    joined_at=now, demand_ref=candidate.demand_ref).model_dump()
                if not trial["baseline_ref"]:
                    raise ValueError("candidate_gate_baseline_reference_missing")
                stats = {m["artifact_ref"]: member_statistics(m, request.books.get(m["artifact_ref"]), policy)
                         for m in live}
                candidate_stat = member_statistics(trial, request.books.get(candidate.artifact_ref), policy)
                if candidate_stat["drawdown"] + 1e-12 >= policy.remove_drawdown:
                    raise ValueError("candidate_virtual_book_drawdown_exceeds_removal_policy")
                # Risk-parity start from observed member volatility; caps
                # leave unused risk budget idle.
                inv = {ref: 1 / stat["volatility"] for ref, stat in {**stats, candidate.artifact_ref: candidate_stat}.items()}
                trial["risk_weight"] = min(policy.trial_risk_cap, inv[candidate.artifact_ref] / sum(inv.values()))
                health = decision["health"]
                if any(health[key]["status"] != "known" for key in ("gross_utilisation", "drawdown_utilisation")):
                    raise ValueError("risk_budget_observations_missing")
                last = request.observations[-1]
                full = max(sum(m["risk_weight"] for m in live),
                           health["gross_utilisation"]["value"], health["drawdown_utilisation"]["value"],
                           last["gross_notional"] / last["gross_limit"], last["drawdown"] / last["drawdown_limit"]) >= policy.budget_full - _WEIGHT_EPSILON
                proposed = [dict(m) for m in members]
                if full:
                    if not live:
                        raise ValueError("no_member_available_to_replace")
                    weakest = min(live, key=lambda m: stats[m["artifact_ref"]]["sharpe"])
                    if candidate_stat["sharpe"] <= stats[weakest["artifact_ref"]]["sharpe"]:
                        raise ValueError("full_budget_candidate_not_stronger_than_weakest")
                    for m in proposed:
                        if m["artifact_ref"] == weakest["artifact_ref"]:
                            trial["risk_weight"] = min(trial["risk_weight"], m["risk_weight"])
                            m.update(stage="removed", risk_weight=0, removed_at=now, removal_reason="replaced_by_stronger_trial")
                    decision["replaced_artifact_ref"] = weakest["artifact_ref"]
                elif len(live) >= policy.member_ceiling:
                    raise ValueError("pool_member_safety_ceiling")
                else:
                    trial["risk_weight"] = min(trial["risk_weight"], max(0, 1 - sum(m["risk_weight"] for m in live)))
                proposed.append(trial)
                evaluation = evaluate_admission(dataset=request.dataset, current=members, proposed=proposed,
                    candidate=trial, entries=entries, trial_count=count, policy=policy)
                decision["backtest"] = evaluation
                if not evaluation["admitted"]:
                    raise ValueError("; ".join(evaluation["reasons"]))
                members = proposed
                decision["status"] = "applied"
        except RegistryUnavailable:
            raise
        except ValueError as exc:
            decision["reasons"] = [str(exc)]
            members = [dict(m) for m in current["members"]]
        # Registry unavailability is 503, not a market verdict. A refused
        # policy decision with unchanged members still passes normal CAS and
        # registry integrity checks in the existing append implementation.
        return self.append(persona_id, tenant_id, AppendPoolVersionRequest(
            expected_version=request.expected_version, members=[PoolMember.model_validate(m) for m in members],
            change_reason=f"paper pool {request.action}: {decision['status']}; " + "; ".join(decision["reasons"])),
            actor_id=actor_id, decision=decision)

    def _record_refusal(
        self,
        persona_id: str,
        tenant_id: str,
        request: AppendPoolVersionRequest,
        actor_id: str,
        exc: PoolMemberRefused,
    ) -> None:
        for _ in range(8):
            current = self._records.get(persona_id)
            if current is not None and current.get("tenant_id") != tenant_id:
                return
            record: dict[str, Any] = (
                dict(current)
                if current is not None
                else {"persona_id": persona_id, "tenant_id": tenant_id, "versions": [], "refusals": []}
            )
            record["refusals"] = list(record.get("refusals", [])) + [
                {
                    "refused_at": self._now(),
                    "actor_id": actor_id,
                    "artifact_ref": exc.artifact_ref,
                    "reason": exc.reason,
                    "attempted_against_version": request.expected_version,
                    "change_reason": request.change_reason,
                }
            ]
            ok, _ = self._records.compare_and_set(persona_id, current, record)
            if ok:
                return
        raise PoolVersionConflict("could not record refusal under contention")

    # -- first member ------------------------------------------------------
    def seed_from_owner_records(
        self, persona_id: str, tenant_id: str, *, actor_id: str
    ) -> dict[str, Any]:
        """Version 1: the persona's one approved paper artifact becomes member 1.

        Everything is read from registry owner records.  Zero or several
        candidates, or a missing baseline reference, fails closed.
        """
        existing = self._records.get(persona_id)
        if existing is not None and existing.get("tenant_id") == tenant_id and existing["versions"]:
            raise PoolVersionConflict("pool already exists; append a version instead")
        candidates = list(self._lister(persona_id, tenant_id))
        if len(candidates) != 1:
            raise PoolMemberRefused(
                f"expected exactly one approved paper artifact bound to the persona, found {len(candidates)}"
            )
        entry = candidates[0]
        # The gate baseline is the approval decision that admitted this
        # artifact.  ``rollback_target`` is the prior no-trade rollback
        # artifact and is a different thing; never substitute it.
        baseline = str(entry.get("approval_decision_id") or "").strip()
        if not baseline or baseline == str(entry.get("rollback_target") or "").strip():
            raise PoolMemberRefused(
                "owner record has no gate baseline reference", artifact_ref=str(entry.get("registry_id"))
            )
        joined = str(entry.get("approved_at") or entry.get("created_at") or "").strip()
        if not joined:
            raise PoolMemberRefused(
                "owner record has no approval or creation time", artifact_ref=str(entry.get("registry_id"))
            )
        member = PoolMember(
            artifact_ref=str(entry["registry_id"]),
            checksum=str(entry["checksum"]),
            stage="active",
            risk_weight=1.0,
            baseline_ref=baseline,
            joined_at=joined,
            parent_ref=None,
            demand_ref=None,
        )
        return self.append(
            persona_id,
            tenant_id,
            AppendPoolVersionRequest(
                expected_version=0,
                members=[member],
                change_reason="first member derived from the persona's approved paper artifact (owner records)",
            ),
            actor_id=actor_id,
        )


def build_pool_store() -> PersonaStrategyPoolStore:
    from services.foundation.reliable_delivery import build_record_store

    records = build_record_store(
        backend=os.getenv("PERSONA_POOL_STORE_BACKEND", os.getenv("PERSONA_STORE_BACKEND", "json")),
        dsn=os.getenv("PERSONA_POOL_STORE_DSN") or os.getenv("PERSONA_STORE_DSN") or os.getenv("DATABASE_URL"),
        table_name=os.getenv("PERSONA_POOL_STORE_TABLE", "persona.strategy_pools"),
        json_path=os.getenv("PERSONA_POOL_STORE_PATH", "/tmp/pantheon/persona/strategy_pools.json"),
        owner_service="persona-svc",
    )
    return PersonaStrategyPoolStore(records)
