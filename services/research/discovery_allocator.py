"""Daily discovery allocator: create discovery personas for gate-passed strategies.

Pure decision logic with injected ports.  Only candidates whose exact strategy
artifact passed the real-backtest replication gate (recorded backtest run and
dataset universe) are considered.  Selection uses the existing
``persona_strategy_match`` score (see ``docs/contracts/persona_strategy_match
.schema.json``) against a discovery mandate profile from allocator settings; the
score and match id are recorded on the created persona.  Each persona is created
through the existing ``POST /bff/personas`` provisioning saga (never the
capital-only route) with ``strategy_artifact_ref`` naming the Registry entry of
the judged artifact and paper symbols equal to the gate dataset's universe.  At
most ``MAX_DISCOVERY_PERSONAS`` marked personas count, within the paper capital
cap; personas without the allocator's explicit marker are never selected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

MAX_DISCOVERY_PERSONAS = 5
EXECUTION_CONTEXT = "paper"
DEFAULT_MIN_SCORE = 50.0

# score(persona, artifact) -> object/mapping with ``score`` and ``match_id``;
# blocked matches carry non-empty ``blockers``.
Scorer = Callable[[Mapping[str, Any], Mapping[str, Any]], Any]
# provision(request) -> provisioning record/receipt (opaque to the allocator).
Provisioner = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class Binding:
    artifact_ref: str
    persona_id: str
    score: float
    match_id: str
    paper_capital: float
    execution_context: str = EXECUTION_CONTEXT
    backtest_run_id: str = ""
    symbols: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["symbols"] = list(self.symbols)
        return out


@dataclass
class AllocationResult:
    bindings: list[Binding] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


class DiscoveryAllocator:
    def __init__(
        self,
        *,
        scorer: Scorer,
        provision: Provisioner,
        paper_capital_cap: float,
        per_persona_capital: float,
        max_personas: int = MAX_DISCOVERY_PERSONAS,
        min_score: float = DEFAULT_MIN_SCORE,
    ) -> None:
        if paper_capital_cap <= 0 or per_persona_capital <= 0:
            raise ValueError("paper capital cap and per-persona capital must be positive")
        self.scorer = scorer
        self.provision = provision
        self.paper_capital_cap = float(paper_capital_cap)
        self.per_persona_capital = float(per_persona_capital)
        # The five-slot ceiling is a hard limit, not a tunable above it.
        self.max_personas = max(0, min(int(max_personas), MAX_DISCOVERY_PERSONAS))
        self.min_score = float(min_score)

    def allocate(
        self,
        *,
        candidates: Sequence[Mapping[str, Any]],
        mandate_profile: Mapping[str, Any],
        active_slots: Sequence[Mapping[str, Any]] = (),
        taken_artifacts: Sequence[str] = (),
        tenant_id: str,
        date: str,
    ) -> AllocationResult:
        """Create a discovery persona for the best-scoring gate-passed candidates.

        ``candidates`` are gate-passed artifacts (``registry_id`` = the approved
        execution_bundle ref, ``symbols`` = gate dataset universe, ``backtest_run_id``).
        ``active_slots`` are marked, non-retired allocator personas (they count
        against slots and capital); ``taken_artifacts`` are the artifact refs of
        every marked persona including retired ones, which are never re-bound.
        """

        result = AllocationResult()
        slots = self.max_personas - len(active_slots)
        capital_left = self.paper_capital_cap - sum(
            float(b.get("paper_capital") or 0.0) for b in active_slots
        )
        used_artifacts = {str(t) for t in taken_artifacts} | {
            str(b.get("artifact_ref")) for b in active_slots
        }

        scored: list[tuple[float, str, str, Mapping[str, Any]]] = []
        for artifact in candidates:
            ref = str(artifact.get("registry_id") or artifact.get("artifact_id") or "")
            if not ref:
                result.skipped.append({"artifact_ref": ref, "reason": "artifact_ref_missing"})
                continue
            if ref in used_artifacts:
                continue
            if str(artifact.get("execution_context") or EXECUTION_CONTEXT) != EXECUTION_CONTEXT:
                result.skipped.append({"artifact_ref": ref, "reason": "not_paper"})
                continue
            symbols = artifact.get("symbols")
            if not isinstance(symbols, (list, tuple)) or not symbols or not all(
                isinstance(x, str) and x.strip() for x in symbols
            ):
                result.skipped.append({"artifact_ref": ref, "reason": "universe_missing"})
                continue
            if not str(artifact.get("backtest_run_id") or "").strip():
                result.skipped.append({"artifact_ref": ref, "reason": "backtest_run_missing"})
                continue
            match = self.scorer(mandate_profile, artifact)
            score = float(_get(match, "score", 0.0))
            if _get(match, "blockers"):
                result.skipped.append({"artifact_ref": ref, "reason": "blocked"})
                continue
            if score < self.min_score:
                result.skipped.append({"artifact_ref": ref, "reason": "below_min_score", "score": score})
                continue
            scored.append((score, ref, str(_get(match, "match_id", "")), artifact))
        # Highest score first; deterministic tie-break on ref.
        scored.sort(key=lambda t: (-t[0], t[1]))

        for score, ref, match_id, artifact in scored:
            if slots <= 0:
                result.skipped.append({"artifact_ref": ref, "reason": "no_slot"})
                continue
            if ref in used_artifacts:
                continue
            if capital_left < self.per_persona_capital:
                result.skipped.append({"artifact_ref": ref, "reason": "capital_cap"})
                continue
            request = {
                "tenant_id": tenant_id,
                "idempotency_key": f"discovery-allocator:{tenant_id}:{ref}",
                "strategy_artifact_ref": ref,
                "execution_context": EXECUTION_CONTEXT,
                "budget": self.per_persona_capital,
                "symbols": [str(x).strip() for x in artifact["symbols"]],
                "discovery_allocator": {
                    "marker": True,
                    "strategy_artifact_ref": ref,
                    "strategy_spec_id": artifact.get("strategy_spec_id"),
                    "backtest_run_id": artifact.get("backtest_run_id"),
                    "match_id": match_id,
                    "match_score": score,
                    "paper_capital": self.per_persona_capital,
                    "allocated_on": date,
                },
            }
            try:
                receipt = self.provision(request)
            except Exception as exc:  # noqa: BLE001 - keep earlier bindings, record this failure
                result.skipped.append({
                    "artifact_ref": ref,
                    "reason": f"provision_failed:{type(exc).__name__}:{exc}"[:300],
                })
                if isinstance(exc, OwnerUnreachable):
                    result.error = f"owner_unreachable:{exc.service}:{exc}"
                else:
                    # A rejection may follow durable saga writes, so slots/capital are
                    # uncertain: halt; the next run reconciles from the marked personas.
                    result.error = f"provision_failed_halted:{type(exc).__name__}"
                break
            result.bindings.append(
                Binding(
                    ref,
                    str(_get(receipt, "persona_id", "") or ""),
                    score,
                    match_id,
                    self.per_persona_capital,
                    backtest_run_id=str(artifact.get("backtest_run_id") or ""),
                    symbols=tuple(request["symbols"]),
                )
            )
            used_artifacts.add(ref)
            slots -= 1
            capital_left -= self.per_persona_capital
        return result


# ---------------------------------------------------------------------------
# Scheduled production run (design §11: once a day, paper only, cap 5).
# ---------------------------------------------------------------------------
import json as _json  # noqa: E402
import threading as _threading  # noqa: E402
from datetime import datetime as _datetime, timezone as _timezone  # noqa: E402
from pathlib import Path as _Path  # noqa: E402

DAILY_INTERVAL_SECONDS = 24 * 60 * 60


class OwnerUnreachable(RuntimeError):
    """An owning service (persona / provisioning) could not be reached."""

    def __init__(self, service: str, detail: str) -> None:
        super().__init__(detail)
        self.service = service


class OwnerRejected(RuntimeError):
    """An owning service answered with a non-auth 4xx rejection."""

    def __init__(self, service: str, status: int, detail: str) -> None:
        super().__init__(detail)
        self.service = service
        self.status = status


# ---------------------------------------------------------------------------
# Production ports: real persona/binding readers, scorer and provisioner.
# ``call(service, method, url, body)`` is the orchestrator's authenticated
# owner-call transport; it must raise OwnerUnreachable on transport/auth/5xx.
# ---------------------------------------------------------------------------
OwnerCall = Callable[..., Any]  # (service, method, url, body, headers=None)


def _load_match_persona_strategies() -> Callable[..., Any]:
    import importlib.util as _ilu
    import sys as _sys

    persona_dir = _Path(__file__).resolve().parents[1] / "control-plane" / "persona"
    path = persona_dir / "persona_strategy_discovery.py"
    if not path.is_file():
        raise OwnerUnreachable("persona-matcher", f"persona_strategy_discovery.py not found at {path}")
    if str(persona_dir) not in _sys.path:
        _sys.path.append(str(persona_dir))
    spec = _ilu.spec_from_file_location("_discovery_allocator_persona_match", path)
    module = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
    _sys.modules[spec.name] = module  # type: ignore[union-attr]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.match_persona_strategies


def make_persona_scorer(match_fn: Callable[..., Any] | None = None) -> Scorer:
    def scorer(persona: Mapping[str, Any], artifact: Mapping[str, Any]) -> Any:
        fn = match_fn or _load_match_persona_strategies()
        matches = fn(persona, strategy_specs=[dict(artifact)])
        if not matches:
            return {"score": 0.0, "match_id": "", "blockers": ["no_match"]}
        top = matches[0]
        return {"score": top.score, "match_id": top.match_id, "blockers": tuple(top.blockers)}

    return scorer


def _persona_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        payload = payload.get("data", payload.get("personas", []))
    return [dict(p) for p in payload if isinstance(p, Mapping)] if isinstance(payload, list) else []


DEFAULT_CURRENCY = "TWD"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


SLOT_FREEING_LIFECYCLE_STATES = frozenset({"retired", "provisioning_failed"})


def discovery_marker(persona: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The allocator's explicit marker on a Persona owner record, else ``None``.

    Only ``metadata.discovery_allocator.marker is True`` counts: baseline,
    coordinator-created and release-probe personas never carry it, so they are
    never counted, selected or modified.
    """

    marker = _mapping(persona.get("metadata")).get("discovery_allocator")
    return marker if isinstance(marker, Mapping) and marker.get("marker") is True else None


