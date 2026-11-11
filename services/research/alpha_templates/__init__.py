"""Taiwan point-in-time evaluation and redundancy analysis for ``alpha_template.v1``."""
from __future__ import annotations

from .evaluator import (
    DEFAULT_TAIWAN_BAR_COUNT,
    DEFAULT_TAIWAN_SYMBOLS,
    EVALUATION_HORIZONS,
    HORIZON_KEYS,
    REDUNDANCY_CORRELATION_THRESHOLD,
    TaiwanEvaluationDataError,
    summarize_universe_provenance,
    build_taiwan_point_in_time_universe,
    compute_active_template_redundancy,
    compute_template_evaluation_metrics,
    evaluate_alpha_template_library,
    evaluate_single_alpha_template,
    evaluate_template_point_in_time_panel,
    evaluate_template_point_in_time_symbol,
    load_taiwan_evaluation_universe,
    run_alpha_template_evaluation,
)

__all__ = [
    "DEFAULT_TAIWAN_BAR_COUNT",
    "DEFAULT_TAIWAN_SYMBOLS",
    "EVALUATION_HORIZONS",
    "HORIZON_KEYS",
    "REDUNDANCY_CORRELATION_THRESHOLD",
    "TaiwanEvaluationDataError",
    "summarize_universe_provenance",
    "build_taiwan_point_in_time_universe",
    "compute_active_template_redundancy",
    "compute_template_evaluation_metrics",
    "evaluate_alpha_template_library",
    "evaluate_single_alpha_template",
    "evaluate_template_point_in_time_panel",
    "evaluate_template_point_in_time_symbol",
    "load_taiwan_evaluation_universe",
    "run_alpha_template_evaluation",
]
