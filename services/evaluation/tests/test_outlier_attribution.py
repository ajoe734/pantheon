"""Tests for weekly outlier ranking, 5-component decomposition, C3 routes, and production integration (Acceptances 2-5)."""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Mapping
import urllib.parse

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from services.agora.shadow_mode import build_decision_lock
from services.control_plane.bff.agora.platform_source_router import (
    AgoraPlatformSourceOwner,
    create_platform_source_router,
)
from services.evaluation.attribution.decomposition import (
    COMPONENT_NAMES,
    NO_LANGUAGE_MODEL_PROOF,
    bootstrap_luck_interval,
    decompose_observation,
    decompose_outlier,
    resolve_regime_context,
)
from services.evaluation.attribution.outliers import (
    OutlierAttributionConfig,
    detect_cohort_outliers,
    detect_disagreement_outliers,
)
from services.evaluation.attribution.store import AgoraPlatformSourceClient
from services.evaluation.main import (
    AttributionOwnerRuntime,
    app as production_app,
    create_evaluation_app,
)
from services.evaluation.tests.test_shadow_settlement import (
    SERVICE_TOKEN,
    load_recorded_twse_rows,
)
from services.execution.lean_runtime.member_virtual_book import MemberVirtualBooks


def test_config_defaults_match_design_and_support_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = OutlierAttributionConfig()
    assert cfg.min_sample_size == 10
    assert cfg.top_quantile == 0.10
    assert cfg.bottom_quantile == 0.10
    assert cfg.disagreement_std_multiplier == 1.0
    assert cfg.bootstrap_resamples == 1000
    assert cfg.observation_window == "5d"

    monkeypatch.setenv("PANTHEON_ATTRIBUTION_MIN_SAMPLE_SIZE", "12")
    monkeypatch.setenv("PANTHEON_ATTRIBUTION_TOP_QUANTILE", "0.2")
    monkeypatch.setenv("PANTHEON_ATTRIBUTION_DISAGREEMENT_STD_MULTIPLIER", "1.5")
    monkeypatch.setenv("PANTHEON_ATTRIBUTION_BOOTSTRAP_RESAMPLES", "500")
    overridden = OutlierAttributionConfig.from_env()
    assert overridden.min_sample_size == 12
    assert overridden.top_quantile == 0.2
    assert overridden.disagreement_std_multiplier == 1.5
    assert overridden.bootstrap_resamples == 500


def test_five_component_decomposition_telescoping_sum_luck_interval_and_regime_context() -> None:
    obs = {
        "direction": 1,
        "weight": 1.4,
        "decision_price": 100.0,
        "entry_price": 98.0,
        "exit_price": 106.0,
        "benchmark_exit_price": 104.0,
        "cost_pct": 0.004425,
    }
    comp = decompose_observation(obs, benchmark_return=0.01, baseline_weight=1.0)
    assert set(comp) == set(COMPONENT_NAMES)
    assert comp["entry_timing"] > 0.0  # bought below decision cutoff price
    assert comp["exit"] > 0.0  # exited above window benchmark close
    assert comp["sizing"] > 0.0  # overweight on a winning trade
    assert comp["costs"] == -0.004425

    # Exact telescoping sum check: selection + entry_timing + exit + sizing + costs == net_return - w_base * bench_return
    expected_gross = 1.4 * ((106.0 / 98.0) - 1.0)
    expected_net_excess = expected_gross - 0.004425 - 0.01
    assert math.isclose(sum(comp.values()), expected_net_excess, abs_tol=1e-5)

    # Bootstrap luck interval is deterministic and requires no LLM
    observations = [
        {**obs, "exit_price": 104.0 + i * 0.5}
        for i in range(12)
    ]
    governed_regime = {
        "regime_id": "reg-tw-20241115-v1",
        "regime_class": "bull_low_vol",
        "primary_axis": "volatility",
        "session": "2024-11-15",
        "confidence": 0.87,
        "macro": {"state": "expansion"},
    }
    bundle = decompose_outlier(
        observations,
        subject_id="persona-alpha",
        window_end="2024-11-15",
        resamples=1000,
        regime_state=governed_regime,
    )
    assert bundle["sample_size"] == 12
    assert bundle["computation_mode"] == NO_LANGUAGE_MODEL_PROOF
    assert bundle["regime_ref"] == "regime-state://reg-tw-20241115-v1"
    assert bundle["regime_context"]["available"] is True
    assert bundle["regime_context"]["regime_class"] == "bull_low_vol"
    assert bundle["regime_context"]["macro_state"] == "expansion"
    luck = bundle["luck_interval"]
    assert luck["resamples"] == 1000
    assert luck["low"] <= luck["mean"] <= luck["high"]
    assert set(luck["components"]) == set(COMPONENT_NAMES)

    # Missing governed RegimeState leaves regime_ref None
    no_ref, no_ctx = resolve_regime_context(None)
    assert no_ref is None
    assert no_ctx["available"] is False


