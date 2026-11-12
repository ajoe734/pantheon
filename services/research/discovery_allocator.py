"""Daily discovery allocator: bind admitted strategies to discovery personas.

Pure decision logic with injected ports.  Selection uses the existing
``persona_strategy_match`` score (see ``docs/contracts/persona_strategy_match
.schema.json``); the chosen score and match id are recorded on every binding.
The allocator only ever requests *paper* provisioning, at most
``MAX_DISCOVERY_PERSONAS`` concurrent discovery personas, and never exceeds
the paper capital cap.  Baseline personas are never counted or touched.
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

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


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
        admitted_artifacts: Sequence[Mapping[str, Any]],
        discovery_personas: Sequence[Mapping[str, Any]],
        active_bindings: Sequence[Mapping[str, Any]] = (),
        tenant_id: str,
        date: str,
    ) -> AllocationResult:
        """Bind best (persona, artifact) pairs.

        ``discovery_personas`` are candidate personas; ``active_bindings`` are
        currently-bound discovery personas (``artifact_ref``, ``paper_capital``,
        ``persona_id``) that count against slots and capital.
        """

        result = AllocationResult()
        slots = self.max_personas - len(active_bindings)
        capital_left = self.paper_capital_cap - sum(
            float(b.get("paper_capital") or 0.0) for b in active_bindings
        )
        used_artifacts = {b.get("artifact_ref") for b in active_bindings}
        used_personas = {b.get("persona_id") for b in active_bindings}

        scored: list[tuple[float, str, str, str]] = []
        for persona in discovery_personas:
            pid = str(persona.get("persona_id"))
            if pid in used_personas:
                continue
            for artifact in admitted_artifacts:
                ref = str(artifact.get("registry_id") or artifact.get("artifact_id"))
                if ref in used_artifacts:
                    continue
                if str(artifact.get("execution_context") or EXECUTION_CONTEXT) != EXECUTION_CONTEXT:
                    result.skipped.append({"artifact_ref": ref, "reason": "not_paper"})
                    continue
                match = self.scorer(persona, artifact)
                score = float(_get(match, "score", 0.0))
                if _get(match, "blockers"):
                    result.skipped.append({"artifact_ref": ref, "persona_id": pid, "reason": "blocked"})
                    continue
                if score < self.min_score:
                    continue
                scored.append((score, pid, ref, str(_get(match, "match_id", ""))))
        # Highest score first; deterministic tie-break on ids.
        scored.sort(key=lambda t: (-t[0], t[1], t[2]))

        for score, pid, ref, match_id in scored:
            if slots <= 0:
                result.skipped.append({"artifact_ref": ref, "persona_id": pid, "reason": "no_slot"})
                continue
            if pid in used_personas or ref in used_artifacts:
                continue
            if capital_left < self.per_persona_capital:
                result.skipped.append({"artifact_ref": ref, "persona_id": pid, "reason": "capital_cap"})
                continue
            try:
                self.provision(
                {
                    "tenant_id": tenant_id,
                    "idempotency_key": f"discovery-allocator:{date}:{pid}:{ref}",
                    "persona_id": pid,
                    "strategy_artifact_ref": ref,
                    "execution_context": EXECUTION_CONTEXT,
                    "budget": self.per_persona_capital,
                    "metadata": {
                        "discovery_match_id": match_id,
                        "discovery_match_score": score,
                    },
                }
                )
            except Exception as exc:  # noqa: BLE001 - keep earlier bindings, record this failure
                result.skipped.append({
                    "artifact_ref": ref, "persona_id": pid,
                    "reason": f"provision_failed:{type(exc).__name__}:{exc}"[:300],
                })
                if isinstance(exc, OwnerUnreachable):
                    result.error = f"owner_unreachable:{exc.service}:{exc}"
                else:
                    # A rejection (e.g. 409 on activation) may follow durable
                    # pool/pending-binding writes, so slots/capital are uncertain:
                    # halt; the next run reconciles from Capital pending/active bindings.
                    result.error = f"provision_failed_halted:{type(exc).__name__}"
                break
            result.bindings.append(
                Binding(ref, pid, score, match_id, self.per_persona_capital)
            )
            used_personas.add(pid)
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
OwnerCall = Callable[[str, str, str, "dict[str, Any] | None"], Any]


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


DISCOVERY_BINDING_PREFIX = "pcb-discovery-"
DISCOVERY_POOL_PREFIX = "cp-discovery-"
DEFAULT_CURRENCY = "USD"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


BASELINE_STRATEGY_FAMILIES = frozenset({"dev_paper_baseline"})
INELIGIBLE_LIFECYCLE_STATES = frozenset({"frozen", "retired"})


def is_discovery_eligible_persona(persona: Mapping[str, Any], *, tenant_id: str) -> bool:
    """Eligible = real Persona owner record (PersonaBody) that is tenant-bound,
    administratively ``active``, not frozen/retired, and not a baseline persona
    (``strategy_family`` / ``metadata`` markers; PersonaBody has no top-level
    ``persona_class`` or ``labels``)."""

    # tenant_id may be None on legacy records: no provable tenant binding -> not eligible.
    if str(persona.get("tenant_id") or "") != tenant_id:
        return False
    if str(persona.get("status") or "") != "active":
        return False
    if str(persona.get("lifecycle_state") or "") in INELIGIBLE_LIFECYCLE_STATES:
        return False
    if str(persona.get("strategy_family") or "") in BASELINE_STRATEGY_FAMILIES:
        return False
    meta = _mapping(persona.get("metadata"))
    if (
        str(meta.get("strategy_family") or "") in BASELINE_STRATEGY_FAMILIES
        or str(meta.get("persona_class") or "") == "baseline"
        or meta.get("baseline")
    ):
        return False
    return True


def normalize_admitted_spec(admission: Mapping[str, Any], view: Any, *, tenant_id: str) -> dict[str, Any]:
    """Resolve one ReplicationAdmission to its approved registry StrategySpec.

    Raises ``ValueError`` (named reason) unless the registry entry is an
    approved, tenant-owned StrategySpec whose checksum matches the admission.
    The returned mapping is the inline spec (what the matcher scores) plus
    ``registry_id``/``artifact_id``/``strategy_artifact_ref`` and paper context.
    """

    entry = _mapping(_mapping(view).get("entry")) or _mapping(view)
    spec_id = str(admission.get("strategy_spec_id") or admission.get("registry_id") or "")
    metadata = _mapping(entry.get("metadata"))
    spec = metadata.get("strategy_spec")
    if entry.get("registry_id") != spec_id or entry.get("artifact_type") != "strategy_spec":
        raise ValueError("registry_entry_mismatch")
    if entry.get("artifact_state") != "approved":
        raise ValueError("spec_not_approved")
    owner = str(entry.get("owner_tenant") or metadata.get("tenant_id") or "")
    if owner != tenant_id:
        raise ValueError("spec_tenant_mismatch")
    if admission.get("checksum") and entry.get("checksum") != admission.get("checksum"):
        raise ValueError("spec_checksum_changed")
    if not isinstance(spec, Mapping):
        raise ValueError("spec_payload_missing")
    out = dict(spec)
    out.update(
        registry_id=spec_id,
        artifact_id=spec_id,
        strategy_artifact_ref=spec_id,
        strategy_spec_id=spec_id,
        execution_context=EXECUTION_CONTEXT,
    )
    return out


def make_production_ports(
    *,
    call: OwnerCall,
    persona_url: str,
    provisioning_url: str,
    registry_url: str,
    capital_url: str,
    tenant_id: str,
    paper_capital_cap: float,
    per_persona_capital: float,
    min_score: float = DEFAULT_MIN_SCORE,
    currency: str = DEFAULT_CURRENCY,
) -> dict[str, Any]:
    persona_url = persona_url.rstrip("/")
    provisioning_url = provisioning_url.rstrip("/")
    registry_url = registry_url.rstrip("/")
    capital_url = capital_url.rstrip("/")

    def list_personas() -> list[dict[str, Any]]:
        rows = _persona_rows(call("persona", "GET", f"{persona_url}/api/personas", None))
        return [p for p in rows if is_discovery_eligible_persona(p, tenant_id=tenant_id)]

    def list_active_bindings() -> list[dict[str, Any]]:
        """Durable discovery bindings, read from the Capital owner (not labels)."""

        payload = call("capital", "GET", f"{capital_url}/api/bindings", None)
        out = []
        for b in _persona_rows(payload):
            meta = _mapping(b.get("metadata"))
            if not str(b.get("binding_id") or "").startswith(DISCOVERY_BINDING_PREFIX):
                continue
            if str(b.get("tenant_id") or meta.get("tenant_id") or tenant_id) != tenant_id:
                continue
            if str(b.get("status") or "") not in {"pending", "active"}:
                continue
            out.append({
                "persona_id": str(b.get("persona_id")),
                "artifact_ref": meta.get("strategy_spec_registry_id"),
                "paper_capital": float(b.get("budget") or 0.0),
            })
        return out

    def resolve_admitted(admissions: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (scorable approved specs, unresolved [{artifact_ref, reason}])."""

        import urllib.parse as _up

        specs: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for adm in admissions:
            ref = str(adm.get("strategy_spec_id") or adm.get("registry_id") or "")
            try:
                view = call(
                    "registry", "GET",
                    f"{registry_url}/api/registry/strategy-specs/{_up.quote(ref, safe='')}", None,
                )
                specs.append(normalize_admitted_spec(adm, view, tenant_id=tenant_id))
            except OwnerRejected as exc:
                unresolved.append({"artifact_ref": ref, "reason": f"registry_rejected:{exc.status}"})
            except ValueError as exc:
                unresolved.append({"artifact_ref": ref, "reason": str(exc)})
        return specs, unresolved

    def provision(request: dict[str, Any]) -> Any:
        import hashlib as _hashlib

        # Stable per (persona, artifact): a retry or a later day adopts the same pool/binding.
        token = _hashlib.sha256(
            f"{request['tenant_id']}:{request['persona_id']}:{request['strategy_artifact_ref']}".encode()
        ).hexdigest()[:16]
        body = {
            "capital_pool_id": f"{DISCOVERY_POOL_PREFIX}{token}",
            "binding_id": f"{DISCOVERY_BINDING_PREFIX}{token}",
            "currency": currency,
            "budget": request["budget"],
            "strategy_spec_registry_id": request["strategy_artifact_ref"],
            "labels": {"paper_only": True},
        }
        try:
            return call(
                "provisioning", "POST",
                f"{provisioning_url}/bff/personas/{request['persona_id']}/provisioning/paper-capital",
                body,
            )
        except OwnerRejected as exc:
            raise RuntimeError(f"provisioning rejected HTTP {exc.status}: {exc}") from None

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
        "list_personas": list_personas,
        "list_active_bindings": list_active_bindings,
        "resolve_admitted": resolve_admitted,
        "allocator_factory": allocator_factory,
    }


