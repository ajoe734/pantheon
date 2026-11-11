"""Bounded evolution search for discovery strategies (design §12.2).

* ``search_in_sample`` – fixed-budget random search over the artifact's declared
  ``mutation_surface`` ranges, scored by walk-forward folds on the **in-sample**
  window only. It replaces the former two-value toggle.
* ``build_child_artifact`` – a validated child artifact (parameter search result
  or ``factor_rule_v1`` structural mutation) with lineage to its parent.
* ``judge_child_out_of_sample`` – every child is judged by the §10 replication
  gate on the untouched out-of-sample window; the gate accumulates the trial
  count per family, and every in-sample search variant is counted as a trial
  before the gate runs.

Nothing here talks to the network: the dataset, the scorer, the backtest runner
and the gate are injected so the module is deterministic and testable.
"""

from __future__ import annotations

import copy
import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

DEFAULT_SEARCH_BUDGET = 50
DEFAULT_IN_SAMPLE_FRACTION = 0.70
DEFAULT_WALK_FORWARD_FOLDS = 3

ScoreFn = Callable[[Mapping[str, Any]], float]


class EvolutionSearchError(ValueError):
    """Raised when a bounded search cannot run (fail closed, never a fallback toggle)."""


def stable_seed(*parts: str) -> int:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def _bounds(control: Mapping[str, Any]) -> tuple[float, float]:
    rng = control["allowed_range"]
    if isinstance(rng, Mapping):
        return float(rng["min"]), float(rng["max"])
    return float(rng[0]), float(rng[1])


def _on_grid_value(control: Mapping[str, Any], rng: random.Random) -> Any:
    lo, hi = _bounds(control)
    step = float(control["step"])
    n_steps = int(math.floor((hi - lo) / step + 1e-9))
    raw = lo + rng.randint(0, max(n_steps, 0)) * step
    if control.get("value_type") == "integer":
        return int(round(raw))
    return round(raw, 10)


