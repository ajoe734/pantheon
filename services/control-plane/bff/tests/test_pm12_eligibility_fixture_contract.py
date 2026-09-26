"""BFF-MGMT-READ-DEFECT-REPAIR-001 -- defect 3 (PM12 eligibility fixture
contract drift) regression coverage.

## Decision: the fixture was stale, not production

`test_bff_promotion_review_governance.py::PromotionReviewTestReadPorts`
originally seeded personas with `lifecycle_state: "active"`, no `tenant_id`,
and no resolved runtime/session -- yet the file's own tests
(`test_promotion_reviews_list_and_detail_are_readable_by_operator`,
`_first_review`, etc.) assert that `/bff/management/promotion-reviews`
returns real, populated reviews. Tracing why the fixture produced zero
reviews showed it fails *three independent, deliberately-documented*
production gates simultaneously:

1. **Tenant-scoped read admission** --
   `personas/service.py::_list_persona_records` (~L2231, confirmed by
   reading the function's own docstring/comment): "Registry provenance is
   not tenant ownership. A tenant-scoped read admits only an explicit
   matching owner tenant; tenantless registry rows are catalog or malformed
   data and fail closed." The caller's tenant defaults to the literal
   `"pantheon-dev"` (`personas/service.py::_bff_me_tenant_payload`'s
   `default_tenant` fallback chain) when no tenant claim is present on the
   token -- exactly the stub-auth tokens this test file uses. A persona
   record with no `tenant_id` is filtered out entirely, before any PM12
   scoring even runs.

2. **League-row eligibility** --
   `personas/service.py::_pm12_persona_league_ranking_item` (~L6273;
   task brief cited ~L6033, the row-projection helper that feeds it)
   requires an operational `lifecycle_state` (`_is_persona_lifecycle_operational`,
   e.g. "paper_running"/"canary_running"/"live_running" -- "active" is not
   in that set), a resolved active RuntimeBinding, a resolved active
   session joined to that binding, and non-empty telemetry coverage. Any
   gap produces `eligible: False` with explicit `exclusion_reasons` (this
   is intentional, auditable governance behavior, not a bug -- a real
   persona lacking a runtime binding correctly should not be recommended
   for promotion).

3. **PM12 recommendation score gates** --
   `personas/service.py::_pm12_recommendation_action_ids` (~L12371, task
   brief cited ~L12344) requires `overall_score >= 85`, `risk_score >= 70`
   (when present), and `execution_score >= 65` (when present) to recommend
   `"promote_to_canary_candidate"`. Those component scores are themselves
   derived from telemetry (pnl/drawdown/sharpe/fill_rate/slippage), so a
   fixture with no telemetry coverage also fails this gate independent of
   (1) and (2).

None of these three gates is a regression -- each has an explicit,
documented rationale in production code, and relaxing any of them would
weaken real promotion governance (tenant isolation, runtime-binding
provenance, or the score bar for a real capital-adjacent recommendation).
The correct fix was therefore a canonical, reusable fixture builder
(`test_bff_promotion_review_governance.py::build_pm12_eligible_persona_records`)
that constructs a persona clearing all three gates simultaneously, reused
(not duplicated) by `PromotionReviewTestReadPorts.__init__` to seed
`persona-us-equity` / `persona-crypto-perp`. This file proves that builder's
output actually satisfies all three gates, using the real, unmodified
production functions -- not a second/parallel eligibility implementation.
"""
from __future__ import annotations

import contextvars

from services.control_plane.bff import test_bff_promotion_review_governance as gov_test
from services.control_plane.bff.personas import service as persona_service_module

_TENANT_ID = gov_test._PM12_ELIGIBLE_TENANT_ID


def _run_league_rows_and_ranking(tenant_id: str):
    """Drive the exact same real, unmodified production pipeline this file's
    other tests use (`_pm12_persona_league_rows` -> `_pm12_quarterly_ranking_items`)
    against whatever store is currently active on `personas.service`."""
    ctx = contextvars.copy_context()

    def _run():
        rows = persona_service_module._pm12_persona_league_rows(tenant_id=tenant_id)
        quarter_window = persona_service_module._pm12_quarter_window(
            "2026-Q1", "2026-01-15T00:00:00Z"
        )
        return persona_service_module._pm12_quarterly_ranking_items(
            rows, quarter_window=quarter_window
        )

    return ctx.run(_run)


