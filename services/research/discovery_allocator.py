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
