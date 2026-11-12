"""
Replication Gate Executor

Core implementation of the first-pass replication gate.
Validates research candidates against admission criteria before
allowing promotion to the registry.
"""

import copy
import hashlib
import math
import os
import uuid
import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import date as date_cls, datetime

from jsonschema import Draft7Validator, RefResolver

from .gate_schema import (
    ReplicationRequest,
    ReplicationResponse,
    ReplicationResult,
    ReplicationStatus,
    CandidateAdmissionStatus,
    RegistryPromotionRequest,
)
from .gate_config import GateConfig, AdmissionRules
from .mechanism_tests import (
    CONTRADICTED as MECHANISM_PREDICTION_CONTRADICTED,
    MECHANISM_CONTRADICTED,
    run_mechanism_stage,
)
from services.research.vectorbt.adapter.vectorbt_adapter import (
    INSUFFICIENT_HISTORY_STATUS,
    INTRADAY_FREQUENCY_MINUTES,
    REPLAY_LABEL,
    BacktestConfig,
    GovernedVectorbtInputAdapter,
    VectorbtBackend,
    VectorbtWorkflowError,
    _artifact_identity,
    _compute_buy_and_hold_benchmark,
    _session_of,
    _slice_prepared_dataset,
    _summarize_window_result,
    bars_per_session,
    run_vectorbt_workflow,
    TAIWAN_BROKER_COMMISSION_RATE,
    VECTORBT_VERSION_PIN,
    TAIWAN_COST_RATE_CITATIONS,
    TAIWAN_EQUITY_TRANSACTION_TAX_RATE,
    TAIWAN_ETF_TRANSACTION_TAX_RATE,
    build_canonical_tw_strategy_artifact,
    run_replication_backtest_protocol,
)


INTRADAY_UNQUALIFIED_STATUS = "intraday_evidence_unqualified"
INTRADAY_SOURCE_DATASET = "tw_price_1m"
_ISOLATION_FLAGS = (
    "waived", "waiver", "quarantined", "quarantine", "isolated", "partial", "unreconciled",
    "waived_days", "quarantined_days", "isolated_days",
)


def _is_intraday_evaluation(evaluation: Dict[str, Any]) -> bool:
    block = evaluation.get("intraday")
    return (
        isinstance(block, dict)
        or evaluation.get("evidence_mode") == REPLAY_LABEL
        or str(evaluation.get("bar_frequency") or "") in INTRADAY_FREQUENCY_MINUTES
    )