def declared_search_space(artifact: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The mutable controls the search may move (declared range + step only)."""
    surface = artifact.get("mutation_surface") or {}
    immutable = set(surface.get("immutable_parameters") or ())
    controls = [
        c
        for c in surface.get("controls") or ()
        if isinstance(c, Mapping)
        and c.get("parameter_key") not in immutable
        and c.get("allowed_range") is not None
        and c.get("step")
    ]
    if not controls:
        raise EvolutionSearchError("artifact declares no searchable mutation_surface control")
    return controls


def sample_variants(
    artifact: Mapping[str, Any], *, budget: int, seed: int
) -> list[dict[str, Any]]:
    """Up to ``budget`` distinct on-grid parameter-update dicts inside declared ranges.

    Each variant moves a random non-empty subset of controls; the current point
    is never emitted. The sample is deterministic for a seed.
    """
    if budget < 1:
        raise EvolutionSearchError("search budget must be at least 1")
    controls = declared_search_space(artifact)
    current = {c["parameter_key"]: c.get("current_value") for c in controls}
    rng = random.Random(seed)
    seen: set[tuple] = set()
    variants: list[dict[str, Any]] = []
    attempts = 0
    while len(variants) < budget and attempts < budget * 40:
        attempts += 1
        k = rng.randint(1, len(controls))
        chosen = rng.sample(controls, k)
        update = {c["parameter_key"]: _on_grid_value(c, rng) for c in chosen}
        update = {key: value for key, value in update.items() if value != current[key]}
        if not update:
            continue
        signature = tuple(sorted(update.items()))
        if signature in seen:
            continue
        seen.add(signature)
        variants.append(update)
    return variants


@dataclass
class SearchTrial:
    index: int
    parameter_updates: dict[str, Any]
    score: float | None
    error: str | None = None


@dataclass
class SearchOutcome:
    window: str
    budget: int
    seed: int
    baseline_score: float | None
    trials: list[SearchTrial] = field(default_factory=list)
    best: SearchTrial | None = None
    trials_counted: int = 0
    family_trial_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "budget": self.budget,
            "seed": self.seed,
            "baseline_score": self.baseline_score,
            "evaluated": len(self.trials),
            "best": None
            if self.best is None
            else {
                "index": self.best.index,
                "parameter_updates": self.best.parameter_updates,
                "score": self.best.score,
            },
            "trials_counted": self.trials_counted,
            "family_trial_count": self.family_trial_count,
        }


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def apply_parameter_updates(
    parent: Mapping[str, Any], updates: Mapping[str, Any], *, label: str, decision_id: str
) -> dict[str, Any]:
    """Validated artifact with ``updates`` applied (ephemeral id, patch-bumped version)."""
    from services.registry.strategy_artifact import mutate_strategy_artifact

    major, minor, patch = (int(p) for p in str(parent["version"]).split("."))
    return mutate_strategy_artifact(
        parent,
        new_artifact_id=f"{parent['artifact_id']}-{label}",
        new_version=f"{major}.{minor}.{patch + 1}",
        parameter_updates=updates,
        source_run_ids=[decision_id],
    )


def search_in_sample(
    parent: Mapping[str, Any],
    *,
    score_fn: ScoreFn,
    decision_id: str,
    family_id: str,
    budget: int = DEFAULT_SEARCH_BUDGET,
    seed: int | None = None,
    trial_counter: Any | None = None,
) -> SearchOutcome:
    """Fixed-budget random search; ``score_fn`` must only see the in-sample window.

    Every variant evaluated increments the family trial counter (multiple-testing
    accounting) whether it scores well or not. The winner is the best strictly
    positive improvement over the parent's own in-sample score, else ``best`` is
    None (no candidate).
    """
    seed = stable_seed(decision_id, family_id) if seed is None else seed
    outcome = SearchOutcome(window="in_sample", budget=budget, seed=seed, baseline_score=_finite(score_fn(parent)))
    for index, update in enumerate(sample_variants(parent, budget=budget, seed=seed)):
        trial = SearchTrial(index=index, parameter_updates=update, score=None)
        try:
            variant = apply_parameter_updates(
                parent, update, label=f"trial-{index}", decision_id=decision_id
            )
            trial.score = _finite(score_fn(variant))
        except Exception as exc:  # a variant that cannot be built/scored is a failed trial
            trial.error = f"{type(exc).__name__}: {exc}"
        outcome.trials.append(trial)
        if trial_counter is not None:
            outcome.family_trial_count = trial_counter.increment(family_id)
            outcome.trials_counted += 1
    scored = [t for t in outcome.trials if t.score is not None]
    if scored:
        best = max(scored, key=lambda t: (t.score, -t.index))
        if outcome.baseline_score is None or best.score > outcome.baseline_score:
            outcome.best = best
    return outcome


def make_in_sample_walk_forward_scorer(
    dataset: Mapping[str, Any],
    *,
    backend: Any,
    in_sample_fraction: float = DEFAULT_IN_SAMPLE_FRACTION,
    folds: int = DEFAULT_WALK_FORWARD_FOLDS,
) -> ScoreFn:
    """Score = mean Sharpe over walk-forward folds carved from the in-sample window.

    The out-of-sample bars ``[split_idx, total_len)`` are never sliced, so they
    never reach the backend: search cannot overfit what it cannot see.
    A fold with a non-finite Sharpe makes the whole variant unscoreable.
    """
    from services.execution.shioaji_adapter import normalize_taiwan_symbol
    from services.research.vectorbt.adapter.vectorbt_adapter import (
        BacktestConfig,
        GovernedVectorbtInputAdapter,
        _slice_prepared_dataset,
        _summarize_window_result,
    )

    prepared = GovernedVectorbtInputAdapter().prepare(dataset)

    def score(artifact: Mapping[str, Any]) -> float:
        params = artifact.get("parameters") or {}
        wanted = {normalize_taiwan_symbol(s) for s in params.get("symbols", [])}
        targets = [i for i in prepared.instruments if normalize_taiwan_symbol(i) in wanted]
        if not targets:
            raise EvolutionSearchError("dataset has no bars for the artifact symbols")
        total_len = min(prepared.bars_per_instrument[i] for i in targets)
        split_idx = int(total_len * in_sample_fraction)
        cfg = BacktestConfig(
            strategy_params=dict(params), strategy_artifact=dict(artifact)
        )
        sharpes: list[float] = []
        for k in range(folds):
            start, end = (k * split_idx) // folds, ((k + 1) * split_idx) // folds
            fold_ds = _slice_prepared_dataset(prepared, start, end, f"search_fold_{k + 1}")
            summary = _summarize_window_result(
                fold_ds, backend.run(fold_ds, cfg), window_fraction=in_sample_fraction / folds
            )
            value = _finite(summary.get("sharpe_ratio"))
            if value is None:
                raise EvolutionSearchError("non-finite walk-forward Sharpe")
            sharpes.append(value)
        return sum(sharpes) / len(sharpes)

    return score


def build_child_artifact(
    parent: Mapping[str, Any],
    *,
    decision_id: str,
    kind: str,
    parameter_updates: Mapping[str, Any] | None = None,
    rng: random.Random | None = None,
    structural_operator: str | None = None,
    source_run_ids: Sequence[str] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build one registered-ready child. Returns ``(child_artifact, descriptor)``.

    ``kind`` is ``"retrain"`` (``parameter_updates`` from the search) or
    ``"mutate"`` (one structural operator, ``factor_rule_v1`` parents only).
    The child's ``lineage.parent_registry_ids`` is the parent's artifact id.
    """
    from services.registry.factor_rule import mutate_factor_rule_structure
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        mutate_strategy_artifact,
        validate_strategy_artifact,
    )

    suffix = hashlib.sha256(f"{decision_id}|{kind}".encode("utf-8")).hexdigest()[:10]
    child_id = f"{parent['artifact_id']}-{kind}-{suffix}"
    major, minor, _patch = (int(p) for p in str(parent["version"]).split("."))
    child_version = f"{major}.{minor + 1}.0"
    runs = [decision_id, *[r for r in source_run_ids if r != decision_id]]

    if kind == "retrain":
        if not parameter_updates:
            raise EvolutionSearchError("retrain child requires the search's parameter_updates")
        child = mutate_strategy_artifact(
            parent,
            new_artifact_id=child_id,
            new_version=child_version,
            parameter_updates=parameter_updates,
            source_run_ids=runs,
        )
        return child, {"kind": "retrain", "parameter_updates": dict(parameter_updates)}

    if kind != "mutate":
        raise EvolutionSearchError(f"unsupported child kind {kind!r}")
    logic = parent.get("strategy_logic")
    if not isinstance(logic, Mapping) or logic.get("kind") != "factor_rule_v1":
        raise EvolutionSearchError("structural mutation is defined for factor_rule_v1 only")
    result = mutate_factor_rule_structure(
        logic,
        parent["parameters"],
        parent["mutation_surface"]["controls"],
        rng=rng or random.Random(stable_seed(decision_id, "mutate")),
        operator=structural_operator,
    )
    child = copy.deepcopy(dict(parent))
    child["strategy_logic"] = result["strategy_logic"]
    child["parameters"] = result["parameters"]
    child["mutation_surface"]["controls"] = result["controls"]
    child["artifact_id"] = child_id
    child["version"] = child_version
    child["lineage"]["parent_registry_ids"] = [parent["artifact_id"]]
    child["lineage"]["source_run_ids"] = runs
    try:
        validate_strategy_artifact(child)
    except StrategyArtifactValidationError as exc:
        raise EvolutionSearchError(f"structural child failed artifact validation: {exc}") from exc
    return child, {"kind": "mutate", **result["mutation"]}