def test_cohort_ranking_top_bottom_decile_minimum_sample_and_baseline() -> None:
    window = {"start": "2024-11-04", "end": "2024-11-10", "iso_week": "2024-W45"}
    subjects: List[Dict[str, Any]] = []
    # Create 10 personas with >= 10 decisions and 1 persona with only 5 decisions (below min_sample_size=10)
    for idx in range(10):
        base_ret = -0.015 + idx * 0.004  # idx 0 is worst (-1.5%), idx 9 is best (+2.1%)
        obs = [
            {
                "return_pct": base_ret + (0.001 if j % 2 == 0 else -0.0005),
                "decision_price": 100.0,
                "entry_price": 100.0,
                "exit_price": 100.0 * (1.0 + base_ret),
                "benchmark_exit_price": 100.0 * (1.0 + base_ret),
                "cost_pct": 0.0015,
                "direction": 1,
                "weight": 1.0,
            }
            for j in range(12)
        ]
        subjects.append(
            {
                "subject_id": f"persona-{idx:02d}",
                "observations": obs,
                "baseline": {"risk_adjusted_return": 0.2, "total_return": 0.01},
            }
        )
    # Ineligible subject with huge return but only 5 samples (< min_sample_size=10)
    subjects.append(
        {
            "subject_id": "persona-small-sample",
            "observations": [{"return_pct": 0.09} for _ in range(5)],
        }
    )

    records = detect_cohort_outliers(
        subjects,
        subject_kind="persona",
        window=window,
        config=OutlierAttributionConfig(min_sample_size=10, top_quantile=0.10, bottom_quantile=0.10),
    )
    by_id = {r["subject_id"]: r for r in records}
    assert "persona-small-sample" not in by_id
    assert "persona-09" in by_id and by_id["persona-09"]["direction"] == "outstanding"
    assert by_id["persona-09"]["metrics"]["peer_rank"] == 1
    assert by_id["persona-09"]["metrics"]["peer_count"] == 10
    assert by_id["persona-09"]["metrics"]["baseline_delta"] > 0
    assert "persona-00" in by_id and by_id["persona-00"]["direction"] == "underperform"
    assert by_id["persona-00"]["metrics"]["peer_rank"] == 10