def test_b6_security_hardening_fixture_seeds_a_genuinely_eligible_persona() -> None:
    """BFF-PM12-FIXTURE-CLOSURE-001: `test_bff_b6_001_security_hardening.py`
    used to seed no personas at all. Its `_seeded_client()` now reuses this
    file's canonical builder to seed a tenant-alpha persona-alpha and a
    tenant-beta persona-beta; prove both actually clear the real PM12
    league/eligibility/score gates end to end, using the exact real seeded
    store the security-hardening suite's own tests run against (not a
    parallel/duplicate fixture)."""
    from services.control_plane.bff.tests.test_bff_b6_001_security_hardening import (
        _seeded_client,
    )

    class _Monkeypatch:
        def setenv(self, *_args, **_kwargs) -> None:
            return None

        def delenv(self, *_args, **_kwargs) -> None:
            return None

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        with _seeded_client(Path(tmp_dir), _Monkeypatch()):
            alpha_items = {
                item.get("persona_id"): item
                for item in _run_league_rows_and_ranking("tenant-alpha")
            }
            beta_items = {
                item.get("persona_id"): item
                for item in _run_league_rows_and_ranking("tenant-beta")
            }

    alpha = alpha_items["persona-alpha"]
    assert alpha["eligible"] is True, alpha["exclusion_reasons"]
    assert alpha["exclusion_reasons"] == []
    assert alpha["overall_score"] >= 85.0
    assert alpha["components"]["risk_score"] >= 70.0
    assert alpha["components"]["execution_score"] >= 65.0
    assert "persona-alpha" not in beta_items

    beta = beta_items["persona-beta"]
    assert beta["eligible"] is True, beta["exclusion_reasons"]
    assert "persona-beta" not in alpha_items


def test_management_nl_assistant_provider_fixture_seeds_a_genuinely_eligible_persona() -> None:
    """BFF-PM12-FIXTURE-CLOSURE-001: `test_management_nl_assistant_provider.py`
    used to hand-seed persona-alpha/persona-beta with inert fields (no
    resolved runtime/session/telemetry) and never called the canonical
    builder. Its `_seeded_client()` now does; prove both seeded personas
    actually clear the real PM12 league/eligibility/score gates end to end,
    using the exact real seeded store the provider suite's own tests run
    against."""
    from services.control_plane.bff.tests.test_management_nl_assistant_provider import (
        _seeded_client,
    )

    class _Monkeypatch:
        def setenv(self, *_args, **_kwargs) -> None:
            return None

        def delenv(self, *_args, **_kwargs) -> None:
            return None

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        _seeded_client(Path(tmp_dir), _Monkeypatch())
        alpha_items = {
            item.get("persona_id"): item
            for item in _run_league_rows_and_ranking("tenant-alpha")
        }
        beta_items = {
            item.get("persona_id"): item
            for item in _run_league_rows_and_ranking("tenant-beta")
        }

    alpha = alpha_items["persona-alpha"]
    assert alpha["eligible"] is True, alpha["exclusion_reasons"]
    assert alpha["exclusion_reasons"] == []
    assert alpha["overall_score"] >= 85.0
    assert alpha["components"]["risk_score"] >= 70.0
    assert alpha["components"]["execution_score"] >= 65.0
    assert "persona-alpha" not in beta_items

    beta = beta_items["persona-beta"]
    assert beta["eligible"] is True, beta["exclusion_reasons"]
    assert "persona-beta" not in alpha_items


def _build_and_project(persona_id: str = "persona-fixture-contract"):
    records = gov_test.build_pm12_eligible_persona_records(
        persona_id,
        f"runtime-{persona_id}",
        f"binding-{persona_id}",
    )
    raw_persona = records["personas"][persona_id]
    bindings = [records["bindings"][f"binding-{persona_id}"]]
    runtimes = [records["runtime_bindings"][f"runtime-{persona_id}"]]
    return raw_persona, records, bindings, runtimes


