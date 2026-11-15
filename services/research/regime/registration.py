"""Register fitted regime models as registry ``model_artifact`` entries (design R6, R9).

Lifecycle: a model is created as ``draft`` and moved to ``candidate``. It moves on to
``approved`` (paper scope) only when its walk-forward validation passed and its training
history met the minimum; otherwise it stays a candidate. Live scope is never requested here.

``model_version`` is ``<method>@<library version>/<artifact id>`` (design R6), where the
artifact id is derived from the model's content so the same fit always maps to the same id.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from services.research.regime.features import RegimeConfig
from services.research.regime.models import JumpAxisModel, VolatilityModel
from services.research.regime.validation import ValidationReport

APPROVER = "regime-validation-gate"
SCOPE = "paper"


class RegistryClient(Protocol):
    """The registry owner calls the research service needs; implemented over HTTP in ``main``."""

    def create_entry(self, payload: Mapping[str, Any], *, idempotency_key: str) -> Mapping[str, Any]: ...

    def advance(self, registry_id: str, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass
class RegisteredModel:
    axis: str
    horizon: str
    model_version: str
    artifact_id: str
    registry_id: str | None
    artifact_state: str
    reason: str


def library_version(model: VolatilityModel | JumpAxisModel) -> str:
    return model.library.split(" ", 1)[1] if " " in model.library else model.library


def artifact_id_for(model: VolatilityModel | JumpAxisModel, market: str = "TW") -> str:
    axis = "volatility" if isinstance(model, VolatilityModel) else model.axis
    return f"regime-{market.lower()}-{axis}-{model.horizon}-{model.fingerprint()[:16]}"


def model_version_for(model: VolatilityModel | JumpAxisModel, market: str = "TW") -> str:
    """``<method>@<library version>/<artifact id>``, e.g. ``jumpmodels-cjm@0.1.1/regime-tw-trend-short-…``."""
    return f"{model.method}@{library_version(model)}/{artifact_id_for(model, market)}"


def strategy_family_for(model: VolatilityModel | JumpAxisModel, market: str = "TW") -> str:
    axis = "volatility" if isinstance(model, VolatilityModel) else model.axis
    return f"regime-{market.lower()}-{axis}-{model.horizon}"


def _semver(model_fingerprint: str) -> str:
    # One registry (strategy_id, version) per fit; a 3-part number built from the content hash keeps
    # distinct fits distinct and the same fit idempotent.
    n = int(model_fingerprint[:12], 16)
    return f"1.{n % 100000}.{(n // 100000) % 100000}"


def register_regime_model(
    client: RegistryClient, model: VolatilityModel | JumpAxisModel, report: ValidationReport, *,
    input_refs: Sequence[str], config: RegimeConfig | None = None, market: str = "TW",
    source_run_id: str | None = None, storage_path: str = "", market_scope: str = SCOPE,
) -> RegisteredModel:
    """Create the entry as draft, move it to candidate, and approve it for paper only on a pass.

    A failed registry call raises; the caller fails the request closed. No approval is attempted
    unless ``report.passed`` and the model was trained on at least ``min_training_sessions``.
    """
    config = config or RegimeConfig()
    artifact_id = artifact_id_for(model, market)
    version = model_version_for(model, market)
    payload_json = json.dumps(model.to_dict(), sort_keys=True, separators=(",", ":"))
    checksum = hashlib.sha256(payload_json.encode()).hexdigest()
    lineage: dict[str, Any] = {"source_dataset_refs": sorted(set(input_refs))}
    if source_run_id:
        lineage["source_run_ids"] = [source_run_id]
    entry = client.create_entry({
        "artifact_type": "model_artifact",
        "strategy_id": strategy_family_for(model, market),
        "version": _semver(model.fingerprint()),
        "artifact_state": "draft",
        "lineage": lineage,
        "storage_ref": {"backend": "inline", "path": storage_path or f"inline:{artifact_id}"},
        "checksum": checksum,
        "producer_run_id": source_run_id,
        "evaluation_summary": {"validation": report.to_dict(), "model_version": version},
        "metadata": {
            "regime_classifier": True, "model_family": model.method, "model_version": version,
            "model_artifact_id": artifact_id, "axis": getattr(model, "axis", "volatility"),
            "horizon": model.horizon, "market": market, "library": model.library,
            "training_start": model.train_start, "training_end": model.train_end,
            "training_sessions": model.train_sessions, "validation_status": report.status,
            "scope": market_scope, "model": model.to_dict(), "no_language_model": True,
            "input_refs": sorted(set(input_refs)),
        },
    }, idempotency_key=f"regime-model:{artifact_id}")
    registry_id = str(entry.get("entry", entry).get("registry_id") or entry.get("registry_id") or "")
    if not registry_id:
        raise ValueError("registry_entry_without_id")
    state = "draft"
    entry = client.advance(registry_id, {"target_state": "candidate", "expected_artifact_state": "draft",
                                         "command_key": f"{artifact_id}:candidate"})
    state = str((entry.get("entry", entry)).get("artifact_state") or "candidate")
    reason = report.reason
    enough_history = model.train_sessions >= config.min_training_sessions
    if report.passed and enough_history and market_scope == SCOPE:
        entry = client.advance(registry_id, {
            "target_state": "approved", "expected_artifact_state": "candidate", "approver": APPROVER,
            "approval_decision_id": f"regime-validation:{artifact_id}", "command_key": f"{artifact_id}:approved"})
        state = str((entry.get("entry", entry)).get("artifact_state") or "approved")
        reason = "approved_for_paper"
    elif not enough_history:
        reason = f"insufficient_training_history:{model.train_sessions}<{config.min_training_sessions}"
    return RegisteredModel(axis=getattr(model, "axis", "volatility"), horizon=model.horizon, model_version=version,
                           artifact_id=artifact_id, registry_id=registry_id, artifact_state=state, reason=reason)


# ---------------------------------------------------------------------------
# Production entry point: fit, validate and register on request
# ---------------------------------------------------------------------------

MODEL_AXES = ("volatility", "trend", "risk_appetite")


def run_regime_research(
    observations: Sequence[Mapping[str, Any]], *, decision_time: Any, client: RegistryClient | None,
    config: RegimeConfig | None = None, market: str = "TW", source_run_id: str | None = None,
    axes: Sequence[str] = MODEL_AXES, horizons: Sequence[str] = ("short", "medium"),
) -> dict[str, Any]:
    """Fit, walk-forward validate and register the regime models over governed observations.

    Fail-closed by construction: an axis whose inputs are missing is reported ``unavailable``
    with the reason and fits nothing; with too little history a model may be fitted but is only
    registered as a candidate; nothing is approved unless validation passed. The macro axis is a
    rule set (no fitted model) and is reported from the NDC series. No language model is used.
    """
    from services.research.regime.features import BUILDERS, PROPOSALS, build_macro_inputs
    from services.research.regime.validation import fit_and_validate

    config = config or RegimeConfig()
    results: list[dict[str, Any]] = []
    for axis in axes:
        for horizon in horizons:
            inputs = BUILDERS[axis](observations, decision_time=decision_time, horizon=horizon, config=config)
            entry: dict[str, Any] = {"axis": axis, "horizon": horizon, "input_refs": list(inputs.input_refs)}
            if not inputs.available:
                entry.update(status="unavailable", reason=inputs.reason)
                results.append(entry)
                continue
            model, report = fit_and_validate(inputs, config)
            entry.update(status=report.status, reason=report.reason, validation=report.to_dict(), sessions=inputs.sessions)
            if model is None:
                results.append(entry)
                continue
            if client is None:
                entry.update(registration={"status": "not_requested"})
            else:
                registered = register_regime_model(client, model, report, input_refs=inputs.input_refs, config=config,
                                                   market=market, source_run_id=source_run_id)
                entry.update(registration={"status": registered.artifact_state, "registry_id": registered.registry_id,
                                           "model_version": registered.model_version, "reason": registered.reason})
            results.append(entry)
    macro_sessions, macro_inputs = build_macro_inputs(observations, decision_time=decision_time, config=config)
    macro = {"axis": "macro", "method": "rule_based", "input_refs": list(macro_inputs.input_refs)}
    if macro_inputs.available:
        macro.update(status="available", sessions=len(macro_sessions), latest=macro_sessions[-1].__dict__)
    else:
        macro.update(status="unavailable", reason=macro_inputs.reason)
    results.append(macro)
    fitted = [r for r in results if r.get("registration", {}).get("status") in ("candidate", "approved")]
    return {"market": market, "decision_time": str(decision_time), "paper_only": True, "no_language_model": True,
            "status": "completed" if fitted else "unavailable", "axes": results,
            "approved": [r["registration"]["model_version"] for r in fitted if r["registration"]["status"] == "approved"],
            "config": config.to_dict(), "proposals": list(PROPOSALS)}
