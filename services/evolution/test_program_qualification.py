"""Program qualification tests (EVOLUTION-PROGRAM-QUALIFY-R2-20261105).

Decision and drift shapes are the real hosted shapes quoted in the task:
the infra-component ``flag_for_review`` decision, the deployment-saga
``freeze`` decision, and one ``reconciliation_drift.drift_evaluations`` row
whose baseline-sourced drawdown check carries ``threshold_metric
rolling_drawdown_multiple``. The one-IncidentCase-per-binding-and-cluster fact
holds in every fixture: breach history comes from evaluations, never from
extra IncidentCases.
"""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from services.evolution import sweep as _sweep  # noqa: F401  (puts governance on sys.path)
from services.evolution.program_qualification import (
    CRITERIA,
    OwnerNotFoundError,
    OwnerReadError,
    PolicyError,
    ProgramQualifier,
    QualificationOwnerReader,
    QualificationReportStore,
    load_policy,
)
from services.evolution.program_service import ProgramService
from services.evolution.program_store import JsonProgramStore
from services.evolution.threshold_sweep_worker import (
    metric_is_stale,
    summary_field_for_metric,
    summary_is_stale_or_degraded,
)

from evolution_decision import (  # type: ignore
    ComparisonOperator,
    EvolutionActionType,
    EvolutionDecision,
    EvolutionDecisionStore,
    EvolutionTargetType,
    ThresholdSignalType,
    ThresholdSnapshot,
)

TENANT = "tenant-a"
NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
BINDING = "rtb-paper-1bad2eca"
RUNTIME = "rt-paper-1bad2eca"
TARGET = "artifact-persona-paper-1bad2eca6831457cd885"

ACTION_PATHS = [
    {"path_key": "research_retrain", "execution_plane": "research", "cooldown_days": 3},
    {"path_key": "research_revalidate", "execution_plane": "research", "cooldown_days": 3},
    {"path_key": "freeze_non_live", "execution_plane": "governance", "cooldown_days": 7},
]


def _iso(moment: datetime) -> str:
    return moment.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# -- Real hosted decision shapes ---------------------------------------------

def infra_flag_decision() -> EvolutionDecision:
    """Real shape 1 (decision evo-sweep-agent-000b205c0ef9-0000)."""
    return EvolutionDecision.create_proposed(
        decision_id="evo-sweep-agent-000b205c0ef9-0000",
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="infra-component-agent-finding-000b205c0ef9",
        target_version="v1",
        action_type=EvolutionActionType.FLAG_FOR_REVIEW,
        rationale="Daily evolution sweep derived a governed flag_for_review proposal.",
        created_by_id="evolution-daily-sweep",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                signal_type=ThresholdSignalType.MANUAL_REVIEW,
                metric_name="incident_followup_required",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="active-incident",
                breached=True,
            )
        ],
        tenant_id=TENANT,
    )


def saga_freeze_decision() -> EvolutionDecision:
    """Real shape 2 (deployment saga severity-1 freeze)."""
    return EvolutionDecision.create_proposed(
        decision_id="evo-sweep-inc-deployment-comp-deployment-saga-plan-persona-paper-1bad2eca6831457cd885",
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id="artifact-persona-paper-1bad2eca6831457cd885",
        target_version="v1",
        action_type=EvolutionActionType.FREEZE,
        target_stage="paper",
        rationale="Daily evolution sweep derived a governed freeze proposal.",
        created_by_id="evolution-daily-sweep",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.5",
                signal_type=ThresholdSignalType.GOVERNANCE_INCIDENT,
                metric_name="severity1_incident_count",
                comparator=ComparisonOperator.GTE,
                observed_value=1,
                threshold_value=1,
                window="active-incident",
                breached=True,
            )
        ],
        tenant_id=TENANT,
    )


def retrain_decision(
    *,
    decision_id: str = "evo-sweep-inc-drift-retrain-0001",
    action: EvolutionActionType = EvolutionActionType.RETRAIN,
    observed: float = 1.6,
    metric: str = "rolling_drawdown_multiple",
    persona_id: str | None = None,
) -> EvolutionDecision:
    return EvolutionDecision.create_proposed(
        decision_id=decision_id,
        target_type=EvolutionTargetType.CANDIDATE_ARTIFACT,
        target_id=TARGET,
        target_version="v1",
        action_type=action,
        rationale="Daily evolution sweep derived a governed retrain proposal.",
        created_by_id="evolution-daily-sweep",
        threshold_snapshots=[
            ThresholdSnapshot(
                policy_source="EVOLUTION_REVIEW_AND_THRESHOLDS.md section 7.1",
                signal_type=ThresholdSignalType.PERFORMANCE_DEGRADATION,
                metric_name=metric,
                comparator=ComparisonOperator.GT,
                observed_value=observed,
                threshold_value=1.25,
                window="paper-daily-sweep",
                breached=True,
            )
        ],
        persona_id=persona_id,
        target_stage="paper",
        metadata={"runtime_binding_id": BINDING, "runtime_id": RUNTIME},
        tenant_id=TENANT,
    )