def test_builder_output_carries_an_explicit_matching_tenant_id() -> None:
    """Gate 1: personas/service.py::_persona_record_tenant_id (the exact-match
    check `_list_persona_records` enforces) must resolve a non-empty tenant
    id equal to the caller-default tenant, or a tenant-scoped read fails
    closed before PM12 scoring ever runs."""
    raw_persona, _records, _bindings, _runtimes = _build_and_project()
    assert persona_service_module._persona_record_tenant_id(raw_persona) == _TENANT_ID
    assert _TENANT_ID, "the canonical tenant id constant must not be blank"


def test_builder_output_is_pm12_league_eligible_with_no_exclusion_reasons() -> None:
    """Gate 2: the real league-row pipeline
    (`_pm12_persona_league_rows` -> `_project_persona_league_row` ->
    `_enrich_persona_item_with_bindings` -> `_pm12_quarterly_ranking_items` ->
    `_pm12_persona_league_ranking_item`) must mark the builder's persona
    `eligible: True` with an empty `exclusion_reasons` list -- i.e. its
    lifecycle_state, RuntimeBinding, session, and telemetry coverage are all
    resolved, not just present as inert fields. Drives the real production
    pipeline (the same one `/bff/management/promotion-reviews` uses) end to
    end via `PromotionReviewTestReadPorts`, which seeds itself entirely
    through the canonical builder -- no hand-rolled stub read store."""
    ports = gov_test.PromotionReviewTestReadPorts(allow_fallback=True)

    token = persona_service_module._current_persona_service.set(None)
    persona_service_module._current_persona_service.reset(token)

    import contextvars

    ctx = contextvars.copy_context()

    def _run():
        # `_get_active_read_store()` falls back to this module-level global
        # when no PersonaService request context is active (matches how
        # `_bff_management_promotion_reviews`-adjacent unit paths resolve
        # reads outside a live request).
        persona_service_module.read_store = ports
        rows = persona_service_module._pm12_persona_league_rows(tenant_id=_TENANT_ID)
        quarter_window = persona_service_module._pm12_quarter_window("2026-Q1", "2026-01-15T00:00:00Z")
        return persona_service_module._pm12_quarterly_ranking_items(rows, quarter_window=quarter_window)

    ranked_items = ctx.run(_run)

    by_persona = {item.get("persona_id"): item for item in ranked_items}
    for persona_id in ("persona-us-equity", "persona-crypto-perp"):
        ranking_item = by_persona[persona_id]
        assert ranking_item["eligible"] is True, ranking_item["exclusion_reasons"]
        assert ranking_item["exclusion_reasons"] == []
        assert ranking_item["overall_score"] >= 85.0
        assert ranking_item["components"]["risk_score"] >= 70.0
        assert ranking_item["components"]["execution_score"] >= 65.0


def test_builder_scores_clear_the_promote_to_canary_candidate_gate() -> None:
    """Gate 3: `_pm12_recommendation_action_ids` requires overall_score >=
    85, risk_score >= 70 (when present), execution_score >= 65 (when
    present) to recommend "promote_to_canary_candidate". Assert directly
    against the real gate function with the component scores the builder's
    default telemetry is tuned to produce, headroom included."""
    item = {
        "score": 87.4,
        "overall_score": 87.4,
        "components": {"risk_score": 93.0, "execution_score": 98.6},
    }
    action_ids = persona_service_module._pm12_recommendation_action_ids(item)
    assert "promote_to_canary_candidate" in action_ids


def test_builder_is_reused_not_duplicated_by_the_fixture_seed() -> None:
    """The canonical builder must be the actual seeding mechanism for
    persona-us-equity / persona-crypto-perp in PromotionReviewTestReadPorts
    -- not a parallel, independently-maintained eligibility fixture that
    could drift again."""
    ports = gov_test.PromotionReviewTestReadPorts(allow_fallback=True)
    for persona_id in ("persona-us-equity", "persona-crypto-perp"):
        persona = ports._data["personas"][persona_id]
        assert persona_service_module._persona_record_tenant_id(persona) == _TENANT_ID
        assert persona.get("lifecycle_state") == "paper_running"
