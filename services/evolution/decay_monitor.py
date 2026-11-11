"""Paper-decay monitor: real telemetry -> governed discovery retire proposal (design §12.2, acceptance 5).

Each scheduler tick it reads the telemetry owner's runtime summaries for the
tenant's paper discovery bindings (Capital ``pcb-discovery-*`` bindings), appends
every new paper observation (one per distinct ``sharpe_ratio_at``) to a durable
window ledger, and evaluates persistent decay against the governed baselines
(``expected_sharpe_ratio`` / ``expected_drawdown`` in the approved baseline
config; no approved baseline = no decision, never a guess).  A decayed strategy
gets a deterministic, discovery-scoped retire proposal on the evolution API; the
AutoConsumer then reviews/approves it and retires the persona through the
governed lifecycle.  Re-running is idempotent (deterministic decision id, window
dedupe) and a failed proposal is retried on the next tick.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.evolution.auto_consumer import (
    DecayPolicy,
    EvidenceSink,
    build_retire_proposal,
    evaluate_paper_decay,
)

MAX_WINDOWS = 12


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


class WindowLedger:
    """Durable per-(tenant, persona, artifact) chronological paper windows (JSONL)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def append_if_new(self, row: Mapping[str, Any]) -> bool:
        key = (row["tenant_id"], row["persona_id"], row["artifact_id"], row["as_of"])
        if any((r["tenant_id"], r["persona_id"], r["artifact_id"], r["as_of"]) == key for r in self._rows()):
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(row), sort_keys=True) + "\n")
        return True

    def windows(self, tenant_id: str, persona_id: str, artifact_id: str) -> list[dict[str, Any]]:
        rows = [r for r in self._rows()
                if (r["tenant_id"], r["persona_id"], r["artifact_id"]) == (tenant_id, persona_id, artifact_id)]
        rows.sort(key=lambda r: r["as_of"])
        return rows[-MAX_WINDOWS:]


@dataclass
class DecayReport:
    observed: int = 0
    proposed: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


class DecayMonitor:
    def __init__(
        self,
        *,
        tenant_id: str,
        scope: Any,  # DiscoveryBindingScope: discovery_bindings(tenant) + resolve(decision)
        fetch_summaries: Callable[[], Sequence[Mapping[str, Any]]],
        baseline: Callable[[str, str], float | None],  # (artifact_id, key) -> approved positive value
        ledger: WindowLedger,
        propose: Callable[[Mapping[str, Any]], Any],
        evidence: EvidenceSink,
        policy: DecayPolicy = DecayPolicy(),
    ) -> None:
        self.tenant_id = tenant_id
        self.scope = scope
        self.fetch_summaries = fetch_summaries
        self.baseline = baseline
        self.ledger = ledger
        self.propose = propose
        self.evidence = evidence
        self.policy = policy

    def tick(self) -> DecayReport:
        report = DecayReport()
        bindings = {str(b.get("binding_id")): b for b in self.scope.discovery_bindings(self.tenant_id)}
        summaries = list(self.fetch_summaries())
        for summary in summaries:
            binding = bindings.get(str(summary.get("persona_capital_binding_id") or ""))
            if binding is None or str(summary.get("deployment_stage") or "").lower() != "paper":
                continue
            artifact_id = str(summary.get("artifact_id") or "")
            persona_id = str(binding.get("persona_id") or "")
            key = f"{persona_id}:{artifact_id}"
            sharpe, drawdown = _finite(summary.get("sharpe_ratio")), _finite(summary.get("drawdown"))
            as_of = str(summary.get("sharpe_ratio_at") or "")
            observed_binding = summary.get("sharpe_ratio_binding_id")
            if not artifact_id or sharpe is None or drawdown is None or not as_of or (
                observed_binding not in (None, "", summary.get("binding_id"), summary.get("runtime_binding_id"))
            ):
                report.skipped[key] = "no_current_paper_observation"
                continue
            try:
                if self.ledger.append_if_new({
                    "tenant_id": self.tenant_id, "persona_id": persona_id, "artifact_id": artifact_id, "as_of": as_of,
                    "sharpe_ratio": sharpe, "max_drawdown": abs(drawdown),
                    "binding_id": binding.get("binding_id"),
                }):
                    report.observed += 1
                self._evaluate(summary, persona_id, artifact_id, key, report)
            except Exception as exc:  # one persona never stops the others; retried next tick
                report.errors[key] = f"{type(exc).__name__}: {exc}"
        return report

    def _evaluate(self, summary: Mapping[str, Any], persona_id: str, artifact_id: str, key: str, report: DecayReport) -> None:
        base_sharpe = self.baseline(artifact_id, "expected_sharpe_ratio")
        base_dd = self.baseline(artifact_id, "expected_drawdown")
        if base_sharpe is None or base_dd is None:
            report.skipped[key] = "no_approved_baseline"
            return
        windows = self.ledger.windows(self.tenant_id, persona_id, artifact_id)
        verdict = evaluate_paper_decay(
            baseline_sharpe=base_sharpe, baseline_max_drawdown=base_dd, paper_windows=windows, policy=self.policy
        )
        if not verdict.decayed:
            report.skipped[key] = "not_decayed:" + ",".join(verdict.reasons)
            return
        latest = windows[-1]["as_of"]
        decision_id = "evd-decay-" + hashlib.sha256(
            json.dumps([self.tenant_id, persona_id, artifact_id], sort_keys=True).encode()
        ).hexdigest()[:20]
        proposal = build_retire_proposal(
            decision_id=decision_id, tenant_id=self.tenant_id, persona_id=persona_id, artifact_id=artifact_id,
            artifact_version=str(summary.get("artifact_version") or "unknown"), verdict=verdict,
        )
        # Only a target the owners resolve as this tenant's paper discovery artifact is proposed at all.
        if self.scope.resolve(proposal) is None:
            report.skipped[key] = "not_a_discovery_persona_artifact"
            return
        self.evidence.record({"event": "paper_decay_detected", "decision_id": decision_id, "persona_id": persona_id,
                              "artifact_id": artifact_id, "tenant_id": self.tenant_id, "latest_window": latest,
                              "observed": verdict.observed, "reasons": verdict.reasons})
        try:
            self.propose(proposal)
        except RuntimeError as exc:
            if " -> 409" not in str(exc):
                raise
            report.existing.append(decision_id)  # already proposed on an earlier tick: idempotent
            return
        self.evidence.record({"event": "retire_proposed", "decision_id": decision_id, "persona_id": persona_id,
                              "artifact_id": artifact_id, "tenant_id": self.tenant_id})
        report.proposed.append(decision_id)
