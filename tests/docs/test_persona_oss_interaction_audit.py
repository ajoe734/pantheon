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
    "Persona Runtime Session Through OpenClaw",
    "Observe Evidence With vectorbt, statsmodels, and MLflow",
    "Supervised Alpha / Rolling OOS With Qlib",
    "Derivatives Risk Evidence With QuantLib",
    "Persona Policy Optimization With DSPy",
    "Behavior Cloning And Preference Learning With imitation And TRL",
    "Research-Only RL With FinRL, RLlib, And Ray Tune",
    "Optional W&B Tracking",
    "Multi-Persona OODA Proposal Synthesis",
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
        "LEAN Launcher",
        "broker adapter internals",
        "RuntimeBinding mutation",
        "RuntimeBootstrapRequest creation",
        "capital binding",
        "paper/canary/live promotion",
    ]
    for term in out_of_scope_terms:
        assert term in report


def test_persona_oss_report_cites_existing_proof_tests() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")

    for reference in PROOF_REFERENCES:
        assert reference in report
        assert (ROOT / reference).exists()


def test_persona_oss_report_includes_all_e2e_scenarios() -> None:
    report = REPORT_DOC.read_text(encoding="utf-8")

    for scenario in SCENARIOS:
        assert scenario in report

    forbidden_scope = [
        "No LEAN",
        "broker adapter",
        "RuntimeBinding",
        "separate downstream gates",
    ]
    for term in forbidden_scope:
        assert term in report


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