def test_disagreement_detection_when_arms_diverge_beyond_subject_std() -> None:
    window = {"start": "2024-11-04", "end": "2024-11-10", "iso_week": "2024-W45"}
    # Build 10 decisions for trader-alice with small typical variance, plus 1 large disagreement decision
    decisions: List[Dict[str, Any]] = []
    for idx in range(10):
        ret = 0.002 if idx % 2 == 0 else -0.001
        decisions.append(
            {
                "decision_id": f"dec-norm-{idx}",
                "tenant_id": "tenant-alpha",
                "agora_user_id": "trader-alice",
                "arms": [
                    {
                        "arm": "human_actual",
                        "settlement_source": "pantheon_paper_proxy",
                        "outcome": {
                            "action": "enter",
                            "direction": 1,
                            "weight": 1.0,
                            "decision_price": 100.0,
                            "entry_price": 100.0,
                            "exit_price": 100.0 * (1.0 + ret),
                            "benchmark_exit_price": 100.0 * (1.0 + ret),
                            "cost_pct": 0.001,
                            "return_pct": ret,
                        },
                        "settled_at": "2024-11-09T00:00:00Z",
                        "unavailable_reason": None,
                    },
                    {
                        "arm": "private_servant",
                        "settlement_source": "pantheon_paper_proxy",
                        "outcome": {
                            "action": "enter",
                            "direction": 1,
                            "weight": 1.0,
                            "decision_price": 100.0,
                            "entry_price": 100.0,
                            "exit_price": 100.0 * (1.0 + ret),
                            "benchmark_exit_price": 100.0 * (1.0 + ret),
                            "cost_pct": 0.001,
                            "return_pct": ret,
                        },
                        "settled_at": "2024-11-09T00:00:00Z",
                        "unavailable_reason": None,
                    },
                ],
            }
        )

    # Decision 11: human entered and lost -4.5%, while private_servant suggested exit/short and gained +3.8%
    decisions.append(
        {
            "decision_id": "dec-diverged-11",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-alice",
            "account_id": "acct-alice-01",
            "broker_ids": ["tw-cathay"],
            "raw_text": "逆勢加碼，不採納僕人停損建議",
            "arms": [
                {
                    "arm": "human_actual",
                    "settlement_source": "pantheon_paper_proxy",
                    "outcome": {
                        "action": "enter",
                        "direction": 1,
                        "weight": 1.2,
                        "decision_price": 100.0,
                        "entry_price": 100.0,
                        "exit_price": 96.5,
                        "benchmark_exit_price": 97.0,
                        "cost_pct": 0.003,
                        "return_pct": -0.045,
                    },
                    "settled_at": "2024-11-10T00:00:00Z",
                    "unavailable_reason": None,
                },
                {
                    "arm": "private_servant",
                    "settlement_source": "pantheon_paper_proxy",
                    "outcome": {
                        "action": "exit",
                        "direction": -1,
                        "weight": 1.0,
                        "decision_price": 100.0,
                        "entry_price": 100.0,
                        "exit_price": 96.5,
                        "benchmark_exit_price": 97.0,
                        "cost_pct": 0.002,
                        "return_pct": 0.033,
                    },
                    "settled_at": "2024-11-10T00:00:00Z",
                    "unavailable_reason": None,
                },
            ],
        }
    )

    disagreements = detect_disagreement_outliers(decisions, window=window)
    assert len(disagreements) == 1
    rec = disagreements[0]
    assert rec["direction"] == "disagreement"
    assert rec["subject_kind"] == "trader"
    assert rec["subject_id"] == "trader-alice"
    assert rec["decision_id"] == "dec-diverged-11"
    assert rec["metrics"]["counterpart_arm"] == "private_servant"
    assert rec["metrics"]["abs_outcome_gap"] > rec["metrics"]["disagreement_threshold"]
    assert set(rec["decomposition"]) == set(COMPONENT_NAMES)
    assert rec["account_id"] == "acct-alice-01"
    assert rec["raw_text"] == "逆勢加碼，不採納僕人停損建議"