def run_intraday_replication_protocol(
    dataset: Dict[str, Any],
    strategy_artifact: Dict[str, Any],
    *,
    config: GateConfig,
    mandate_max_drawdown: float = 0.25,
    run_id: Optional[str] = None,
    backend: Any = None,
) -> Dict[str, Any]:
    """Session-based replication protocol on a governed minute replay dataset (K6).

    Same 70/30 IS/OOS + walk-forward shape, statutory costs and benchmarks as the daily C2
    protocol, but windows are cut on SESSION boundaries and sizes are counted in clean sessions
    and round-trip trades.  Fills, annualisation, flatten-by-13:25 and the replay label come from
    the K5 engine (``run_vectorbt_workflow`` / ``VectorbtBackend``); nothing is reconstructed.
    Only reconciled ``tw_price_1m`` replay data is accepted (the K5 governed boundary enforces
    it); there is no daily or synthetic fallback.
    """
    from services.execution.shioaji_adapter import normalize_taiwan_symbol
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        validate_strategy_artifact,
    )

    try:
        validate_strategy_artifact(strategy_artifact)
    except StrategyArtifactValidationError as exc:
        raise VectorbtWorkflowError(f"strategy artifact validation failed: {exc}") from exc
    parameters = strategy_artifact.get("parameters") or {}
    if "screen" in (strategy_artifact.get("strategy_logic") or {}):
        raise VectorbtWorkflowError("screen strategies are daily-only; no intraday screen replay is defined")
    if str(parameters.get("market") or "TW").strip().upper() != "TW":
        raise VectorbtWorkflowError("intraday replication protocol requires governed Taiwan market ('TW')")

    prepared = GovernedVectorbtInputAdapter().prepare(dataset)
    frequency = prepared.data_frequency
    if frequency not in INTRADAY_FREQUENCY_MINUTES:
        raise VectorbtWorkflowError(f"{frequency!r} is not an intraday frequency")
    snapshot = dict((prepared.replay or {}).get("source_snapshot") or {})

    wanted = {normalize_taiwan_symbol(s) for s in parameters.get("symbols", [])}
    targets = [i for i in prepared.instruments if normalize_taiwan_symbol(i) in wanted]
    bench_0050 = [i for i in prepared.instruments if i.split(".")[0].strip() == "0050"]
    if not targets:
        raise VectorbtWorkflowError("no target instrument present in the replay dataset")

    # Clean session = every target instrument has the full regular bar count for that session.
    expected = bars_per_session(frequency)
    per_symbol_sessions: Dict[str, Dict[str, int]] = {}
    for inst in targets:
        counts: Dict[str, int] = {}
        for key in prepared.dates_by_instrument[inst]:
            counts[_session_of(key)] = counts.get(_session_of(key), 0) + 1
        per_symbol_sessions[inst] = counts
    all_sessions = sorted(set().union(*[set(c) for c in per_symbol_sessions.values()]))
    clean = [
        s for s in all_sessions
        if all(per_symbol_sessions[i].get(s) == expected for i in targets)
    ]
    unclean = [s for s in all_sessions if s not in clean]
    aligned = all(
        prepared.dates_by_instrument[i] == prepared.dates_by_instrument[targets[0]] for i in targets
    )
    n_sessions = len(clean)
    is_sessions = int(n_sessions * config.in_sample_ratio)
    oos_sessions = n_sessions - is_sessions
    fold_edges = [(k * is_sessions) // config.walk_forward_folds for k in range(config.walk_forward_folds + 1)]

    history_sufficient = (
        aligned and not unclean
        and n_sessions >= config.min_clean_sessions
        and oos_sessions >= config.min_oos_sessions
    )
    first_day = date_cls.fromisoformat(clean[0]) if clean else None
    last_day = date_cls.fromisoformat(clean[-1]) if clean else None
    history_check = {
        "sufficient": bool(history_sufficient),
        "calendar_days": (last_day - first_day).days if first_day and last_day else 0,
        "trading_days": n_sessions,
        "required_calendar_days": 0,
        "required_trading_days": config.min_clean_sessions,
        "unit": "clean_sessions",
        "details": {
            "clean_sessions": n_sessions,
            "unclean_sessions": unclean,
            "bar_aligned_across_targets": aligned,
            "start_session": clean[0] if clean else None,
            "end_session": clean[-1] if clean else None,
        },
    }
    ident = _artifact_identity(strategy_artifact)
    base: Dict[str, Any] = {
        "backtest_run_id": run_id or f"bt-intraday-{_sha(prepared.dataset_id + str(ident))[:12]}",
        "backend": "vectorbt_portfolio",
        "framework": "vectorbt",
        "market": "TW",
        "governed_data": True,
        "dataset_id": prepared.dataset_id,
        "source_dataset_refs": list(prepared.source_dataset_refs),
        "strategy_artifact": ident,
        "bar_frequency": frequency,
        "evidence_mode": REPLAY_LABEL,
        "live_or_paper_evidence": False,
        "history_check": history_check,
        "pit_history_days": history_check["calendar_days"],
        "pit_history_bars": n_sessions,
    }
    base["artifact_ref"] = f"vectorbt://backtest-runs/{base['backtest_run_id']}"
    if not history_sufficient:
        return {
            **base,
            "status": INSUFFICIENT_HISTORY_STATUS,
            "reason": (
                f"intraday replay has {n_sessions} clean sessions ({len(unclean)} unclean, "
                f"{oos_sessions} OOS); need >= {config.min_clean_sessions} clean sessions and "
                f">= {config.min_oos_sessions} OOS sessions"
            ),
        }

    split_idx = prepared.dates_by_instrument[targets[0]].index(
        next(k for k in prepared.dates_by_instrument[targets[0]] if _session_of(k) == clean[is_sessions])
    )
    fold_idx = []
    for e in fold_edges:
        fold_idx.append(
            split_idx if e >= is_sessions else next(
                i for i, k in enumerate(prepared.dates_by_instrument[targets[0]]) if _session_of(k) == clean[e]
            )
        )
    total_len = len(prepared.dates_by_instrument[targets[0]])
    is_ds = _slice_prepared_dataset(prepared, 0, split_idx, "in_sample")
    oos_ds = _slice_prepared_dataset(prepared, split_idx, total_len, "out_of_sample")
    fold_ds = [
        _slice_prepared_dataset(prepared, fold_idx[k], fold_idx[k + 1], f"wf_fold_{k + 1}")
        for k in range(config.walk_forward_folds)
    ]
    base_cfg = BacktestConfig(strategy_artifact=dict(strategy_artifact))
    bt_config = BacktestConfig(
        version=base_cfg.version,
        requested_by=base_cfg.requested_by,
        strategy_params=dict(parameters),
        strategy_artifact=dict(strategy_artifact),
        init_cash=base_cfg.init_cash,
        fees=base_cfg.commission_rate,
        commission_rate=base_cfg.commission_rate,
        transaction_tax_rate=base_cfg.transaction_tax_rate,
        etf_transaction_tax_rate=base_cfg.etf_transaction_tax_rate,
        apply_taiwan_fill_costs=True,
        storage_backend=base_cfg.storage_backend,
        storage_path_template=base_cfg.storage_path_template,
    )
    previous = os.environ.get("PANTHEON_VECTORBT_BACKEND")
    os.environ["PANTHEON_VECTORBT_BACKEND"] = "real"
    try:
        runner = backend or VectorbtBackend()
        full = run_vectorbt_workflow(dataset, backend=runner, config=bt_config)
        is_res = runner.run(is_ds, bt_config)
        oos_res = runner.run(oos_ds, bt_config)
        fold_res = [runner.run(d, bt_config) for d in fold_ds]
    finally:
        if previous is None:
            os.environ.pop("PANTHEON_VECTORBT_BACKEND", None)
        else:
            os.environ["PANTHEON_VECTORBT_BACKEND"] = previous
    is_sum = _summarize_window_result(is_ds, is_res, window_fraction=config.in_sample_ratio)
    oos_sum = _summarize_window_result(oos_ds, oos_res, window_fraction=round(1.0 - config.in_sample_ratio, 4))
    folds = []
    for idx, (d, r) in enumerate(zip(fold_ds, fold_res), start=1):
        f = _summarize_window_result(d, r, window_fraction=round(config.in_sample_ratio / config.walk_forward_folds, 4))
        f["fold_index"] = idx
        folds.append(f)
    sharpes = [float(f["sharpe_ratio"]) for f in folds]
    bh_same = _compute_buy_and_hold_benchmark(oos_ds, targets, bt_config)
    bh_0050 = _compute_buy_and_hold_benchmark(oos_ds, bench_0050, bt_config)
    agg = full.backtest_result.aggregate_metrics
    costs = {
        "applied_on_every_fill": True,
        "commission_rate": round(float(bt_config.commission_rate), 6),
        "transaction_tax_rate": round(float(bt_config.transaction_tax_rate), 6),
        "equity_transaction_tax_rate": round(float(bt_config.transaction_tax_rate), 6),
        "etf_transaction_tax_rate": round(float(bt_config.etf_transaction_tax_rate), 6),
        "total_commission_paid": round(float(agg.get("total_commission_paid", 0.0)), 6),
        "total_transaction_tax_paid": round(float(agg.get("total_transaction_tax_paid", 0.0)), 6),
        "total_fees_paid": round(float(agg.get("total_fees_paid", 0.0)), 6),
        "citations": copy.deepcopy(TAIWAN_COST_RATE_CITATIONS),
        "rate_citations": copy.deepcopy(TAIWAN_COST_RATE_CITATIONS),
    }
    return {
        **base,
        "status": "completed",
        "framework_version": VECTORBT_VERSION_PIN,
        "protocol": {
            "in_sample_ratio": round(config.in_sample_ratio, 4),
            "out_of_sample_ratio": round(1.0 - config.in_sample_ratio, 4),
            "walk_forward_folds": config.walk_forward_folds,
            "mandate_max_drawdown": mandate_max_drawdown,
            "window_unit": "sessions",
        },
        "intraday": {
            "bar_frequency": frequency,
            "evidence_mode": REPLAY_LABEL,
            "live_or_paper_evidence": False,
            "replay_clock": "bar_end",
            "availability_ceiling": (prepared.replay or {}).get("availability_ceiling"),
            "source_snapshot": snapshot,
            "clean_sessions": n_sessions,
            "unclean_sessions": len(unclean),
            "in_sample_sessions": is_sessions,
            "out_of_sample_sessions": oos_sessions,
            "in_sample_trades": int(is_sum["round_trips"]),
            "out_of_sample_trades": int(oos_sum["round_trips"]),
            "trade_unit": "round_trips",
            "fill_rule": "next_bar_open",
        },
        "in_sample": is_sum,
        "out_of_sample": oos_sum,
        "walk_forward_folds": folds,
        "walk_forward": {
            "folds": folds,
            "consistent_sign": len(sharpes) == config.walk_forward_folds
            and all(math.isfinite(x) and x != 0.0 for x in sharpes)
            and (all(x > 0 for x in sharpes) or all(x < 0 for x in sharpes)),
            "all_positive": all(math.isfinite(x) and x > 0 for x in sharpes),
        },
        "costs": costs,
        "transaction_costs": copy.deepcopy(costs),
        "benchmarks": {
            "buy_and_hold_same_symbols": bh_same,
            "benchmark_0050": bh_0050,
            "tw_0050": bh_0050,
        },
        "artifact_bundle": full.artifact_bundle,
        "registry_entry": full.registry_entry,
    }


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


CONFLICT = object()
SURVIVORSHIP_LIMITED_STATUS = "survivorship_limited"


class FamilyTrialCounterStore:
    """Tracks multiple-testing trial counts per strategy family."""

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path is not None else None
        self._counts: Dict[str, int] = {}
        if self._path is not None and self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._counts = {str(k): int(v) for k, v in loaded.items()}
            except Exception:
                self._counts = {}

    def get(self, family_id: str) -> int:
        return int(self._counts.get(family_id, 0))

    def increment(self, family_id: str, preset_count: Optional[int] = None) -> int:
        next_val = self.get(family_id) + 1
        if preset_count is not None and int(preset_count) > next_val:
            next_val = int(preset_count)
        self._counts[family_id] = next_val
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._counts, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        return next_val


class BacktestRunStore:
    """Authoritative record of backtest runs the protocol actually executed.

    Only ``record`` (called by the code that ran the protocol) can create an entry;
    the gate accepts a caller-supplied evaluation only when it is byte-identical
    (digest) to the persisted completed run and is bound to the expected artifact
    checksum and governed dataset version.
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path is not None else None
        self._runs: Dict[str, Dict[str, Any]] = {}
        if self._path is not None and self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._runs = {str(k): dict(v) for k, v in loaded.items() if isinstance(v, dict)}
            except Exception:
                self._runs = {}

    @staticmethod
    def evaluation_digest(evaluation: Dict[str, Any]) -> str:
        payload = json.dumps(evaluation, sort_keys=True, default=str, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def record(self, evaluation: Dict[str, Any]) -> Optional[str]:
        run_id = evaluation.get("backtest_run_id")
        if not isinstance(run_id, str) or not run_id.strip():
            return None
        identity = evaluation.get("strategy_artifact")
        identity = identity if isinstance(identity, dict) else {}
        self._runs[run_id] = {
            "backtest_run_id": run_id,
            "status": evaluation.get("status"),
            "artifact_checksum": identity.get("checksum"),
            "dataset_id": evaluation.get("dataset_id"),
            "source_dataset_refs": list(evaluation.get("source_dataset_refs") or []),
            "artifact_ref": evaluation.get("artifact_ref"),
            "evaluation_digest": self.evaluation_digest(evaluation),
        }
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._runs, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        return run_id

    def get(self, run_id: str) -> Optional[Dict[str, Any]]:
        if self._path is not None and self._path.exists():
            try:
                loaded = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict) and isinstance(loaded.get(run_id), dict):
                    return dict(loaded[run_id])
            except Exception:
                pass
        record = self._runs.get(run_id)
        return dict(record) if record is not None else None


class ReplicationGate:
    """
    Gate that sits between discovered research (RS-001/RS-002) and registry entry (REG-001).

    Validates research candidates against:
    1. Schema compliance
    2. Lineage completeness
    3. Governance context
    4. No live bypass attempts
    5. Real Taiwan backtest with 70/30 IS/OOS, 3 walk-forward folds, costs, benchmarks, and trial deflation
    6. Optional: Confidence, replication notes, evaluation hypotheses, implementation readiness
    """

    def __init__(
        self,
        config: Optional[GateConfig] = None,
        trial_counter_path: Optional[Path] = None,
        backtest_run_store: Optional[BacktestRunStore] = None,
    ):
        self.config = config or GateConfig()
        self.backtest_run_store = backtest_run_store or BacktestRunStore()
        self.gate_run_logs: List[Dict[str, Any]] = []
        self._canonical_spec_validator = self._build_strategy_spec_validator()
        self._trial_counter_store = FamilyTrialCounterStore(trial_counter_path)

    def get_family_trial_count(self, family_id: str) -> int:
        """Return the current number of evaluated trials for a strategy family."""
        return self._trial_counter_store.get(family_id)

    def evaluate_candidate(self, request: ReplicationRequest) -> ReplicationResponse:
        """
        Evaluate a research candidate for registry admission.

        Args:
            request: Candidate research request with handoff and proposed StrategySpec

        Returns:
            ReplicationResponse with admission decision and evidence
        """
        gate_run_id = str(uuid.uuid4())[:8]
        results: List[ReplicationResult] = []

        # Mechanism stage (SD_DISCOVERY_DEMAND §3.2). It runs before any criterion, so
        # before the backtest that produces the out-of-sample window, and it reads the
        # in-sample slice of the dataset only.
        mechanism = self._run_mechanism_stage(request)
        mechanism_contradicted = mechanism["mechanism_status"] == MECHANISM_CONTRADICTED
        results.append(
            ReplicationResult(
                criterion_id="mechanism_stage",
                passed=not mechanism_contradicted,
                evidence=self._mechanism_evidence(mechanism),
                details=mechanism,
            )
        )

        # Required criteria checks
        for criterion in self.config.get_required_criteria():
            result = self._check_criterion(criterion, request)
            results.append(result)

        # Optional criteria checks
        for criterion in self.config.get_optional_criteria():
            result = self._check_criterion(criterion, request)
            results.append(result)

        # Determine admission status
        required_results = {
            r.criterion_id: r.passed
            for r in results
            if any(r.criterion_id == c.criterion_id for c in self.config.get_required_criteria())
        }
        optional_results = {
            r.criterion_id: r.passed
            for r in results
            if any(r.criterion_id == c.criterion_id for c in self.config.get_optional_criteria())
        }

        backtest_result = next(
            (r for r in results if r.criterion_id == "real_backtest_validation"),
            None,
        )
        backtest_details = (
            backtest_result.details
            if backtest_result is not None and isinstance(backtest_result.details, dict)
            else {}
        )
        is_insufficient_history = (
            backtest_details.get("status") == INSUFFICIENT_HISTORY_STATUS
        )

        screen_status = backtest_details.get("status")
        if is_insufficient_history:
            admitted = False
            reasoning = (
                "Candidate inputs have less than two years of point-in-time history; "
                "recorded as insufficient_history (neither failed nor admitted)."
            )
            replication_status = ReplicationStatus.INSUFFICIENT_HISTORY
            admission_status = CandidateAdmissionStatus.INSUFFICIENT_HISTORY
        else:
            admitted, reasoning = AdmissionRules.evaluate_admission(
                required_results, optional_results
            )
            if not admitted and screen_status in (
                SURVIVORSHIP_LIMITED_STATUS,
                "screen_dropped",
                "control_unavailable",
            ):
                reasoning = f"{reasoning}. {backtest_result.evidence}"
            replication_status = (
                ReplicationStatus.PASSED
                if admitted
                else ReplicationStatus.FAILED
            )
            admission_status = (
                CandidateAdmissionStatus.ADMITTED
                if admitted
                else CandidateAdmissionStatus.REJECTED
            )

        if admitted and mechanism_contradicted:
            admitted = False
            reasoning = f"{reasoning}. Refused: {self._mechanism_evidence(mechanism)}"
            replication_status = ReplicationStatus.FAILED
            admission_status = CandidateAdmissionStatus.REJECTED

        # Build summary
        required_passed = sum(1 for r in required_results.values() if r)
        required_total = len(required_results)
        optional_passed = sum(1 for r in optional_results.values() if r)
        optional_total = len(optional_results)

        summary = (
            f"Replication gate evaluation: {required_passed}/{required_total} required criteria, "
            f"{optional_passed}/{optional_total} optional criteria. {reasoning}"
        )

        response = ReplicationResponse(
            gate_run_id=gate_run_id,
            candidate_id=request.candidate_id,
            admission_status=admission_status,
            replication_status=replication_status,
            results=results,
            summary=summary,
            metadata={
                "source_task_id": request.source_task_id,
                "passed_all_required": all(required_results.values()),
                "optional_pass_rate": (
                    optional_passed / optional_total if optional_total > 0 else 1.0
                ),
                "backtest_summary": backtest_details,
                "mechanism_status": mechanism["mechanism_status"],
                "mechanism_support": mechanism["mechanism_support"],
                "mechanism": mechanism,
            },
        )

        # Log the evaluation
        self._log_evaluation(gate_run_id, request, response)

        return response

    @staticmethod
    def _mechanism_evidence(mechanism: Dict[str, Any]) -> str:
        status = mechanism["mechanism_status"]
        if status == MECHANISM_CONTRADICTED:
            bad = [
                f"#{r['index']} {r['test_kind']} (expected {r['expected_sign']}, "
                f"Holm p={r['holm_p_value']:.4g})"
                for r in mechanism["predictions"]
                if r["status"] == MECHANISM_PREDICTION_CONTRADICTED
            ]
            return "In-sample mechanism test contradicted card prediction(s): " + "; ".join(bad)
        if status == "mechanism_untested":
            return (
                f"mechanism_untested: {mechanism['testable_predictions']} testable of "
                f"{mechanism['prediction_count']} predictions"
            )
        return (
            f"mechanism_support={mechanism['mechanism_support']:.3f} "
            f"({mechanism['supported_predictions']} supported of "
            f"{mechanism['testable_predictions']} testable), none contradicted"
        )

    def _run_mechanism_stage(self, request: ReplicationRequest) -> Dict[str, Any]:
        """Run the card's predictions on the in-sample window. Never reads out-of-sample bars."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}

        def first(key: str, kind: type) -> Any:
            for source in (handoff, spec):
                if isinstance(source.get(key), kind):
                    return source[key]
            return None

        predictions = first("hypothesis_predictions", list)
        if predictions is None:
            for source in (handoff, spec):
                card = source.get("hypothesis_card")
                if isinstance(card, dict) and isinstance(card.get("predictions"), list):
                    predictions = card["predictions"]
                    break
        artifact = first("strategy_artifact", dict) or {}
        params = artifact.get("parameters") if isinstance(artifact.get("parameters"), dict) else {}
        symbols = params.get("symbols") if isinstance(params.get("symbols"), list) else []
        logic = artifact.get("strategy_logic")
        if isinstance(logic, dict) and "screen" in logic:
            # Screened artifacts: the protocol's window is the min over the whole candidate pool
            # (vectorbt_adapter uses pool_market["symbols"] as the target universe).
            dataset = first("backtest_dataset", dict) or {}
            pool = dataset.get("candidate_pool_market")
            pool_symbols = pool.get("symbols") if isinstance(pool, dict) else None
            if isinstance(pool_symbols, dict) and pool_symbols:
                symbols = list(pool_symbols)
        return run_mechanism_stage(
            predictions,
            first("backtest_dataset", dict),
            first("mechanism_inputs", dict),
            config=self.config,
            target_symbols=symbols,
        )

    def _check_criterion(
        self, criterion, request: ReplicationRequest
    ) -> ReplicationResult:
        """Check a single admission criterion."""
        try:
            if criterion.criterion_id == "schema_validity":
                return self._check_schema_validity(request)
            elif criterion.criterion_id == "lineage_complete":
                return self._check_lineage_completeness(request)
            elif criterion.criterion_id == "governance_context":
                return self._check_governance_context(request)
            elif criterion.criterion_id == "no_live_bypass":
                return self._check_no_live_bypass(request)
            elif criterion.criterion_id == "real_backtest_validation":
                return self._check_real_backtest_validation(request)
            elif criterion.criterion_id == "confidence_score":
                return self._check_confidence_score(request)
            elif criterion.criterion_id == "replication_notes_present":
                return self._check_replication_notes(request)
            elif criterion.criterion_id == "evaluation_hypotheses":
                return self._check_evaluation_hypotheses(request)
            elif criterion.criterion_id == "implementation_ready":
                return self._check_implementation_readiness(request)
            else:
                return ReplicationResult(
                    criterion_id=criterion.criterion_id,
                    passed=False,
                    evidence="Unknown criterion",
                )
        except Exception as e:
            return ReplicationResult(
                criterion_id=criterion.criterion_id,
                passed=False,
                evidence=f"Check failed with error: {str(e)}",
                details={"error": str(e)},
            )

    def _check_schema_validity(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if proposed StrategySpec is valid JSON."""
        try:
            spec = request.proposed_strategy_spec
            if not isinstance(spec, dict):
                return ReplicationResult(
                    criterion_id="schema_validity",
                    passed=False,
                    evidence="StrategySpec must be a JSON object",
                )

            if self._is_canonical_strategy_spec(spec):
                spec_for_validation = {
                    k: v
                    for k, v in spec.items()
                    if k
                    not in {
                        "backtest_evaluation",
                        "backtest_dataset",
                        "strategy_artifact",
                        "mandate_max_drawdown",
                        "family_id",
                        "trial_count",
                        "skip_backtest",
                        "history_trading_days",
                    }
                }
                errors = sorted(
                    self._canonical_spec_validator.iter_errors(spec_for_validation),
                    key=lambda item: list(item.path),
                )
                if errors:
                    first = errors[0]
                    location = ".".join(str(part) for part in first.path) or "<root>"
                    return ReplicationResult(
                        criterion_id="schema_validity",
                        passed=False,
                        evidence=f"Canonical StrategySpec invalid at {location}: {first.message}",
                        details={"schema": "OC-003", "path": list(first.path)},
                    )

                return ReplicationResult(
                    criterion_id="schema_validity",
                    passed=True,
                    evidence="Canonical StrategySpec schema is valid",
                    details={"schema": "OC-003", "strategy_id": spec.get("strategy_id")},
                )

            # Check required fields
            required_fields = ["name", "description", "signals", "parameters"]
            missing = [f for f in required_fields if f not in spec]

            if missing:
                return ReplicationResult(
                    criterion_id="schema_validity",
                    passed=False,
                    evidence=f"Missing required fields: {', '.join(missing)}",
                    details={"missing_fields": missing},
                )

            return ReplicationResult(
                criterion_id="schema_validity",
                passed=True,
                evidence="StrategySpec schema is valid",
                details={"validated_fields": required_fields},
            )
        except Exception as e:
            return ReplicationResult(
                criterion_id="schema_validity",
                passed=False,
                evidence=f"Schema validation failed: {str(e)}",
            )

    def _build_strategy_spec_validator(self) -> Draft7Validator:
        schema_path = (
            Path(__file__).resolve().parents[2]
            / "control-plane"
            / "specs"
            / "strategy_spec.schema.json"
        )
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        resolver = RefResolver(
            base_uri=f"{schema_path.parent.resolve().as_uri()}/",
            referrer=schema,
        )
        return Draft7Validator(schema, resolver=resolver)

    def _is_canonical_strategy_spec(self, spec: Dict[str, Any]) -> bool:
        return any(
            field in spec
            for field in ("spec_version", "strategy_id", "market_scope", "execution_profile")
        )

    def _check_lineage_completeness(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if research handoff has complete lineage information."""
        handoff = request.research_handoff
        if not isinstance(handoff, dict):
            return ReplicationResult(
                criterion_id="lineage_complete",
                passed=False,
                evidence="Research handoff must be a JSON object",
            )

        # Check for source metadata
        source_metadata = handoff.get("source_metadata", {})
        required_metadata = ["api_endpoint", "retrieved_at", "governance_context"]
        missing_metadata = [f for f in required_metadata if f not in source_metadata]

        if missing_metadata:
            return ReplicationResult(
                criterion_id="lineage_complete",
                passed=False,
                evidence=f"Source metadata missing fields: {', '.join(missing_metadata)}",
                details={"missing_metadata": missing_metadata},
            )

        return ReplicationResult(
            criterion_id="lineage_complete",
            passed=True,
            evidence="Research handoff has complete lineage metadata",
            details={"source_metadata_fields": required_metadata},
        )

    def _check_governance_context(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if research includes verified governance compliance metadata."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}

        if "grok_processing_notes" in handoff:
            grok_notes = handoff.get("grok_processing_notes")
            if not isinstance(grok_notes, dict):
                return ReplicationResult(
                    criterion_id="governance_context",
                    passed=False,
                    evidence="grok_processing_notes must be a JSON object when provided",
                )
            required_governance = [
                "normalization_confidence",
                "governance_compliance",
                "downstream_readiness",
            ]
            missing = [f for f in required_governance if f not in grok_notes]

            if missing:
                return ReplicationResult(
                    criterion_id="governance_context",
                    passed=False,
                    evidence=f"Missing governance metadata: {', '.join(missing)}",
                    details={"missing_fields": missing},
                )

            compliance = grok_notes.get("governance_compliance", "")
            if compliance != "verified":
                return ReplicationResult(
                    criterion_id="governance_context",
                    passed=False,
                    evidence=f"Governance compliance not verified: {compliance}",
                    details={"compliance_status": compliance},
                )

            return ReplicationResult(
                criterion_id="governance_context",
                passed=True,
                evidence="Governance context verified and complete",
                details={
                    "compliance": compliance,
                    "confidence": grok_notes.get("normalization_confidence"),
                    "readiness": grok_notes.get("downstream_readiness"),
                },
            )

        # When grok_processing_notes is not self-attested by the worker, verify actual
        # governance policy in the proposed strategy spec and source metadata.
        source_metadata = handoff.get("source_metadata", {})
        gov_context = (
            source_metadata.get("governance_context")
            if isinstance(source_metadata, dict)
            else None
        )
        spec_gov = spec.get("governance", {})
        policy_id = spec_gov.get("policy_id") if isinstance(spec_gov, dict) else None
        approval_required = (
            spec_gov.get("approval_required") if isinstance(spec_gov, dict) else None
        )

        if not gov_context:
            return ReplicationResult(
                criterion_id="governance_context",
                passed=False,
                evidence="Missing source_metadata.governance_context",
                details={"missing_fields": ["source_metadata.governance_context"]},
            )
        if not policy_id or approval_required is not True:
            return ReplicationResult(
                criterion_id="governance_context",
                passed=False,
                evidence="StrategySpec governance section must specify policy_id and approval_required=True",
                details={"policy_id": policy_id, "approval_required": approval_required},
            )

        return ReplicationResult(
            criterion_id="governance_context",
            passed=True,
            evidence="Governance context verified from StrategySpec policy and source lineage",
            details={
                "policy_id": policy_id,
                "approval_required": approval_required,
                "governance_context": gov_context,
            },
        )

    def _resolve_family_id(self, request: ReplicationRequest) -> str:
        """Resolve strategy family identifier for trial-count accounting."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}
        normalized = (
            handoff.get("normalized_findings", {})
            if isinstance(handoff.get("normalized_findings"), dict)
            else {}
        )
        strategy_artifact = (
            spec.get("strategy_artifact")
            if isinstance(spec.get("strategy_artifact"), dict)
            else handoff.get("strategy_artifact")
            if isinstance(handoff.get("strategy_artifact"), dict)
            else {}
        )

        for candidate in (
            handoff.get("family_id"),
            spec.get("family_id"),
            normalized.get("family_id"),
            strategy_artifact.get("family_id"),
            strategy_artifact.get("strategy_id"),
            spec.get("strategy_id"),
            spec.get("name"),
            request.candidate_id,
        ):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return "default_family"

    def _resolve_or_run_backtest(
        self, request: ReplicationRequest
    ) -> Optional[Dict[str, Any]]:
        """Resolve a provided real backtest evaluation or execute protocol if dataset is supplied."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}

        for candidate in (
            request.backtest_evaluation,
            handoff.get("backtest_evaluation"),
            spec.get("backtest_evaluation"),
        ):
            if isinstance(candidate, dict):
                return candidate

        dataset = handoff.get("backtest_dataset") or spec.get("backtest_dataset")
        if isinstance(dataset, dict):
            strategy_artifact = (
                spec.get("strategy_artifact")
                or handoff.get("strategy_artifact")
                or build_canonical_tw_strategy_artifact(
                    strategy_id=str(
                        spec.get("strategy_id")
                        or spec.get("name")
                        or request.candidate_id
                    )
                )
            )
            mandate_dd = float(
                spec.get("mandate_max_drawdown", self.config.max_oos_drawdown)
            )
            if str(dataset.get("data_frequency") or "") in INTRADAY_FREQUENCY_MINUTES:
                computed = run_intraday_replication_protocol(
                    dataset, strategy_artifact, config=self.config, mandate_max_drawdown=mandate_dd
                )
                if computed.get("status") in ("completed", INSUFFICIENT_HISTORY_STATUS):
                    self.backtest_run_store.record(computed)
                request.backtest_evaluation = computed
                return computed
            computed = run_replication_backtest_protocol(
                strategy_artifact=strategy_artifact,
                dataset=dataset,
                mandate_max_drawdown=mandate_dd,
                min_oos_round_trips=self.config.min_oos_round_trips,
                min_pit_history_days=self.config.min_pit_history_days,
                min_pit_history_bars=self.config.min_pit_history_bars,
                in_sample_fraction=self.config.in_sample_ratio,
                walk_forward_folds=self.config.walk_forward_folds,
            )
            if computed.get("status") in ("completed", INSUFFICIENT_HISTORY_STATUS):
                self.backtest_run_store.record(computed)
            request.backtest_evaluation = computed
            return computed

        return None

    def _expected_binding(self, request: ReplicationRequest) -> tuple:
        """Return (artifact_checksum, dataset_version_id) the candidate must be bound to."""
        from services.registry.strategy_artifact import strategy_artifact_checksum

        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}

        checksums = set()
        meta_checksum = metadata.get("strategy_artifact_checksum")
        if isinstance(meta_checksum, str) and meta_checksum.strip():
            checksums.add(meta_checksum.strip())
        for artifact in (spec.get("strategy_artifact"), handoff.get("strategy_artifact")):
            if isinstance(artifact, dict):
                try:
                    checksums.add(strategy_artifact_checksum(artifact))
                except Exception:
                    checksums.add("<uncomputable_artifact_checksum>")

        dataset_ids = set()
        meta_dataset = metadata.get("dataset_version_id")
        if isinstance(meta_dataset, str) and meta_dataset.strip():
            dataset_ids.add(meta_dataset.strip())
        for dataset in (handoff.get("backtest_dataset"), spec.get("backtest_dataset")):
            if isinstance(dataset, dict):
                ds_id = dataset.get("dataset_id")
                if isinstance(ds_id, str) and ds_id.strip():
                    dataset_ids.add(ds_id.strip())
                else:
                    dataset_ids.add("<missing_dataset_id>")

        def resolve(values: set) -> Any:
            if not values:
                return None
            if len(values) > 1:
                return CONFLICT
            return next(iter(values))

        return resolve(checksums), resolve(dataset_ids)

    @staticmethod
    def _screen_inputs(
        request: ReplicationRequest, backtest_eval: Dict[str, Any]
    ) -> Optional[tuple]:
        """Return None for an unscreened candidate, else (artifact|None, dataset|None)."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}
        artifact = next(
            (
                c
                for c in (spec.get("strategy_artifact"), handoff.get("strategy_artifact"))
                if isinstance(c, dict)
            ),
            None,
        )
        dataset = next(
            (
                c
                for c in (spec.get("backtest_dataset"), handoff.get("backtest_dataset"))
                if isinstance(c, dict)
            ),
            None,
        )
        logic = artifact.get("strategy_logic") if isinstance(artifact, dict) else None
        artifact_screened = isinstance(logic, dict) and "screen" in logic
        if artifact_screened and ReplicationGate._is_registered_entry_only(artifact):
            # Durable entry-only registration: governed eligibility + min_size remain
            # in the artifact (and so in the backtest), but there is no selection left
            # to contribute, so the screen-contribution comparison does not apply.
            return None
        bundle = backtest_eval.get("artifact_bundle")
        evidence_screened = isinstance(bundle, dict) and bool(bundle.get("candidate_pool_snapshots"))
        if not (artifact_screened or evidence_screened):
            return None
        return (
            artifact if artifact_screened else None,
            dataset if isinstance(dataset, dict) and dataset.get("candidate_pool_market") else None,
        )

    @staticmethod
    def _is_registered_entry_only(artifact: Dict[str, Any]) -> bool:
        """Validate structurally that an artifact is a gate-produced entry-only form.

        No caller flag is trusted: the artifact must be exactly the fixed point of the
        whole-boundary control construction, i.e. its screen holds only the governed
        eligibility block, ``min_size`` and the single pass-everything floor filter,
        with no selection step left.
        """
        try:
            symbols = artifact["parameters"]["symbols"]
            if not isinstance(symbols, list) or not symbols:
                return False
            return ReplicationGate._whole_boundary_control_artifact(
                artifact, list(symbols)
            ) == artifact
        except Exception:
            return False

    @staticmethod
    def _whole_boundary_control_artifact(
        artifact: Dict[str, Any], boundary: List[str]
    ) -> Dict[str, Any]:
        """The candidate's own entry/exit rules with every *selection* step removed.

        The governed daily boundary exclusions (suspension, liquidity, history, stale
        data) are market-eligibility safety, not selection, so they stay in force: the
        screen keeps its eligibility block and ``min_size`` and its steps become one
        pass-everything filter (governed close > 0 floor), so the control trades exactly
        the eligible whole boundary and nothing the screened run could not.
        """
        control = copy.deepcopy(artifact)
        logic = control["strategy_logic"]
        parameters = control["parameters"]
        parameters["symbols"] = boundary
        canonical_input = {
            "dataset": "tw_price_daily",
            "field": "close",
            "availability_lag_days": 0,
        }
        canonical_control = {
            "value_type": "integer",
            "current_value": 0,
            "allowed_range": {"min": 0, "max": 1},
            "step": 1,
        }
        declared = {i["name"]: i for i in logic["inputs"]}
        controls = control.setdefault("mutation_surface", {}).setdefault("controls", [])
        used = set(declared) | set(parameters) | {c.get("parameter_key") for c in controls}

        # Reuse an existing pair only when the screen is already the canonical
        # pass-everything filter AND the bound input/parameter have exact canonical
        # semantics. Any other same-named binding is left untouched (it may serve the
        # entry/exit rules or be an actual selection) and fresh names are allocated.
        input_name = floor_key = None
        steps = logic["screen"].get("steps")
        if isinstance(steps, list) and len(steps) == 1 and isinstance(steps[0], dict):
            expr = steps[0].get("expression") or {}
            args = expr.get("args") if isinstance(expr, dict) else None
            if (
                steps[0].get("kind") == "filter"
                and steps[0].get("refresh") == "daily"
                and isinstance(expr, dict)
                and expr.get("op") == "gt"
                and isinstance(args, list)
                and len(args) == 2
                and isinstance(args[0], dict)
                and isinstance(args[1], dict)
            ):
                n, k = args[0].get("ref"), args[1].get("const_param")
                existing_ctl = [c for c in controls if c.get("parameter_key") == k]
                if (
                    set(args[0]) == {"ref"}
                    and set(args[1]) == {"const_param"}
                    and n in declared
                    and {kk: vv for kk, vv in declared[n].items() if kk != "name"} == canonical_input
                    and parameters.get(k) == 0
                    and not isinstance(parameters.get(k), bool)
                    and len(existing_ctl) == 1
                    and {kk: vv for kk, vv in existing_ctl[0].items() if kk != "parameter_key"}
                    == canonical_control
                    and str(n).startswith("screen_control_close")
                    and str(k).startswith("screen_control_close_floor")
                ):
                    input_name, floor_key = n, k
        if input_name is None:
            def fresh(base: str) -> str:
                name, i = base, 1
                while name in used:
                    i += 1
                    name = f"{base}_{i}"
                used.add(name)
                return name

            input_name = fresh("screen_control_close")
            floor_key = fresh("screen_control_close_floor")
            logic["inputs"].append({"name": input_name, **canonical_input})
            parameters[floor_key] = 0
            controls.append({"parameter_key": floor_key, **canonical_control})
        logic["screen"]["steps"] = [
            {
                "kind": "filter",
                "expression": {
                    "op": "gt",
                    "args": [{"ref": input_name}, {"const_param": floor_key}],
                },
                "refresh": "daily",
            }
        ]
        return control

    def _measure_screen_contribution(
        self,
        request: ReplicationRequest,
        screened_eval: Dict[str, Any],
        artifact: Dict[str, Any],
        dataset: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Run the same entry/exit rules over the whole boundary and compare out of sample.

        The control is the candidate's own artifact with only the ``screen`` removed and
        its symbols set to every governed symbol of the supplied market; nothing is
        generated. Fails closed when the control cannot complete.
        """
        from services.registry.strategy_artifact import strategy_artifact_checksum

        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}
        market = dataset.get("candidate_pool_market")
        boundary = sorted((market.get("symbols") or {}).keys()) if isinstance(market, dict) else []
        screened_oos = screened_eval.get("out_of_sample") or {}
        screened_sharpe = float(screened_oos.get("sharpe_ratio", 0.0))
        screened_return = float(screened_oos.get("total_return", 0.0))
        measured: Dict[str, Any] = {
            "rule": (
                "screened out-of-sample after-cost result must exceed the same entry/exit "
                "rules over the whole boundary"
            ),
            "required_sharpe_improvement": self.config.min_screen_oos_sharpe_improvement,
            "boundary_symbols": boundary,
            "screened_backtest_run_id": screened_eval.get("backtest_run_id"),
            "screened_oos_sharpe": screened_sharpe if math.isfinite(screened_sharpe) else None,
            "screened_oos_total_return": screened_return,
        }
        if not boundary:
            return {**measured, "status": "control_unavailable", "reason": "market_boundary_empty"}

        try:
            control = self._whole_boundary_control_artifact(artifact, boundary)
            control_checksum = strategy_artifact_checksum(control)
            control_eval = run_replication_backtest_protocol(
                strategy_artifact=control,
                dataset=dataset,
                mandate_max_drawdown=float(
                    spec.get("mandate_max_drawdown", self.config.max_oos_drawdown)
                ),
                min_oos_round_trips=self.config.min_oos_round_trips,
                min_pit_history_days=self.config.min_pit_history_days,
                min_pit_history_bars=self.config.min_pit_history_bars,
                in_sample_fraction=self.config.in_sample_ratio,
                walk_forward_folds=self.config.walk_forward_folds,
                run_id=f"{screened_eval.get('backtest_run_id')}-entry-only",
            )
        except Exception as exc:  # fail closed; never admit on an unmeasured screen
            return {
                **measured,
                "status": "control_unavailable",
                "reason": f"whole_boundary_control_failed:{type(exc).__name__}:{exc}",
            }
        if control_eval.get("status") != "completed":
            return {
                **measured,
                "status": "control_unavailable",
                "reason": f"whole_boundary_control_{control_eval.get('status')}",
            }
        self.backtest_run_store.record(control_eval)
        control_oos = control_eval.get("out_of_sample") or {}
        control_sharpe = float(control_oos.get("sharpe_ratio", 0.0))
        control_return = float(control_oos.get("total_return", 0.0))
        # Sharpe is the comparison metric. A zero-variance window yields a non-finite
        # Sharpe that cannot rank anything, so fall back to after-cost total return.
        if math.isfinite(screened_sharpe) and math.isfinite(control_sharpe):
            metric = "oos_sharpe"
            improvement = screened_sharpe - control_sharpe
            required = self.config.min_screen_oos_sharpe_improvement
        else:
            metric = "oos_total_return"
            improvement = screened_return - control_return
            required = 0.0
        measured.update(
            {
                "control_backtest_run_id": control_eval.get("backtest_run_id"),
                "control_artifact_checksum": control_checksum,
                "control_oos_sharpe": control_sharpe if math.isfinite(control_sharpe) else None,
                "control_oos_total_return": control_return,
                "comparison_metric": metric,
                "oos_improvement": improvement,
                "oos_sharpe_improvement": (
                    screened_sharpe - control_sharpe
                    if metric == "oos_sharpe"
                    else None
                ),
            }
        )
        if improvement > required:
            return {**measured, "status": "contributes"}
        reason = (
            f"screened {metric} improvement {improvement:.4f} does not exceed required "
            f"{required:.4f} over the whole-boundary entry-only run "
            f"(screened {screened_sharpe if metric == 'oos_sharpe' else screened_return:.4f} vs "
            f"control {control_sharpe if metric == 'oos_sharpe' else control_return:.4f})"
        )
        return {
            **measured,
            "status": "screen_dropped",
            "reason": reason,
            "re_registration": {
                "candidate_kind": "entry_only",
                "screen_dropped": True,
                "reason": reason,
                "strategy_artifact": control,
                "artifact_checksum": control_checksum,
                "backtest_run_id": control_eval.get("backtest_run_id"),
            },
        }

    def _verify_authoritative_run(
        self, request: ReplicationRequest, backtest_eval: Dict[str, Any]
    ) -> Optional[ReplicationResult]:
        """Refuse evaluations not backed by a persisted run bound to the candidate."""

        def refuse(reason: str, **details: Any) -> ReplicationResult:
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=f"Backtest evaluation is not an authoritative bound run: {reason}",
                details={"reason": reason, **details},
            )

        run_id = backtest_eval.get("backtest_run_id")
        record = (
            self.backtest_run_store.get(run_id)
            if isinstance(run_id, str) and run_id.strip()
            else None
        )
        if record is None:
            return refuse("unknown_backtest_run", backtest_run_id=run_id)
        if record.get("evaluation_digest") != BacktestRunStore.evaluation_digest(backtest_eval):
            return refuse("evaluation_differs_from_persisted_run", backtest_run_id=run_id)
        if record.get("status") not in ("completed", INSUFFICIENT_HISTORY_STATUS) or record.get(
            "status"
        ) != backtest_eval.get("status"):
            return refuse("run_not_completed", backtest_run_id=run_id, status=record.get("status"))
        expected_checksum, expected_dataset = self._expected_binding(request)
        if expected_checksum is CONFLICT or expected_dataset is CONFLICT:
            return refuse("candidate_binding_conflict")
        if expected_checksum is None or expected_dataset is None:
            return refuse("candidate_artifact_or_dataset_binding_missing")
        if record.get("artifact_checksum") != expected_checksum:
            return refuse("artifact_checksum_mismatch", expected=expected_checksum)
        if record.get("dataset_id") != expected_dataset:
            return refuse("dataset_version_mismatch", expected=expected_dataset)
        if not record.get("source_dataset_refs"):
            return refuse("missing_source_dataset_refs")
        history = backtest_eval.get("history_check")
        if not (
            isinstance(history, dict)
            and isinstance(history.get("calendar_days"), int)
            and isinstance(history.get("trading_days"), int)
            and isinstance(history.get("sufficient"), bool)
        ):
            return refuse("missing_point_in_time_history_proof")
        return None

    def _check_real_backtest_validation(
        self, request: ReplicationRequest
    ) -> ReplicationResult:
        """
        Verify that a real backtest run of the candidate artifact exists on governed
        Taiwan market data with 70/30 IS/OOS split, 3 walk-forward folds, statutory
        transaction costs, benchmarks, and multiple-testing trial-count deflation.
        """
        backtest_eval = self._resolve_or_run_backtest(request)
        if not isinstance(backtest_eval, dict):
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence="No real backtest run exists for candidate artifact; admission refused",
                details={"reason": "missing_backtest_run"},
            )

        if (
            backtest_eval.get("status") == SURVIVORSHIP_LIMITED_STATUS
            or backtest_eval.get("survivorship_limited") is True
        ):
            reason = str(
                backtest_eval.get("reason")
                or "backtest history does not attest delisted and suspended symbol coverage"
            )
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=(
                    f"Backtest is {SURVIVORSHIP_LIMITED_STATUS}; not admitted: {reason}"
                ),
                details={
                    "status": SURVIVORSHIP_LIMITED_STATUS,
                    "survivorship_limited": True,
                    "reason": reason,
                    "source_dataset_refs": list(backtest_eval.get("source_dataset_refs") or []),
                },
            )

        unbound = self._verify_authoritative_run(request, backtest_eval)
        if unbound is not None:
            return unbound

        intraday = _is_intraday_evaluation(backtest_eval)
        if intraday:
            unqualified = self._intraday_refusal(request, backtest_eval)
            if unqualified is not None:
                return unqualified

        history_check = backtest_eval["history_check"]
        eval_status = str(backtest_eval.get("status", ""))
        calendar_days = int(history_check.get("calendar_days", 0))
        trading_days = int(history_check.get("trading_days", 0))
        history_sufficient = bool(history_check["sufficient"])

        if not intraday and (
            eval_status == INSUFFICIENT_HISTORY_STATUS
            or not history_sufficient
            or (
                history_check
                and (
                    calendar_days < self.config.min_history_calendar_days
                    or trading_days < self.config.min_history_trading_days
                )
            )
        ):
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=(
                    f"Candidate inputs have insufficient point-in-time history "
                    f"({calendar_days} calendar days, {trading_days} trading days; "
                    f"minimum {self.config.min_history_calendar_days} calendar days / 2 years): "
                    f"recorded as {INSUFFICIENT_HISTORY_STATUS}"
                ),
                details={
                    "status": INSUFFICIENT_HISTORY_STATUS,
                    "backtest_run_id": backtest_eval.get("backtest_run_id"),
                    "artifact_ref": backtest_eval.get("artifact_ref"),
                    "history_check": history_check,
                },
            )

        backtest_run_id = backtest_eval.get("backtest_run_id")
        backend = backtest_eval.get("backend")
        market = backtest_eval.get("market")
        governed_data = bool(backtest_eval.get("governed_data"))

        if not isinstance(backtest_run_id, str) or not backtest_run_id.strip():
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence="Backtest evaluation missing backtest_run_id",
                details={"reason": "missing_backtest_run_id"},
            )
        if backend != "vectorbt_portfolio" or market != "TW" or not governed_data:
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=(
                    f"Backtest must run on governed Taiwan market data via vectorbt_portfolio "
                    f"(got backend={backend!r}, market={market!r}, governed_data={governed_data!r})"
                ),
                details={
                    "backend": backend,
                    "market": market,
                    "governed_data": governed_data,
                },
            )

        protocol = (
            backtest_eval.get("protocol")
            if isinstance(backtest_eval.get("protocol"), dict)
            else {}
        )
        if (
            abs(float(protocol.get("in_sample_ratio", 0.0)) - self.config.in_sample_ratio) > 1e-6
            or abs(float(protocol.get("out_of_sample_ratio", 0.0)) - self.config.out_of_sample_ratio) > 1e-6
            or int(protocol.get("walk_forward_folds", 0)) < self.config.walk_forward_folds
        ):
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence="Backtest protocol does not match required 70/30 IS/OOS and 3 walk-forward folds",
                details={"protocol": protocol},
            )

        in_sample = backtest_eval.get("in_sample")
        out_of_sample = backtest_eval.get("out_of_sample")
        walk_forward = backtest_eval.get("walk_forward")
        benchmarks = backtest_eval.get("benchmarks")
        tx_costs = backtest_eval.get("transaction_costs")

        if not all(
            isinstance(section, dict)
            for section in (in_sample, out_of_sample, walk_forward, benchmarks, tx_costs)
        ):
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence="Backtest evaluation missing in_sample, out_of_sample, walk_forward, benchmarks, or transaction_costs",
                details={"backtest_run_id": backtest_run_id},
            )

        # Verify transaction cost rates and citations
        commission_rate = float(tx_costs.get("commission_rate", 0.0))
        equity_tax_rate = float(tx_costs.get("equity_transaction_tax_rate", 0.0))
        etf_tax_rate = float(tx_costs.get("etf_transaction_tax_rate", 0.0))
        applied_on_every_fill = bool(tx_costs.get("applied_on_every_fill"))
        citations = tx_costs.get("citations")

        if (
            abs(commission_rate - TAIWAN_BROKER_COMMISSION_RATE) > 1e-9
            or abs(equity_tax_rate - TAIWAN_EQUITY_TRANSACTION_TAX_RATE) > 1e-9
            or abs(etf_tax_rate - TAIWAN_ETF_TRANSACTION_TAX_RATE) > 1e-9
            or not applied_on_every_fill
            or not isinstance(citations, dict)
            or not citations
        ):
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence="Taiwan commission and transaction tax must be applied on every fill with statutory rate citations",
                details={"transaction_costs": tx_costs},
            )

        # Verify benchmarks exist (buy-and-hold same symbols + 0050)
        bh_bench = benchmarks.get("buy_and_hold_same_symbols")
        tw0050_bench = benchmarks.get("tw_0050")
        if not isinstance(bh_bench, dict) or not isinstance(tw0050_bench, dict):
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence="Backtest must include both buy-and-hold same-symbol and 0050 benchmarks",
                details={"benchmarks": list(benchmarks.keys())},
            )

        # Increment family trial counter and compute deflated Sharpe threshold
        family_id = self._resolve_family_id(request)
        preset_trials = backtest_eval.get("trial_count")
        trial_count = self._trial_counter_store.increment(
            family_id,
            int(preset_trials) if isinstance(preset_trials, int) else None,
        )
        screen_contribution: Optional[Dict[str, Any]] = None
        screen_inputs = self._screen_inputs(request, backtest_eval)
        if screen_inputs is not None:
            screen_artifact, screen_dataset = screen_inputs
            if screen_artifact is None or screen_dataset is None:
                return ReplicationResult(
                    criterion_id="real_backtest_validation",
                    passed=False,
                    evidence=(
                        "Candidate has a screen but its strategy artifact or governed market "
                        "dataset is absent; the screen contribution test cannot run, admission refused"
                    ),
                    details={
                        "status": "screen_contribution_unavailable",
                        "reason": "screen_contribution_inputs_missing",
                        "family_id": family_id,
                    },
                )
            # The screened evaluation above is a screen variant of the family; the
            # whole-boundary control below is an entry variant. Both are trials.
            trial_count = self._trial_counter_store.increment(family_id)
            screen_contribution = self._measure_screen_contribution(
                request, backtest_eval, screen_artifact, screen_dataset
            )
            screen_contribution["trial_accounting"] = {
                "screen_variant_trials": 1,
                "entry_variant_trials": 1,
                "family_trial_count_after": trial_count,
            }
            if screen_contribution["status"] != "contributes":
                failure_reason = screen_contribution.get("reason")
                dropped = screen_contribution["status"] == "screen_dropped"
                return ReplicationResult(
                    criterion_id="real_backtest_validation",
                    passed=False,
                    evidence=(
                        "Screen adds nothing out of sample after costs: dropped and candidate "
                        f"re-registered as entry-only ({failure_reason})"
                        if dropped
                        else f"Screen contribution test could not be completed: {failure_reason}"
                    ),
                    details={
                        "status": screen_contribution["status"],
                        "backtest_run_id": backtest_run_id,
                        "family_id": family_id,
                        "trial_count": trial_count,
                        "screen_contribution": screen_contribution,
                    },
                )
        deflation = self.config.compute_deflated_sharpe_threshold(trial_count)
        deflated_sharpe_threshold = float(deflation["deflated_sharpe_threshold"])
        sharpe_haircut = float(deflation["sharpe_haircut"])

        is_sharpe = float(in_sample.get("sharpe_ratio", 0.0))
        oos_sharpe = float(out_of_sample.get("sharpe_ratio", 0.0))
        oos_max_drawdown = abs(float(out_of_sample.get("max_drawdown", 1.0)))
        oos_total_return = float(out_of_sample.get("total_return", 0.0))
        oos_round_trips = int(out_of_sample.get("round_trips", 0))
        deflated_sharpe_ratio = oos_sharpe - sharpe_haircut

        bh_sharpe = float(bh_bench.get("sharpe_ratio", 0.0))
        tw0050_sharpe = float(tw0050_bench.get("sharpe_ratio", 0.0))

        wf_folds = walk_forward.get("folds", [])
        if not isinstance(wf_folds, list) or len(wf_folds) < self.config.walk_forward_folds:
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=f"Walk-forward evaluation requires {self.config.walk_forward_folds} folds",
                details={"walk_forward": walk_forward},
            )
        wf_fold_sharpes = [float(f.get("sharpe_ratio", 0.0)) for f in wf_folds]
        wf_fold_returns = [float(f.get("total_return", 0.0)) for f in wf_folds]
        positive_folds = [r for r in wf_fold_returns if r > 0.0]
        total_positive_return = sum(positive_folds)
        max_fold_profit_share = (
            max(positive_folds) / total_positive_return
            if total_positive_return > 0.0
            else 1.0
        )
        walk_forward_consistent = (
            all(s > 0.0 for s in wf_fold_sharpes)
            and len(positive_folds) >= 2
            and max_fold_profit_share < 1.0
        )

        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}
        mandate_max_drawdown = float(
            spec.get("mandate_max_drawdown", self.config.max_oos_drawdown)
        )

        checks = {
            "oos_sharpe_positive_after_costs": oos_sharpe > 0.0,
            "oos_sharpe_exceeds_deflated_threshold": oos_sharpe > deflated_sharpe_threshold,
            "oos_sharpe_beats_buy_and_hold": oos_sharpe > bh_sharpe,
            "oos_max_drawdown_within_mandate": oos_max_drawdown <= mandate_max_drawdown,
            "oos_minimum_round_trips_met": oos_round_trips
            >= (self.config.min_oos_trades if intraday else self.config.min_oos_round_trips),
            "walk_forward_consistent_across_folds": walk_forward_consistent,
        }
        failed_checks = [name for name, ok in checks.items() if not ok]
        passed = len(failed_checks) == 0

        details = {
            "status": "passed" if passed else "failed",
            "backtest_run_id": backtest_run_id,
            "artifact_ref": backtest_eval.get("artifact_ref")
            or {
                "artifact_type": "backtest_run",
                "uri": f"backtest://{backtest_run_id}",
                "run_id": backtest_run_id,
            },
            "family_id": family_id,
            "trial_count": trial_count,
            "base_sharpe_threshold": float(deflation["base_sharpe_threshold"]),
            "sharpe_haircut": sharpe_haircut,
            "deflated_sharpe_threshold": deflated_sharpe_threshold,
            "deflated_sharpe_ratio": deflated_sharpe_ratio,
            "in_sample_sharpe_ratio": is_sharpe,
            "out_of_sample_sharpe_ratio": oos_sharpe,
            "out_of_sample_max_drawdown": oos_max_drawdown,
            "out_of_sample_total_return": oos_total_return,
            "out_of_sample_round_trips": oos_round_trips,
            "mandate_max_drawdown": mandate_max_drawdown,
            "buy_and_hold_sharpe_ratio": bh_sharpe,
            "tw_0050_sharpe_ratio": tw0050_sharpe,
            "walk_forward_fold_sharpes": wf_fold_sharpes,
            "walk_forward_fold_returns": wf_fold_returns,
            "max_fold_profit_share": max_fold_profit_share,
            "transaction_costs": tx_costs,
            "history_check": history_check,
            "checks": checks,
            "failed_checks": failed_checks,
        }
        if intraday:
            details["intraday"] = self._intraday_details(backtest_eval)
        if screen_contribution is not None:
            details["screen_contribution"] = screen_contribution

        if not passed:
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=(
                    f"Real backtest failed admission conditions ({', '.join(failed_checks)}): "
                    f"IS Sharpe={is_sharpe:.3f}, OOS Sharpe={oos_sharpe:.3f}, "
                    f"deflated threshold={deflated_sharpe_threshold:.3f} (trial {trial_count})"
                ),
                details=details,
            )

        return ReplicationResult(
            criterion_id="real_backtest_validation",
            passed=True,
            evidence=(
                f"Real Taiwan backtest passed: OOS Sharpe={oos_sharpe:.3f} > "
                f"deflated threshold={deflated_sharpe_threshold:.3f} (trial {trial_count}), "
                f"beat buy-and-hold ({bh_sharpe:.3f}), {oos_round_trips} OOS round trips"
            ),
            details=details,
        )

    def _intraday_details(self, evaluation: Dict[str, Any]) -> Dict[str, Any]:
        block = evaluation.get("intraday") if isinstance(evaluation.get("intraday"), dict) else {}
        snapshot = block.get("source_snapshot") if isinstance(block.get("source_snapshot"), dict) else {}
        return {
            "bar_frequency": block.get("bar_frequency") or evaluation.get("bar_frequency"),
            "evidence_mode": block.get("evidence_mode"),
            "live_or_paper_evidence": block.get("live_or_paper_evidence"),
            "replay_clock": block.get("replay_clock"),
            "availability_ceiling": block.get("availability_ceiling"),
            "source_dataset": snapshot.get("source_dataset"),
            "source_snapshot_sha256": snapshot.get("content_sha256"),
            "clean_sessions": block.get("clean_sessions"),
            "in_sample_sessions": block.get("in_sample_sessions"),
            "out_of_sample_sessions": block.get("out_of_sample_sessions"),
            "in_sample_trades": block.get("in_sample_trades"),
            "out_of_sample_trades": block.get("out_of_sample_trades"),
            "trade_unit": block.get("trade_unit"),
            "thresholds": {
                "min_clean_sessions": self.config.min_clean_sessions,
                "min_in_sample_trades": self.config.min_in_sample_trades,
                "min_oos_sessions": self.config.min_oos_sessions,
                "min_oos_trades": self.config.min_oos_trades,
            },
        }

    def _intraday_refusal(
        self, request: ReplicationRequest, evaluation: Dict[str, Any]
    ) -> Optional[ReplicationResult]:
        """K6: minute evidence must be replay-labelled, reconciled, clean, and large enough."""
        run_id = evaluation.get("backtest_run_id")

        def refuse(status: str, reason: str, **extra: Any) -> ReplicationResult:
            return ReplicationResult(
                criterion_id="real_backtest_validation",
                passed=False,
                evidence=f"Intraday replication evidence refused ({reason})",
                details={
                    "status": status,
                    "reason": reason,
                    "backtest_run_id": run_id,
                    "intraday": self._intraday_details(evaluation),
                    **extra,
                },
            )

        block = evaluation.get("intraday")
        if not isinstance(block, dict):
            return refuse(INTRADAY_UNQUALIFIED_STATUS, "missing_intraday_evidence_block")
        frequency = str(block.get("bar_frequency") or "")
        if (
            frequency not in INTRADAY_FREQUENCY_MINUTES
            or evaluation.get("bar_frequency") != frequency
        ):
            return refuse(INTRADAY_UNQUALIFIED_STATUS, "bar_frequency_missing_or_inconsistent")
        declared = self._declared_bar_frequency(request)
        if declared is not None and declared != frequency:
            return refuse(
                INTRADAY_UNQUALIFIED_STATUS, "strategy_frequency_differs_from_evidence", declared=declared
            )
        if (
            block.get("evidence_mode") != REPLAY_LABEL
            or evaluation.get("evidence_mode") != REPLAY_LABEL
            or block.get("live_or_paper_evidence") is not False
            or block.get("replay_clock") != "bar_end"
            or not block.get("availability_ceiling")
        ):
            return refuse(INTRADAY_UNQUALIFIED_STATUS, "not_labelled_replay_with_bar_end_clock")
        snapshot = block.get("source_snapshot")
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("source_dataset") != INTRADAY_SOURCE_DATASET
            or snapshot.get("reconciled_only") is not True
            or not snapshot.get("content_sha256")
        ):
            return refuse(INTRADAY_UNQUALIFIED_STATUS, "source_snapshot_not_reconciled_tw_price_1m")
        flagged = [
            name for name in _ISOLATION_FLAGS
            if snapshot.get(name) or block.get(name) or evaluation.get(name)
        ]
        if flagged:
            return refuse(
                INTRADAY_UNQUALIFIED_STATUS, "quarantined_or_waived_data_is_not_replication_evidence",
                flags=flagged,
            )
        counts = {
            key: block.get(key)
            for key in (
                "clean_sessions", "unclean_sessions", "in_sample_sessions",
                "out_of_sample_sessions", "in_sample_trades", "out_of_sample_trades",
            )
        }
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in counts.values()):
            return refuse(INTRADAY_UNQUALIFIED_STATUS, "session_or_trade_counts_missing")
        if counts["unclean_sessions"] > 0:
            return refuse(INTRADAY_UNQUALIFIED_STATUS, "unclean_sessions_in_window", counts=counts)
        cfg = self.config
        shortfalls = {
            name: {"have": counts[key], "need": need}
            for name, key, need in (
                ("clean_sessions", "clean_sessions", cfg.min_clean_sessions),
                ("out_of_sample_sessions", "out_of_sample_sessions", cfg.min_oos_sessions),
                ("in_sample_trades", "in_sample_trades", cfg.min_in_sample_trades),
                ("out_of_sample_trades", "out_of_sample_trades", cfg.min_oos_trades),
            )
            if counts[key] < need
        }
        if shortfalls or str(evaluation.get("status")) == INSUFFICIENT_HISTORY_STATUS:
            return refuse(
                INSUFFICIENT_HISTORY_STATUS,
                "insufficient_clean_sessions_or_trades; thresholds are not lowered for thin data",
                shortfalls=shortfalls,
            )
        return None

    @staticmethod
    def _declared_bar_frequency(request: ReplicationRequest) -> Optional[str]:
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        spec = request.proposed_strategy_spec if isinstance(request.proposed_strategy_spec, dict) else {}
        for holder in (spec.get("strategy_artifact"), handoff.get("strategy_artifact")):
            if isinstance(holder, dict):
                value = (holder.get("parameters") or {}).get("bar_frequency")
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _check_no_live_bypass(self, request: ReplicationRequest) -> ReplicationResult:
        """Check that candidate doesn't attempt to bypass promotion gates."""
        spec = request.proposed_strategy_spec

        # Check for suspicious patterns that might indicate bypass attempts
        dangerous_fields = ["live_execution_direct", "skip_promotion_gate", "force_live"]
        found_dangerous = [f for f in dangerous_fields if f in spec]

        if found_dangerous:
            return ReplicationResult(
                criterion_id="no_live_bypass",
                passed=False,
                evidence=f"Found suspicious bypass fields: {', '.join(found_dangerous)}",
                details={"dangerous_fields": found_dangerous},
            )

        # Check lifecycle_state if present
        lifecycle = spec.get("lifecycle_state")
        if lifecycle and lifecycle not in ["draft", "candidate"]:
            return ReplicationResult(
                criterion_id="no_live_bypass",
                passed=False,
                evidence=f"Candidate attempting to set lifecycle_state to {lifecycle}",
                details={"attempted_lifecycle": lifecycle},
            )

        return ReplicationResult(
            criterion_id="no_live_bypass",
            passed=True,
            evidence="No live bypass attempts detected",
        )

    def _check_confidence_score(self, request: ReplicationRequest) -> ReplicationResult:
        """Check research confidence (optional criterion)."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        if "grok_processing_notes" in handoff and isinstance(
            handoff.get("grok_processing_notes"), dict
        ):
            grok_notes = handoff.get("grok_processing_notes", {})
            confidence = grok_notes.get("normalization_confidence", "low")
            confidence_scores = {"high": 0.9, "medium": 0.7, "low": 0.5}
            score = confidence_scores.get(confidence, 0.5)
        else:
            backtest_eval = self._resolve_or_run_backtest(request)
            oos = (
                backtest_eval.get("out_of_sample", {})
                if isinstance(backtest_eval, dict)
                else {}
            )
            oos_sharpe = float(oos.get("sharpe_ratio", 0.0)) if isinstance(oos, dict) else 0.0
            if oos_sharpe > self.config.base_oos_sharpe_threshold:
                confidence = "high"
                score = 0.9
            elif oos_sharpe > 0.0:
                confidence = "medium"
                score = 0.7
            else:
                confidence = "low"
                score = 0.5

        threshold = 0.7
        passed = score >= threshold

        return ReplicationResult(
            criterion_id="confidence_score",
            passed=passed,
            evidence=f"Research confidence: {confidence} (score: {score})",
            details={"confidence_level": confidence, "score": score, "threshold": threshold},
        )

    def _check_replication_notes(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if replication notes are present (optional criterion)."""
        handoff = request.research_handoff
        normalized = handoff.get("normalized_findings", {})
        notes = normalized.get("replication_notes", "")

        passed = bool(notes and len(notes.strip()) > 0)

        return ReplicationResult(
            criterion_id="replication_notes_present",
            passed=passed,
            evidence="Replication notes present" if passed else "Replication notes missing",
            details={"has_notes": passed, "notes_length": len(notes) if notes else 0},
        )

    def _check_evaluation_hypotheses(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if evaluation hypotheses are defined (optional criterion)."""
        handoff = request.research_handoff
        normalized = handoff.get("normalized_findings", {})
        hypotheses = normalized.get("evaluation_hypotheses", "")

        passed = bool(hypotheses and len(hypotheses.strip()) > 0)

        return ReplicationResult(
            criterion_id="evaluation_hypotheses",
            passed=passed,
            evidence=(
                "Evaluation hypotheses defined" if passed else "Evaluation hypotheses missing"
            ),
            details={"has_hypotheses": passed, "hypotheses_length": len(hypotheses) if hypotheses else 0},
        )

    def _check_implementation_readiness(self, request: ReplicationRequest) -> ReplicationResult:
        """Check if research indicates implementation readiness (optional criterion)."""
        handoff = request.research_handoff if isinstance(request.research_handoff, dict) else {}
        if "grok_processing_notes" in handoff and isinstance(
            handoff.get("grok_processing_notes"), dict
        ):
            grok_notes = handoff.get("grok_processing_notes", {})
            readiness = grok_notes.get("downstream_readiness", "")
            passed = readiness == "ready_for_replication"
        else:
            backtest_eval = self._resolve_or_run_backtest(request)
            has_backtest_run = isinstance(backtest_eval, dict) and bool(
                backtest_eval.get("backtest_run_id")
            )
            readiness = "ready_for_replication" if has_backtest_run else "missing_backtest_run"
            passed = has_backtest_run

        return ReplicationResult(
            criterion_id="implementation_ready",
            passed=passed,
            evidence=f"Implementation readiness: {readiness}",
            details={"readiness_status": readiness},
        )

    def _log_evaluation(
        self,
        gate_run_id: str,
        request: ReplicationRequest,
        response: ReplicationResponse,
    ):
        """Log evaluation for audit trail."""
        log_entry = {
            "gate_run_id": gate_run_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "candidate_id": request.candidate_id,
            "admission_status": response.admission_status.value,
            "replication_status": response.replication_status.value,
            "required_passed": sum(1 for r in response.results if r.passed and any(
                r.criterion_id == c.criterion_id for c in self.config.get_required_criteria()
            )),
            "optional_passed": sum(1 for r in response.results if r.passed and any(
                r.criterion_id == c.criterion_id for c in self.config.get_optional_criteria()
            )),
        }
        self.gate_run_logs.append(log_entry)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log of all evaluations."""
        return self.gate_run_logs


def create_promotion_request(
    response: ReplicationResponse,
    strategy_spec: Dict[str, Any],
    storage_backend: str = "object_store",
    fallback_backtest_summary: Optional[Dict[str, Any]] = None,
) -> Optional[RegistryPromotionRequest]:
    """
    Create a registry promotion request if candidate was admitted.

    Returns:
        RegistryPromotionRequest if admitted, None otherwise
    """
    if not getattr(response, "passed", False):
        return None

    resp_meta = getattr(response, "metadata", None)
    resp_results = getattr(response, "results", None) or []
    backtest_summary = (
        resp_meta.get("backtest_summary")
        if isinstance(resp_meta, dict)
        and isinstance(resp_meta.get("backtest_summary"), dict)
        else {}
    )
    if not backtest_summary:
        backtest_result = next(
            (r for r in resp_results if getattr(r, "criterion_id", None) == "real_backtest_validation"),
            None,
        )
        if backtest_result is not None and isinstance(getattr(backtest_result, "details", None), dict):
            backtest_summary = backtest_result.details
    if not backtest_summary and isinstance(fallback_backtest_summary, dict):
        backtest_summary = fallback_backtest_summary

    backtest_run_id = backtest_summary.get("backtest_run_id")
    oos_sharpe = backtest_summary.get("out_of_sample_sharpe_ratio")
    is_sharpe = backtest_summary.get("in_sample_sharpe_ratio")
    deflated_sharpe = backtest_summary.get("deflated_sharpe_ratio")
    deflated_threshold = backtest_summary.get("deflated_sharpe_threshold")
    trial_count = backtest_summary.get("trial_count")

    evaluation_summary = {
        "sharpe_ratio": oos_sharpe,
        "in_sample_sharpe_ratio": is_sharpe,
        "out_of_sample_sharpe_ratio": oos_sharpe,
        "deflated_sharpe_ratio": deflated_sharpe,
        "deflated_sharpe_threshold": deflated_threshold,
        "trial_count": trial_count,
        "max_drawdown": backtest_summary.get("out_of_sample_max_drawdown"),
        "total_return": backtest_summary.get("out_of_sample_total_return"),
        "round_trips": backtest_summary.get("out_of_sample_round_trips"),
        "buy_and_hold_sharpe_ratio": backtest_summary.get("buy_and_hold_sharpe_ratio"),
        "tw_0050_sharpe_ratio": backtest_summary.get("tw_0050_sharpe_ratio"),
        "walk_forward_fold_sharpes": backtest_summary.get("walk_forward_fold_sharpes"),
        "backtest_run_id": backtest_run_id,
    }

    gate_run_id = str(getattr(response, "gate_run_id", None) or "gate-run-001")
    candidate_id = str(getattr(response, "candidate_id", None) or "candidate-001")
    admission_status_obj = getattr(response, "admission_status", CandidateAdmissionStatus.ADMITTED)
    admission_status_val = (
        admission_status_obj.value
        if hasattr(admission_status_obj, "value")
        else str(admission_status_obj)
    )
    resp_timestamp = str(
        getattr(response, "timestamp", None) or (datetime.utcnow().isoformat() + "Z")
    )

    # Build registry entry with real replication_success and evaluation_summary
    registry_entry = {
        "artifact_type": "strategy_spec",
        "lifecycle_state": "candidate",
        "version": "1.0.0",
        "content": strategy_spec,
        "replication_success": bool(getattr(response, "passed", False)),
        "evaluation_summary": evaluation_summary,
    }

    # Build replication proof
    replication_proof = {
        "gate_run_id": gate_run_id,
        "replication_results": [r.to_dict() for r in resp_results if hasattr(r, "to_dict")],
        "admission_status": admission_status_val,
        "backtest_run_id": backtest_run_id,
        "evaluation_summary": evaluation_summary,
        "transaction_costs": backtest_summary.get("transaction_costs"),
    }
    if isinstance(resp_meta, dict) and isinstance(resp_meta.get("mechanism"), dict):
        replication_proof["mechanism_status"] = resp_meta.get("mechanism_status")
        replication_proof["mechanism_support"] = resp_meta.get("mechanism_support")
        replication_proof["mechanism"] = resp_meta["mechanism"]
        registry_entry["mechanism_support"] = resp_meta.get("mechanism_support")
        registry_entry["mechanism_status"] = resp_meta.get("mechanism_status")

    # Build lineage
    source_run_ids = [backtest_run_id] if backtest_run_id else []
    lineage = {
        "source_gate_run": gate_run_id,
        "source_candidate_id": candidate_id,
        "replication_timestamp": resp_timestamp,
        "source_run_ids": source_run_ids,
    }

    # Determine storage path
    storage_path = f"research/replication/{candidate_id}/strategy_spec_v1.0.0.json"

    return RegistryPromotionRequest(
        gate_run_id=gate_run_id,
        candidate_id=candidate_id,
        registry_entry=registry_entry,
        replication_proof=replication_proof,
        lineage=lineage,
        storage_backend=storage_backend,
        storage_path=storage_path,
    )
