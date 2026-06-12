"""Persona OODA cycle runtime for management-visible autonomy drills.

The runtime in this module starts from existing persona records supplied by the
management read surface, attaches repo-backed alpha seed evidence, runs a small
bank of real OSS backtests, and emits replayable OODA packet records.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from services.persona.oss_runtime import PersonaOSSRequest, PersonaOSSResult, run_persona_oss_request


REPO_ROOT = Path(__file__).resolve().parents[2]
OODA_DIR = REPO_ROOT / "services" / "control-plane" / "ooda"
if str(OODA_DIR) not in sys.path:
    sys.path.insert(0, str(OODA_DIR))

from jsonl_store import OodaJsonlAppendStore  # noqa: E402
from ooda_loop_packet import (  # noqa: E402
    ActBundle,
    DecideBundle,
    LearnBundle,
    LoopEnvironment,
    LoopStatus,
    LoopType,
    ObservationWindow,
    ObserveBundle,
    OodaLoopPacket,
    OrientBundle,
)
from stage_transition import validate_status_transition  # noqa: E402


CYCLES_PER_PERSONA = 15
DEFAULT_BACKTEST_COUNT = 5
DEFAULT_GENERATED_AT = "2026-06-12T09:00:00Z"
HISTORICAL_OHLCV_FIXTURE = "services/research/qlib/examples/smoke_dataset.json"
HISTORICAL_OHLCV_DATASET_ID = "dataset:tw-equity-ohlcv-top50-2024-daily"


@dataclass(frozen=True)
class AlphaSeedSource:
    key: str
    strategy_id: str
    source_strategy_spec_id: str
    source_dataset_refs: tuple[str, ...]
    evidence_path: str
    anchors: tuple[str, ...]


@dataclass(frozen=True)
class OodaScenario:
    scenario_id: str
    stage: str
    loop_type: str
    title: str
    daily_work: str
    action_taken: str
    operator_interaction: str
    requires_human_gate: bool = False
    incident_like: bool = False


@dataclass(frozen=True)
class PersonaOodaBatchRun:
    store_path: Path
    summary: dict[str, Any]
    packets: tuple[dict[str, Any], ...]
    backtest_results: tuple[dict[str, Any], ...]
    session_results: tuple[dict[str, Any], ...]


ALPHA_SEED_SOURCES: tuple[AlphaSeedSource, ...] = (
    AlphaSeedSource(
        key="qlib_tw_cross_sectional",
        strategy_id="tw-cross-sectional-equity-alpha",
        source_strategy_spec_id="qlib-tw-cross-sectional-alpha-spec-v1",
        source_dataset_refs=("dataset:tw-equity-ohlcv-top50-2024-daily",),
        evidence_path="services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md",
        anchors=("Strategy ID**: `tw-cross-sectional-equity-alpha`", "5 trading days"),
    ),
    AlphaSeedSource(
        key="per002_alpha_momentum",
        strategy_id="per-002-alpha-tw-equity-momentum",
        source_strategy_spec_id="eb-per-002-alpha-001",
        source_dataset_refs=("src-per-002-alpha-001", "ei-per-002-alpha-001"),
        evidence_path="tests/e2e/test_persona_abc_ooda_evidence_chain.py",
        anchors=("src-per-002-alpha-001", "TW equity momentum mandate"),
    ),
    AlphaSeedSource(
        key="per002_beta_reversal",
        strategy_id="per-002-beta-tw-equity-reversal",
        source_strategy_spec_id="eb-per-002-beta-001",
        source_dataset_refs=("src-per-002-beta-001", "ei-per-002-beta-001"),
        evidence_path="tests/e2e/test_persona_abc_ooda_evidence_chain.py",
        anchors=("src-per-002-beta-001", "short-term reversal patterns"),
    ),
    AlphaSeedSource(
        key="per002_gamma_value_quality",
        strategy_id="per-002-gamma-global-equity-value-quality",
        source_strategy_spec_id="eb-per-002-gamma-001",
        source_dataset_refs=("src-per-002-gamma-001", "ei-per-002-gamma-001"),
        evidence_path="tests/e2e/test_persona_abc_ooda_evidence_chain.py",
        anchors=("src-per-002-gamma-001", "low price-to-book ratio"),
    ),
    AlphaSeedSource(
        key="source_ingestion_lightgbm_alpha",
        strategy_id="paper-lightgbm-tw-alpha",
        source_strategy_spec_id="strat-tw-lightgbm-alpha-v1",
        source_dataset_refs=("src-paper-alpha-001", "evbundle-alpha-001", "evi-alpha-001"),
        evidence_path="services/source_ingestion/tests/test_strategy_seed_builder.py",
        anchors=("src-paper-alpha-001", "LightGBM TW equity factors"),
    ),
)


OODA_SCENARIOS: tuple[OodaScenario, ...] = (
    OodaScenario(
        "daily_market_source_observe",
        "observe",
        LoopType.PAPER_STRATEGY.value,
        "Daily market/source readback",
        "Read persona catalog state, market data refs, and current work queue.",
        "Recorded read-only market/source evidence and session heartbeat.",
        "Management can open the packet detail and verify the source refs.",
    ),
    OodaScenario(
        "telemetry_health_observe",
        "observe",
        LoopType.REBALANCE.value,
        "Telemetry and health observe",
        "Check persona performance, runtime health, and policy restrictions.",
        "Recorded telemetry health and no-live-side-effect posture.",
        "Management can filter closed observe packets for health triage.",
    ),
    OodaScenario(
        "human_feedback_observe",
        "observe",
        LoopType.INCIDENT_RESPONSE.value,
        "Human feedback observe",
        "Capture operator feedback and current approval pressure.",
        "Recorded human feedback refs without mutating capital.",
        "Management can inspect the human gate references.",
        requires_human_gate=True,
    ),
    OodaScenario(
        "regime_orientation",
        "orient",
        LoopType.PAPER_STRATEGY.value,
        "Regime orientation",
        "Interpret market regime and persona mandate fit.",
        "Attached regime, research status, and alpha seed interpretation.",
        "Management can compare orient evidence across personas.",
    ),
    OodaScenario(
        "risk_pricing_orientation",
        "orient",
        LoopType.REBALANCE.value,
        "Risk and pricing orientation",
        "Review risk posture, drawdown, and instrument pricing context.",
        "Attached risk adjudication and pricing/risk refs.",
        "Management can drill into risk-oriented closed packets.",
    ),
    OodaScenario(
        "data_quality_orientation",
        "orient",
        LoopType.INCIDENT_RESPONSE.value,
        "Data quality orientation",
        "Resolve provider readback gaps and unavailable data sources.",
        "Recorded data-source quality and unavailable-provider refs.",
        "Management can see degraded provider context before decisions.",
        incident_like=True,
    ),
    OodaScenario(
        "alpha_backtest_decision",
        "decide",
        LoopType.PAPER_STRATEGY.value,
        "Alpha backtest decision",
        "Use one of the bounded real vectorbt backtest results.",
        "Drafted a paper strategy decision from actual backtest metrics.",
        "Management can follow strategy_id to related OODA packets.",
    ),
    OodaScenario(
        "allocation_rebalance_decision",
        "decide",
        LoopType.PERSONA_SYNTHESIS.value,
        "Allocation rebalance decision",
        "Resolve persona allocation proposal and sponsor context.",
        "Recorded allocation proposal refs and sponsor decision.",
        "Management can inspect synthesis and policy refs.",
    ),
    OodaScenario(
        "governance_policy_decision",
        "decide",
        LoopType.EVOLUTION.value,
        "Governance policy decision",
        "Apply allowed actions, restrictions, and human gate policy.",
        "Recorded governance advisory action and policy decision refs.",
        "Management can interact through the persona action links.",
        requires_human_gate=True,
    ),
    OodaScenario(
        "runtime_handoff_act",
        "act",
        LoopType.PAPER_STRATEGY.value,
        "Runtime handoff act",
        "Submit paper-runtime handoff context without broker/order side effects.",
        "Recorded runtime binding and command receipt refs.",
        "Management can query runtime-linked OODA packets.",
    ),
    OodaScenario(
        "safe_mode_act",
        "act",
        LoopType.REBALANCE.value,
        "Safe-mode act",
        "Apply safe-mode posture when thresholds or operator review require it.",
        "Recorded safe-mode refs and no-live-capital assertion.",
        "Management can verify fail-closed posture.",
        requires_human_gate=True,
    ),
    OodaScenario(
        "incident_recovery_act",
        "act",
        LoopType.INCIDENT_RESPONSE.value,
        "Incident recovery act",
        "Handle incident-like data or runtime degradation with rollback refs.",
        "Recorded rollback/safe-mode refs and recovery command receipts.",
        "Management can inspect incident response evidence.",
        incident_like=True,
    ),
    OodaScenario(
        "experiment_tracking_learn",
        "learn",
        LoopType.PAPER_STRATEGY.value,
        "Experiment tracking learn",
        "Link experiment/backtest outcomes into follow-up evidence.",
        "Recorded experiment tracking and post-action observation refs.",
        "Management can cite the experiment refs in follow-up work.",
    ),
    OodaScenario(
        "trainer_retrain_learn",
        "learn",
        LoopType.REBALANCE.value,
        "Trainer retrain learn",
        "Capture trainer or retrain follow-through from cycle outcomes.",
        "Recorded trainer and retrain refs for persona evolution.",
        "Management can see retrain follow-through remains non-live.",
    ),
    OodaScenario(
        "evolution_followthrough_learn",
        "learn",
        LoopType.EVOLUTION.value,
        "Evolution follow-through learn",
        "Close the evolution loop and schedule the next daily autonomous work.",
        "Recorded evolution follow-through and autonomous next-work refs.",
        "Management can see the persona closed the cycle and queued next work.",
    ),
)


def validate_alpha_seed_sources() -> None:
    """Ensure every alpha seed source used by the runtime exists in this repo."""

    for source in ALPHA_SEED_SOURCES:
        evidence_path = REPO_ROOT / source.evidence_path
        if not evidence_path.exists():
            raise FileNotFoundError(source.evidence_path)
        text = evidence_path.read_text(encoding="utf-8")
        for anchor in source.anchors:
            if anchor not in text:
                raise ValueError(f"Missing anchor {anchor!r} in {source.evidence_path}")


def run_management_persona_ooda_cycles(
    personas: Sequence[Mapping[str, Any]],
    *,
    store_path: str | Path,
    persona_contexts: Mapping[str, Mapping[str, Any]] | None = None,
    cycles_per_persona: int = CYCLES_PER_PERSONA,
    backtest_count: int = DEFAULT_BACKTEST_COUNT,
    generated_at: str = DEFAULT_GENERATED_AT,
    reset_store: bool = False,
) -> PersonaOodaBatchRun:
    """Run every supplied persona through a management-visible OODA batch."""

    if cycles_per_persona != len(OODA_SCENARIOS):
        raise ValueError(f"cycles_per_persona must be {len(OODA_SCENARIOS)} for full scenario coverage")
    if not 3 <= backtest_count <= 5:
        raise ValueError("backtest_count must stay within the requested 3-5 result window")
    if not personas:
        raise ValueError("at least one persona is required")

    validate_alpha_seed_sources()
    normalized_personas = [dict(persona) for persona in personas]
    contexts = {str(key): dict(value) for key, value in (persona_contexts or {}).items()}
    target_store_path = Path(store_path)
    if reset_store and target_store_path.exists():
        target_store_path.unlink()
    append_store = OodaJsonlAppendStore(target_store_path)

    backtest_results = _run_backtest_bank(normalized_personas, backtest_count)
    session_results = _run_openclaw_session_bank(normalized_personas, contexts)
    packets: list[dict[str, Any]] = []

    for persona_index, persona in enumerate(normalized_personas):
        persona_id = _persona_id(persona)
        context = contexts.get(persona_id, {})
        for scenario_index, scenario in enumerate(OODA_SCENARIOS, start=1):
            seed = ALPHA_SEED_SOURCES[(persona_index + scenario_index - 1) % len(ALPHA_SEED_SOURCES)]
            backtest = backtest_results[(persona_index + scenario_index - 1) % len(backtest_results)]
            session = session_results[persona_id]
            packet, transitions, snapshots = _build_closed_cycle_packet(
                persona=persona,
                persona_context=context,
                scenario=scenario,
                cycle_no=scenario_index,
                persona_index=persona_index,
                seed=seed,
                backtest=backtest,
                session=session,
                generated_at=generated_at,
            )
            append_store.append_packet(snapshots[0])
            for transition, snapshot in zip(transitions, snapshots[1:]):
                append_store.append_stage_transition(packet["packet_id"], transition, packet=snapshot)
            packets.append(packet)

    replayed = OodaJsonlAppendStore(target_store_path)
    replayed_packets = replayed.list_packets()
    summary = _build_batch_summary(
        personas=normalized_personas,
        packets=replayed_packets,
        backtest_results=backtest_results,
        session_results=session_results,
    )
    return PersonaOodaBatchRun(
        store_path=target_store_path,
        summary=summary,
        packets=tuple(replayed_packets),
        backtest_results=tuple(backtest_results),
        session_results=tuple(session_results.values()),
    )


def _run_backtest_bank(personas: Sequence[Mapping[str, Any]], backtest_count: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for index in range(backtest_count):
        persona = personas[index % len(personas)]
        persona_id = _persona_id(persona)
        seed = ALPHA_SEED_SOURCES[index % len(ALPHA_SEED_SOURCES)]
        short_window = 3 + index
        source_dataset_refs = _dedupe([HISTORICAL_OHLCV_DATASET_ID, *seed.source_dataset_refs])
        payload = {
            "dataset_fixture_path": HISTORICAL_OHLCV_FIXTURE,
            "dataset_id": HISTORICAL_OHLCV_DATASET_ID,
            "strategy_id": f"{seed.strategy_id}-ooda15-backtest-{index + 1:02d}",
            "source_strategy_spec_id": seed.source_strategy_spec_id,
            "source_dataset_refs": source_dataset_refs,
            "version": f"15.0.{index + 1}",
            "instrument_count": 2,
            "instrument_offset": index * 2,
            "short_window": short_window,
            "long_window": short_window + 9,
            "init_cash": 100_000 + index * 10_000,
            "fees": round(0.0005 + index * 0.0001, 5),
            "metadata": {
                "alpha_seed_key": seed.key,
                "alpha_seed_base_strategy_id": seed.strategy_id,
                "alpha_seed_source_ref": seed.evidence_path,
                "cycle_bank": "persona_ooda_15_cycles",
                "historical_ohlcv_fixture": HISTORICAL_OHLCV_FIXTURE,
                "historical_ohlcv_dataset_id": HISTORICAL_OHLCV_DATASET_ID,
            },
        }
        request = PersonaOSSRequest(
            persona_id=persona_id,
            session_id=f"session-ooda15-backtest-{index + 1:02d}",
            component="vectorbt",
            intent=f"ooda15_backtest_{seed.key}_{index + 1:02d}",
            payload=payload,
            request_id=f"req-ooda15-backtest-{index + 1:02d}",
        )
        result = run_persona_oss_request(request)
        results.append(_oss_result_summary(result, seed=seed, payload=payload))
    return results


def _run_openclaw_session_bank(
    personas: Sequence[Mapping[str, Any]],
    contexts: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for index, persona in enumerate(personas):
        persona_id = _persona_id(persona)
        metadata = _metadata(persona)
        context = contexts.get(persona_id, {})
        request = PersonaOSSRequest(
            persona_id=persona_id,
            session_id=f"session-ooda15-{_safe_id(persona_id)}",
            component="openclaw",
            intent="start_daily_ooda_autonomy_session",
            payload={
                "session_type": "background_job",
                "operator_id": "management-ai-ooda-runner",
                "upstream_state": "active",
                "context_bundle": {
                    "persona_id": persona_id,
                    "persona_name": persona.get("name"),
                    "mandate": persona.get("mandate"),
                    "strategy_family": persona.get("strategy_family"),
                    "current_work": metadata.get("current_work"),
                    "runtime_binding_id": _runtime_id(persona, context),
                    "cycle_count": CYCLES_PER_PERSONA,
                    "catalog_hash": _stable_hash(persona),
                },
            },
            request_id=f"req-ooda15-openclaw-{index + 1:02d}",
        )
        result = run_persona_oss_request(request)
        results[persona_id] = _oss_result_summary(result, seed=None, payload=request.payload)
    return results


def _build_closed_cycle_packet(
    *,
    persona: Mapping[str, Any],
    persona_context: Mapping[str, Any],
    scenario: OodaScenario,
    cycle_no: int,
    persona_index: int,
    seed: AlphaSeedSource,
    backtest: Mapping[str, Any],
    session: Mapping[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    persona_id = _persona_id(persona)
    safe_persona_id = _safe_id(persona_id)
    base_time = _parse_utc(generated_at)
    packet = OodaLoopPacket(
        packet_id=f"ooda-{safe_persona_id}-{cycle_no:02d}-{scenario.scenario_id}",
        loop_type=scenario.loop_type,
        status=LoopStatus.OPEN,
        environment=LoopEnvironment.PAPER,
        created_at=_cycle_timestamp(base_time, persona_index, cycle_no, 0),
        updated_at=_cycle_timestamp(base_time, persona_index, cycle_no, 0),
        capital_pool_id=_capital_pool_id(persona, persona_context),
        strategy_id=_strategy_id(persona, seed, backtest),
        persona_ids=[persona_id],
        observe=ObserveBundle(),
        orient=OrientBundle(),
        decide=DecideBundle(),
        act=ActBundle(live_capital_side_effects=False),
        learn=LearnBundle(),
        audit_refs=[
            f"audit://persona-ooda15/{persona_id}/{scenario.scenario_id}/catalog-hash/{_stable_hash(persona)}",
            f"audit://persona-ooda15/{persona_id}/{scenario.scenario_id}/no-live-capital-side-effects",
        ],
    )

    metadata = _metadata(persona)
    source_refs = _source_refs(persona, seed)
    packet.observe.source_refs.extend(source_refs)
    packet.observe.telemetry_refs.extend(_telemetry_refs(persona, scenario))
    packet.observe.signal_refs.extend([
        f"signal://persona-ooda15/{persona_id}/{scenario.scenario_id}",
        f"oss://openclaw/{session['request_id']}",
    ])
    packet.observe.market_refs.extend(_market_refs(persona))
    if scenario.requires_human_gate:
        packet.observe.human_feedback_refs.append(f"human-gate://persona-ooda15/{persona_id}/{scenario.scenario_id}")

    packet.orient.regime_state_ref = f"regime://persona-ooda15/{persona_id}/{_research_stage(persona)}"
    packet.orient.universe_selection_ref = f"universe://persona-ooda15/{persona_id}/{_market_scope_token(persona)}"
    packet.orient.signal_inference_refs.extend([
        f"signal-inference://persona-ooda15/{persona_id}/{scenario.scenario_id}",
        f"backtest://{backtest['request_id']}/metric-sharpe",
    ])
    packet.orient.allocation_proposal_refs.append(
        f"allocation-proposal://persona-ooda15/{persona_id}/{cycle_no:02d}"
    )
    packet.orient.risk_adjudication_ref = f"risk-adjudication://persona-ooda15/{persona_id}/{scenario.stage}"
    packet.orient.persona_proposal_refs.append(f"persona://{persona_id}")
    packet.orient.evidence_bundle_refs.extend(_evidence_bundle_refs(persona, seed, backtest))

    packet.decide.approval_decision_id = f"approval-persona-ooda15-{safe_persona_id}-{cycle_no:02d}"
    packet.decide.deployment_plan_id = f"deployment-plan-persona-ooda15-{safe_persona_id}-{cycle_no:02d}"
    packet.decide.evolution_decision_id = f"evolution-persona-ooda15-{safe_persona_id}-{cycle_no:02d}"
    packet.decide.sponsor_persona_id = persona_id
    packet.decide.decision_rationale_ref = (
        f"rationale://persona-ooda15/{persona_id}/{scenario.scenario_id}/"
        f"backtest-{backtest['request_id']}"
    )
    packet.decide.policy_decision_refs.extend(_policy_refs(persona, scenario))

    runtime_id = _runtime_id(persona, persona_context)
    packet.act.runtime_binding_id = runtime_id
    packet.act.command_receipt_refs.extend([
        f"command-receipt://persona-ooda15/{persona_id}/{scenario.scenario_id}/paper",
        f"oss-result://{backtest['component']}/{backtest['request_id']}",
    ])
    if scenario.incident_like:
        packet.act.rollback_refs.append(f"rollback://persona-ooda15/{persona_id}/{scenario.scenario_id}")
    if scenario.requires_human_gate or scenario.incident_like:
        packet.act.safe_mode_refs.append(f"safe-mode://persona-ooda15/{persona_id}/{scenario.scenario_id}")
    else:
        packet.act.safe_mode_refs.append("safe-mode://no-live-capital-side-effects")
    packet.act.live_capital_side_effects = False

    packet.learn.telemetry_refs.append(f"telemetry://persona-ooda15/{persona_id}/{scenario.scenario_id}/post-act")
    packet.learn.postmortem_refs.append(f"postmortem://persona-ooda15/{persona_id}/{scenario.scenario_id}")
    packet.learn.evolution_followthrough_refs.extend([
        f"evolution-program://persona-ooda15/{persona_id}",
        f"evolution-followthrough://persona-ooda15/{persona_id}/{cycle_no:02d}",
    ])
    packet.learn.trainer_refs.append(f"trainer://persona-ooda15/{persona_id}/{scenario.scenario_id}")
    packet.learn.retrain_refs.append(f"retrain://persona-ooda15/{persona_id}/{seed.key}")
    packet.learn.observation_window = ObservationWindow(
        start_at=_cycle_timestamp(base_time, persona_index, cycle_no, 5),
        end_at=_cycle_timestamp(base_time, persona_index, cycle_no, 6),
        duration_hours=1.0,
    )

    snapshots: list[dict[str, Any]] = [
        _enrich_packet_snapshot(
            packet.to_dict(),
            persona=persona,
            persona_context=persona_context,
            scenario=scenario,
            cycle_no=cycle_no,
            seed=seed,
            backtest=backtest,
            session=session,
        )
    ]
    transitions: list[dict[str, Any]] = []
    for offset, (stage, status) in enumerate(
        (
            ("observe", LoopStatus.OBSERVING),
            ("orient", LoopStatus.ORIENTED),
            ("decide", LoopStatus.DECIDED),
            ("act", LoopStatus.ACTED),
            ("learn", LoopStatus.EVOLVING),
            ("close", LoopStatus.CLOSED),
        ),
        start=1,
    ):
        _advance_packet(packet, stage=stage, status=status, at=_cycle_timestamp(base_time, persona_index, cycle_no, offset))
        transition = {
            "packet_id": packet.packet_id,
            "stage": stage,
            "event": stage,
            "from_status": transitions[-1]["to_status"] if transitions else LoopStatus.OPEN.value,
            "to_status": status.value,
            "actor_id": "persona-ooda-autonomy-runtime",
            "persona_id": persona_id,
            "transitioned_at": packet.updated_at,
            "autonomous": True,
        }
        transitions.append(transition)
        snapshots.append(
            _enrich_packet_snapshot(
                packet.to_dict(),
                persona=persona,
                persona_context=persona_context,
                scenario=scenario,
                cycle_no=cycle_no,
                seed=seed,
                backtest=backtest,
                session=session,
            )
        )

    validation_errors = packet.validate()
    if validation_errors:
        raise ValueError(f"invalid OODA packet {packet.packet_id}: {validation_errors}")
    final_packet = snapshots[-1]
    return final_packet, transitions, snapshots


def _advance_packet(packet: OodaLoopPacket, *, stage: str, status: LoopStatus, at: str) -> None:
    validate_status_transition(LoopStatus(packet.status).value, status.value)
    packet.status = status
    packet.updated_at = at
    if status == LoopStatus.CLOSED:
        packet.closed_at = at


def _enrich_packet_snapshot(
    packet: dict[str, Any],
    *,
    persona: Mapping[str, Any],
    persona_context: Mapping[str, Any],
    scenario: OodaScenario,
    cycle_no: int,
    seed: AlphaSeedSource,
    backtest: Mapping[str, Any],
    session: Mapping[str, Any],
) -> dict[str, Any]:
    persona_id = _persona_id(persona)
    metadata = _metadata(persona)
    enriched = json.loads(json.dumps(packet, ensure_ascii=False))
    enriched.update(
        {
            "stage": scenario.stage,
            "current_stage": scenario.stage,
            "cycle_no": cycle_no,
            "scenario_id": scenario.scenario_id,
            "scenario_title": scenario.title,
            "persona_id": persona_id,
            "runtime_id": _runtime_id(persona, persona_context),
            "evolution_program_id": f"persona-ooda15-{_safe_id(persona_id)}",
            "management_summary": {
                "persona_id": persona_id,
                "persona_name": persona.get("name"),
                "cycle_no": cycle_no,
                "scenario_id": scenario.scenario_id,
                "stage_focus": scenario.stage,
                "daily_work": scenario.daily_work,
                "current_work": metadata.get("current_work") or persona.get("mandate"),
                "action_taken": scenario.action_taken,
                "operator_interaction": scenario.operator_interaction,
                "requires_human_gate": scenario.requires_human_gate,
                "incident_like": scenario.incident_like,
                "autonomy_state": "autonomous_closed_loop",
                "visible_to_management": True,
                "allowed_actions": sorted(_allowed_actions(persona_context)),
            },
            "autonomous_next_work": {
                "next_cycle_hint": "continue_daily_work" if cycle_no == CYCLES_PER_PERSONA else "advance_next_ooda_scenario",
                "next_cycle_no": None if cycle_no == CYCLES_PER_PERSONA else cycle_no + 1,
                "evolution_ready": scenario.stage == "learn",
                "daily_work_queue_ref": f"daily-work://persona-ooda15/{persona_id}/{cycle_no:02d}",
            },
            "source_truth": {
                "persona_catalog_source": "ReadSurfaceStore.list_personas",
                "persona_record_hash": _stable_hash(persona),
                "alpha_seed_key": seed.key,
                "alpha_seed_source_ref": seed.evidence_path,
                "backtest_request_id": backtest["request_id"],
                "openclaw_request_id": session["request_id"],
            },
            "oss_results": {
                "session_ref": f"oss://openclaw/{session['request_id']}",
                "backtest_ref": f"oss://vectorbt/{backtest['request_id']}",
                "backtest_metrics": backtest["metrics"],
                "backtest_strategy_id": backtest["strategy_id"],
                "backtest_backend": backtest.get("backtest_backend"),
                "backtest_dataset_summary": backtest.get("dataset_summary", {}),
                "backtest_source_dataset_refs": backtest.get("source_dataset_refs", []),
                "backtest_persona_followup": backtest.get("persona_followup", {}),
            },
        }
    )
    return enriched


def _build_batch_summary(
    *,
    personas: Sequence[Mapping[str, Any]],
    packets: Sequence[Mapping[str, Any]],
    backtest_results: Sequence[Mapping[str, Any]],
    session_results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    persona_ids = [_persona_id(persona) for persona in personas]
    packets_by_persona = {
        persona_id: [packet for packet in packets if persona_id in (packet.get("persona_ids") or [])]
        for persona_id in persona_ids
    }
    stage_counts = {
        stage: len([packet for packet in packets if packet.get("stage") == stage])
        for stage in ("observe", "orient", "decide", "act", "learn")
    }
    scenario_counts = {
        scenario.scenario_id: len([packet for packet in packets if packet.get("scenario_id") == scenario.scenario_id])
        for scenario in OODA_SCENARIOS
    }
    return {
        "persona_count": len(persona_ids),
        "persona_ids": persona_ids,
        "cycles_per_persona": CYCLES_PER_PERSONA,
        "total_cycles": len(packets),
        "closed_cycles": len([packet for packet in packets if packet.get("status") == LoopStatus.CLOSED.value]),
        "stage_counts": stage_counts,
        "scenario_counts": scenario_counts,
        "loop_type_counts": {
            loop_type: len([packet for packet in packets if packet.get("loop_type") == loop_type])
            for loop_type in sorted({scenario.loop_type for scenario in OODA_SCENARIOS})
        },
        "per_persona_cycle_counts": {
            persona_id: len(persona_packets)
            for persona_id, persona_packets in packets_by_persona.items()
        },
        "per_persona_stage_coverage": {
            persona_id: sorted({str(packet.get("stage")) for packet in persona_packets})
            for persona_id, persona_packets in packets_by_persona.items()
        },
        "backtest_result_count": len(backtest_results),
        "backtest_request_ids": [result["request_id"] for result in backtest_results],
        "session_result_count": len(session_results),
        "management_visibility": {
            "ooda_packet_store": True,
            "control_room_status_card": True,
            "strategy_related_route": True,
            "runtime_related_route": True,
            "packet_detail": True,
        },
        "live_capital_side_effects": False,
    }


def _oss_result_summary(
    result: PersonaOSSResult,
    *,
    seed: AlphaSeedSource | None,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    registry_entry = result.registry_entry or {}
    artifact_bundle = result.artifact_bundle or {}
    dataset_summary = artifact_bundle.get("dataset_summary") if isinstance(artifact_bundle, Mapping) else {}
    metadata = artifact_bundle.get("metadata") if isinstance(artifact_bundle, Mapping) else {}
    strategy_id = str(
        registry_entry.get("strategy_id")
        or payload.get("strategy_id")
        or result.primary_output.get("strategy_id")
        or result.request_id
    )
    return {
        "component": result.component,
        "persona_id": result.persona_id,
        "session_id": result.session_id,
        "request_id": result.request_id,
        "artifact_family": result.artifact_family,
        "status": result.status,
        "strategy_id": strategy_id,
        "metrics": result.metrics,
        "refs": result.refs,
        "primary_output": result.primary_output,
        "dataset_summary": dict(dataset_summary) if isinstance(dataset_summary, Mapping) else {},
        "source_dataset_refs": list(result.refs.get("source_dataset_refs") or []),
        "backtest_backend": (
            str(metadata.get("backtest_backend"))
            if isinstance(metadata, Mapping) and metadata.get("backtest_backend")
            else str(result.primary_output.get("backend", ""))
        ),
        "persona_followup": result.persona_followup,
        "seed_key": seed.key if seed else None,
        "seed_evidence_path": seed.evidence_path if seed else None,
    }


def _persona_id(persona: Mapping[str, Any]) -> str:
    persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
    if not persona_id:
        raise ValueError("persona record is missing persona_id/id")
    return persona_id


def _metadata(persona: Mapping[str, Any]) -> dict[str, Any]:
    raw = persona.get("metadata")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _stable_hash(value: Mapping[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")


def _capital_pool_id(persona: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    metadata = _metadata(persona)
    if metadata.get("capital_pool_id"):
        return str(metadata["capital_pool_id"])
    for binding in context.get("bindings") or []:
        if isinstance(binding, Mapping) and binding.get("capital_pool_id"):
            return str(binding["capital_pool_id"])
    return f"pool-{_safe_id(_persona_id(persona))}-paper"


def _runtime_id(persona: Mapping[str, Any], context: Mapping[str, Any]) -> str:
    metadata = _metadata(persona)
    if metadata.get("runtime_binding_id"):
        return str(metadata["runtime_binding_id"])
    for session in context.get("sessions") or []:
        if isinstance(session, Mapping):
            runtime_id = session.get("runtime_binding_id") or session.get("runtime_id")
            if runtime_id:
                return str(runtime_id)
    for binding in context.get("runtime_bindings") or []:
        if isinstance(binding, Mapping):
            runtime_id = binding.get("runtime_id") or binding.get("runtime_binding_id") or binding.get("id")
            if runtime_id:
                return str(runtime_id)
    return f"runtime-{_safe_id(_persona_id(persona))}-paper"


def _strategy_id(
    persona: Mapping[str, Any],
    seed: AlphaSeedSource,
    backtest: Mapping[str, Any],
) -> str:
    metadata = _metadata(persona)
    research = metadata.get("research_status") if isinstance(metadata.get("research_status"), Mapping) else {}
    if research.get("strategy_id"):
        return str(research["strategy_id"])
    for project in metadata.get("current_research_projects") or []:
        if isinstance(project, Mapping) and project.get("strategy_id"):
            return str(project["strategy_id"])
    return str(backtest.get("strategy_id") or seed.strategy_id)


def _source_refs(persona: Mapping[str, Any], seed: AlphaSeedSource) -> list[str]:
    metadata = _metadata(persona)
    refs = [
        f"management-persona-catalog://{_persona_id(persona)}",
        f"repo://{seed.evidence_path}",
    ]
    refs.extend(str(ref) for ref in metadata.get("data_source_refs") or [] if str(ref).strip())
    refs.extend(str(ref) for ref in seed.source_dataset_refs)
    return _dedupe(refs)


def _market_refs(persona: Mapping[str, Any]) -> list[str]:
    metadata = _metadata(persona)
    refs: list[str] = []
    refs.extend(str(ref) for ref in metadata.get("data_source_refs") or [] if str(ref).strip())
    data_source_status = metadata.get("data_source_status")
    if isinstance(data_source_status, Mapping):
        refs.extend(str(ref) for ref in data_source_status.get("readback_refs") or [] if str(ref).strip())
        refs.extend(str(ref) for ref in data_source_status.get("unavailable_refs") or [] if str(ref).strip())
    return _dedupe(refs or [f"market-context://{_persona_id(persona)}/{persona.get('strategy_family') or 'general'}"])


def _telemetry_refs(persona: Mapping[str, Any], scenario: OodaScenario) -> list[str]:
    metadata = _metadata(persona)
    performance = metadata.get("performance") if isinstance(metadata.get("performance"), Mapping) else {}
    refs = [
        f"telemetry://persona/{_persona_id(persona)}/scenario/{scenario.scenario_id}",
        f"telemetry://persona/{_persona_id(persona)}/catalog-hash/{_stable_hash(persona)}",
    ]
    if performance:
        refs.append(f"telemetry://persona/{_persona_id(persona)}/performance/{_stable_hash(performance)}")
    return refs


def _evidence_bundle_refs(
    persona: Mapping[str, Any],
    seed: AlphaSeedSource,
    backtest: Mapping[str, Any],
) -> list[str]:
    metadata = _metadata(persona)
    refs = [seed.evidence_path, f"oss://vectorbt/{backtest['request_id']}"]
    for project in metadata.get("current_research_projects") or []:
        if not isinstance(project, Mapping):
            continue
        for ref in project.get("evidence_refs") or []:
            if isinstance(ref, Mapping):
                refs.append(str(ref.get("ref") or ref.get("route") or ref.get("ref_id") or ""))
            else:
                refs.append(str(ref))
    for ref in metadata.get("research_refs") or []:
        if isinstance(ref, Mapping):
            refs.append(str(ref.get("ref") or ref.get("route") or ref.get("ref_id") or ""))
        else:
            refs.append(str(ref))
    return _dedupe([ref for ref in refs if ref])


def _policy_refs(persona: Mapping[str, Any], scenario: OodaScenario) -> list[str]:
    metadata = _metadata(persona)
    refs = [
        "PAPER_CANARY_LIVE_POLICY.md",
        "KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md",
        f"persona-policy://{_persona_id(persona)}/{scenario.stage}",
    ]
    if metadata.get("governance_required") or scenario.requires_human_gate:
        refs.append("human-gate://required-before-capital-mutation")
    return refs


def _research_stage(persona: Mapping[str, Any]) -> str:
    metadata = _metadata(persona)
    research = metadata.get("research_status") if isinstance(metadata.get("research_status"), Mapping) else {}
    return _safe_id(str(research.get("stage") or metadata.get("ooda_stage") or "daily"))


def _market_scope_token(persona: Mapping[str, Any]) -> str:
    metadata = _metadata(persona)
    scope = metadata.get("market_scope") or metadata.get("asset_classes") or [persona.get("strategy_family") or "general"]
    if isinstance(scope, list):
        return _safe_id("-".join(str(item) for item in scope if str(item).strip()) or "general")
    return _safe_id(str(scope))


def _allowed_actions(context: Mapping[str, Any]) -> set[str]:
    actions = context.get("allowed_actions")
    if isinstance(actions, Mapping):
        return {str(key) for key, value in actions.items() if bool(value)}
    return set()


def _parse_utc(value: str) -> datetime:
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _cycle_timestamp(base_time: datetime, persona_index: int, cycle_no: int, offset: int) -> str:
    minute = persona_index * CYCLES_PER_PERSONA * 10 + cycle_no * 10 + offset
    return (base_time + timedelta(minutes=minute)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = str(value).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
