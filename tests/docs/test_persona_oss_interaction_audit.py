from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CHECKLIST_DOC = ROOT / "OSS_INTEGRATION_CHECKLIST.md"
MATRIX_DOC = ROOT / "RESEARCH_BACKEND_MATURITY_MATRIX.md"
OPENCLAW_CONTRACT = ROOT / "OPENCLAW_RUNTIME_CONTRACT.md"
REPORT_DOC = (
    ROOT
    / "docs/04/pantheon_persona_oss_interaction_audit_2026-06-12"
    / "PERSONA_OSS_INTERACTION_AUDIT_2026-06-12.md"
)
RUNTIME_HARNESS = ROOT / "services/persona/oss_runtime.py"
RUNTIME_E2E_TEST = ROOT / "tests/e2e/test_persona_oss_runtime_matrix.py"
RUNTIME_100_E2E_TEST = ROOT / "tests/e2e/test_persona_oss_100_alpha_seed_roundtrips.py"


PERSONA_OSS_COMPONENTS = {
    "OpenClaw": "governed",
    "DSPy": "governed",
    "imitation": "governed",
    "TRL": "smoke-tested",
    "Qlib": "smoke-tested",
    "vectorbt": "governed",
    "statsmodels": "governed",
    "QuantLib": "governed",
    "FinRL": "smoke-tested",
    "RLlib": "smoke-tested",
    "Ray Tune": "smoke-tested",
    "MLflow": "governed",
    "W&B": "activation-gated",
}


PROOF_REFERENCES = [
    "services/openclaw-gateway-adapter/test_session_lifecycle.py",
    "services/openclaw-gateway-adapter/test_tool_workflow_bridge.py",
    "services/learning/dspy/test_adapter.py",
    "services/learning/imitation/test_adapter.py",
    "services/learning/trl/test_adapter.py",
    "services/learning/trl/test_activation_smoke.py",
    "services/research/qlib/test_adapter.py",
    "services/research/qlib/test_rolling_pipeline.py",
    "tests/governance/test_qlib_proof_artifacts.py",
    "services/research/vectorbt/test_adapter.py",
    "services/research/statsmodels/test_adapter.py",
    "tests/governance/test_statsmodels_proof_artifacts.py",
    "services/research/quantlib/test_adapter.py",
    "tests/governance/test_quantlib_proof_artifacts.py",
    "services/research/finrl/test_adapter.py",
    "services/research/finrl/test_production_drl_run.py",
    "services/research/rllib/test_adapter.py",
    "services/research/rllib/test_production_ppo_run.py",
    "services/research/rllib/test_ray_tune_adapter.py",
    "services/registry/experiments/test_adapter.py",
    "tests/integrations/test_wandb_sync.py",
]


SCENARIOS = [
    "OpenClaw Runtime Session",
    "DSPy Persona Optimization",
    "Imitation Behavior Cloning",
    "TRL Preference Learning",
    "Qlib Supervised Alpha",
    "vectorbt Historical Backtest",
    "statsmodels Regime Interpretation",
    "QuantLib Pricing/Risk Interpretation",
    "FinRL Offline Policy Evidence",
    "RLlib Offline Train/Eval Evidence",
    "Ray Tune Optimizer Evidence",
    "MLflow Experiment Tracking",
    "W&B Offline Experiment Tracking",
    "vectorbt -> MLflow -> LEAN Handoff Packet",
]

RUNTIME_COMPONENT_DISPLAYS = {
    "openclaw": "OpenClaw",
    "dspy": "DSPy",
    "imitation": "imitation",
    "trl": "TRL",
    "qlib": "Qlib",
    "vectorbt": "vectorbt",
    "statsmodels": "statsmodels",
    "quantlib": "QuantLib",
    "finrl": "FinRL",
    "rllib": "RLlib",
    "ray_tune": "Ray Tune",
    "mlflow": "MLflow",
    "wandb": "W&B",
    "lean_handoff": "lean_handoff",
}

ROUND_TRIP_COMPONENT_COUNTS = {
    "openclaw": 8,
    "dspy": 7,
    "imitation": 7,
    "trl": 7,
    "qlib": 8,
    "vectorbt": 10,
    "statsmodels": 7,
    "quantlib": 7,
    "finrl": 7,
    "rllib": 7,
    "ray_tune": 6,
    "mlflow": 7,
    "wandb": 6,
    "lean_handoff": 6,
}

ALPHA_SEED_REFERENCES = [
    "services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md",
    "tests/e2e/fixtures/strategy_spec_for_experiment.json",
    "tests/e2e/fixtures/experiment_run_for_admission.json",
    "tests/e2e/fixtures/candidate_artifact_for_decision.json",
    "tests/e2e/test_persona_abc_ooda_evidence_chain.py",
    "services/source_ingestion/tests/test_strategy_seed_builder.py",
]