# -- Real drift evaluation shape ---------------------------------------------

def evaluation(evaluated_at: datetime, *, event_id: str | None, breached: bool = True, n: int = 0) -> dict:
    check = {
        "metric": "drawdown",
        "status": "warning" if breached else "ok",
        "baseline": 0.0325,
        "observed": 0.052 if breached else 0.0,
        "one_sided": True,
        "comparator": "gt",
        "signal_type": "performance_degradation",
        "threshold_value": 1.25,
        "threshold_metric": "rolling_drawdown_multiple",
        "threshold_window": "paper-daily-sweep",
        "observed_multiple": 1.6 if breached else 0.0,
        "governed_threshold_breached": breached,
        "baseline_source": {"artifact_id": TARGET, "baseline_key": "expected_drawdown", "policy_source": "x"},
    }
    if event_id:
        check["drawdown_event_id"] = event_id
    return {
        "id": f"rdeval-{n}",
        "evaluation_id": f"rdeval-{n}",
        "tenant_id": TENANT,
        "binding_id": BINDING,
        "runtime_id": RUNTIME,
        "evaluated_at": _iso(evaluated_at),
        "status": "warning" if breached else "ok",
        "drift_checks": [check],
        "evidence_refs": [{"type": "telemetry_event", "id": event_id}] if event_id else [],
    }


def drawdown_event(event_id: str, mark_day: str) -> dict:
    """Real shape 4: mark as_of is the market date; created_at is wall clock later."""
    return {
        "event_id": event_id,
        "event_type": "drawdown_snapshot",
        "created_at": mark_day + "T15:07:03Z",
        "metadata": {
            "valuation_as_of": mark_day + "T00:00:00Z",
            "mark_refs": [{"symbol": "AAPL", "as_of": mark_day + "T00:00:00Z"}],
        },
    }


def fresh_summary() -> dict:
    stamp = _iso(NOW - timedelta(minutes=5))
    return {
        "runtime_id": RUNTIME,
        "binding_id": BINDING,
        "deployment_stage": "paper",
        "state": "active",
        "last_heartbeat_at": stamp,
        "drawdown": 0.052,
        "drawdown_at": stamp,
        "drawdown_binding_id": BINDING,
    }


