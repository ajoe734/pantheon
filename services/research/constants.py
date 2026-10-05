"""Shared immutable constants and contract definitions for research orchestrator."""
ALLOWLISTED_STAGE_BACKENDS: dict[str, str] = {
    "source_discovery": "source_ingestion", "data_validation": "data_validation",
    "prototype_backtest": "vectorbt", "alpha_training": "qlib", "rolling_oos": "qlib",
    "econometric_validation": "statsmodels", "derivatives_pricing_risk": "quantlib",
    "policy_training": "finrl", "parameter_search": "ray_tune",
    "portfolio_synthesis": "optimizer_svc", "robustness_stress": "rllib",
    "evidence_synthesis": "openclaw_result_synthesis",
}
ALLOWLISTED_STAGE_TYPES: set[str] = set(ALLOWLISTED_STAGE_BACKENDS.keys())
