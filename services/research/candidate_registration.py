"""Register gate-passed demand template artifacts as approved paper pool candidates.

Bridges a passing ``TemplateDemandSearch`` attempt to ``HttpPoolCyclePorts.candidates``
through the existing Research backtest-owner binding, Registry ``execution_bundle``
registration, Governance paper approval, and Alpha Replication admission/queue stores.
Failing attempts never enter this path and register nothing.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from services.evolution.approved_baselines import resolve_member_baseline
from services.registry.promotion.gate import real_backtest_verdict_digest
from services.registry.strategy_artifact import (
     strategy_artifact_checksum,
    validate_strategy_artifact,
)
from services.research.alpha_replication.admission import ReplicationAdmissionStore
from services.research.alpha_replication.queue import AlphaReplicationQueue
from services.research.alpha_replication.shared_stores import SharedBacktestRunStore


class CandidateRegistrationError(RuntimeError):
    """Raised when a passing template candidate cannot be registered or approved."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _backtest_owner_path(data_dir: Path) -> Path:
    return Path(data_dir) / "backtest_run_owners.json"


def _update_backtest_owner_file(data_dir: Path, mutate: Callable[[dict[str, Any]], Any]) -> None:
    path = _backtest_owner_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(loaded, dict):
                    raise ValueError(f"{path.name} is not a JSON object")
            else:
                loaded = {}
            if mutate(loaded) is False:
                return
            fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(json.dumps(loaded, indent=2, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp_name, path)
            except BaseException:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def bind_backtest_run_owner(data_dir: Path, run_id: str, *, tenant_id: str, decision_id: str) -> None:
    """Bind a completed backtest run to its tenant and demand decision id."""
    def mutate(loaded: dict[str, Any]) -> None:
        existing = loaded.get(run_id)
        entry = dict(existing) if isinstance(existing, dict) else {}
        entry["tenant_id"] = tenant_id
        entry["decision_id"] = decision_id
        loaded[run_id] = entry

    _update_backtest_owner_file(Path(data_dir), mutate)


def bind_backtest_run_gate_verdict(data_dir: Path, run_id: str, gate_verdict: Mapping[str, Any]) -> None:
    """Persist the gate verdict digest alongside the backtest run owner record."""
    def mutate(loaded: dict[str, Any]) -> bool | None:
        owner = loaded.get(run_id)
        if not isinstance(owner, dict):
            return False
        owner["gate_verdict"] = dict(gate_verdict)
        return None

    _update_backtest_owner_file(Path(data_dir), mutate)


class TemplateProofCapturingGate:
    """Wraps a replication gate to retain the backtest evaluation for passing attempts only."""

    def __init__(self, gate: Any) -> None:
        self._gate = gate
        self.proofs: dict[str, dict[str, Any]] = {}
        self.evaluations: dict[str, dict[str, Any]] = {}
        self.last_evaluation: dict[str, Any] | None = None

    @property
    def backtest_run_store(self) -> Any:
        return getattr(self._gate, "backtest_run_store", None)

    def release(self, run_id: Any) -> None:
        self.proofs.pop(str(run_id or ""), None)
        self.evaluations.pop(str(run_id or ""), None)

    def get_family_trial_count(self, family_id: str) -> int:
        return self._gate.get_family_trial_count(family_id)

    def get_family_total_trial_count(self, family_id: str) -> int:
        total = getattr(self._gate, "get_family_total_trial_count", None)
        return total(family_id) if callable(total) else self._gate.get_family_trial_count(family_id)

    def record_family_trial(self, family_id: str) -> int:
        record = getattr(self._gate, "record_family_trial", None)
        return record(family_id) if callable(record) else self._gate.get_family_trial_count(family_id)

    def evaluate_candidate(self, request: Any) -> Any:
        self.last_evaluation = None
        response = self._gate.evaluate_candidate(request)
        evaluation = getattr(request, "backtest_evaluation", None)
        if not isinstance(evaluation, Mapping):
            handoff = getattr(request, "research_handoff", None)
            if isinstance(handoff, Mapping):
                evaluation = handoff.get("backtest_evaluation")
        for result in getattr(response, "results", []) or []:
            criterion = getattr(result, "criterion_id", "")
            criterion_val = getattr(criterion, "value", criterion)
            if criterion_val == "real_backtest_validation":
                details = getattr(result, "details", None) or {}
                run_id = str(details.get("backtest_run_id") or "")
                if not run_id:
                    continue
                if getattr(response, "passed", False) and callable(getattr(response, "to_dict", None)):
                    self.proofs[run_id] = response.to_dict()
                    if isinstance(evaluation, Mapping):
                        copied = copy.deepcopy(dict(evaluation))
                        self.evaluations[run_id] = copied
                        self.last_evaluation = copied
                else:
                    self.release(run_id)
        return response


def record_admitted_template_replication(
    data_dir: Path,
    *,
    tenant_id: str,
    persona_id: str,
    demand_ref: str,
    strategy_spec_id: str,
    strategy_id: str,
    spec_version: str,
    checksum: str,
    approval_decision_id: str,
    approver: str,
    approved_at: str,
    backtest_run_id: str,
    admission_store: ReplicationAdmissionStore | None = None,
    queue_store: AlphaReplicationQueue | None = None,
) -> dict[str, Any]:
    """Durably record the admitted replication entry and completed queue state for a template pass."""
    stores_to_update: list[ tuple[ReplicationAdmissionStore, AlphaReplicationQueue] ] = []
    primary_adm = admission_store or ReplicationAdmissionStore(data_dir)
    primary_q = queue_store or AlphaReplicationQueue(data_dir)
    stores_to_update.append((primary_adm, primary_q))
    if Path(getattr(primary_adm, "_data_dir", data_dir)) != Path(data_dir):
        stores_to_update.append((ReplicationAdmissionStore(data_dir), AlphaReplicationQueue(data_dir)))

    admission_id = "adm-tpl-" + hashlib.sha256(
        f"{tenant_id}:{demand_ref}:{strategy_spec_id}".encode("utf-8")
    ).hexdigest()[:16]
    result_record: dict[str, Any] = {}

    for adm_store, q_store in stores_to_update:
        created = adm_store.create_admission(
            {
                "admission_id": admission_id,
                "tenant_id": tenant_id,
                "strategy_spec_id": strategy_spec_id,
                "strategy_id": strategy_id,
                "spec_version": spec_version,
                "checksum": checksum,
                "approval_decision_id": approval_decision_id,
                "approver": approver,
                "approved_at": approved_at,
                "requested_by": "persona-pool-cycle",
                "review_ref": f"review://{approval_decision_id}",
                "mode": "initial",
            }
        )
        with adm_store._lock_context():
            entries = adm_store._read()
            for entry in entries:
                if (
                    entry.get("tenant_id") == tenant_id
                    and entry.get("strategy_spec_id") == strategy_spec_id
                ):
                    entry["persona_id"] = persona_id
                    entry["demand_ref"] = demand_ref
                    created = dict(entry)
            adm_store._rewrite_durable(entries)

        with q_store._lock_context():
            q_entries = q_store._read()
            found_q = None
            for q_entry in q_entries:
                if (
                    q_entry.get("tenant_id") == tenant_id
                    and q_entry.get("strategy_spec_id") == strategy_spec_id
                ):
                    found_q = q_entry
                    break
            if found_q is None:
                found_q = {
                    "tenant_id": tenant_id,
                    "strategy_spec_id": strategy_spec_id,
                    "strategy_id": strategy_id,
                    "spec_version": spec_version,
                    "checksum": checksum,
                    "approval_decision_id": approval_decision_id,
                    "approver": approver,
                    "approved_at": approved_at,
                    "lifecycle_state": "approved",
                    "enqueued_at": approved_at,
                    "enqueued_by": "persona-pool-cycle",
                    "attempt_count": 1,
                    "revalidation_count": 1,
                    "claim_generation": 1,
                    "reclaimed_count": 0,
                    "replay_count": 0,
                    "consumed_replay_ids": [],
                }
                q_entries.append(found_q)
            found_q.update(
                {
                    "persona_id": persona_id,
                    "demand_ref": demand_ref,
                    "status": "completed",
                    "last_revalidation_at": approved_at,
                    "last_revalidation_status": "completed",
                    "authority_task_id": f"demand:{demand_ref}",
                    "authority_run_ids": [backtest_run_id],
                    "experiment_task_id": f"demand:{demand_ref}",
                    "experiment_run_ids": [backtest_run_id],
                    "failure_reason": None,
                }
            )
            q_store._rewrite_durable(q_entries)
            if not result_record:
                result_record = {**created, "queue_status": "completed"}

    return result_record


class TemplateCandidateRegistrar:
    """Registers a gate-passed demand template candidate through Registry and Governance."""

    def __init__(
        self,
        *,
        data_dir: str | Path,
        owner_call: Callable[..., Any],
        registry_url: str | None = None,
        governance_url: str | None = None,
        proposer_id: str | None = None,
        gate_id: str | None = None,
        admission_store: ReplicationAdmissionStore | None = None,
        queue_store: AlphaReplicationQueue | None = None,
        backtest_run_store: SharedBacktestRunStore | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.owner_call = owner_call
        self.registry_url = (
            registry_url
            or os.getenv("PANTHEON_REGISTRY_URL")
            or os.getenv("REGISTRY_URL")
            or "http://registry:8087"
        ).rstrip("/")
        self.governance_url = (
            governance_url
            or os.getenv("PANTHEON_GOVERNANCE_URL")
            or os.getenv("GOVERNANCE_API_URL")
            or os.getenv("GOVERNANCE_URL")
            or "http://governance:8006"
        ).rstrip("/")
        self.proposer_id = (
            proposer_id
            or os.getenv("RESEARCH_GOVERNANCE_PROPOSER_ID")
            or os.getenv("AUTO_CONSUMER_PROPOSER_ID")
            or "research-orchestrator"
        ).strip()
        self.gate_id = (
            gate_id
            or os.getenv("RESEARCH_GOVERNANCE_GATE_ID")
            or os.getenv("AUTO_CONSUMER_GATE_ID")
            or "automated-replication-gate"
        ).strip()
        self.admission_store = admission_store
        self.queue_store = queue_store
        self.backtest_run_store = backtest_run_store or SharedBacktestRunStore(
            self.data_dir / "backtest_runs.json"
        )

    @staticmethod
    def _token_var(candidates: tuple[str, ...], fallback: str) -> str:
        for name in candidates:
            if os.getenv(name, "").strip() or os.getenv(name + "_FILE", "").strip():
                return name
        return fallback

    def _proposer_token_var(self) -> str:
        return self._token_var(
            (
                "RESEARCH_GOVERNANCE_PROPOSER_TOKEN",
                "RESEARCH_GOVERNANCE_SERVICE_TOKEN",
                "AUTO_CONSUMER_PROPOSER_TOKEN",
            ),
            "RESEARCH_GOVERNANCE_SERVICE_TOKEN",
        )

    def _gate_token_var(self) -> str:
        return self._token_var(
            (
                "RESEARCH_GOVERNANCE_GATE_TOKEN",
                "RESEARCH_GOVERNANCE_SERVICE_TOKEN",
                "AUTO_CONSUMER_GATE_TOKEN",
            ),
            "RESEARCH_GOVERNANCE_SERVICE_TOKEN",
        )

    def _read_optional(
        self,
        *,
        step: str,
        service: str,
        url: str,
        tenant_id: str,
        token_variable: str,
    ) -> dict[str, Any] | None:
        try:
            res = self.owner_call(
                step=step,
                service=service,
                method="GET",
                url=url,
                tenant_id=tenant_id,
                token_variable=token_variable,
            )
            return res if isinstance(res, dict) and res else None
        except Exception as exc:  # noqa: BLE001
            status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
            if status == 404 or "404" in str(exc) or "not found" in str(exc).lower():
                return None
            raise

    def register_passed_attempt(
        self,
        *,
        demand: Mapping[str, Any],
        template: Mapping[str, Any],
        artifact: Mapping[str, Any],
        receipt: Mapping[str, Any],
        backtest_evaluation: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Register and paper-approve a gate-passed template candidate; reject any non-passing input."""
        if receipt.get("status") != "admitted":
            raise CandidateRegistrationError("attempt_did_not_pass_gate")
        gate_proof = receipt.get("gate")
        if not isinstance(gate_proof, Mapping):
            raise CandidateRegistrationError("gate_proof_missing")
        if gate_proof.get("passed") is False or gate_proof.get("admission_status") not in (None, "admitted"):
            raise CandidateRegistrationError("attempt_did_not_pass_gate")

        validate_strategy_artifact(artifact)
        checksum = strategy_artifact_checksum(artifact)
        artifact_id = str(artifact["artifact_id"])
        tenant_id = str(demand["tenant_id"])
        persona_id = str(demand["persona_id"])
        demand_id = str(demand["demand_id"])
        source_spec_id = str((artifact.get("lineage") or {}).get("source_strategy_spec_id") or "")
        if not source_spec_id:
            raise CandidateRegistrationError("artifact_lineage_missing_source_strategy_spec_id")

        results = gate_proof.get("results") or gate_proof.get("replication_results") or []
        real_bt = next(
            (
                r
                for r in results
                if isinstance(r, Mapping) and r.get("criterion_id") == "real_backtest_validation"
            ),
            None,
        )
        if not isinstance(real_bt, Mapping) or not real_bt.get("passed"):
            raise CandidateRegistrationError("real_backtest_validation_not_passed")
        details = real_bt.get("details")
        if not isinstance(details, Mapping):
            raise CandidateRegistrationError("real_backtest_validation_details_missing")
        checks = details.get("checks")
        if not isinstance(checks, Mapping) or not checks or not all(bool(v) for v in checks.values()):
            raise CandidateRegistrationError("real_backtest_validation_checks_incomplete")

        backtest_run_id = str(details.get("backtest_run_id") or "").strip()
        if not backtest_run_id:
            raise CandidateRegistrationError("backtest_run_id_missing")

        evaluation = backtest_evaluation
        if evaluation is None and self.backtest_run_store is not None:
            evaluation = self.backtest_run_store.get(backtest_run_id)
        if not isinstance(evaluation, Mapping) or evaluation.get("status") != "completed":
            raise CandidateRegistrationError("completed_backtest_evaluation_missing")

        if self.backtest_run_store is not None and self.backtest_run_store.get(backtest_run_id) is None:
            self.backtest_run_store.record(dict(evaluation))

        verdict_payload = {
            "admission_status": "admitted",
            "trial_count": details.get("trial_count"),
            "mandate_max_drawdown": details.get("mandate_max_drawdown"),
            "family_id": details.get("family_id"),
            "verdict_digest": real_backtest_verdict_digest(json.loads(json.dumps(dict(details), default=str))),
        }
        bind_backtest_run_owner(self.data_dir, backtest_run_id, tenant_id=tenant_id, decision_id=demand_id)
        bind_backtest_run_gate_verdict(self.data_dir, backtest_run_id, verdict_payload)

        summary = {
            "backtest_run_id": backtest_run_id,
            "out_of_sample_sharpe_ratio": details.get("out_of_sample_sharpe_ratio"),
            "max_drawdown": abs(float(details.get("out_of_sample_max_drawdown") or 0.0)),
            "round_trips": details.get("out_of_sample_round_trips"),
            "demand_ref": demand_id,
            "persona_id": persona_id,
            "template_id": str(template["template_id"]),
            "family_id": str(receipt["family_id"]),
        }
        wire = json.loads(json.dumps(dict(gate_proof), default=str))
        if "results" in wire:
            wire["replication_results"] = wire.pop("results")
        wire["backtest_run_id"] = backtest_run_id
        wire["evaluation_summary"] = summary
        full_evaluation = json.loads(json.dumps(dict(evaluation), default=str))

        data_sources = sorted(
            {
                str(inp["dataset"])
                for inp in (template.get("inputs") or [])
                if isinstance(inp, Mapping) and inp.get("dataset")
            }
        )
        metadata = {
            "tenant_id": tenant_id,
            "persona_id": persona_id,
            "demand_ref": demand_id,
            "template_id": str(template["template_id"]),
            "family_id": str(receipt["family_id"]),
            "execution_context": "paper",
            "capital_scale_pct": 0.0,
            "data_sources": data_sources,
        }

        artifact_url = (
            f"{self.registry_url}/api/registry/strategy-artifacts/"
            f"{urllib.parse.quote(artifact_id, safe='')}"
        )
        view = self._read_optional(
            step="read_template_candidate",
            service="registry",
            url=artifact_url,
            tenant_id=tenant_id,
            token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
        )
        if view is None:
            try:
                view = self.owner_call(
                    step="register_template_candidate",
                    service="registry",
                    method="POST",
                    url=f"{self.registry_url}/api/registry/strategy-artifacts",
                    tenant_id=tenant_id,
                    token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
                    body={
                        "registry_id": artifact_id,
                        "artifact_state": "candidate",
                        "strategy_artifact": dict(artifact),
                        "producer_run_id": backtest_run_id,
                        "evaluation_summary": summary,
                        "replication_proof": wire,
                        "backtest_evaluation": full_evaluation,
                        "metadata": metadata,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                status = getattr(exc, "status", None) or getattr(exc, "status_code", None)
                if status == 409:
                    view = self._read_optional(
                        step="read_template_candidate",
                        service="registry",
                        url=artifact_url,
                        tenant_id=tenant_id,
                        token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
                    )
                if view is None:
                    raise CandidateRegistrationError(f"registry_registration_failed:{exc}") from exc

        entry = (view or {}).get("entry") if isinstance(view, Mapping) else None
        if not isinstance(entry, Mapping):
            raise CandidateRegistrationError("registry_entry_missing")
        if (
            entry.get("registry_id") != artifact_id
            or entry.get("owner_tenant") != tenant_id
            or entry.get("checksum") != checksum
            or entry.get("artifact_type") != "execution_bundle"
        ):
            raise CandidateRegistrationError("registry_entry_identity_mismatch")
        if entry.get("replication_success") is not True or not isinstance(entry.get("replication_proof"), Mapping):
            raise CandidateRegistrationError("registry_entry_missing_verified_proof")

        approval_id = "apv-tpl-" + hashlib.sha256(
            f"{tenant_id}:{demand_id}:{artifact_id}:{checksum}".encode("utf-8")
        ).hexdigest()[:24]
        approval_path = f"/api/governance/approvals/{urllib.parse.quote(approval_id, safe='')}"
        approval_url = f"{self.governance_url}{approval_path}"

        if entry.get("artifact_state") == "candidate":
            approval = self._read_optional(
                step="read_template_approval",
                service="governance",
                url=approval_url,
                tenant_id=tenant_id,
                token_variable=self._proposer_token_var(),
            )
            if approval is None:
                proposal_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "tenant_id": tenant_id,
                            "persona_id": persona_id,
                            "demand_id": demand_id,
                            "artifact_id": artifact_id,
                            "checksum": checksum,
                        },
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
                approval = self.owner_call(
                    step="propose_template_approval",
                    service="governance",
                    method="POST",
                    url=f"{self.governance_url}/api/governance/approvals",
                    tenant_id=tenant_id,
                    token_variable=self._proposer_token_var(),
                    extra_headers={"Idempotency-Key": f"propose:{approval_id}"},
                    body={
                        "decision_id": approval_id,
                        "expected_version": 0,
                        "tenant_id": tenant_id,
                        "target_type": "registry_entry",
                        "target_id": artifact_id,
                        "target_version": entry["version"],
                        "risk_level": "low",
                        "persona_id": persona_id,
                        "owner_user_id": self.proposer_id,
                        "proposal_id": demand_id,
                        "proposal_revision": 1,
                        "proposal_content_digest": f"sha256:{proposal_digest}",
                        "validation_result_digest": checksum,
                        "candidate_digest": checksum,
                        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=72)).isoformat(),
                    },
                )
            if not isinstance(approval, Mapping) or any(
                approval.get(k) != v
                for k, v in {
                    "target_id": artifact_id,
                    "target_version": entry["version"],
                    "tenant_id": tenant_id,
                    "candidate_digest": checksum,
                    "risk_level": "low",
                }.items()
            ):
                raise CandidateRegistrationError("governance_approval_identity_mismatch")

            for step_name in ("review", "decide"):
                if approval.get("decision_state") == "decided":
                    break
                if step_name == "review" and approval.get("decision_state") == "under_review":
                    continue
                step_body: dict[str, Any] = {
                    "actor_role": "automated_gate",
                    "actor_id": self.gate_id,
                    "expected_version": approval["version"],
                }
                if step_name == "decide":
                    step_body.update(
                        outcome="approved",
                        candidate_digest=checksum,
                        expires_at=approval["expires_at"],
                        rationale=f"Gate-passed demand template candidate for {demand_id} (paper only)",
                        evidence_refs=[
                            {"ref_type": "registry_entry", "ref_id": artifact_id},
                            {"ref_type": "research_demand", "ref_id": demand_id},
                        ],
                    )
                approval = self.owner_call(
                    step=f"{step_name}_template_approval",
                    service="governance",
                    method="POST",
                    url=f"{approval_url}/{step_name}",
                    tenant_id=tenant_id,
                    token_variable=self._gate_token_var(),
                    extra_headers={"Idempotency-Key": f"{step_name}:{approval_id}"},
                    body=step_body,
                )

            if not isinstance(approval, Mapping) or approval.get("decision_state") != "decided" or approval.get("decision") != "approved":
                raise CandidateRegistrationError("governance_approval_not_decided_approved")

            advanced = self.owner_call(
                step="advance_template_candidate",
                service="registry",
                method="POST",
                url=f"{artifact_url}/advance",
                tenant_id=tenant_id,
                token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
                body={
                    "target_state": "approved",
                    "approval_decision_id": approval_id,
                    "expected_artifact_state": "candidate",
                    "expected_version": entry["version"],
                    "expected_updated_at": entry.get("updated_at"),
                    "command_key": approval_id,
                },
            )
            entry = (advanced or {}).get("entry") if isinstance(advanced, Mapping) else None
            if not isinstance(entry, Mapping):
                reread = self._read_optional(
                    step="read_template_candidate",
                    service="registry",
                    url=artifact_url,
                    tenant_id=tenant_id,
                    token_variable="RESEARCH_REGISTRY_SERVICE_TOKEN",
                )
                entry = (reread or {}).get("entry") if isinstance(reread, Mapping) else None

        if not isinstance(entry, Mapping) or entry.get("artifact_state") != "approved":
            raise CandidateRegistrationError("registry_entry_not_approved")

        baseline = resolve_member_baseline(
            tenant_id=tenant_id,
            persona_id=persona_id,
            artifact_ref=artifact_id,
            checksum=checksum,
            entry=entry,
        )
        if baseline.get("status") != "approved":
            raise CandidateRegistrationError(
                f"approved_baseline_unavailable:{baseline.get('reason')}"
            )

        effective_approval_id = str(entry.get("approval_decision_id") or approval_id)
        approved_at = str(entry.get("approved_at") or entry.get("updated_at") or _utc_now())
        approver = str(entry.get("approver") or self.gate_id)

        replication_entry = record_admitted_template_replication(
            self.data_dir,
            tenant_id=tenant_id,
            persona_id=persona_id,
            demand_ref=demand_id,
            strategy_spec_id=source_spec_id,
            strategy_id=str(artifact["strategy_id"]),
            spec_version=str(artifact["version"]),
            checksum=checksum,
            approval_decision_id=effective_approval_id,
            approver=approver,
            approved_at=approved_at,
            backtest_run_id=backtest_run_id,
            admission_store=self.admission_store,
            queue_store=self.queue_store,
        )

        return {
            "status": "approved",
            "registry_id": artifact_id,
            "checksum": checksum,
            "approval_decision_id": effective_approval_id,
            "baseline_ref": effective_approval_id,
            "baseline_key": baseline["baseline_key"],
            "demand_ref": demand_id,
            "persona_id": persona_id,
            "tenant_id": tenant_id,
            "source_strategy_spec_id": source_spec_id,
            "backtest_run_id": backtest_run_id,
            "admission_id": replication_entry.get("admission_id"),
            "paper_only": True,
        }