def allocator_enabled(env: Mapping[str, str]) -> bool:
    # Paper-only, capped at 5, and off unless explicitly enabled with true.
    return str(env.get("DISCOVERY_ALLOCATOR_ENABLED", "")).strip().lower() == "true"


def run_scheduled_allocation(
    *,
    enabled: bool,
    allocator_factory: Callable[[], DiscoveryAllocator] | None,
    list_admitted: Callable[[], Sequence[Mapping[str, Any]]],
    list_personas: Callable[[], Sequence[Mapping[str, Any]]],
    list_active_bindings: Callable[[], Sequence[Mapping[str, Any]]] = lambda: (),
    resolve_admitted: Callable[[Sequence[Mapping[str, Any]]], Any] | None = None,
    tenant_id: str,
    record_path: _Path | None = None,
    now: _datetime | None = None,
) -> dict[str, Any]:
    """Run one allocation pass and return (and persist) the run record.

    The record always states what was seen and either which personas were
    bound or why none were (``disabled``, ``no_admitted_candidates``,
    ``no_discovery_personas``, ``no_eligible_match``, ...).
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
                str(a.get("registry_id") or a.get("artifact_id") or a.get("strategy_spec_id"))
                for a in admitted
            ]
            if not admitted:
                record["reason"] = "no_admitted_candidates"
            elif allocator_factory is None:
                record["reason"] = "allocator_not_configured"
            elif not (personas := list(list_personas())):
                record["reason"] = "no_discovery_personas"
            else:
                unresolved: list[dict[str, Any]] = []
                artifacts = admitted
                if resolve_admitted is not None:
                    artifacts, unresolved = resolve_admitted(admitted)
                    artifacts = list(artifacts)
                result = allocator_factory().allocate(
                    admitted_artifacts=artifacts,
                    discovery_personas=personas,
                    active_bindings=list(list_active_bindings()),
                    tenant_id=tenant_id,
                    date=now.date().isoformat(),
                )
                record["bound"] = [b.to_dict() for b in result.bindings]
                record["skipped"] = unresolved + result.skipped
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