def _table_statuses(path: Path, bold_names: bool = False) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        name = cells[0]
        status = cells[2]
        if bold_names:
            name = name.removeprefix("**").removesuffix("**")
        name = name.strip("`")
        if name in PERSONA_OSS_COMPONENTS and status.startswith("`"):
            statuses[name] = status.strip("`")
    return statuses


def test_persona_oss_statuses_match_canonical_checklist() -> None:
    checklist_statuses = _table_statuses(CHECKLIST_DOC)
    matrix_statuses = _table_statuses(MATRIX_DOC, bold_names=True)

    assert checklist_statuses == PERSONA_OSS_COMPONENTS
    assert matrix_statuses == PERSONA_OSS_COMPONENTS


def test_activation_gated_is_a_first_class_status_code() -> None:
    checklist = CHECKLIST_DOC.read_text(encoding="utf-8")
    matrix = MATRIX_DOC.read_text(encoding="utf-8")

    assert "`activation-gated`" in checklist
    assert "`activation-gated`" in matrix
    assert "runtime, network, credential, or operator gate" in matrix


def test_persona_oss_report_covers_only_persona_facing_components() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")

    for component, status in PERSONA_OSS_COMPONENTS.items():
        assert f"| `{component}` |" in report
        assert f"`{status}`" in report

    out_of_scope_terms = [
        "LEAN algorithm execution",
        "LEAN Launcher process management",
        "broker adapter internals",
        "Runtime ownership after execution review accepts a handoff",
        "Capital approval",
        "paper/canary/live promotion decisions",
    ]
    for term in out_of_scope_terms:
        assert term in report


def test_persona_oss_report_points_to_runtime_harness_and_e2e_matrix() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")
    harness = RUNTIME_HARNESS.read_text(encoding="utf-8")
    e2e = RUNTIME_E2E_TEST.read_text(encoding="utf-8")

    assert "services/persona/oss_runtime.py" in report
    assert "tests/e2e/test_persona_oss_runtime_matrix.py" in report
    assert "PersonaOSSRequest" in report
    assert "PersonaOSSResult" in report
    assert "response-driven OODA follow-up" in report

    for component, display in RUNTIME_COMPONENT_DISPLAYS.items():
        assert f'"{component}"' in harness
        assert f"`{display}`" in report
    assert "run_persona_oss_matrix" in e2e


def test_persona_oss_report_cites_existing_proof_tests() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")

    for reference in PROOF_REFERENCES:
        assert reference in report
        assert (ROOT / reference).exists()


def test_persona_oss_report_includes_all_e2e_scenarios() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")

    for scenario in SCENARIOS:
        assert scenario in report

    required_runtime_terms = [
        "does not launch LEAN",
        "broker adapters",
        "stops at the handoff packet",
        "runtime bootstrap request",
        "PantheonRuntimeContext.from_mapping()",
    ]
    for term in required_runtime_terms:
        assert term in report


def test_persona_oss_report_includes_100_alpha_seed_roundtrip_matrix() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")
    e2e = RUNTIME_100_E2E_TEST.read_text(encoding="utf-8")

    assert "100 Alpha-Seed Round-Trip Spec Matrix" in report
    assert "tests/e2e/test_persona_oss_100_alpha_seed_roundtrips.py" in report
    assert "persona -> OSS -> persona" in report
    assert "assertion_label" in report
    assert "payload fingerprint" in report
    assert "run_persona_oss_request()" in report
    assert "ROUND_TRIP_SPECS" in e2e
    assert "run_persona_oss_request(request)" in e2e
    assert "assertion_label" in e2e
    assert "_fingerprint(spec.payload)" in e2e

    for component, count in ROUND_TRIP_COMPONENT_COUNTS.items():
        assert f"| `{component}` | {count} |" in report
        assert f'("{component}", {count})' in e2e

    assert sum(ROUND_TRIP_COMPONENT_COUNTS.values()) == 100
    assert "assert len(ROUND_TRIP_SPECS) == 100" in e2e

    for reference in ALPHA_SEED_REFERENCES:
        assert reference in report
        assert reference in e2e
        assert (ROOT / reference).exists()


def test_openclaw_persona_boundary_denies_downstream_execution() -> None:
    contract = OPENCLAW_CONTRACT.read_text(encoding="utf-8")
    bridge_test = (ROOT / "services/openclaw-gateway-adapter/test_tool_workflow_bridge.py").read_text(
        encoding="utf-8"
    )

    contract_terms = [
        "persona context",
        "must not create `RuntimeBootstrapRequest`",
        "mutate\n  `RuntimeBinding`",
        "invoke the Lean Launcher",
        "invoke broker SDK order routes",
        "approve capital authorization",
    ]
    for term in contract_terms:
        assert term in contract

    for denied_prefix in ("broker.", "live.", "paper.", "capital."):
        assert denied_prefix in bridge_test