class FakeOwners:
    """Owner reads with call accounting; no network."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.entry = {
            "entry": {"registry_id": TARGET, "artifact_type": "strategy_spec", "metadata": {}},
            "deployment_stage": "paper",
        }
        self.persona: dict | Exception = {"persona_id": "p-1", "data_scope": "regular"}
        self.summary: dict | Exception = fresh_summary()
        self.history: list[dict] | Exception = []
        self.events: dict[str, dict] = {}

    def get_registry_entry(self, registry_id, *, tenant_id, timeout):
        self.calls.append("registry")
        if isinstance(self.entry, Exception):
            raise self.entry
        return self.entry

    def get_persona(self, persona_id, *, tenant_id, timeout):
        self.calls.append("persona")
        if isinstance(self.persona, Exception):
            raise self.persona
        return self.persona

    def get_runtime_summary(self, runtime_id, *, tenant_id, timeout):
        self.calls.append("summary")
        if isinstance(self.summary, Exception):
            raise self.summary
        return self.summary

    def list_evaluations(self, binding_id, *, tenant_id, timeout, since, governed_breached_only):
        self.calls.append("evaluations_breached" if governed_breached_only else "evaluations_latest")
        if isinstance(self.history, Exception):
            raise self.history
        rows = [e for e in self.history if e["evaluated_at"] >= since]
        if governed_breached_only:
            rows = [e for e in rows if any(c.get("governed_threshold_breached") is True for c in e["drift_checks"])]
        return rows

    def get_telemetry_event(self, event_id, *, tenant_id, timeout):
        self.calls.append("event")
        if event_id not in self.events:
            raise OwnerNotFoundError("telemetry owner has no event")
        return self.events[event_id]

    def three_marks(self, extra_noise: bool = True) -> None:
        """Breached evaluations citing 3 distinct market mark dates."""
        days = ["2026-11-02", "2026-11-03", "2026-11-04"]
        self.history = []
        for index, day in enumerate(days):
            event_id = f"evt-dd-{day}"
            self.events[event_id] = drawdown_event(event_id, day)
            when = datetime.fromisoformat(day + "T16:00:00+00:00")
            # Several evaluations repeat the same mark inside a day.
            for repeat in range(3 if extra_noise else 1):
                self.history.append(evaluation(when + timedelta(minutes=5 * repeat), event_id=event_id, n=index * 10 + repeat))
        # Latest evaluation is within the last hour and still breached.
        self.history.append(evaluation(NOW - timedelta(minutes=5), event_id="evt-dd-2026-11-04", n=99))


@pytest.fixture
def rig(tmp_path):
    decisions = EvolutionDecisionStore()
    programs = ProgramService(JsonProgramStore(tmp_path / "programs.json"))
    owners = FakeOwners()
    reports = QualificationReportStore(tmp_path / "report.json")

    def make(policy_loader=None, clock=None):
        return ProgramQualifier(
            decision_store=decisions,
            program_service=programs,
            owner_reader=owners,
            report_store=reports,
            action_paths=ACTION_PATHS,
            summary_field_for_metric=summary_field_for_metric,
            summary_is_stale_or_degraded=summary_is_stale_or_degraded,
            metric_is_stale=metric_is_stale,
            policy_loader=policy_loader or load_policy,
            clock=clock or (lambda: NOW),
        )

    class Rig:
        pass

    r = Rig()
    r.decisions, r.programs, r.owners, r.reports, r.make = decisions, programs, owners, reports, make
    r.qualifier = make()
    return r


def inject(store, decision: EvolutionDecision) -> None:
    """Store a fixture decision without the single-active-per-target gate.

    Production sweeps keep one active decision per target; the qualifier still
    has to judge programs that came from an earlier decision for that target,
    so these fixtures place the earlier decision directly.
    """
    store._decisions[decision.decision_id] = decision


def row(report: dict, decision_id: str) -> dict:
    return next(item for item in report["decisions"] if item["decision_id"] == decision_id)


def criterion(report_row: dict, name: str) -> dict:
    return next(c for c in report_row["criteria"] if c["criterion"] == name)


# -- Real shapes do not qualify -------------------------------------------------

def test_infra_component_flag_for_review_shape_fails_real_threshold_signal(rig):
    decision = infra_flag_decision()
    rig.decisions.put(decision)
    report = rig.qualifier.run(tenant_id=TENANT)
    item = row(report, decision.decision_id)
    assert item["qualified"] is False
    assert item["first_failing_criterion"] == "real_threshold_signal"
    assert "manual_review" in criterion(item, "real_threshold_signal")["reason"]
    assert criterion(item, "executable_action")["result"] == "failed"
    for name in ("real_target", "persistent_breach", "fresh_evidence"):
        assert criterion(item, name)["result"] == "not_evaluated"
    assert rig.owners.calls == []
    assert report["counts_by_first_failing_criterion"] == {"real_threshold_signal": 1}


def test_deployment_saga_freeze_shape_fails_real_threshold_signal(rig):
    decision = saga_freeze_decision()
    rig.decisions.put(decision)
    report = rig.qualifier.run(tenant_id=TENANT)
    item = row(report, decision.decision_id)
    assert item["qualified"] is False
    assert item["first_failing_criterion"] == "real_threshold_signal"
    assert "governance_incident" in criterion(item, "real_threshold_signal")["reason"]
    assert rig.owners.calls == []


def test_hundreds_of_unrelated_proposals_make_no_owner_reads(rig):
    for index in range(300):
        decision = infra_flag_decision()
        decision = replace(
            decision,
            decision_id=f"evo-sweep-agent-{index:04d}",
            target_id=f"infra-component-agent-finding-{index:012d}",
        )
        rig.decisions.put(decision)
    report = rig.qualifier.run(tenant_id=TENANT)
    assert report["evaluated"] == 300 and report["qualified"] == 0 and report["drafted"] == 0
    assert rig.owners.calls == []


# -- Qualifying path ------------------------------------------------------------

def test_three_distinct_mark_dates_with_fresh_summary_qualifies_and_submits_review(rig):
    decision = retrain_decision()
    rig.decisions.put(decision)
    rig.owners.three_marks()
    report = rig.qualifier.run(tenant_id=TENANT, sweep_id="s1")
    item = row(report, decision.decision_id)
    assert item["qualified"] is True, item
    assert [c["criterion"] for c in item["criteria"]] == list(CRITERIA)
    assert all(c["result"] == "passed" for c in item["criteria"])
    assert criterion(item, "persistent_breach")["observed"]["distinct_breach_days"] == 3
    assert report["policy_version"] == load_policy().policy_version
    assert (report["evaluated"], report["qualified"], report["drafted"]) == (1, 1, 1)

    programs = rig.programs.list_programs(tenant_id=TENANT)
    assert len(programs) == 1
    program = programs[0]
    assert program["origin"] == "auto_qualified"
    assert program["created_by"] == "evolution-daily-sweep"
    assert program["source_decision_ids"] == [decision.decision_id]
    assert program["status"] == "under_review"  # submitted once, nothing further
    assert program["qualification"]["policy_version"] == report["policy_version"]
    assert program["qualification"]["evaluated_at"] == report["evaluated_at"]
    assert {c["criterion"] for c in program["qualification"]["criteria"]} == set(CRITERIA)
    assert [r["action_id"] for r in program["action_receipts"]] == ["submit_evolution_review"]


def test_repeated_sweeps_are_idempotent_on_decision(rig):
    decision = retrain_decision()
    rig.decisions.put(decision)
    rig.owners.three_marks()
    first = rig.qualifier.run(tenant_id=TENANT)
    second = rig.qualifier.run(tenant_id=TENANT)
    assert first["drafted"] == 1 and second["drafted"] == 0
    assert second["qualified"] == 1
    programs = rig.programs.list_programs(tenant_id=TENANT)
    assert len(programs) == 1 and programs[0]["status"] == "under_review"
    assert row(second, decision.decision_id)["program_id"] == programs[0]["program_id"]


def test_two_mark_dates_do_not_qualify(rig):
    rig.decisions.put(retrain_decision())
    rig.owners.three_marks()
    rig.owners.history = [e for e in rig.owners.history if "2026-11-02" not in e["evaluated_at"]]
    report = rig.qualifier.run(tenant_id=TENANT)
    item = row(report, "evo-sweep-inc-drift-retrain-0001")
    assert item["first_failing_criterion"] == "persistent_breach"
    assert criterion(item, "persistent_breach")["observed"]["distinct_breach_days"] == 2
    assert criterion(item, "fresh_evidence")["result"] == "passed"
    assert rig.programs.list_programs(tenant_id=TENANT) == []


def test_one_mark_evaluated_across_three_utc_days_does_not_qualify(rig):
    rig.decisions.put(retrain_decision())
    # One drawdown_snapshot (market date 2026-11-02, e.g. a weekend/holiday run)
    # is the cause of breached evaluations on three UTC days.
    rig.owners.events["evt-dd-weekend"] = drawdown_event("evt-dd-weekend", "2026-11-02")
    rig.owners.history = [
        evaluation(datetime(2026, 11, 2, 16, tzinfo=timezone.utc), event_id="evt-dd-weekend", n=1),
        evaluation(datetime(2026, 11, 3, 16, tzinfo=timezone.utc), event_id="evt-dd-weekend", n=2),
        evaluation(datetime(2026, 11, 4, 16, tzinfo=timezone.utc), event_id="evt-dd-weekend", n=3),
        evaluation(NOW - timedelta(minutes=5), event_id="evt-dd-weekend", n=4),
    ]
    report = rig.qualifier.run(tenant_id=TENANT)
    item = row(report, "evo-sweep-inc-drift-retrain-0001")
    assert item["first_failing_criterion"] == "persistent_breach"
    assert criterion(item, "persistent_breach")["observed"]["distinct_breach_days"] == 1


def test_evaluation_naming_no_causal_event_adds_no_day(rig):
    rig.decisions.put(retrain_decision())
    rig.owners.three_marks(extra_noise=False)
    assert row(rig.qualifier.run(tenant_id=TENANT), "evo-sweep-inc-drift-retrain-0001")["qualified"] is True
    # Every breached evaluation of 2026-11-04 (and the latest one) stops naming
    # the drawdown event: that market day no longer counts.
    for item in rig.owners.history:
        if "2026-11-04" in item["evaluated_at"] or item["evaluation_id"] == "rdeval-99":
            del item["drift_checks"][0]["drawdown_event_id"]
    report = rig.make().run(tenant_id=TENANT)
    found = row(report, "evo-sweep-inc-drift-retrain-0001")
    assert found["first_failing_criterion"] == "persistent_breach"
    assert criterion(found, "persistent_breach")["observed"]["distinct_breach_days"] == 2


def test_mark_event_unreadable_fails_closed(rig):
    rig.decisions.put(retrain_decision())
    rig.owners.three_marks()
    rig.owners.events.clear()
    item = row(rig.qualifier.run(tenant_id=TENANT), "evo-sweep-inc-drift-retrain-0001")
    assert criterion(item, "persistent_breach")["result"] == "failed"
    assert "evidence_unreadable" in criterion(item, "persistent_breach")["reason"]


def test_current_breach_qualifies_even_when_incident_first_event_is_old(rig):
    # The IncidentCase keeps its first telemetry event; its age says nothing
    # about current evidence. Only current runtime state and the latest
    # evaluation decide freshness, and the old event is never read.
    from approval_decision import EvidenceRef, EvidenceRefType  # type: ignore

    old_event = "tel-first-event-ten-days-old"
    decision = replace(
        retrain_decision(),
        evidence_refs=[
            EvidenceRef(
                ref_type=EvidenceRefType.TELEMETRY_SUMMARY,
                ref_id=old_event,
                storage_ref={"backend": "telemetry", "object_type": "TelemetryEvent"},
                note="first event of the single IncidentCase for this binding and cluster",
            )
        ],
    )
    rig.decisions.put(decision)
    rig.owners.three_marks()
    report = rig.qualifier.run(tenant_id=TENANT)
    assert row(report, decision.decision_id)["qualified"] is True
    assert old_event not in rig.owners.events


# -- One passing and one failing fixture per criterion --------------------------

def test_signal_criterion_pass_and_fail_variants(rig):
    for decision_id, kwargs in {
        "d-not-breaching": {"observed": 1.1},
        "d-denied-metric": {"metric": "severity1_incident_count"},
        "d-not-allowlisted": {"metric": "sharpe_pct_of_baseline"},
    }.items():
        inject(rig.decisions, retrain_decision(decision_id=decision_id, **kwargs))
    report = rig.qualifier.run(tenant_id=TENANT)
    for decision_id in ("d-not-breaching", "d-denied-metric", "d-not-allowlisted"):
        assert row(report, decision_id)["first_failing_criterion"] == "real_threshold_signal"
    assert "does not breach" in criterion(row(report, "d-not-breaching"), "real_threshold_signal")["reason"]
    assert rig.owners.calls == []

    rig.decisions._decisions.clear()
    rig.decisions.put(retrain_decision(decision_id="d-ok"))
    rig.owners.three_marks()
    assert criterion(row(rig.qualifier.run(tenant_id=TENANT), "d-ok"), "real_threshold_signal")["result"] == "passed"


def test_signal_non_numeric_value_fails(rig):
    decision = retrain_decision(observed="high")  # type: ignore[arg-type]
    rig.decisions.put(decision)
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "numeric" in criterion(item, "real_threshold_signal")["reason"]


def test_target_criterion_variants(rig):
    decision = retrain_decision()
    rig.decisions.put(decision)
    rig.owners.three_marks()

    ok = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert criterion(ok, "real_target")["result"] == "passed"

    rig.owners.entry = OwnerNotFoundError("registry owner has no entry")
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert criterion(item, "real_target")["result"] == "failed"
    assert "target_unverifiable" in criterion(item, "real_target")["reason"]
    assert criterion(item, "persistent_breach")["result"] == "not_evaluated"

    rig.owners.entry = {"entry": {"registry_id": TARGET, "artifact_type": "strategy_spec"}, "deployment_stage": "live"}
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "deployment_stage live" in criterion(item, "real_target")["reason"]

    rig.owners.entry = {"entry": {"registry_id": TARGET, "artifact_type": "evaluation_result"}, "deployment_stage": "paper"}
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "artifact_type evaluation_result" in criterion(item, "real_target")["reason"]


def test_target_infra_component_is_refused(rig):
    decision = replace(retrain_decision(), target_id="infra-component-agent-finding-000b205c0ef9")
    rig.decisions.put(decision)
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert item["first_failing_criterion"] == "real_target"
    assert "infra-component" in criterion(item, "real_target")["reason"]
    assert rig.owners.calls == []  # refused before any registry read


def test_test_scope_persona_is_refused_unless_policy_allows(rig, tmp_path):
    decision = retrain_decision(persona_id="persona-paper-1bad2eca")
    rig.decisions.put(decision)
    rig.owners.three_marks()
    rig.owners.persona = {"persona_id": "persona-paper-1bad2eca", "data_scope": "test"}
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert item["first_failing_criterion"] == "real_target"
    assert "data_scope test" in criterion(item, "real_target")["reason"]

    policy = json.loads(Path(load_policy.__globals__["DEFAULT_POLICY_PATH"]).read_text())
    policy["allow_test_scope"] = True
    path = tmp_path / "allow_test.json"
    path.write_text(json.dumps(policy))
    allowed = rig.make(policy_loader=lambda: load_policy(str(path)))
    assert row(allowed.run(tenant_id=TENANT), decision.decision_id)["qualified"] is True


def test_persona_unreadable_is_unverifiable(rig):
    decision = retrain_decision(persona_id="persona-paper-1bad2eca")
    rig.decisions.put(decision)
    rig.owners.persona = OwnerReadError("persona owner read returned HTTP 503")
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "target_unverifiable" in criterion(item, "real_target")["reason"]


def test_fresh_evidence_variants(rig):
    decision = retrain_decision()
    rig.decisions.put(decision)
    rig.owners.three_marks()
    assert row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)["qualified"] is True
    good_summary = dict(rig.owners.summary)

    rig.owners.summary = {**good_summary, "state": "degraded"}
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert item["first_failing_criterion"] == "fresh_evidence"
    assert "stale or degraded" in criterion(item, "fresh_evidence")["reason"]

    rig.owners.summary = {**good_summary, "drawdown_at": _iso(NOW - timedelta(days=5))}
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "no fresh as-of time" in criterion(item, "fresh_evidence")["reason"]

    rig.owners.summary = OwnerReadError("telemetry owner read returned HTTP 503")
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "evidence_unreadable" in criterion(item, "fresh_evidence")["reason"]

    rig.owners.summary = good_summary
    rig.owners.history[-1] = evaluation(NOW - timedelta(minutes=5), event_id=None, breached=False, n=99)
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "no longer reports a governed breach" in criterion(item, "fresh_evidence")["reason"]

    rig.owners.history = [e for e in rig.owners.history if e["evaluation_id"] != "rdeval-99"]
    item = row(rig.qualifier.run(tenant_id=TENANT), decision.decision_id)
    assert "no drift evaluation inside the latest-evaluation window" in criterion(item, "fresh_evidence")["reason"]


def test_executable_action_freeze_never_creates_a_program(rig):
    freeze_with_drawdown = retrain_decision(decision_id="d-freeze", action=EvolutionActionType.FREEZE)
    rig.decisions.put(freeze_with_drawdown)
    rig.owners.three_marks()
    item = row(rig.qualifier.run(tenant_id=TENANT), "d-freeze")
    assert criterion(item, "real_threshold_signal")["result"] == "passed"
    assert criterion(item, "executable_action")["result"] == "failed"
    assert item["first_failing_criterion"] == "executable_action"
    assert criterion(item, "real_target")["result"] == "not_evaluated"
    assert rig.owners.calls == []
    assert rig.programs.list_programs(tenant_id=TENANT) == []

    rig.decisions._decisions.clear()
    rig.decisions.put(retrain_decision(decision_id="d-revalidate", action=EvolutionActionType.REVALIDATE))
    assert criterion(row(rig.qualifier.run(tenant_id=TENANT), "d-revalidate"), "executable_action")["result"] == "passed"


def test_no_open_duplicate_variants(rig):
    rig.owners.three_marks()
    older = retrain_decision(decision_id="d-older")
    inject(rig.decisions, older)
    # A draft program from an earlier decision for the same target and metric.
    program, _ = rig.programs.create_auto_qualified_program(
        tenant_id=TENANT, actor_id="evolution-daily-sweep", name="existing",
        decision_id="d-older", qualification={"policy_version": "v"},
    )
    inject(rig.decisions, retrain_decision(decision_id="d-newer"))
    item = row(rig.qualifier.run(tenant_id=TENANT), "d-newer")
    assert item["first_failing_criterion"] == "no_open_duplicate"
    assert program["program_id"] in criterion(item, "no_open_duplicate")["reason"]
    assert criterion(item, "real_target")["result"] == "not_evaluated"


def test_stopped_program_inside_cooldown_blocks_and_after_cooldown_does_not(rig):
    rig.owners.three_marks()
    inject(rig.decisions, retrain_decision(decision_id="d-older"))
    program, _ = rig.programs.create_auto_qualified_program(
        tenant_id=TENANT, actor_id="evolution-daily-sweep", name="existing",
        decision_id="d-older", qualification={"policy_version": "v"},
    )
    store = rig.programs._store
    store._refresh()
    record = store._programs[program["program_id"]]
    record["status"] = "stopped"
    record["updated_at"] = _iso(NOW - timedelta(days=1))
    store._save()
    inject(rig.decisions, retrain_decision(decision_id="d-newer"))
    blocked = row(rig.qualifier.run(tenant_id=TENANT), "d-newer")
    assert "cooldown" in criterion(blocked, "no_open_duplicate")["reason"]

    store._refresh()
    store._programs[program["program_id"]]["updated_at"] = _iso(NOW - timedelta(days=4))
    store._save()
    assert criterion(row(rig.qualifier.run(tenant_id=TENANT), "d-newer"), "no_open_duplicate")["result"] == "passed"


def test_decision_that_is_no_longer_proposed_is_not_evaluated(rig):
    decision = retrain_decision()
    reviewed = replace(decision, decision_state=decision.decision_state.__class__("reviewed"))
    inject(rig.decisions, reviewed)
    report = rig.qualifier.run(tenant_id=TENANT)
    assert report["evaluated"] == 0


# -- Policy and report ----------------------------------------------------------

@pytest.mark.parametrize("content", [None, "not json", "[]", json.dumps({"policy_version": ""})])
def test_missing_or_invalid_policy_fails_closed_with_recorded_reason(rig, tmp_path, content):
    path = tmp_path / "policy.json"
    if content is not None:
        path.write_text(content)
    rig.decisions.put(retrain_decision())
    rig.owners.three_marks()
    qualifier = rig.make(policy_loader=lambda: load_policy(str(path)))
    report = qualifier.run(tenant_id=TENANT)
    assert report["drafted"] == 0 and report["evaluated"] == 0
    assert report["failure_reason"].startswith("policy_invalid")
    assert rig.programs.list_programs(tenant_id=TENANT) == []
    assert rig.owners.calls == []
    assert qualifier.report(TENANT)["failure_reason"] == report["failure_reason"]


def test_default_policy_loads_with_documented_defaults():
    policy = load_policy()
    assert policy.min_distinct_breach_days == 3 and policy.lookback_days == 30
    assert policy.allow_test_scope is False
    assert "rolling_drawdown_multiple" in policy.metric_allowlist
    assert {"manual_review", "governance_incident"}.isdisjoint(policy.allowed_signal_types)


def test_report_survives_restart(rig, tmp_path):
    rig.decisions.put(infra_flag_decision())
    produced = rig.qualifier.run(tenant_id=TENANT)
    reloaded = QualificationReportStore(tmp_path / "report.json").load(TENANT)
    assert reloaded == produced
    assert QualificationReportStore(tmp_path / "report.json").load("tenant-b") is None


def test_one_decision_error_does_not_stop_the_rest(rig):
    rig.decisions.put(infra_flag_decision())
    rig.decisions.put(retrain_decision())
    rig.owners.three_marks()

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected")

    rig.owners.get_registry_entry = boom
    report = rig.qualifier.run(tenant_id=TENANT)
    assert report["evaluated"] == 2
    assert "qualifier_error" in criterion(row(report, "evo-sweep-inc-drift-retrain-0001"), "real_threshold_signal")["reason"]


# -- Program model -------------------------------------------------------------

def test_program_loads_records_written_before_origin_existed():
    from services.evolution.models import EvolutionProgram

    legacy = {
        "program_id": "evp-1", "tenant_id": TENANT, "created_by": "a", "name": "n",
        "status": "draft", "revision": 1, "created_at": "t", "updated_at": "t",
    }
    program = EvolutionProgram.from_dict(legacy)
    assert program.origin == "manual" and program.source_decision_ids == [] and program.qualification is None
    assert program.to_dict()["origin"] == "manual"


def test_create_program_api_cannot_set_origin_or_qualification(tmp_path):
    service = ProgramService(JsonProgramStore(tmp_path / "p.json"))
    created, _ = service.create_program(tenant_id=TENANT, actor_id="human", name="By hand")
    assert created["origin"] == "manual" and created["qualification"] is None
    import inspect

    assert set(inspect.signature(service.create_program).parameters) == {
        "tenant_id", "actor_id", "name", "idempotency_key", "legacy_params",
    }
    from pydantic import ValidationError
    from services.evolution.program_router import ProgramCreateRequest

    for forbidden in ("origin", "source_decision_ids", "qualification"):
        with pytest.raises(ValidationError):
            ProgramCreateRequest(name="n", actor_id="a", **{forbidden: "auto_qualified"})


# -- Sweep hook, report endpoint, status ---------------------------------------

@pytest.fixture
def evo_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from services.evolution import main as evo_main

    def wipe_decisions():
        evo_main.store._decisions.clear()
        if evo_main.store._storage_path and evo_main.store._storage_path.exists():
            evo_main.store._storage_path.unlink()

    saved = dict(evo_main._sweep_state)
    wipe_decisions()
    reports = QualificationReportStore(tmp_path / "http-report.json")
    qualifier = ProgramQualifier(
        decision_store=evo_main.store,
        program_service=evo_main.program_service,
        owner_reader=FakeOwners(),
        report_store=reports,
        action_paths=evo_main._ACTION_PATHS,
        summary_field_for_metric=summary_field_for_metric,
        summary_is_stale_or_degraded=summary_is_stale_or_degraded,
        metric_is_stale=metric_is_stale,
    )
    monkeypatch.setattr(evo_main, "program_qualifier", qualifier)
    yield TestClient(evo_main.app), evo_main, qualifier
    wipe_decisions()
    evo_main._sweep_state.clear()
    evo_main._sweep_state.update(saved)


def test_sweep_runs_the_qualifier_and_report_endpoint_pages_every_decision(evo_client):
    client, evo_main, _ = evo_client
    tenant = evo_main._current_tenant()
    for index in range(3):
        decision = replace(
            infra_flag_decision(),
            decision_id=f"evo-sweep-agent-{index:04d}",
            target_id=f"infra-component-agent-finding-{index:012d}",
            tenant_id=tenant,
        )
        evo_main.store.put(decision)
    response = client.post("/api/evolution/daily-sweep", json={"sweep_id": "qualify-http"})
    assert response.status_code == 200, response.text
    report = client.get("/api/evolution/program-qualification", params={"limit": 2}).json()
    assert report["policy_version"] == load_policy().policy_version
    assert (report["evaluated"], report["qualified"], report["drafted"]) == (3, 0, 0)
    assert report["counts_by_first_failing_criterion"] == {"real_threshold_signal": 3}
    assert report["total"] == 3 and len(report["items"]) == 2
    assert {c["criterion"] for c in report["items"][0]["criteria"]} == set(CRITERIA)
    assert client.get("/api/evolution/program-qualification", params={"qualified": "true"}).json()["total"] == 0
    status = client.get("/api/evolution/sweep-status").json()
    assert status["qualifier_last_success_at"] and status["qualifier_last_failure_reason"] is None


def test_qualifier_failure_never_fails_the_sweep_and_is_exposed_in_status(evo_client, monkeypatch):
    client, evo_main, qualifier = evo_client

    def boom(**kwargs):
        raise RuntimeError("owner exploded")

    monkeypatch.setattr(qualifier, "run", boom)
    response = client.post("/api/evolution/daily-sweep", json={"sweep_id": "qualify-fail"})
    assert response.status_code == 200
    assert response.json()["sweep_id"] == "qualify-fail"
    status = client.get("/api/evolution/sweep-status").json()
    assert status["qualifier_last_failure_at"]
    assert "owner exploded" in status["qualifier_last_failure_reason"]
    assert status["last_success_at"]  # the sweep itself still counts as a success


def test_invalid_policy_is_recorded_as_a_sweep_status_failure(evo_client, tmp_path, monkeypatch):
    client, evo_main, qualifier = evo_client
    bad = tmp_path / "bad-policy.json"
    bad.write_text("{}")
    monkeypatch.setattr(qualifier, "_policy_loader", lambda: load_policy(str(bad)))
    assert client.post("/api/evolution/daily-sweep", json={"sweep_id": "bad-policy"}).status_code == 200
    status = client.get("/api/evolution/sweep-status").json()
    assert status["qualifier_last_failure_reason"].startswith("policy_invalid")
    report = client.get("/api/evolution/program-qualification").json()
    assert report["drafted"] == 0 and report["failure_reason"].startswith("policy_invalid")


# -- Owner reader against the real drift service, auth middleware active --------

def _drift_app(tmp_path, monkeypatch):
    import importlib.util
    import sys

    service_dir = Path(__file__).resolve().parents[1] / "reconciliation-drift"
    monkeypatch.setenv("RECONCILIATION_DRIFT_DATA_DIR", str(tmp_path / "drift"))
    monkeypatch.setenv("RECONCILIATION_DRIFT_AUTH_MODE", "token")
    monkeypatch.setenv("RECONCILIATION_DRIFT_AUTH_TOKEN", "drift-token-for-test")
    monkeypatch.setenv("PANTHEON_TELEMETRY_API_URL", "http://telemetry:8083")
    monkeypatch.setenv("PANTHEON_LINEAGE_READ_URL", "http://lineage-read:8094")
    monkeypatch.setenv("PANTHEON_RUNTIME_MANAGER_URL", "http://runtime-manager:8081")
    sys.modules.pop("store", None)
    sys.path.insert(0, str(service_dir))
    try:
        spec = importlib.util.spec_from_file_location("drift_main_for_qualification_test", service_dir / "main.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["drift_main_for_qualification_test"] = module
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("store", None)
        sys.path.remove(str(service_dir))
    return module


def test_owner_reader_reads_filtered_history_from_real_drift_service_with_auth(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    drift = _drift_app(tmp_path, monkeypatch)
    for item in (
        evaluation(datetime(2026, 10, 1, 16, tzinfo=timezone.utc), event_id="evt-old", n=1),
        evaluation(datetime(2026, 11, 3, 16, tzinfo=timezone.utc), event_id="evt-a", n=2),
        evaluation(datetime(2026, 11, 4, 16, tzinfo=timezone.utc), event_id="evt-b", breached=False, n=3),
    ):
        drift.store.put_evaluation(item)
    other = evaluation(datetime(2026, 11, 3, 17, tzinfo=timezone.utc), event_id="evt-c", n=4)
    other["tenant_id"] = "tenant-b"
    drift.store.put_evaluation(other)

    http = TestClient(drift.app)
    reader = QualificationOwnerReader(drift_url="http://drift", drift_token="drift-token-for-test", http=http)
    breached = reader.list_evaluations(
        BINDING, tenant_id=TENANT, timeout=5, since="2026-11-01T00:00:00Z", governed_breached_only=True
    )
    assert [e["evaluation_id"] for e in breached] == ["rdeval-2"]  # window + breach + tenant scoping
    assert breached[0]["drift_checks"][0]["drawdown_event_id"] == "evt-a"
    latest = reader.list_evaluations(
        BINDING, tenant_id=TENANT, timeout=5, since="2026-11-01T00:00:00Z", governed_breached_only=False
    )
    assert {e["evaluation_id"] for e in latest} == {"rdeval-2", "rdeval-3"}

    wrong = QualificationOwnerReader(drift_url="http://drift", drift_token="wrong", http=http)
    with pytest.raises(OwnerReadError, match="HTTP 401"):
        wrong.list_evaluations(BINDING, tenant_id=TENANT, timeout=5, since="2026-11-01T00:00:00Z", governed_breached_only=True)


def test_owner_reader_fails_closed_when_an_owner_is_not_configured():
    reader = QualificationOwnerReader()
    with pytest.raises(OwnerReadError, match="not configured"):
        reader.get_registry_entry("x", tenant_id=TENANT, timeout=1)
    with pytest.raises(OwnerReadError, match="PANTHEON_TELEMETRY_SERVICE_TOKEN"):
        reader.get_runtime_summary("rt", tenant_id=TENANT, timeout=1)
