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
