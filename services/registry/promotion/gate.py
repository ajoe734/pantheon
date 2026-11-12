"""
REG-002: Pantheon promotion gate and EX-001 execution projection helpers.
"""
from __future__ import annotations

import copy
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class PromotionState(str, Enum):
    DRAFT = "draft"
    CANDIDATE = "candidate"
    PAPER = "paper"
    CANARY = "canary"
    LIVE = "live"
    RETIRED = "retired"


class PromotionError(ValueError):
    """Raised when a promotion requirement is not met."""


@dataclass(frozen=True)
class ExecutionProjection:
    metadata_key: str
    artifact_key: str
    metadata: dict[str, Any]


_LIFECYCLE_TO_ARTIFACT_STATE: dict[str, str] = {
    "candidate": "candidate",
    "paper": "approved",
    "canary": "approved",
    "live": "approved",
    "retired": "retired",
}

_LIFECYCLE_TO_DEPLOYMENT_STAGE: dict[str, str] = {
    "candidate": "none",
    "paper": "paper",
    "canary": "canary",
    "live": "live",
    "retired": "none",
}


def real_backtest_verdict_digest(details: Any) -> str:
    """Canonical digest of the gate's real_backtest_validation details (producer and Registry agree)."""
    import hashlib
    import json

    return "sha256:" + hashlib.sha256(
        json.dumps(details, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


class PromotionGate:
    def __init__(self, logger: logging.Logger | None = None):
        self.log = logger or logging.getLogger(__name__)

    @staticmethod
    def build_object_store_keys(strategy_id: str, version: str) -> tuple[str, str]:
        base_key = f"openclaw/registry/{strategy_id}/{version}"
        return f"{base_key}/metadata.json", f"{base_key}/artifact.bin"

    def validate_transition(self, current_state: PromotionState, target_state: PromotionState) -> None:
        """Enforces allowed transitions per REG-001 and PAPER_CANARY_LIVE_POLICY canonical path."""
        allowed = {
            PromotionState.DRAFT: [PromotionState.CANDIDATE, PromotionState.RETIRED],
            PromotionState.CANDIDATE: [PromotionState.PAPER, PromotionState.RETIRED],
            PromotionState.PAPER: [PromotionState.CANARY, PromotionState.RETIRED],
            PromotionState.CANARY: [PromotionState.LIVE, PromotionState.RETIRED],
            PromotionState.LIVE: [PromotionState.RETIRED],
        }

        if target_state not in allowed.get(current_state, []):
            raise PromotionError(f"Forbidden transition: {current_state.value} -> {target_state.value}")

    @staticmethod
    def build_draft_entry_from_replication_request(
        promotion_request: Any,
        *,
        registry_id: str | None = None,
        strategy_id: str | None = None,
        checksum: str = "sha256:replication-verified",
    ) -> dict[str, Any]:
        """
        Build a draft registry entry from a ReplicationGate RegistryPromotionRequest,
        preserving real replication_success, out-of-sample sharpe_ratio, and backtest lineage.
        """
        payload = (
            promotion_request.to_dict()
            if hasattr(promotion_request, "to_dict")
            else dict(promotion_request)
        )
        reg_entry = copy.deepcopy(dict(payload.get("registry_entry") or {}))
        content = reg_entry.get("content") if isinstance(reg_entry.get("content"), Mapping) else {}
        resolved_strategy_id = (
            strategy_id
            or reg_entry.get("strategy_id")
            or content.get("strategy_id")
            or content.get("name")
            or payload.get("candidate_id")
            or "unknown-strategy"
        )
        version = str(reg_entry.get("version") or "1.0.0")
        resolved_registry_id = registry_id or f"reg-{resolved_strategy_id}-{version}"
        lineage = copy.deepcopy(dict(payload.get("lineage") or {}))
        eval_summary = copy.deepcopy(dict(reg_entry.get("evaluation_summary") or {}))
        rep_proof = payload.get("replication_proof")
        if not eval_summary and isinstance(rep_proof, Mapping):
            if isinstance(rep_proof.get("evaluation_summary"), Mapping):
                eval_summary = copy.deepcopy(dict(rep_proof["evaluation_summary"]))

        mechanism_meta: dict[str, Any] = {}
        for mkey in ("mechanism", "mechanism_status", "mechanism_support"):
            for holder in (reg_entry, rep_proof if isinstance(rep_proof, Mapping) else {}):
                if holder.get(mkey) is not None:
                    mechanism_meta[mkey] = copy.deepcopy(holder[mkey])
                    break

        draft = {
            "registry_id": resolved_registry_id,
            "artifact_type": reg_entry.get("artifact_type", "strategy_spec"),
            "strategy_id": resolved_strategy_id,
            "version": version,
            "lifecycle_state": "draft",
            "checksum": checksum,
            "replication_success": bool(reg_entry.get("replication_success")),
            "evaluation_summary": eval_summary,
            "replication_proof": copy.deepcopy(rep_proof) if isinstance(rep_proof, Mapping) else {},
            "lineage": lineage,
            "storage_ref": {
                "backend": payload.get("storage_backend", "object_store"),
                "path": payload.get("storage_path", ""),
            },
        }
        if mechanism_meta:
            # persisted in entry metadata so the registry read projection carries the gate's mechanism record
            draft["metadata"] = mechanism_meta
        return draft

    @staticmethod
    def verify_admitted_replication_proof(
        proof: Any, evaluation_summary: Any, lineage: Any, *,
        backtest_evaluation: Any = None, artifact_checksum: str = "", producer_run_id: str = "",
    ) -> dict[str, Any]:
        """Return a copy of ``proof`` iff it is an admitted gate verdict bound to its evaluation.

        The caller must also supply the producer's full ``backtest_evaluation`` (the
        run the ReplicationGate evaluated).  The registry owner recomputes from it:
        the run digest (same canonical form as ``BacktestRunStore``), the artifact
        checksum binding, the out-of-sample metrics, and every
        ``real_backtest_validation`` check.  The summary, verdict details and checks
        must equal those recomputed values, so altered metrics, an all-true
        fabricated checks block, or a proof for another artifact/run is refused.
        The registry-verified binding is stored under ``owner_verified_binding``.
        """
        import hashlib
        import json

        if not isinstance(proof, Mapping) or not proof:
            raise PromotionError("replication_proof must be a non-empty object.")
        summary = evaluation_summary if isinstance(evaluation_summary, Mapping) else {}
        run_id = str(summary.get("backtest_run_id") or "").strip()
        status = getattr(proof.get("admission_status"), "value", proof.get("admission_status"))
        if str(status) != "admitted" or not run_id or str(proof.get("backtest_run_id") or "") != run_id:
            raise PromotionError("replication_proof is not an admitted verdict for this backtest run.")
        if proof.get("evaluation_summary") != summary:
            raise PromotionError("replication_proof evaluation_summary differs from the entry summary.")
        if run_id != producer_run_id and run_id not in (_normalize_lineage(lineage).get("source_run_ids") or []):
            raise PromotionError("replication_proof backtest run is neither the producer run nor in entry lineage.")
        from services.research.replication.gate_config import GateConfig as _GC

        _gc = _GC()
        results = [r for r in proof.get("replication_results") or [] if isinstance(r, Mapping)]
        for criterion in _gc.get_required_criteria():
            matching = [r for r in results if r.get("criterion_id") == criterion.criterion_id]
            if len(matching) != 1 or matching[0].get("passed") is not True:
                raise PromotionError(
                    f"replication_proof required criterion {criterion.criterion_id} is missing or did not pass."
                )
        optional = [r for r in results if r.get("criterion_id") in {c.criterion_id for c in _gc.get_optional_criteria()}]
        if optional and sum(1 for r in optional if r.get("passed") is True) / len(optional) < _GC.ADMISSION_THRESHOLD_OPTIONAL:
            raise PromotionError("replication_proof optional criteria pass rate is below the admission threshold.")
        verdicts = [r for r in results if r.get("criterion_id") == "real_backtest_validation"]
        details = verdicts[0].get("details") if len(verdicts) == 1 else None
        details = details if isinstance(details, Mapping) else {}
        checks = details.get("checks")
        if (len(verdicts) != 1 or verdicts[0].get("passed") is not True
                or details.get("status") != "passed" or details.get("failed_checks")
                or str(details.get("backtest_run_id") or "") != run_id
                or not isinstance(checks, Mapping) or not checks
                or not all(v is True for v in checks.values())):
            raise PromotionError("replication_proof real_backtest_validation did not pass.")

        ev = backtest_evaluation
        if not isinstance(ev, Mapping) or not ev:
            raise PromotionError("backtest_evaluation is required to bind the gate verdict.")
        identity = ev.get("strategy_artifact") if isinstance(ev.get("strategy_artifact"), Mapping) else {}
        if (str(ev.get("backtest_run_id") or "") != run_id or ev.get("status") != "completed"
                or not artifact_checksum or str(identity.get("checksum") or "") != artifact_checksum
                or ev.get("backend") != "vectorbt_portfolio" or ev.get("market") != "TW"
                or ev.get("governed_data") is not True or not ev.get("source_dataset_refs")):
            raise PromotionError("backtest_evaluation is not a completed governed run of this artifact.")
        digest = "sha256:" + hashlib.sha256(
            json.dumps(dict(ev), sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

        try:
            from services.research.replication.gate_config import GateConfig
            from services.research.vectorbt.adapter.vectorbt_adapter import (
                TAIWAN_BROKER_COMMISSION_RATE, TAIWAN_EQUITY_TRANSACTION_TAX_RATE,
                TAIWAN_ETF_TRANSACTION_TAX_RATE,
            )

            cfg = GateConfig()
            # Every non-metric prerequisite the ReplicationGate enforces before it computes checks.
            hist, proto = ev["history_check"], ev["protocol"]
            costs, benches = ev["transaction_costs"], ev["benchmarks"]
            if not (
                hist["sufficient"] is True and int(hist["calendar_days"]) >= cfg.min_history_calendar_days
                and int(hist["trading_days"]) >= cfg.min_history_trading_days
                and abs(float(proto["in_sample_ratio"]) - cfg.in_sample_ratio) <= 1e-6
                and abs(float(proto["out_of_sample_ratio"]) - cfg.out_of_sample_ratio) <= 1e-6
                and int(proto["walk_forward_folds"]) >= cfg.walk_forward_folds
                and abs(float(costs["commission_rate"]) - TAIWAN_BROKER_COMMISSION_RATE) <= 1e-9
                and abs(float(costs["equity_transaction_tax_rate"]) - TAIWAN_EQUITY_TRANSACTION_TAX_RATE) <= 1e-9
                and abs(float(costs["etf_transaction_tax_rate"]) - TAIWAN_ETF_TRANSACTION_TAX_RATE) <= 1e-9
                and costs["applied_on_every_fill"] is True
                and isinstance(costs["citations"], Mapping) and costs["citations"]
                and isinstance(benches["buy_and_hold_same_symbols"], Mapping)
                and isinstance(benches["tw_0050"], Mapping)
            ):
                raise PromotionError("backtest_evaluation fails the gate's history/protocol/cost/benchmark prerequisites.")
            oos, ins = ev["out_of_sample"], ev["in_sample"]
            bench = ev["benchmarks"]["buy_and_hold_same_symbols"]
            folds = ev["walk_forward"]["folds"]
            sharpe = float(oos["sharpe_ratio"])
            drawdown = abs(float(oos["max_drawdown"]))
            trips = int(oos["round_trips"])
            fold_sharpes = [float(f["sharpe_ratio"]) for f in folds]
            fold_returns = [float(f["total_return"]) for f in folds]
            trial_count = int(details["trial_count"])
            deflation = cfg.compute_deflated_sharpe_threshold(trial_count)
            mandate = float(details["mandate_max_drawdown"])
            # Policy context is caller-supplied in the verdict: it may tighten but never loosen
            # the gate's mandate, and the trial count may never undercut the producer-recorded one.
            preset_trials = ev.get("trial_count")
            if (mandate > cfg.max_oos_drawdown + 1e-12 or trial_count < 1
                    or (isinstance(preset_trials, int) and trial_count < preset_trials)):
                raise PromotionError("replication_proof policy context is looser than the authoritative gate policy.")
            threshold = float(deflation["deflated_sharpe_threshold"])
            positive = [r for r in fold_returns if r > 0.0]
            share = max(positive) / sum(positive) if positive and sum(positive) > 0.0 else 1.0
            expected_checks = {
                "oos_sharpe_positive_after_costs": sharpe > 0.0,
                "oos_sharpe_exceeds_deflated_threshold": sharpe > threshold,
                "oos_sharpe_beats_buy_and_hold": sharpe > float(bench["sharpe_ratio"]),
                "oos_max_drawdown_within_mandate": drawdown <= mandate,
                "oos_minimum_round_trips_met": trips >= cfg.min_oos_round_trips,
                "walk_forward_consistent_across_folds": (
                    len(folds) >= cfg.walk_forward_folds and all(x > 0.0 for x in fold_sharpes)
                    and len(positive) >= 2 and share < 1.0),
            }
            derived = {
                "out_of_sample_sharpe_ratio": sharpe, "out_of_sample_max_drawdown": drawdown,
                "out_of_sample_round_trips": trips, "in_sample_sharpe_ratio": float(ins["sharpe_ratio"]),
                "out_of_sample_total_return": float(oos["total_return"]),
                "buy_and_hold_sharpe_ratio": float(bench["sharpe_ratio"]),
                "walk_forward_fold_sharpes": fold_sharpes,
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise PromotionError("backtest_evaluation lacks the gate's out-of-sample evidence.") from exc
        if (not all(expected_checks.values()) or dict(checks) != expected_checks
                or any(details.get(k) != v for k, v in derived.items())
                or summary.get("out_of_sample_sharpe_ratio") != sharpe
                or summary.get("max_drawdown") != drawdown or summary.get("round_trips") != trips):
            raise PromotionError("replication_proof differs from the recomputed backtest evaluation.")

        verified = copy.deepcopy(dict(proof))
        verified["owner_verified_binding"] = {
            "backtest_run_id": run_id, "artifact_checksum": artifact_checksum,
            "evaluation_digest": digest, "status": "completed",
            "out_of_sample_sharpe_ratio": sharpe, "out_of_sample_max_drawdown": drawdown,
            "out_of_sample_round_trips": trips,
            "gate_verdict": {
                "admission_status": "admitted", "trial_count": trial_count, "mandate_max_drawdown": mandate,
                "family_id": details.get("family_id"), "verdict_digest": real_backtest_verdict_digest(dict(details)),
            },
        }
        return verified

    def check_requirements(self, target_state: PromotionState, metadata: Mapping[str, Any]) -> None:
        """Enforces metadata requirements for each promotion state."""
        lineage = _normalize_lineage(metadata.get("lineage"))

        if target_state == PromotionState.CANDIDATE:
            if metadata.get("replication_success") is not True:
                raise PromotionError("Candidate promotion requires 'replication_success' flag.")
            if not _has_source_reference(lineage):
                raise PromotionError(
                    "Candidate promotion requires lineage with source_run_ids, "
                    "source_strategy_spec_id, or source_dataset_refs."
                )
            eval_summary = metadata.get("evaluation_summary")
            if isinstance(eval_summary, Mapping) and eval_summary:
                _validate_sharpe_summary(eval_summary, stage_label="Candidate")

        elif target_state == PromotionState.PAPER:
            eval_summary = metadata.get("evaluation_summary", {})
            if not isinstance(eval_summary, Mapping):
                raise PromotionError("Paper promotion requires an evaluation_summary object.")
            if not eval_summary.get("risk_review_passed"):
                raise PromotionError("Paper promotion requires 'risk_review_passed' in evaluation_summary.")
            if eval_summary.get("sharpe_ratio") in (None, ""):
                raise PromotionError("Paper promotion requires Sharpe Ratio metric.")
            _validate_sharpe_summary(eval_summary, stage_label="Paper")
            if not _has_source_reference(lineage):
                raise PromotionError("Paper promotion requires lineage with at least one source reference.")

        elif target_state == PromotionState.CANARY:
            eval_summary = metadata.get("evaluation_summary", {})
            if not isinstance(eval_summary, Mapping):
                raise PromotionError("Canary promotion requires an evaluation_summary object.")
            if not eval_summary.get("risk_review_passed"):
                raise PromotionError("Canary promotion requires 'risk_review_passed' in evaluation_summary.")
            if eval_summary.get("sharpe_ratio") in (None, ""):
                raise PromotionError("Canary promotion requires Sharpe Ratio metric.")
            _validate_sharpe_summary(eval_summary, stage_label="Canary")
            if not metadata.get("approver"):
                raise PromotionError("Canary promotion requires an explicit 'approver'.")
            if not _has_source_reference(lineage):
                raise PromotionError("Canary promotion requires lineage with at least one source reference.")
            rollback = _extract_rollback(metadata)
            if rollback is None:
                raise PromotionError(
                    "Canary promotion requires metadata.rollback or "
                    "metadata.rollback_target_registry_id plus rollback_target."
                )
            _validate_rollback(
                rollback,
                current_registry_id=metadata.get("registry_id"),
                current_version=metadata.get("version"),
            )

        elif target_state == PromotionState.LIVE:
            if not metadata.get("approver"):
                raise PromotionError("Live promotion requires an explicit 'approver'.")
            if not _has_source_reference(lineage):
                raise PromotionError("Live promotion requires lineage with at least one source reference.")

            rollback = _extract_rollback(metadata)
            if rollback is None:
                raise PromotionError(
                    "Live promotion requires metadata.rollback or "
                    "metadata.rollback_target_registry_id plus rollback_target."
                )
            _validate_rollback(
                rollback,
                current_registry_id=metadata.get("registry_id"),
                current_version=metadata.get("version"),
            )

    def promote(
        self,
        entry: Mapping[str, Any],
        target_state: PromotionState,
        approver: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Attempts to promote a registry entry and returns the updated entry if successful.
        """
        updated_entry = copy.deepcopy(dict(entry))
        current_state = PromotionState(updated_entry.get("lifecycle_state", "draft"))

        self.log.info(
            "Attempting promotion: %s@%s (%s -> %s)",
            updated_entry.get("strategy_id"),
            updated_entry.get("version"),
            current_state.value,
            target_state.value,
        )

        self.validate_transition(current_state, target_state)
        if approver:
            updated_entry["approver"] = approver

        self.check_requirements(target_state, updated_entry)

        updated_entry["lifecycle_state"] = target_state.value
        updated_entry["promoted_at"] = utc_now()
        self.log.info(
            "Promotion successful: %s is now %s",
            updated_entry.get("strategy_id"),
            target_state.value,
        )
        return updated_entry

    def build_execution_projection(self, entry: Mapping[str, Any]) -> ExecutionProjection:
        """
        Materialize the EX-001 metadata envelope and canonical Object Store keys.
        """
        normalized = copy.deepcopy(dict(entry))
        state = normalized.get("lifecycle_state")
        if state in (None, "", "draft"):
            raise PromotionError("Execution projection requires a promoted entry, not draft state.")

        required = ("registry_id", "artifact_type", "strategy_id", "version", "checksum")
        missing = [field for field in required if normalized.get(field) in (None, "")]
        if missing:
            raise PromotionError(
                "Execution projection requires registry fields: " + ", ".join(missing)
            )

        lineage = _normalize_lineage(normalized.get("lineage"))
        if not isinstance(lineage, dict):
            raise PromotionError("Execution projection requires a lineage object.")

        artifact_state = _LIFECYCLE_TO_ARTIFACT_STATE.get(state, state)
        deployment_stage = _LIFECYCLE_TO_DEPLOYMENT_STAGE.get(state, "none")
        metadata = {
            "registry_id": normalized["registry_id"],
            "strategy_id": normalized["strategy_id"],
            "version": normalized["version"],
            "artifact_type": normalized["artifact_type"],
            "artifact_state": artifact_state,
            "deployment_stage": deployment_stage,
            "checksum": normalized["checksum"],
            "lineage": lineage,
            "created_at": normalized.get("created_at") or normalized.get("promoted_at") or utc_now(),
        }

        if normalized.get("promoted_at"):
            metadata["approved_at"] = normalized["promoted_at"]
        if normalized.get("approver"):
            metadata["approver"] = normalized["approver"]

        rollback = _extract_rollback(normalized)
        if rollback is not None:
            _validate_rollback(
                rollback,
                current_registry_id=normalized.get("registry_id"),
                current_version=normalized.get("version"),
            )
            metadata["rollback"] = rollback
        if state in (PromotionState.CANARY.value, PromotionState.LIVE.value) and rollback is None:
            raise PromotionError("Canary and live execution projections require explicit rollback metadata.")

        metadata_key, artifact_key = self.build_object_store_keys(
            strategy_id=normalized["strategy_id"],
            version=normalized["version"],
        )
        return ExecutionProjection(
            metadata_key=metadata_key,
            artifact_key=artifact_key,
            metadata=metadata,
        )


def _normalize_lineage(lineage: Any) -> dict[str, Any]:
    if not isinstance(lineage, Mapping):
        return {}

    normalized = {key: value for key, value in lineage.items() if value is not None}
    if "source_run_ids" not in normalized and normalized.get("source_run_id"):
        normalized["source_run_ids"] = [normalized["source_run_id"]]
    return normalized


def _has_source_reference(lineage: Mapping[str, Any]) -> bool:
    return bool(
        lineage.get("source_run_ids")
        or lineage.get("source_strategy_spec_id")
        or lineage.get("source_dataset_refs")
    )


def _extract_rollback(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    metadata = entry.get("metadata")
    if isinstance(metadata, Mapping):
        rollback = metadata.get("rollback")
        if isinstance(rollback, Mapping):
            required = ("target_registry_id", "target_version")
            missing = [field for field in required if rollback.get(field) in (None, "")]
            if missing:
                raise PromotionError(
                    "metadata.rollback missing required fields: " + ", ".join(missing)
                )
            return dict(rollback)

        registry_id = metadata.get("rollback_target_registry_id")
        version = entry.get("rollback_target")
        if registry_id and version:
            rollback_payload = {
                "target_registry_id": registry_id,
                "target_version": version,
            }
            reason = metadata.get("rollback_reason")
            verified_at = metadata.get("rollback_verified_at")
            if reason:
                rollback_payload["reason"] = reason
            if verified_at:
                rollback_payload["verified_at"] = verified_at
            return rollback_payload

    return None


def _validate_sharpe_summary(
    eval_summary: Mapping[str, Any],
    *,
    stage_label: str,
) -> None:
    raw_sharpe = eval_summary.get("sharpe_ratio")
    if raw_sharpe in (None, ""):
        raise PromotionError(f"{stage_label} promotion requires Sharpe Ratio metric.")
    try:
        sharpe_value = float(raw_sharpe)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{stage_label} promotion requires a numeric sharpe_ratio.") from exc
    if not math.isfinite(sharpe_value) or sharpe_value <= 0.0:
        raise PromotionError(
            f"{stage_label} promotion requires a positive out-of-sample sharpe_ratio (got {sharpe_value})."
        )
    deflated_threshold = eval_summary.get("deflated_sharpe_threshold")
    if deflated_threshold not in (None, ""):
        try:
            threshold_val = float(deflated_threshold)
        except (TypeError, ValueError) as exc:
            raise PromotionError(
                f"{stage_label} promotion deflated_sharpe_threshold must be numeric."
            ) from exc
        if sharpe_value <= threshold_val:
            raise PromotionError(
                f"{stage_label} promotion requires sharpe_ratio ({sharpe_value:.3f}) > "
                f"deflated_sharpe_threshold ({threshold_val:.3f})."
            )


def _validate_rollback(
    rollback: Mapping[str, Any],
    *,
    current_registry_id: Any,
    current_version: Any,
) -> None:
    if rollback.get("target_registry_id") in (None, ""):
        raise PromotionError("Rollback metadata requires target_registry_id.")
    if rollback.get("target_version") in (None, ""):
        raise PromotionError("Rollback metadata requires target_version.")
    if current_registry_id and rollback.get("target_registry_id") == current_registry_id:
        raise PromotionError("Rollback target_registry_id cannot equal the current registry_id.")
    if current_version and rollback.get("target_version") == current_version:
        raise PromotionError("Rollback target_version cannot equal the current version.")