def test_production_entrypoint_integration_with_real_agora_platform_source_and_recorded_twse_data() -> None:
    """Acceptance 5: Run production entry point over real recorded market rows, Agora locks, virtual books, and paper fills."""
    market_rows = load_recorded_twse_rows()
    tw_2330_dates = sorted({r["date"] for r in market_rows if r["symbol"] == "2330.TWSE"})
    assert len(tw_2330_dates) >= 40

    # 1. Mount the real AgoraPlatformSourceOwner and router from AGORA-PLATFORM-SOURCE-20261116
    agora_source_owner = AgoraPlatformSourceOwner()
    agora_app = FastAPI()
    agora_app.include_router(create_platform_source_router(owner=agora_source_owner))
    agora_client = TestClient(agora_app)

    # Record 10 real Agora decision locks for trader-winner and 10 for trader-loser via build_decision_lock
    paper_fills: List[Dict[str, Any]] = []
    for i in range(10):
        cutoff_date = tw_2330_dates[i]
        # Winner trader lock on 2330.TWSE
        win_id = f"lock-win-{i:02d}"
        win_lock = build_decision_lock(
            human_decision_id=win_id,
            decision_record_id=f"rec-win-{i:02d}",
            event={
                "strategy_id": "strat-semi-leader",
                "strategy_version_id": "v1",
                "suggested_action": "enter",
                "event_kind": "entry",
                "data_cutoff": f"{cutoff_date}T05:30:00Z",
                "subject": {"symbol": "2330.TWSE"},
                "servant_suggestion": {"action": "no_trade" if i == 0 else "enter", "persona_id": "servant-win"},
                "institutional_view": {"verdict": "enter", "meeting_id": "mtg-inst-01"},
            },
            decision_event_id=f"evt-win-{i:02d}",
            tenant_id="tenant-alpha",
            user_id="trader-winner",
            body_decision="approve",
            rationale=f"台積電順勢做多 #{i}",
            modifications=None,
            decision_time=f"{cutoff_date}T06:00:00Z",
            account_id="acct-win-01",
            broker_ids=["tw-fubon"],
        )
        agora_source_owner.record_decision_lock(win_lock)
        paper_fills.append(
            {
                "fill_id": f"fill-win-{i:02d}",
                "decision_id": win_id,
                "arm": "human_actual",
                "fill_price": 900.0,
                "exit_price": 965.0 if i == 0 else 935.0,
                "settlement_source": "pantheon_paper_proxy",
            }
        )

        # Loser trader lock on 2317.TWSE
        loss_id = f"lock-loss-{i:02d}"
        loss_lock = build_decision_lock(
            human_decision_id=loss_id,
            decision_record_id=f"rec-loss-{i:02d}",
            event={
                "strategy_id": "strat-countertrend",
                "strategy_version_id": "v1",
                "suggested_action": "enter",
                "event_kind": "entry",
                "data_cutoff": f"{cutoff_date}T05:30:00Z",
                "subject": {"symbol": "2317.TWSE"},
                "servant_suggestion": {"action": "enter", "persona_id": "servant-loss"},
            },
            decision_event_id=f"evt-loss-{i:02d}",
            tenant_id="tenant-alpha",
            user_id="trader-loser",
            body_decision="approve",
            rationale=f"追高測試 #{i}",
            modifications=None,
            decision_time=f"{cutoff_date}T06:00:00Z",
            account_id="acct-loss-01",
            broker_ids=["tw-yuanta"],
        )
        agora_source_owner.record_decision_lock(loss_lock)
        paper_fills.append(
            {
                "fill_id": f"fill-loss-{i:02d}",
                "decision_id": loss_id,
                "arm": "human_actual",
                "fill_price": 210.0,
                "exit_price": 195.0,
                "settlement_source": "pantheon_paper_proxy",
            }
        )

    agora_source_owner.record_decision_journal_entry(
        {
            "entry_id": "jrn-win-01",
            "tenant_id": "tenant-alpha",
            "agora_user_id": "trader-winner",
            "account_id": "acct-win-01",
            "broker_ids": ["tw-fubon"],
            "raw_text": "法說會前分批進場紀錄",
            "created_at": "2024-10-15T06:00:00Z",
        }
    )

    # 2. Build real MemberVirtualBooks snapshots over recorded TWSE bars for pool members & institutional personas
    vb_engine = MemberVirtualBooks()
    bars_2330 = [r for r in market_rows if r["symbol"] == "2330.TWSE"][:15]
    for bar in bars_2330:
        vb_engine.observe(
            binding_id="bind-paper-01",
            envelope_checksum="sha256:env01",
            symbol="2330.TWSE",
            event_time=f"{bar['date']}T06:00:00Z",
            price=bar["close"],
            open_price=bar["open"],
            source_ref=bar["source_ref"],
            bar_frequency="1d",
            members={
                "member-bull": {
                    "artifact_id": "art-bull",
                    "version": "1.0",
                    "checksum": "sha256:bull",
                    "virtual_only": False,
                    "underlying_count": 1,
                    "view": {"direction": "LONG", "strength": 1.0},
                },
                "member-bear": {
                    "artifact_id": "art-bear",
                    "version": "1.0",
                    "checksum": "sha256:bear",
                    "virtual_only": False,
                    "underlying_count": 1,
                    "view": {"direction": "SHORT", "strength": 1.0},
                },
            },
        )
    vb_snapshots = vb_engine.snapshot(binding_id="bind-paper-01", envelope_checksum="sha256:env01")
    for snap in vb_snapshots:
        snap["persona_id"] = "persona-inst-bull" if snap["member_id"] == "member-bull" else "persona-inst-bear"

    # Connect AgoraPlatformSourceClient to the real Agora platform_source_router ASGI app
    def _agora_asgi_transport(url: str, headers: Mapping[str, str]) -> Mapping[str, Any]:
        parsed = urllib.parse.urlsplit(url)
        path_qs = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        res = agora_client.get(path_qs, headers=dict(headers))
        assert res.status_code == 200, res.text
        return res.json()

    source_client = AgoraPlatformSourceClient(
        api_url="http://operator-bff:8001/internal/agora/platform-source/records",
        service_token="pantheon-local-agora-platform-source-service-token",
        service_actor="attribution",
        transport=_agora_asgi_transport,
    )

    runtime = AttributionOwnerRuntime(
        service_tokens=[SERVICE_TOKEN],
        agora_source_client=source_client,
        market_rows=market_rows,
        paper_fills=paper_fills,
        member_virtual_books=vb_snapshots,
        regime_state={
            "regime_id": "reg-tw-20241025",
            "regime_class": "bull_expansion",
            "primary_axis": "trend",
            "session": "2024-10-25",
            "confidence": 0.91,
            "macro": {"state": "expansion"},
        },
    )
    eval_app = create_evaluation_app(runtime)
    client = TestClient(eval_app)
    auth_headers = {"Authorization": f"Bearer {SERVICE_TOKEN}", "X-Tenant-Id": "tenant-alpha"}

    # Verify production module-level app also mounts all C3 routes
    prod_routes = {(m, getattr(r, "path", None)) for r in production_app.routes for m in getattr(r, "methods", ())}
    for expected in (
        ("POST", "/api/attribution/runs"),
        ("GET", "/api/attribution/records"),
        ("GET", "/api/attribution/records/{record_id}"),
        ("GET", "/api/attribution/shadow/{decision_id}"),
    ):
        assert expected in prod_routes, expected

    # 3. Trigger weekly run via POST /api/attribution/runs and verify ISO-week idempotency
    run_resp = client.post(
        "/api/attribution/runs",
        json={"kind": "weekly", "window_end": "2024-10-27"},
        headers=auth_headers,
    )
    assert run_resp.status_code == 200, run_resp.text
    run_data = run_resp.json()
    assert run_data["status"] == "completed"
    assert run_data["iso_week"] == "2024-W43"
    assert run_data["idempotent_replay"] is False
    assert len(run_data["record_ids"]) >= 5

    # Second call for the same ISO week is idempotent and does not duplicate records
    replay_resp = client.post(
        "/api/attribution/runs",
        json={"kind": "weekly", "window_end": "2024-10-26"},
        headers=auth_headers,
    )
    assert replay_resp.status_code == 200
    replay_data = replay_resp.json()
    assert replay_data["run_id"] == run_data["run_id"]
    assert replay_data["idempotent_replay"] is True
    assert replay_data["record_ids"] == run_data["record_ids"]

    # 4. Query GET /api/attribution/records across subject_kind and direction filters + pagination
    all_list = client.get("/api/attribution/records?since=2024-10-20&limit=2", headers=auth_headers).json()
    assert len(all_list["items"]) == 2
    assert all_list["next_cursor"] is not None

    page2 = client.get(
        f"/api/attribution/records?since=2024-10-20&limit=50&cursor={urllib.parse.quote(all_list['next_cursor'])}",
        headers=auth_headers,
    ).json()
    combined_ids = [r["record_id"] for r in all_list["items"]] + [r["record_id"] for r in page2["items"]]
    assert set(combined_ids) == set(run_data["record_ids"])

    for s_kind in ("trader", "persona", "pool_member"):
        filtered = client.get(f"/api/attribution/records?subject_kind={s_kind}", headers=auth_headers)
        assert filtered.status_code == 200
        items = filtered.json()["items"]
        assert items, f"expected outlier records for {s_kind}"
        assert all(i["subject_kind"] == s_kind for i in items)
        for item in items:
            assert set(item["decomposition"]) == set(COMPONENT_NAMES)
            assert item["sample_size"] >= 10
            assert item["luck_interval"]["resamples"] == 1000
            assert item["regime_ref"] == "regime-state://reg-tw-20241025"

    disagreement_list = client.get("/api/attribution/records?direction=disagreement", headers=auth_headers).json()
    assert len(disagreement_list["items"]) >= 1
    assert disagreement_list["items"][0]["decision_id"] == "lock-win-00"

    # 5. Query GET /api/attribution/records/{record_id} and verify 404 on unknown
    first_rid = run_data["record_ids"][0]
    detail_resp = client.get(f"/api/attribution/records/{first_rid}", headers=auth_headers)
    assert detail_resp.status_code == 200
    assert detail_resp.json()["record_id"] == first_rid

    assert client.get("/api/attribution/records/nonexistent-record-id", headers=auth_headers).status_code == 404
    assert client.get("/api/attribution/records?subject_kind=invalid", headers=auth_headers).status_code == 422
    assert client.get("/api/attribution/records?direction=invalid", headers=auth_headers).status_code == 422

    # 6. Query GET /api/attribution/shadow/{decision_id} settled during the run
    shadow_resp = client.get("/api/attribution/shadow/lock-win-00", headers=auth_headers)
    assert shadow_resp.status_code == 200
    shadow_arms = {a["arm"]: a for a in shadow_resp.json()["arms"]}
    assert shadow_arms["human_actual"]["settlement_source"] == "pantheon_paper_proxy"
    assert shadow_arms["private_servant"]["settlement_source"] == "declared_no_trade"