def read_marked_personas(rows: Sequence[Mapping[str, Any]], *, tenant_id: str) -> tuple[list[dict[str, Any]], list[str]]:
    """(active slots, every marked artifact ref) from Persona owner records."""

    slots: list[dict[str, Any]] = []
    taken: list[str] = []
    for persona in rows:
        marker = discovery_marker(persona)
        if marker is None or str(persona.get("tenant_id") or _mapping(persona.get("metadata")).get("tenant_id") or "") != tenant_id:
            continue
        ref = str(marker.get("strategy_artifact_ref") or _mapping(persona.get("metadata")).get("source_strategy_artifact_ref") or "")
        if ref:
            taken.append(ref)
        if str(persona.get("lifecycle_state") or "") in SLOT_FREEING_LIFECYCLE_STATES:
            continue
        slots.append({
            "persona_id": str(persona.get("persona_id") or persona.get("id") or ""),
            "artifact_ref": ref,
            "paper_capital": float(marker.get("paper_capital") or 0.0),
        })
    return slots, taken


def resolve_gate_passed(
    admissions: Sequence[Mapping[str, Any]],
    findings: Sequence[Mapping[str, Any]],
    run_lookup: Callable[[str], Mapping[str, Any] | None],
    *,
    tenant_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split admissions into gate-passed candidates and recorded skips.

    A pass is a ``research_finding_published`` outbox record (written only when
    the replication gate passed) of this tenant for the admission's spec whose
    ``backtest_run_id`` names a persisted *completed* run bound to the same
    checksum and carrying the gate dataset's symbol universe.  An admission
    without that evidence is skipped with a reason; nothing is synthesized.
    """

    by_spec: dict[str, Mapping[str, Any]] = {}
    for rec in findings:
        meta = _mapping(rec.get("metadata"))
        if meta.get("event_type") != "research_finding_published" or str(meta.get("tenant_id") or "") != tenant_id:
            continue
        by_spec[str(meta.get("strategy_spec_id") or "")] = rec  # later records win
    passed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for adm in admissions:
        ref = str(adm.get("strategy_spec_id") or adm.get("registry_id") or "")
        rec = by_spec.get(ref)
        if rec is None:
            skipped.append({"artifact_ref": ref, "reason": "no_gate_pass"})
            continue
        run_id = str(_mapping(_mapping(rec.get("metadata")).get("metrics")).get("backtest_run_id") or "")
        run = run_lookup(run_id) if run_id else None
        if not run_id or run is None:
            skipped.append({"artifact_ref": ref, "reason": "backtest_run_missing"})
            continue
        if str(run.get("status") or "") != "completed" or not run.get("artifact_checksum"):
            skipped.append({"artifact_ref": ref, "reason": "backtest_run_not_completed"})
            continue
        symbols = run.get("dataset_symbols")
        if not isinstance(symbols, list) or not symbols:
            skipped.append({"artifact_ref": ref, "reason": "universe_missing"})
            continue
        passed.append({
            "strategy_spec_id": ref,
            "backtest_run_id": run_id,
            "artifact_checksum": run["artifact_checksum"],
            "spec_checksum": adm.get("checksum"),
            "dataset_id": run.get("dataset_id"),
            "symbols": [str(x) for x in symbols],
        })
    return passed, skipped


def bundle_registry_id(artifact_checksum: str) -> str:
    """Deterministic Registry id of the execution_bundle for one exact artifact."""

    import hashlib as _hashlib

    return f"artifact-gate-{_hashlib.sha256(str(artifact_checksum).encode()).hexdigest()[:20]}"


def normalize_gate_candidate(passed: Mapping[str, Any], spec_view: Any, bundle_view: Any, *, tenant_id: str) -> dict[str, Any]:
    """Join one gate pass to its approved StrategySpec and approved execution_bundle.

    Raises ``ValueError`` (named reason) unless the spec is approved, tenant-owned,
    its inline artifact is byte-identical (checksum) to what the gate judged, and
    Registry holds that exact artifact as an *approved* execution_bundle.
    """

    from services.registry.strategy_artifact import strategy_artifact_checksum

    entry = _mapping(_mapping(spec_view).get("entry")) or _mapping(spec_view)
    spec_id = str(passed.get("strategy_spec_id") or "")
    metadata = _mapping(entry.get("metadata"))
    spec = metadata.get("strategy_spec")
    if entry.get("registry_id") != spec_id or entry.get("artifact_type") != "strategy_spec":
        raise ValueError("registry_entry_mismatch")
    if entry.get("artifact_state") != "approved":
        raise ValueError("spec_not_approved")
    if str(entry.get("owner_tenant") or metadata.get("tenant_id") or "") != tenant_id:
        raise ValueError("spec_tenant_mismatch")
    if passed.get("spec_checksum") and entry.get("checksum") != passed.get("spec_checksum"):
        raise ValueError("spec_checksum_changed")
    if not isinstance(spec, Mapping) or not isinstance(spec.get("strategy_artifact"), Mapping):
        raise ValueError("spec_artifact_missing")
    try:
        judged = strategy_artifact_checksum(dict(spec["strategy_artifact"]))
    except Exception:  # noqa: BLE001
        raise ValueError("artifact_checksum_unavailable") from None
    if judged != passed.get("artifact_checksum"):
        raise ValueError("artifact_not_the_gate_judged_one")
    bundle = _mapping(_mapping(bundle_view).get("entry")) or _mapping(bundle_view)
    bundle_id = bundle_registry_id(judged)
    bmeta = _mapping(bundle.get("metadata"))
    if bundle.get("registry_id") != bundle_id or bundle.get("artifact_type") != "execution_bundle":
        raise ValueError("execution_bundle_missing")
    if bundle.get("artifact_state") != "approved":
        raise ValueError("execution_bundle_not_approved")
    try:
        registered = strategy_artifact_checksum(dict(_mapping(bmeta.get("strategy_artifact"))))
    except Exception:  # noqa: BLE001
        raise ValueError("execution_bundle_artifact_invalid") from None
    if registered != judged:
        raise ValueError("execution_bundle_artifact_mismatch")
    out = dict(spec)  # inline spec: what the matcher scores
    out.update(
        registry_id=bundle_id,
        artifact_id=bundle_id,
        strategy_artifact_ref=bundle_id,
        strategy_spec_id=spec_id,
        execution_context=EXECUTION_CONTEXT,
        backtest_run_id=passed.get("backtest_run_id"),
        symbols=list(passed.get("symbols") or []),
    )
    return out


def make_production_ports(
    *,
    call: OwnerCall,
    persona_url: str,
    provisioning_url: str,
    registry_url: str,
    tenant_id: str,
    paper_capital_cap: float,
    per_persona_capital: float,
    min_score: float = DEFAULT_MIN_SCORE,
    mandate_profile: Mapping[str, Any] | None = None,
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any]:
    persona_url = persona_url.rstrip("/")
    provisioning_url = provisioning_url.rstrip("/")
    registry_url = registry_url.rstrip("/")

    def read_marked() -> tuple[list[dict[str, Any]], list[str]]:
        """Marked allocator personas, read from the Persona owner (not labels)."""

        return read_marked_personas(
            _persona_rows(call("persona", "GET", f"{persona_url}/api/personas", None)), tenant_id=tenant_id
        )

    def resolve_candidates(passed: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (scorable approved candidates, unresolved [{artifact_ref, reason}])."""

        import urllib.parse as _up

        specs: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for item in passed:
            ref = str(item.get("strategy_spec_id") or "")
            try:
                spec_view = call("registry", "GET", f"{registry_url}/api/registry/strategy-specs/{_up.quote(ref, safe='')}", None)
                bundle_id = bundle_registry_id(str(item.get("artifact_checksum")))
                try:
                    bundle_view = call(
                        "registry", "GET",
                        f"{registry_url}/api/registry/strategy-artifacts/{_up.quote(bundle_id, safe='')}", None,
                    )
                except OwnerRejected as exc:
                    if exc.status != 404:
                        raise
                    bundle_view = _register_bundle(item, spec_view, bundle_id)
                specs.append(normalize_gate_candidate(item, spec_view, bundle_view, tenant_id=tenant_id))
            except OwnerRejected as exc:
                unresolved.append({"artifact_ref": ref, "reason": f"registry_rejected:{exc.status}"})
            except ValueError as exc:
                unresolved.append({"artifact_ref": ref, "reason": str(exc)})
        return specs, unresolved

    def _register_bundle(item: Mapping[str, Any], spec_view: Any, bundle_id: str) -> Any:
        """Existing Registry write path: register the exact judged artifact as a candidate.

        Approval is Governance's; until approved the candidate is skipped
        (``execution_bundle_not_approved``), never bound.
        """

        entry = _mapping(_mapping(spec_view).get("entry")) or _mapping(spec_view)
        spec = _mapping(_mapping(entry.get("metadata")).get("strategy_spec"))
        artifact = spec.get("strategy_artifact")
        if not isinstance(artifact, Mapping):
            raise ValueError("spec_artifact_missing")
        return call(
            "registry", "POST", f"{registry_url}/api/registry/strategy-artifacts",
            {
                "registry_id": bundle_id,
                "artifact_state": "candidate",
                "strategy_artifact": dict(artifact),
                "producer_run_id": str(item.get("backtest_run_id")),
                "evaluation_summary": {
                    "admission": "replication_gate_passed",
                    "backtest_run_id": item.get("backtest_run_id"),
                    "source_strategy_spec_id": item.get("strategy_spec_id"),
                },
                "metadata": {"tenant_id": tenant_id, "source_strategy_spec_id": item.get("strategy_spec_id")},
            },
        )

    def provision(request: dict[str, Any]) -> Any:
        import hashlib as _hashlib

        ref = request["strategy_artifact_ref"]
        token = _hashlib.sha256(f"{request['tenant_id']}:{ref}".encode()).hexdigest()[:16]
        name = f"discovery-{token}"
        marker = dict(request["discovery_allocator"])
        profile = _mapping(mandate_profile)
        body = {
            "name": name,
            "archetype": str(profile.get("archetype") or "discovery"),
            "mandate": str(profile.get("mandate") or "discovery"),
            "strategy_family": str(profile.get("strategy_family") or "discovery"),
            "risk": "low",
            "market": "TW",
            "currency": currency,
            "symbols": list(request["symbols"]),
            "budget": request["budget"],
            "strategy_artifact_ref": ref,
            "discovery_allocator": marker,
        }
        try:
            receipt = call(
                "provisioning", "POST", f"{provisioning_url}/bff/personas", body,
                headers={"Idempotency-Key": request["idempotency_key"]},
            )
        except OwnerRejected as exc:
            raise RuntimeError(f"persona create rejected HTTP {exc.status}: {exc}") from None
        data = _mapping(_mapping(receipt).get("data")) or _mapping(receipt)
        persona_id = str(data.get("id") or data.get("persona_id") or data.get("personaId") or "")
        if not persona_id:
            norm = " ".join(name.casefold().split())
            persona_id = "persona-" + _hashlib.sha256(f"{request['tenant_id']}\x00{norm}".encode()).hexdigest()[:20]
        return {"persona_id": persona_id, "receipt": receipt}

    def allocator_factory() -> DiscoveryAllocator:
        return DiscoveryAllocator(
            scorer=make_persona_scorer(),
            provision=provision,
            paper_capital_cap=paper_capital_cap,
            per_persona_capital=per_persona_capital,
            max_personas=MAX_DISCOVERY_PERSONAS,
            min_score=min_score,
        )

    return {
        "read_marked": read_marked,
        "resolve_candidates": resolve_candidates,
        "allocator_factory": allocator_factory,
        "mandate_profile": dict(mandate_profile) if mandate_profile else None,
    }


def _env_true(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def allocator_enabled(env: Mapping[str, str]) -> bool:
    # Default on (paper-only, capped at 5); set DISCOVERY_ALLOCATOR_ENABLED=false to disable.
    raw = env.get("DISCOVERY_ALLOCATOR_ENABLED")
    return True if raw is None or not str(raw).strip() else _env_true(raw)


def run_scheduled_allocation(
    *,
    enabled: bool,
    allocator_factory: Callable[[], DiscoveryAllocator] | None,
    list_admitted: Callable[[], Sequence[Mapping[str, Any]]],
    list_gate_evidence: Callable[[], tuple[Sequence[Mapping[str, Any]], Callable[[str], Mapping[str, Any] | None]]],
    read_marked: Callable[[], tuple[Sequence[Mapping[str, Any]], Sequence[str]]],
    resolve_candidates: Callable[[Sequence[Mapping[str, Any]]], Any] | None,
    mandate_profile: Mapping[str, Any] | None,
    tenant_id: str,
    record_path: _Path | None = None,
    now: _datetime | None = None,
) -> dict[str, Any]:
    """Run one allocation pass and return (and persist) the run record.

    The record always states what was seen and either which personas were
    created or why none were (``disabled``, ``no_admitted_candidates``,
    ``no_gate_passed_candidates``, ``mandate_profile_missing``,
    ``no_eligible_match``, ...), with a recorded reason per skipped admission.
    """

    now = now or _datetime.now(_timezone.utc)
    record: dict[str, Any] = {
        "ran_at": now.isoformat(),
        "tenant_id": tenant_id,
        "execution_context": EXECUTION_CONTEXT,
        "max_personas": MAX_DISCOVERY_PERSONAS,
        "enabled": bool(enabled),
        "admitted_candidates_seen": 0,
        "admitted_candidate_refs": [],
        "gate_passed_refs": [],
        "bound": [],
        "skipped": [],
        "reason": None,
    }
    try:
        if not enabled:
            record["reason"] = "disabled"
        else:
            admitted = list(list_admitted())
            record["admitted_candidates_seen"] = len(admitted)
            record["admitted_candidate_refs"] = [
                str(a.get("strategy_spec_id") or a.get("registry_id")) for a in admitted
            ]
            if not admitted:
                record["reason"] = "no_admitted_candidates"
            elif allocator_factory is None or resolve_candidates is None:
                record["reason"] = "allocator_not_configured"
            elif not mandate_profile:
                record["reason"] = "mandate_profile_missing"
            else:
                findings, run_lookup = list_gate_evidence()
                passed, skipped = resolve_gate_passed(admitted, findings, run_lookup, tenant_id=tenant_id)
                record["gate_passed_refs"] = [p["strategy_spec_id"] for p in passed]
                record["skipped"] = list(skipped)
                if not passed:
                    record["reason"] = "no_gate_passed_candidates"
                else:
                    candidates, unresolved = resolve_candidates(passed)
                    record["skipped"] += list(unresolved)
                    slots, taken = read_marked()
                    result = allocator_factory().allocate(
                        candidates=list(candidates),
                        mandate_profile=mandate_profile,
                        active_slots=list(slots),
                        taken_artifacts=list(taken),
                        tenant_id=tenant_id,
                        date=now.date().isoformat(),
                    )
                    record["bound"] = [b.to_dict() for b in result.bindings]
                    record["skipped"] += result.skipped
                    if result.error:
                        record["reason"] = result.error
                    elif not result.bindings:
                        failed = any(str(k.get("reason", "")).startswith("provision_failed") for k in result.skipped)
                        record["reason"] = "provision_failed" if failed else "no_eligible_match"
    except OwnerUnreachable as exc:
        record["reason"] = f"owner_unreachable:{exc.service}:{exc}"
    except Exception as exc:  # noqa: BLE001 - a failed run must be recorded, not crash the loop
        record["reason"] = f"error:{type(exc).__name__}:{exc}"
    if record_path is not None:
        record_path = _Path(record_path)
        record_path.parent.mkdir(parents=True, exist_ok=True)
        with record_path.open("a", encoding="utf-8") as fh:
            fh.write(_json.dumps(record, sort_keys=True) + "\n")
    return record


def start_daily_scheduler(
    run_once: Callable[[], Any],
    *,
    interval_seconds: float = DAILY_INTERVAL_SECONDS,
    stop_event: _threading.Event | None = None,
) -> _threading.Thread:
    """Run ``run_once`` immediately, then every ``interval_seconds`` (daemon thread)."""

    stop = stop_event or _threading.Event()

    def _loop() -> None:
        while not stop.is_set():
            try:
                run_once()
            except Exception:  # noqa: BLE001
                pass
            if stop.wait(interval_seconds):
                break

    thread = _threading.Thread(target=_loop, name="discovery-allocator-daily", daemon=True)
    thread.start()
    return thread
