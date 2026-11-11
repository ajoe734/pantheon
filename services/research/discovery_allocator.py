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
    list_personas: Callable[[], Sequence[Mapping[str, Any]]],
    list_active_bindings: Callable[[], Sequence[Mapping[str, Any]]] = lambda: (),
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
            personas = list(list_personas())
            if not admitted:
                record["reason"] = "no_admitted_candidates"
            elif not personas:
                record["reason"] = "no_discovery_personas"
            elif allocator_factory is None:
                record["reason"] = "allocator_not_configured"
            else:
                result = allocator_factory().allocate(
                    admitted_artifacts=admitted,
                    discovery_personas=personas,
                    active_bindings=list(list_active_bindings()),
                    tenant_id=tenant_id,
                    date=now.date().isoformat(),
                )
                record["bound"] = [b.to_dict() for b in result.bindings]
                record["skipped"] = result.skipped
                if not result.bindings:
                    record["reason"] = "no_eligible_match"
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
