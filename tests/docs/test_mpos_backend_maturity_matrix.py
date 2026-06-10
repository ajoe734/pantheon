from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX_DOC = ROOT / "RESEARCH_BACKEND_MATURITY_MATRIX.md"
GAP_DOC = (
    ROOT
    / "docs/04/pantheon_multi_persona_ooda_gap_dispatch_2026-06-09/"
    / "MPOS_GAP_ASSESSMENT_AND_DISPATCH_2026-06-09.md"
)
DISPATCHER = ROOT / "services/research/experiment_orchestrator/parallel_dispatch.py"


def _mpos_section() -> str:
    text = MATRIX_DOC.read_text(encoding="utf-8")
    heading = "## MPOS Observe Backend Matrix (G6)"
    start = text.index(heading)
    next_heading = text.index("\n## ", start + len(heading))
    return text[start:next_heading]


def _compact(text: str) -> str:
    return " ".join(text.split())


def test_mpos_backend_matrix_covers_required_backends_and_posture() -> None:
    section = _mpos_section()
    compact_section = _compact(section)

    required_terms = [
        "`vectorbt`",
        "`vectorbt_portfolio`",
        "`governed`; Production Research Path for rapid strategy backtesting",
        "scoring_only_not_direct_action",
        "`Qlib`",
        "`qlib_rolling_oos`",
        "`smoke-tested`; Activation-Ready, not production-active",
        "no_order_route=true",
        "order_route=none",
        "`statsmodels`",
        "`governed`; Production Research Path for regime and econometrics research",
        "research_only_not_direct_action",
        "`QuantLib`",
        "Separate governed research path, not default-dispatch, not deferred",
        "`governed`; Production Research Path for derivatives pricing and risk research",
        "backend smoke or admission evidence alone is not execution authority",
    ]

    for term in required_terms:
        assert _compact(term) in compact_section


def test_mpos_backend_matrix_cites_proof_tests_for_each_backend() -> None:
    section = _mpos_section()

    proof_commands = [
        "python3 services/research/vectorbt/smoke_test.py",
        "python3 -m pytest services/research/vectorbt/test_adapter.py -q",
        "python3 services/research/qlib/smoke_test.py",
        "python3 -m unittest discover -s services/research/qlib -p 'test_*.py'",
        "python3 -m pytest tests/governance/test_qlib_proof_artifacts.py -q",
        "python3 services/research/statsmodels/smoke_test.py",
        "python3 -m pytest services/research/statsmodels/test_adapter.py -q",
        "python3 -m pytest tests/governance/test_statsmodels_proof_artifacts.py -q",
        "python3 services/research/quantlib/smoke_test.py",
        "python3 -m pytest services/research/quantlib/test_adapter.py -q",
        "python3 -m pytest tests/governance/test_quantlib_proof_artifacts.py -q",
    ]

    for command in proof_commands:
        assert command in section


def test_default_dispatcher_semantics_match_mpos_matrix() -> None:
    text = DISPATCHER.read_text(encoding="utf-8")
    start = text.index("def default_backend_registry()")
    end = text.index("\n\ndef _dispatch_one", start)
    registry_block = text[start:end]

    for backend_id in (
        '"vectorbt"',
        '"vectorbt_portfolio"',
        '"qlib"',
        '"qlib_rolling_oos"',
        '"statsmodels"',
    ):
        assert backend_id in registry_block

    assert "quantlib" not in registry_block.lower()


def test_gap_dispatch_links_mpos_backend_matrix_closure() -> None:
    text = GAP_DOC.read_text(encoding="utf-8")

    assert "RESEARCH_BACKEND_MATURITY_MATRIX.md` (`MPOS Observe Backend Matrix (G6)`)" in text
    assert "| MPOS-P2-BACKEND-001 | Codex | Claude |" in text
    assert (
        "`RESEARCH_BACKEND_MATURITY_MATRIX.md` section "
        "`MPOS Observe Backend Matrix (G6)` is the task-scoped closure artifact"
    ) in text