def judge_child_out_of_sample(
    child: Mapping[str, Any],
    *,
    gate: Any,
    request_factory: Callable[[Mapping[str, Any]], Any],
    family_id: str,
) -> dict[str, Any]:
    """Judge a child with the §10 gate on the untouched OOS window.

    ``request_factory(child)`` returns the ``ReplicationRequest`` (real backtest
    evaluation bound to the child's checksum, ``family_id`` in its handoff). The
    gate increments the family trial count; the verdict records it.
    """
    before = gate.get_family_trial_count(family_id)
    response = gate.evaluate_candidate(request_factory(child))
    after = gate.get_family_trial_count(family_id)
    detail: Mapping[str, Any] = {}
    for result in getattr(response, "results", []) or []:
        if getattr(result, "criterion_id", "") == "real_backtest_validation":
            detail = getattr(result, "details", {}) or {}
    return {
        "child_artifact_id": child["artifact_id"],
        "parent_registry_ids": list(child["lineage"]["parent_registry_ids"]),
        "passed": bool(response.passed),
        "window": "out_of_sample",
        "family_id": family_id,
        "trial_count_before": before,
        "trial_count_after": after,
        "out_of_sample_sharpe_ratio": detail.get("out_of_sample_sharpe_ratio"),
        "deflated_sharpe_threshold": detail.get("deflated_sharpe_threshold"),
        "failed_checks": list(detail.get("failed_checks") or []),
        "backtest_run_id": detail.get("backtest_run_id"),
    }
