from __future__ import annotations

import pytest

from services.governance.research_activation.disclosure_report import (
    BackendDisclosure,
    DisclosureReportError,
    RESEARCH_OUTPUT_SCOPE,
    ResearchBackendDisclosureReport,
    SCHEMA_VERSION,
    build_default_disclosure_report,
    validate_disclosure_report,
)


def test_default_disclosure_report_serializes_current_backend_truth() -> None:
    report = build_default_disclosure_report(generated_at="2026-05-19T00:00:00Z")
    payload = report.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["report_id"] == "research-backend-disclosure-2026-05-19"
    assert payload["validation"]["passed"] is True
    assert payload["summary"]["fail_closed"] is True
    assert payload["summary"]["stub_or_mock_default_count"] >= 8
    assert payload["summary"]["order_route_capable_count"] == 0

    adapter_ids = {adapter["adapter_id"] for adapter in payload["adapters"]}
    assert {
        "qlib",
        "trl",
        "finrl",
        "rllib",
        "ray_tune",
        "quantlib",
        "statsmodels",
        "vectorbt",
        "imitation_bc",
        "wandb_experiment_tracking",
    }.issubset(adapter_ids)
    assert "openclaw" not in adapter_ids
    assert all(
        adapter["output_scope"] == RESEARCH_OUTPUT_SCOPE for adapter in payload["adapters"]
    )

    qlib = report.adapter_by_id("qlib")
    assert qlib.default_backend == "stub_lgbm"
    assert qlib.default_backend_kind == "stub_mock"
    assert qlib.uses_stub_or_mock_by_default is True
    assert qlib.real_backend == "qlib_lgbm"
    assert qlib.real_backend_status == "selectable_gated"
    assert "QLIB_BACKEND=real" in qlib.activation_gates
    assert qlib.silent_stub_fallback is False
    assert qlib.can_route_orders is False

    rllib = report.adapter_by_id("rllib")
    assert rllib.default_backend == "stub_rllib"
    assert rllib.stub_backend == "stub_rllib"
    assert rllib.real_backend == "rllib_ppo"
    assert "PANTHEON_RLLIB_BACKEND=rllib" in rllib.activation_gates

    imitation = report.adapter_by_id("imitation_bc")
    assert imitation.uses_real_backend_by_default is True
    assert imitation.default_backend_kind == "real_local"

    wandb = report.adapter_by_id("wandb_experiment_tracking")
    assert wandb.default_backend == "not_selected"
    assert wandb.uses_real_backend_by_default is False
    assert wandb.real_backend_status == "selectable_gated"


def test_validate_disclosure_report_accepts_serialized_payload() -> None:
    report = build_default_disclosure_report(generated_at="2026-05-19T00:00:00Z")
    round_tripped = validate_disclosure_report(report.to_dict())

    assert round_tripped.adapter_by_id("trl").stub_backend == "stub_dpo"
    assert round_tripped.validate().passed is True


def test_disclosure_report_fails_closed_for_silent_stub_fallback_and_order_route() -> None:
    bad = BackendDisclosure(
        adapter_id="unsafe",
        adapter_kind="unsafe",
        default_backend="unsafe_real",
        default_backend_kind="real_external",
        activation_state="real_backend_default",
        real_backend="unsafe_real",
        real_backend_status="default",
        stub_backend="unsafe_stub",
        stub_backend_available=True,
        evidence_refs=("unsafe.py",),
        silent_stub_fallback=True,
        can_route_orders=True,
    )
    report = ResearchBackendDisclosureReport(
        report_id="bad",
        generated_at="2026-05-19T00:00:00Z",
        adapters=(bad,),
    )

    result = report.validate()

    assert result.passed is False
    codes = {issue.code for issue in result.errors}
    assert "silent_stub_fallback" in codes
    assert "order_route_not_allowed" in codes
    with pytest.raises(DisclosureReportError, match="silent_stub_fallback"):
        validate_disclosure_report(report)
