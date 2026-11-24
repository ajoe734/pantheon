"""Transport-level contract tests for paper Persona provisioning coordination."""
from __future__ import annotations

import os
import sys
from collections import Counter
import copy
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

import pytest

try:
    from services.control_plane.bff.persona_provisioning import (
        MemoryPersonaProvisioningStore,
        ProvisioningRecord,
    )
    from services.control_plane.bff.persona_provisioning_coordinator import (
        FIRST_EVALUATION_WORKFLOW_ID,
        PersonaProvisioningCoordinator,
        PersonaProvisioningCoordinationError,
        deterministic_provisioning_ids,
    )
except ImportError:
    pass
    from persona_provisioning import MemoryPersonaProvisioningStore, ProvisioningRecord  # type: ignore[no-redef]
    from persona_provisioning_coordinator import (  # type: ignore[no-redef]
        FIRST_EVALUATION_WORKFLOW_ID,
        PersonaProvisioningCoordinator,
        PersonaProvisioningCoordinationError,
        deterministic_provisioning_ids,
    )
from services.capital.risk_policy import risk_policy_digest
from services.persona.risk_policy_draft import DraftInputs, DraftResult
from services.registry.paper_strategy_spec import validate_strategy_spec
from services.registry.strategy_artifact import (
    strategy_artifact_checksum,
    validate_strategy_artifact,
)


class TrackingStore(MemoryPersonaProvisioningStore):
    def __init__(self) -> None:
        super().__init__()
        self.checkpoints: list[str] = []
        self.checkpoint_lease_seconds: list[int] = []
        FakeOwnerTransport._TEST_STORES.append(self)
        if len(FakeOwnerTransport._TEST_STORES) > 64:
            del FakeOwnerTransport._TEST_STORES[:-64]

    def checkpoint(self, record, *, lease_owner, lease_seconds=60):
        self.checkpoints.append(record.current_step)
        self.checkpoint_lease_seconds.append(lease_seconds)
        return super().checkpoint(
            record,
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
        )


def _owner_now() -> str:
    """Owner services stamp second-precision UTC creation times."""

    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeOwnerTransport:
    """Small in-memory implementation of only the owner routes under test.

    Owner-side invariants that the coordinator's bodies must satisfy are
    modelled with the real owner code where it is pure: Registry's inline
    StrategySpec checksum and StrategySpec revision lineage rule, and strict
    Governance's proposal-owner / actor binding, CAS versioning and decided
    provenance.  ``governance_subject`` is the verified subject of the
    approval principal the transport would authenticate as; ``governance_roles``
    are the roles that principal has actually been granted.
    """

    _TEST_STORES: list[Any] = []

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "get" in cls.__dict__:
            raw_get = cls.__dict__["get"]

            def _wrapped_get(self: FakeOwnerTransport, owner: str, path: str) -> Mapping[str, Any] | None:
                res = raw_get(self, owner, path)
                if (
                    res is None
                    and owner == "governance"
                    and path.startswith("/api/governance/approvals/")
                ):
                    decision_id = path.removeprefix("/api/governance/approvals/")
                    res = self._synthesized_fixture_pool_approval(decision_id)
                return res

            cls.get = _wrapped_get  # type: ignore[method-assign]

    def __init__(
        self,
        *,
        response_loss: set[str] | None = None,
        mutation_failure: set[str] | None = None,
        governance_subject: str = "pantheon-persona-provisioner",
        governance_roles: tuple[str, ...] = ("approval_proposer", "automated_gate"),
        seed_pool_policy_approval: bool = True,
    ) -> None:
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple[str, str, str, dict[str, Any] | None]] = []
        self.response_loss = set(response_loss or set())
        self.mutation_failure = set(mutation_failure or set())
        self.mutations = Counter()
        self.governance_subject = governance_subject
        self.governance_roles = set(governance_roles)
        self.advance_receipts: dict[tuple[str, str], dict[str, Any]] = {}
        self.seed_pool_policy_approval = seed_pool_policy_approval

    def _authorization_scope(self) -> dict[str, Any] | None:
        """Owner-stamped scope for the dedicated dev paper subject, else None.

        Strict Governance derives this from the verified principal's grant;
        it is never a client body field.
        """

        try:
            from services.governance.paper_approval_scope import (
                DEV_PAPER_AUTHORIZATION_SCOPE,
                DEV_PAPER_PROVISIONER_SUBJECT,
                dev_paper_feature_enabled,
            )
        except ImportError:  # paper scope owner module not integrated yet
            return None
        if self.governance_subject != DEV_PAPER_PROVISIONER_SUBJECT:
            return None
        if not dev_paper_feature_enabled():
            raise PermissionError(
                "HTTP 403: Dev paper approval principal is not enabled in this environment"
            )
        return deepcopy(DEV_PAPER_AUTHORIZATION_SCOPE)

    def _synthesized_fixture_pool_approval(self, decision_id: str) -> dict[str, Any] | None:
        from services.capital.risk_policy import capital_pool_approval_digest, risk_policy_digest
        from services.governance.test_approval_authority import approval_snapshot

        target_pool_id: str | None = None
        if decision_id.startswith("apv-capital-pool-"):
            if not self.seed_pool_policy_approval:
                return None
            target_pool_id = decision_id.removeprefix("apv-capital-pool-")
        elif decision_id != "apv-test-fixture-pool-policy":
            return None

        candidate_stores: list[Any] = []
        _ps = sys.modules.get("services.control_plane.bff.personas.service")
        if _ps is not None and getattr(_ps, "_PERSONA_PROVISIONING_STORE", None) is not None:
            candidate_stores.append(_ps._PERSONA_PROVISIONING_STORE)
        candidate_stores.extend(reversed(self._TEST_STORES))

        matched_record: ProvisioningRecord | None = None
        matched_ids: Any = None
        for st in candidate_stores:
            recs: list[Any] = []
            if hasattr(st, "list_all"):
                try:
                    recs = list(st.list_all())
                except Exception:
                    recs = []
            elif hasattr(st, "_records"):
                recs = list(getattr(st, "_records", {}).values())
            for rec in reversed(recs):
                ids = deterministic_provisioning_ids(rec)
                if target_pool_id is not None:
                    if ids.capital_pool_id == target_pool_id:
                        matched_record = rec
                        matched_ids = ids
                        break
                elif rec.request_payload.get("profile_mapping_approval_id") == decision_id:
                    matched_record = rec
                    matched_ids = ids
                    break
            if matched_record is not None:
                break

        if matched_record is None or matched_ids is None:
            return None

        pool_id = matched_ids.capital_pool_id
        version = str(matched_record.request_payload.get("risk_policy_version") or "1.0").strip() or "1.0"
        policy = _fixture_paper_policy(pool_id, version=version)
        try:
            currency = PersonaProvisioningCoordinator._capital_pool_currency(matched_record)
        except Exception:
            currency = "USD"
        pool_digest = capital_pool_approval_digest(
            {
                "pool_id": pool_id,
                "owner_id": matched_record.tenant_id,
                "owner_type": "org",
                "currency": currency,
                "budget": matched_record.request_payload.get("budget"),
                "risk_policy_ref": None,
                "single_runtime_enforced": True,
                "metadata": {"risk_policy": policy},
            }
        )
        tenant_id = matched_record.tenant_id
        extracted_profile, _, _ = PersonaProvisioningCoordinator._extract_request_risk_profile_context(
            matched_record.request_payload,
            request_hash=matched_record.request_hash,
        )
        req_risk = str(extracted_profile or "low").strip() or "low"

        return approval_snapshot(
            decision_id=decision_id,
            tenant_id=tenant_id,
            target_type="capital_pool_activation",
            target_id=pool_id,
            target_version=pool_digest,
            owner_user_id="proposer-1",
            risk_level="low",
            metadata={
                "subject": {
                    "pool_id": pool_id,
                    "risk_direction": "increase",
                    "requested_risk": req_risk,
                    "risk_policy_id": str(policy.get("risk_policy_id") or f"rp-{pool_id}"),
                    "risk_policy_version": str(policy.get("version") or "1.0"),
                    "risk_policy_digest": risk_policy_digest(policy),
                    "risk_officer_draft": {"policy": policy, "provenance": {"source": "risk_officer_draft"}},
                },
                "approvals": [
                    {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                    {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
                ],
            },
        )

    def get(self, owner: str, path: str) -> Mapping[str, Any] | None:
        self.calls.append(("GET", owner, path, None))
        value = self.objects.get((owner, path))
        if value is None and owner == "governance" and path.startswith("/api/governance/approvals/"):
            decision_id = path.removeprefix("/api/governance/approvals/")
            value = self._synthesized_fixture_pool_approval(decision_id)
        if value is None:
            return None
        result = deepcopy(value)
        if owner == "capital" and path.startswith("/api/capital-pools/") and isinstance(result, dict):
            from services.capital.risk_policy import capital_pool_approval_digest

            result["approval_digest"] = capital_pool_approval_digest(result)
        return result

    def list(self, owner: str, path: str) -> list[Mapping[str, Any]]:
        from urllib.parse import parse_qs

        self.calls.append(("GET", owner, path, None))
        base, _, query_str = path.partition("?")
        filters = parse_qs(query_str) if query_str else {}
        items = [
            deepcopy(v)
            for (o, p), v in self.objects.items()
            if o == owner and p.startswith(f"{base}/") and p.count("/") == base.count("/") + 1
        ]
        if filters:
            filtered: list[Mapping[str, Any]] = []
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                ok = True
                for f_key, f_vals in filters.items():
                    if not f_vals or not f_vals[0]:
                        continue
                    if str(item.get(f_key) or "") != str(f_vals[0]):
                        ok = False
                        break
                if ok:
                    filtered.append(item)
            return filtered
        return items

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = deepcopy(dict(payload))
        self.calls.append(("POST", owner, path, body))
        self.mutations[(owner, path)] += 1
        if path in self.mutation_failure:
            raise ConnectionError(f"owner rejected {path}")
        response = self._apply_post(owner, path, body)
        if path in self.response_loss:
            self.response_loss.remove(path)
            raise ConnectionError(f"response lost for {path}")
        return deepcopy(response)

    def patch(self, owner: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        body = deepcopy(dict(payload))
        self.calls.append(("PATCH", owner, path, body))
        self.mutations[(owner, path)] += 1
        binding_path = path.removesuffix("/status")
        binding = self.objects[(owner, binding_path)]
        binding["status"] = body.get("status") or binding["status"]
        if "risk_policy_ref" in body and body["risk_policy_ref"] is not None:
            binding["risk_policy_ref"] = body["risk_policy_ref"]
        if isinstance(body.get("metadata"), Mapping):
            existing_meta = dict(binding.get("metadata") or {})
            existing_meta.update(body["metadata"])
            binding["metadata"] = existing_meta
        if owner == "capital" and binding_path.startswith("/api/capital-pools/"):
            from services.capital.risk_policy import capital_pool_approval_digest

            binding["approval_digest"] = capital_pool_approval_digest(binding)
        return deepcopy(binding)

    def _owner_registry_entry(self, registry_id: str) -> dict[str, Any]:
        """Resolve an approved RegistryEntry from owner state, by exact ID."""

        view = self.objects.get(
            ("registry", f"/api/registry/strategy-artifacts/{registry_id}")
        )
        if view is None:
            raise AssertionError(f"unknown RegistryEntry: {registry_id}")
        entry = deepcopy(view["entry"])
        if entry.get("artifact_state") != "approved":
            raise AssertionError(f"RegistryEntry {registry_id} is not approved")
        return entry

    def _put(self, owner: str, path: str, value: Mapping[str, Any]) -> dict[str, Any]:
        stored = deepcopy(dict(value))
        self.objects[(owner, path)] = stored
        return stored

    def _read_entry_dict(self, registry_id: str) -> dict[str, Any] | None:
        """Exact local owner read, as RegistryService._read_entry_dict."""

        for (view_owner, view_path), view in self.objects.items():
            if view_owner == "registry" and view_path.endswith(f"/{registry_id}"):
                return deepcopy(view["entry"])
        return None

    def _advance_registry_entry(self, target: str, body: dict[str, Any]) -> dict[str, Any]:
        """Model RegistryService.advance_artifact_state's strict approval contract.

        A caller-supplied ``approver`` is retired; approval needs the decision
        reference, a command key and the caller's exact CAS base
        (state/version/updated_at).  A same-key identical replay returns the
        committed row without re-running the transition, a same-key
        divergent replay is a conflict, and the approval itself is verified
        with the real shared ``ApprovalEvidence`` exactly as Registry does,
        including the paper usage context for a scoped decision.
        """

        from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid

        view = self.objects[("registry", target)]
        entry = view["entry"]
        approving = body["target_state"] == "approved"
        if approving:
            if body.get("approver") is not None:
                raise ValueError(
                    "HTTP 400: Caller-supplied approver is retired; use a Governance decision reference"
                )
            if not body.get("approval_decision_id") or not body.get("command_key"):
                raise ValueError(
                    "HTTP 400: Approval requires verified decision reference, actor and command key"
                )
            if not all(
                body.get(key)
                for key in ("expected_artifact_state", "expected_version", "expected_updated_at")
            ):
                raise ValueError(
                    "HTTP 400: Approval requires the caller artifact base state/version/time"
                )
        fields = {
            key: body.get(key)
            for key in (
                "target_state",
                "approver",
                "approval_decision_id",
                "expected_artifact_state",
                "expected_version",
                "expected_updated_at",
            )
        }
        receipt_key = (target, body["command_key"])
        receipt = self.advance_receipts.get(receipt_key)
        if receipt is not None:
            if receipt["fields"] != fields:
                raise ValueError("HTTP 409: command_key replay diverges from the committed command")
            return deepcopy(receipt["view"])
        base = (entry["artifact_state"], entry["version"], entry["updated_at"])
        claimed = (
            body["expected_artifact_state"],
            body.get("expected_version") or entry["version"],
            body.get("expected_updated_at") or entry["updated_at"],
        )
        if base != claimed:
            raise ValueError(f"HTTP 409: stale artifact base {claimed!r} != {base!r}")
        committed_at = _owner_now()
        if approving:
            decision = self.objects.get(
                ("governance", f"/api/governance/approvals/{body['approval_decision_id']}")
            )
            if decision is None:
                raise ValueError("HTTP 400: Governance denied exact decision read")
            try:
                evidence = ApprovalEvidence.model_validate(decision)
                usage_context = None
                if getattr(evidence, "authorization_scope", None) is not None:
                    from services.governance.paper_approval_scope import (
                        current_environment,
                        paper_candidate_usage_context,
                    )

                    usage_context = paper_candidate_usage_context(
                        deepcopy(entry),
                        evidence=evidence.model_dump(),
                        read_entry=self._read_entry_dict,
                        environment=current_environment(),
                    )
                expected = {
                    "tenant_id": entry["owner_tenant"],
                    "target_type": "registry_entry",
                    "target_id": entry["registry_id"],
                    "target_version": entry["version"],
                    "candidate_digest": entry["checksum"],
                }
                if usage_context is None:
                    evidence.require_valid(expected=expected)
                else:
                    evidence.require_valid(expected=expected, usage_context=usage_context)
            except (ApprovalInvalid, ValueError) as exc:
                raise ValueError(f"HTTP 400: {exc}") from exc
            entry.update(
                artifact_state="approved",
                approval_decision_id=body["approval_decision_id"],
                approver=evidence.actor_id,
                approved_at=committed_at,
                approval_evidence=evidence.model_dump(),
                updated_at=committed_at,
            )
        else:
            entry.update(artifact_state=body["target_state"], updated_at=committed_at)
        self.advance_receipts[receipt_key] = {"fields": fields, "view": deepcopy(view)}
        return view

    def _apply_post(self, owner: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if owner == "capital" and path == "/api/capital-pools":
            from services.capital.risk_policy import capital_pool_approval_digest

            pool = {
                **body,
                # The Capital owner stamps the authenticated formal tenant.
                "tenant_id": body["metadata"]["tenant_id"],
                "created_at": "2026-07-15T00:00:00Z",
                "idempotent_replay": False,
            }
            pool["approval_digest"] = capital_pool_approval_digest(pool)
            return self._put(owner, f"/api/capital-pools/{body['pool_id']}", pool)

        if owner == "registry" and path == "/api/registry/strategy-specs":
            # The real facade validates the inline StrategySpec against the
            # canonical schema, computes its checksum and enforces the
            # per-strategy revision lineage rule before it persists anything;
            # a rejected revision has no readback.
            from services.registry.models import RegistryEntry
            from services.registry.service import (
                StrategySpecRegisterRequest,
                _check_strategy_spec_version_lineage,
                _strategy_spec_register_payload,
            )

            admitted = _strategy_spec_register_payload(
                StrategySpecRegisterRequest.model_validate(body)
            )
            existing = [
                RegistryEntry.from_dict(view["entry"])
                for (view_owner, view_path), view in self.objects.items()
                if view_owner == "registry"
                and view_path.startswith("/api/registry/strategy-specs/")
                and view["entry"]["strategy_id"] == body["strategy_id"]
            ]
            _check_strategy_spec_version_lineage(
                existing,
                body["strategy_id"],
                body["version"],
                admitted.lineage,
                base_checksum=body.get("base_checksum"),
            )
            created_at = _owner_now()
            entry = RegistryEntry(
                registry_id=body["registry_id"],
                owner_tenant=body["metadata"]["tenant_id"],
                created_at=created_at,
                updated_at=created_at,
                **vars(admitted),
            ).to_dict()
            return self._put(
                owner,
                f"/api/registry/strategy-specs/{body['registry_id']}",
                {"entry": entry, "deployment_stage": "none"},
            )

        if owner == "registry" and path == "/api/registry/strategy-artifacts":
            # The real facade validates the StrategyArtifact schema and builds
            # the execution_bundle envelope, including its durable checksum.
            from services.registry.models import RegistryEntry
            from services.registry.strategy_artifact import (
                build_strategy_artifact_registry_payload,
            )

            registry_id, admitted = build_strategy_artifact_registry_payload(body)
            created_at = _owner_now()
            entry = RegistryEntry(
                registry_id=registry_id,
                owner_tenant=(body.get("metadata") or {}).get("tenant_id"),
                created_at=created_at,
                updated_at=created_at,
                **vars(admitted),
            ).to_dict()
            assert entry["checksum"] == strategy_artifact_checksum(body["strategy_artifact"])
            return self._put(
                owner,
                f"/api/registry/strategy-artifacts/{registry_id}",
                {"entry": entry, "deployment_stage": "none"},
            )

        if owner == "governance" and path == "/api/governance/approvals":
            # Strict Governance: the proposal owner must be the verified
            # subject, the body tenant must be the principal's tenant, the
            # base version must be zero, and a same-ID decision is a conflict.
            if body["owner_user_id"] != self.governance_subject:
                raise PermissionError(
                    "HTTP 403: Proposal owner and tenant must match verified principal"
                )
            if "approval_proposer" not in self.governance_roles and not (
                self.governance_roles & {"automated_gate", "governance_reviewer"}
            ):
                raise PermissionError("HTTP 403: Approval propose role required")
            if body["expected_version"] != 0:
                raise ValueError("HTTP 409: Proposal expected_version must be zero")
            decision_path = f"/api/governance/approvals/{body['decision_id']}"
            if (owner, decision_path) in self.objects:
                raise ValueError("HTTP 409: Approval decision already exists")
            initial_metadata = (
                deepcopy(dict(body["metadata"]))
                if isinstance(body.get("metadata"), Mapping)
                else (
                    {"subject": deepcopy(dict(body["subject"]))}
                    if isinstance(body.get("subject"), Mapping)
                    else None
                )
            )
            decision = {
                **{key: value for key, value in body.items() if key != "expected_version"},
                "decision": None,
                "decision_state": "proposed",
                "actor_role": None,
                "actor_id": None,
                "rationale": None,
                "created_at": _owner_now(),
                "decided_at": None,
                "conditions": [],
                "evidence_refs": [],
                "superseded_by": None,
                "revoked_at": None,
                "metadata": initial_metadata,
                "controller_record_ref": None,
                "recorded_at": None,
                "authority_status": None,
                "authorization_scope": self._authorization_scope(),
                "version": 1,
                "event_id": f"event-{body['decision_id']}-1",
            }
            return self._put(owner, decision_path, decision)

        if owner == "governance" and path.endswith(("/review", "/decide")):
            target = path.rsplit("/", 1)[0]
            decision = self.objects[(owner, target)]
            self._authorization_scope()  # the dedicated grant is re-admitted per command
            if body["actor_id"] != self.governance_subject or (
                body["actor_role"] not in self.governance_roles
            ):
                raise PermissionError(
                    "HTTP 403: Body actor and role must match verified principal"
                )
            if body["expected_version"] != decision["version"]:
                raise ValueError("HTTP 409: Approval base version is stale")
            decision["version"] += 1
            decision["event_id"] = f"event-{decision['decision_id']}-{decision['version']}"
            if path.endswith("/review"):
                if decision["decision_state"] != "proposed":
                    raise ValueError("HTTP 400: Can only accept review from 'proposed' state")
                decision.update(
                    decision_state="under_review",
                    actor_role=body["actor_role"],
                    actor_id=body["actor_id"],
                )
                return decision
            if decision["decision_state"] != "under_review":
                raise ValueError("HTTP 400: Can only decide from 'under_review'")
            decided_at = _owner_now()
            decision.update(
                decision_state="decided",
                decision=body["outcome"],
                rationale=body["rationale"],
                actor_role=body["actor_role"],
                actor_id=body["actor_id"],
                evidence_refs=deepcopy(body.get("evidence_refs") or []),
                conditions=list(body.get("conditions") or []),
                decided_at=decided_at,
                recorded_at=decided_at,
                authority_status="authoritative",
                controller_record_ref=(
                    f"governance-controller://approval-{decision['decision_id']}"
                ),
            )
            for field in ("candidate_digest", "proof_digest", "expires_at", "session_id"):
                if body.get(field) is not None:
                    decision[field] = body[field]
            return decision

        if owner == "registry" and path.endswith("/advance"):
            return self._advance_registry_entry(path.removesuffix("/advance"), body)

        if owner == "capital" and path == "/api/bindings":
            binding = {
                **body,
                "status": "pending",
                "approval_decision_id": None,
                "created_at": "2026-07-15T00:00:00Z",
            }
            return self._put(owner, f"/api/bindings/{body['binding_id']}", binding)

        if owner == "capital" and path.endswith("/activate"):
            target = path.removesuffix("/activate")
            binding = self.objects[(owner, target)]
            assert "approval_decision_id" not in body
            binding.update(status="active")
            return binding

        if owner == "deployment" and path == "/api/deployment/plans":
            # Deployment is the owner reader: it resolves the RegistryEntry by
            # the exact registry_id it was given, exactly like the real service.
            # It must never trust a caller-embedded snapshot.
            entry = self._owner_registry_entry(body["registry_id"])
            plan = {
                "plan_id": body["plan_id"],
                "approval_decision_id": body["approval_decision_id"],
                "artifact_id": entry["registry_id"],
                "artifact_version": entry["version"],
                "artifact_type": entry["artifact_type"],
                "strategy_id": entry["strategy_id"],
                "capital_pool_id": body["capital_pool_id"],
                "current_stage": body["current_stage"],
                "target_stage": body["target_stage"],
                "transition_type": "activate",
                "runtime_action": "deploy_new_binding",
                "status": body["status"],
                "created_at": "2026-07-15T00:00:00Z",
                "binding_id": body.get("binding_id"),
                "rollback": body["rollback"],
                "metadata": body["metadata"],
            }
            return self._put(owner, f"/api/deployment/plans/{body['plan_id']}", plan)

        if owner == "deployment" and path.endswith("/dispatch"):
            plan_id = path.split("/")[-2]
            saga = {
                "saga_id": body["saga_id"],
                "plan_id": plan_id,
                "status": "awaiting_binding",
                "current_step": "binding_requested",
                "binding_id": None,
                "runtime_id": None,
            }
            self._put(owner, f"/api/deployment/sagas/{body['saga_id']}", saga)
            return {"deployment_saga": {"saga": deepcopy(saga)}, "replayed": False}

        if owner == "deployment" and path.endswith("/failure"):
            target = path.removesuffix("/failure")
            saga = self.objects[(owner, target)]
            decision = {
                "command_type": "abort_plan",
                "owner_service": "deployment-orchestrator",
                "plan_status": "aborted",
                "binding_status": "none",
                "write_scope": "deployment_plan",
                "reason": body["reason"],
            }
            saga.update(
                status="compensating",
                current_step="compensation_requested",
                failure_reason=body["reason"],
                compensation=decision,
            )
            return decision

        raise AssertionError(f"unexpected mutation: {owner} {path}")


def _record_and_store(
    request_payload: dict[str, Any] | None = None,
    *,
    seed_pool_approval: bool = True,
) -> tuple[TrackingStore, ProvisioningRecord]:
    store = TrackingStore()
    payload: dict[str, Any] = {
        "name": "Trader A",
        "requested_by": "operator-a",
        "mandate": "Paper-only momentum research",
        "traits": {"risk_appetite": "low"},
        "budget": 25000,
    }
    has_explicit_risk_or_policy = bool(
        request_payload
        and any(
            key in request_payload
            for key in (
                "risk",
                "risk_level",
                "riskLevel",
                "risk_profile",
                "risk_policy",
                "risk_policy_ref",
                "riskPolicyRef",
                "profile_mapping_approval_id",
                "approval_decision_id",
                "require_approved_profile_mapping",
            )
        )
    )
    if not has_explicit_risk_or_policy:
        payload["risk"] = "low"  # Explicit fixture provenance, never a production fallback.
    if seed_pool_approval and not has_explicit_risk_or_policy:
        payload["profile_mapping_approval_id"] = "apv-test-fixture-pool-policy"
    if request_payload:
        payload.update(request_payload)
    record, created = store.reserve(
        tenant_id="tenant-a",
        idempotency_key="create-persona-a",
        request_hash="sha256:persona-request",
        normalized_name="trader a",
        persona_id="persona-a",
        request_payload=payload,
    )
    assert created
    FakeOwnerTransport._TEST_STORES.append(store)
    if len(FakeOwnerTransport._TEST_STORES) > 64:
        del FakeOwnerTransport._TEST_STORES[:-64]
    return store, record


def _schedule_receipt(
    persona_id: str, pool_id: str, binding_id: str, **_kwargs: Any
) -> dict[str, Any]:
    return {
        "persona_id": persona_id,
        "capital_pool_id": pool_id,
        "binding_id": binding_id,
        "mode": "gateway_rpc",
        "registered": [
            {
                "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
                "job_id": "cron-first-evaluation",
            }
        ],
        "skipped": [],
        "failed": [],
    }


def _fixture_paper_policy(pool_id: str, *, policy_id: str | None = None, version: str = "1.0") -> dict[str, Any]:
    """Test-only stand-in for a risk-officer drafted policy (production never supplies fixed limits)."""
    return {
        "risk_policy_id": str(policy_id or f"rp-{pool_id}"),
        "version": str(version or "1.0"),
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 2.0,
        "net_limit": 2.0,
        "max_leverage": 2.0,
        "max_single_name_weight": 0.2,
        "min_cash_reserve_pct": 0.1,
        "allowed_asset_classes": ["equity", "future"],
    }


def _draft_embed(owner_decision_request: Mapping[str, Any]) -> dict[str, Any]:
    """Draft carried by an owner decision request, or the fixture for a retained valid inline policy."""
    found = owner_decision_request.get("risk_officer_draft")
    if isinstance(found, Mapping):
        return dict(found)
    return {"policy": _fixture_paper_policy(
        owner_decision_request["capital_pool_id"], version=owner_decision_request["candidate_risk_policy_version"])}


DRAFTER_CALLS: list[DraftInputs] = []


def _fake_risk_officer_drafter(inputs: DraftInputs) -> DraftResult:
    DRAFTER_CALLS.append(inputs)
    policy = _fixture_paper_policy(inputs.pool_id, policy_id=inputs.policy_id, version=inputs.version)
    prov = {"source": "risk_officer_draft", "risk_persona_id": "persona-risk-officer",
            "input_digest": inputs.input_digest, "requested_profile": inputs.profile,
            "capital_pool_id": inputs.pool_id}
    policy["metadata"] = {"risk_officer_draft": {**prov, "rationale": "test"}}
    return DraftResult(True, policy=policy, provenance=prov)


@pytest.fixture(autouse=True)
def _default_risk_officer_drafter(monkeypatch):
    """Coordinators built by service ingress get the fake drafter (a real turn needs the OpenClaw gateway)."""
    module = sys.modules[PersonaProvisioningCoordinator.__module__]
    monkeypatch.setenv("OPENCLAW_GATEWAY_URL", "http://openclaw-gateway.test")
    monkeypatch.setattr(module, "make_drafter", lambda *a, **k: _fake_risk_officer_drafter)
    DRAFTER_CALLS.clear()


def _coordinator(
    store,
    transport,
    registrar,
    *,
    lease_seconds: int = 60,
    actor_id: str = "pantheon-persona-provisioner",
    governance_actor_id: str | None = None,
    seed_pool_approval: bool = True,
) -> PersonaProvisioningCoordinator:
    if store is not None and store not in FakeOwnerTransport._TEST_STORES:
        FakeOwnerTransport._TEST_STORES.append(store)
    target_tx = getattr(transport, "_others", transport)
    if isinstance(target_tx, FakeOwnerTransport):
        target_tx.seed_pool_policy_approval = bool(seed_pool_approval)
    return PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="test-worker",
        lease_seconds=lease_seconds,
        actor_id=actor_id,
        governance_actor_id=governance_actor_id,
        risk_policy_drafter=_fake_risk_officer_drafter,
    )


def _owner_entry(transport: FakeOwnerTransport, kind: str, registry_id: str) -> dict[str, Any]:
    """The durable owner-side RegistryEntry, by exact ID."""

    return deepcopy(transport.objects[("registry", f"/api/registry/{kind}/{registry_id}")]["entry"])


def _plus_24h(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (parsed + timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _post_payload(
    transport: FakeOwnerTransport,
    path: str,
    *,
    identity: tuple[str, str] | None = None,
) -> dict[str, Any]:
    matches = [call[3] for call in transport.calls if call[0] == "POST" and call[2] == path]
    if identity is not None:
        field, expected = identity
        matches = [payload for payload in matches if payload and payload.get(field) == expected]
    assert len(matches) == 1
    assert matches[0] is not None
    return matches[0]


def test_owner_payload_contract_and_checkpointed_dispatch_admission() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    schedule_calls: list[tuple[str, str, str]] = []

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        schedule_calls.append((persona_id, pool_id, binding_id))
        return _schedule_receipt(persona_id, pool_id, binding_id)

    result = _coordinator(
        store,
        transport,
        registrar,
        lease_seconds=137,
    ).coordinate(record)
    ids = deterministic_provisioning_ids(record)

    assert result.state == "provisioning"
    assert result.current_step == "schedule_registered"
    assert result.result["status"] == "dispatch_admitted"
    assert result.result["paper_running"] is False
    assert "runtime_binding_id" not in result.result
    assert result.references["provisioning_readback_started_at"].endswith("Z")
    assert set(store.checkpoint_lease_seconds) == {137}
    assert schedule_calls == [
        (record.persona_id, ids.capital_pool_id, ids.persona_capital_binding_id)
    ]

    expected_steps = {
        "capital_pool_readback",
        "baseline_strategy_spec_candidate_readback",
        "baseline_approval_proposed_readback",
        "baseline_approval_reviewed_readback",
        "baseline_approval_decided_readback",
        "baseline_strategy_spec_approved_readback",
        "baseline_strategy_artifact_candidate_readback",
        "baseline_strategy_artifact_approval_proposed_readback",
        "baseline_strategy_artifact_approval_reviewed_readback",
        "baseline_strategy_artifact_approval_decided_readback",
        "baseline_strategy_artifact_approved_readback",
        "strategy_spec_candidate_readback",
        "approval_proposed_readback",
        "approval_reviewed_readback",
        "approval_decided_readback",
        "strategy_spec_approved_readback",
        "strategy_artifact_candidate_readback",
        "strategy_artifact_approval_proposed_readback",
        "strategy_artifact_approval_reviewed_readback",
        "strategy_artifact_approval_decided_readback",
        "strategy_artifact_approved_readback",
        "persona_capital_binding_created_readback",
        "persona_capital_binding_active_readback",
        "deployment_plan_readback",
        "deployment_dispatch_admitted_readback",
        "schedule_registered_readback",
    }
    assert expected_steps.issubset(store.checkpoints)

    pool = _post_payload(transport, "/api/capital-pools")
    assert pool["pool_id"] == ids.capital_pool_id
    assert pool["status"] == "active"
    assert pool["single_runtime_enforced"] is True
    assert pool["metadata"]["internal"] is True
    assert pool["metadata"]["requested_by"] == "operator-a"

    baseline = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.baseline_registry_id),
    )
    assert baseline["strategy_id"] == ids.strategy_id
    assert baseline["version"] == ids.baseline_version
    assert baseline["artifact_state"] == "candidate"
    assert baseline["strategy_spec"]["metadata"]["capital_scale_pct"] == 0.0
    assert baseline["strategy_spec"]["metadata"]["fail_closed_baseline"] is True

    baseline_approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.baseline_approval_decision_id),
    )
    assert baseline_approval["target_id"] == ids.baseline_registry_id
    assert baseline_approval["target_version"] == ids.baseline_version

    baseline_artifact = _post_payload(
        transport,
        "/api/registry/strategy-artifacts",
        identity=("registry_id", ids.baseline_strategy_artifact_id),
    )
    assert baseline_artifact["artifact_state"] == "candidate"
    assert baseline_artifact["strategy_artifact"]["artifact_id"] == (
        ids.baseline_strategy_artifact_id
    )
    assert baseline_artifact["strategy_artifact"]["strategy_id"] == ids.strategy_id
    assert baseline_artifact["strategy_artifact"]["lineage"][
        "source_strategy_spec_id"
    ] == ids.baseline_registry_id
    assert baseline_artifact["strategy_artifact"]["strategy_logic"][
        "positive_action"
    ] == "HOLD"
    validate_strategy_artifact(baseline_artifact["strategy_artifact"])

    baseline_artifact_approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.baseline_strategy_artifact_approval_decision_id),
    )
    assert baseline_artifact_approval["target_id"] == ids.baseline_strategy_artifact_id
    assert baseline_artifact_approval["target_version"] == ids.baseline_version

    candidate = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.registry_id),
    )
    assert candidate["registry_id"] == ids.registry_id
    assert candidate["artifact_state"] == "candidate"
    assert candidate["rollback_target"] == ids.baseline_version
    assert candidate["metadata"]["rollback_target_registry_id"] == ids.baseline_registry_id
    assert candidate["strategy_spec"]["metadata"]["persona_id"] == record.persona_id
    assert candidate["strategy_spec"]["metadata"]["capital_pool_id"] == ids.capital_pool_id
    # The forward revision names its exact approved parent and, like the
    # baseline, admits zero live capital in a paper-only execution context.
    # Both inline specs satisfy the canonical StrategySpec schema Registry
    # validates and hashes, from persisted request data only.
    assert candidate["lineage"]["parent_registry_ids"] == [ids.baseline_registry_id]
    assert "parent_registry_ids" not in baseline["lineage"]
    for spec_payload in (baseline, candidate):
        spec = spec_payload["strategy_spec"]
        assert validate_strategy_spec(spec) == []
        assert spec["strategy_id"] == ids.strategy_id
        assert spec["execution_profile"]["execution_mode_hint"] == "paper"
        assert spec["provenance"]["created_at"] == record.created_at
        assert spec["provenance"]["created_by"] == "pantheon-persona-provisioner"
        assert spec["metadata"]["capital_scale_pct"] == 0.0
        assert spec["metadata"]["execution_context"] == "paper"
        assert spec["metadata"]["requested_by"] == "operator-a"
        assert spec_payload["evaluation_summary"]["capital_scale_pct"] == 0.0
        assert spec_payload["evaluation_summary"]["execution_context"] == "paper"
        assert spec_payload["metadata"]["execution_context"] == "paper"
        assert spec_payload["metadata"]["capital_scale_pct"] == 0.0

    approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.approval_decision_id),
    )
    assert approval["target_type"] == "registry_entry"
    assert approval["target_id"] == ids.registry_id
    assert approval["risk_level"] == "low"
    # Every proposal binds the durable owner checksum of its exact target and
    # a finite expiry anchored to that entry's owner creation time; the
    # decision retains both.  The proposal owner is the Governance subject,
    # never the human requester.
    for decision_id, kind, registry_id in (
        (ids.baseline_approval_decision_id, "strategy-specs", ids.baseline_registry_id),
        (
            ids.baseline_strategy_artifact_approval_decision_id,
            "strategy-artifacts",
            ids.baseline_strategy_artifact_id,
        ),
        (ids.approval_decision_id, "strategy-specs", ids.registry_id),
        (
            ids.strategy_artifact_approval_decision_id,
            "strategy-artifacts",
            ids.strategy_artifact_id,
        ),
    ):
        owner_entry = _owner_entry(transport, kind, registry_id)
        proposal = _post_payload(
            transport,
            "/api/governance/approvals",
            identity=("decision_id", decision_id),
        )
        decision = _post_payload(
            transport,
            f"/api/governance/approvals/{decision_id}/decide",
        )
        assert owner_entry["checksum"].startswith("sha256:")
        assert proposal["candidate_digest"] == owner_entry["checksum"]
        assert proposal["expires_at"] == _plus_24h(owner_entry["created_at"])
        assert decision["candidate_digest"] == proposal["candidate_digest"]
        assert decision["expires_at"] == proposal["expires_at"]
        assert proposal["owner_user_id"] == "pantheon-persona-provisioner"
        assert proposal["owner_user_id"] != "operator-a"
        persisted = transport.objects[("governance", f"/api/governance/approvals/{decision_id}")]
        assert persisted["expires_at"] == _plus_24h(owner_entry["created_at"])
        assert persisted["expires_at"] <= _plus_24h(persisted["created_at"])
        assert persisted["candidate_digest"] == owner_entry["checksum"]
    artifact = _post_payload(
        transport,
        "/api/registry/strategy-artifacts",
        identity=("registry_id", ids.strategy_artifact_id),
    )
    assert artifact["registry_id"] == ids.strategy_artifact_id
    assert artifact["rollback_target"] == ids.baseline_strategy_artifact_id
    assert artifact["strategy_artifact"]["artifact_id"] == ids.strategy_artifact_id
    assert artifact["strategy_artifact"]["lineage"]["source_strategy_spec_id"] == ids.registry_id
    assert artifact["strategy_artifact"]["lineage"]["parent_registry_ids"] == [
        ids.baseline_strategy_artifact_id
    ]
    assert artifact["strategy_artifact"]["strategy_logic"]["positive_action"] == "BUY"
    validate_strategy_artifact(artifact["strategy_artifact"])
    artifact_approval = _post_payload(
        transport,
        "/api/governance/approvals",
        identity=("decision_id", ids.strategy_artifact_approval_decision_id),
    )
    assert artifact_approval["target_type"] == "registry_entry"
    assert artifact_approval["target_id"] == ids.strategy_artifact_id
    assert artifact_approval["risk_level"] == "low"
    review = _post_payload(
        transport,
        f"/api/governance/approvals/{ids.approval_decision_id}/review",
    )
    decide = _post_payload(
        transport,
        f"/api/governance/approvals/{ids.approval_decision_id}/decide",
    )
    assert review["actor_role"] == "automated_gate"
    assert decide["actor_role"] == "automated_gate"
    assert review["actor_id"] == decide["actor_id"] == "pantheon-persona-provisioner"
    for bundle in (baseline_artifact, artifact):
        assert bundle["evaluation_summary"]["execution_context"] == "paper"
        assert bundle["evaluation_summary"]["capital_scale_pct"] == 0.0
        assert bundle["metadata"]["execution_context"] == "paper"
        assert bundle["metadata"]["capital_scale_pct"] == 0.0
        assert bundle["metadata"]["persona_id"] == record.persona_id
        # No binding_intent: its schema-mandatory observed_* fields describe a
        # RuntimeBinding this coordinator has neither created nor observed.
        assert "binding_intent" not in bundle["strategy_artifact"]
        assert bundle["strategy_artifact"]["parameters"]["symbols"] == (
            candidate["strategy_spec"]["market_scope"]["symbols"]
        )

    # Registry approval: the approver is the Governance decision, the actor
    # is the transport subject (never a body field), and the CAS base is the
    # exact candidate row read back before the transition.
    for kind, registry_id, decision_id in (
        ("strategy-specs", ids.registry_id, ids.approval_decision_id),
        (
            "strategy-artifacts",
            ids.strategy_artifact_id,
            ids.strategy_artifact_approval_decision_id,
        ),
    ):
        advance = _post_payload(transport, f"/api/registry/{kind}/{registry_id}/advance")
        assert "approver" not in advance
        assert advance["target_state"] == "approved"
        assert advance["approval_decision_id"] == decision_id
        assert advance["expected_artifact_state"] == "candidate"
        approved_entry = _owner_entry(transport, kind, registry_id)
        assert advance["expected_version"] == approved_entry["version"]
        assert advance["expected_updated_at"] < approved_entry["updated_at"] or (
            advance["expected_updated_at"] == approved_entry["updated_at"]
        )
        assert advance["command_key"] == (
            f"persona-provisioning:{ids.token}:advance:{registry_id}:approved:"
            + advance["command_key"].rsplit(":", 1)[1]
        )
        assert approved_entry["approver"] == "pantheon-persona-provisioner"
        assert approved_entry["approval_evidence"]["decision_id"] == decision_id

    binding = _post_payload(transport, "/api/bindings")
    assert binding["binding_id"] == ids.persona_capital_binding_id
    assert binding["persona_id"] == record.persona_id
    assert binding["capital_pool_id"] == ids.capital_pool_id
    assert binding["role"] == "paper_owner"
    assert binding["allowed_deployment_scope"] == "paper"

    plan = _post_payload(transport, "/api/deployment/plans")
    assert "binding_id" not in plan
    assert plan["approval_decision_id"] == ids.strategy_artifact_approval_decision_id
    assert plan["registry_id"] == ids.strategy_artifact_id
    assert plan["metadata"]["persona_capital_binding_id"] == (
        ids.persona_capital_binding_id
    )
    assert plan["metadata"]["strategy_spec_registry_id"] == ids.registry_id
    assert plan["metadata"]["strategy_artifact_id"] == ids.strategy_artifact_id
    # Deployment owns the Registry/Governance reads; the coordinator sends the
    # exact IDs and never a client-side snapshot of owner state.
    assert "registry_entry" not in plan
    assert "approval_decision" not in plan
    assert plan["rollback"]["target_artifact_id"] == ids.baseline_strategy_artifact_id
    assert plan["rollback"]["target_version"] == ids.baseline_version
    assert plan["rollback"]["action_type"] == "pause_then_replace"

    dispatch_path = f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch"
    dispatch = _post_payload(transport, dispatch_path)
    assert dispatch["saga_id"] == ids.deployment_saga_id
    assert dispatch["metadata"]["persona_capital_binding_id"] == ids.persona_capital_binding_id
    assert "registry_entry" not in dispatch
    assert "runtime_id" not in dispatch
    assert "runtime_binding_id" not in dispatch
    assert {call[1] for call in transport.calls} == {
        "capital",
        "registry",
        "governance",
        "deployment",
    }

    create_read_paths = {
        "/api/capital-pools": f"/api/capital-pools/{ids.capital_pool_id}",
        "/api/bindings": f"/api/bindings/{ids.persona_capital_binding_id}",
        "/api/deployment/plans": f"/api/deployment/plans/{ids.deployment_plan_id}",
    }
    for index, (method, owner, path, payload) in enumerate(transport.calls):
        if method not in {"POST", "PATCH"}:
            continue
        preceding = transport.calls[:index]
        read_path = create_read_paths.get(path, path)
        if path == "/api/registry/strategy-specs":
            read_path = f"/api/registry/strategy-specs/{payload['registry_id']}"
        elif path == "/api/registry/strategy-artifacts":
            read_path = f"/api/registry/strategy-artifacts/{payload['registry_id']}"
        elif path == "/api/governance/approvals":
            read_path = f"/api/governance/approvals/{payload['decision_id']}"
        if path.endswith("/review") or path.endswith("/decide") or path.endswith("/advance"):
            read_path = path.rsplit("/", 1)[0]
        elif path.endswith("/activate") or path.endswith("/status"):
            read_path = path.rsplit("/", 1)[0]
        elif path.endswith("/dispatch"):
            read_path = f"/api/deployment/sagas/{ids.deployment_saga_id}"
        assert any(
            call[0] == "GET" and call[1] == owner and call[2] == read_path
            for call in preceding
        )


def test_mutation_payloads_parse_with_authoritative_owner_wire_models() -> None:
    from services.capital.models import (
        ActivateBindingRequest,
        CreateBindingRequest,
        CreateCapitalPoolRequest,
    )
    from services.deployment.models import (
        CreateDeploymentPlanRequest,
        DispatchDeploymentPlanRequest,
    )
    from services.governance.models import (
        AcceptReviewRequest,
        DecideRequest,
        ProposeApprovalRequest,
    )
    from services.registry.service import (
        AdvanceRequest,
        StrategyArtifactRegisterRequest,
        StrategySpecRegisterRequest,
    )

    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    transport = FakeOwnerTransport()
    _coordinator(store, transport, _schedule_receipt).coordinate(record)

    CreateCapitalPoolRequest(**_post_payload(transport, "/api/capital-pools"))
    CreateBindingRequest(**_post_payload(transport, "/api/bindings"))
    ActivateBindingRequest(
        **_post_payload(
            transport,
            f"/api/bindings/{ids.persona_capital_binding_id}/activate",
        )
    )

    for registry_id in (ids.baseline_registry_id, ids.registry_id):
        StrategySpecRegisterRequest(
            **_post_payload(
                transport,
                "/api/registry/strategy-specs",
                identity=("registry_id", registry_id),
            )
        )
        AdvanceRequest(
            **_post_payload(
                transport,
                f"/api/registry/strategy-specs/{registry_id}/advance",
            )
        )

    for registry_id in (
        ids.baseline_strategy_artifact_id,
        ids.strategy_artifact_id,
    ):
        artifact_registration = StrategyArtifactRegisterRequest(
            **_post_payload(
                transport,
                "/api/registry/strategy-artifacts",
                identity=("registry_id", registry_id),
            )
        )
        validate_strategy_artifact(artifact_registration.strategy_artifact)
        AdvanceRequest(
            **_post_payload(
                transport,
                f"/api/registry/strategy-artifacts/{registry_id}/advance",
            )
        )

    for decision_id in (
        ids.baseline_approval_decision_id,
        ids.baseline_strategy_artifact_approval_decision_id,
        ids.approval_decision_id,
        ids.strategy_artifact_approval_decision_id,
    ):
        ProposeApprovalRequest(
            **_post_payload(
                transport,
                "/api/governance/approvals",
                identity=("decision_id", decision_id),
            )
        )
        review = AcceptReviewRequest(
            **_post_payload(
                transport,
                f"/api/governance/approvals/{decision_id}/review",
            )
        )
        decision = DecideRequest(
            **_post_payload(
                transport,
                f"/api/governance/approvals/{decision_id}/decide",
            )
        )
        assert review.actor_role.value == "automated_gate"
        assert decision.actor_role.value == "automated_gate"

    plan = CreateDeploymentPlanRequest(
        **_post_payload(transport, "/api/deployment/plans")
    )
    dispatch = DispatchDeploymentPlanRequest(
        **_post_payload(
            transport,
            f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch",
        )
    )
    assert plan.rollback is not None
    assert plan.rollback.target_artifact_id == ids.baseline_strategy_artifact_id
    assert plan.rollback.target_version == ids.baseline_version
    assert plan.binding_id is None
    assert dispatch.saga_id == ids.deployment_saga_id

    # The forbidden client snapshots are gone from both wire requests; the owner
    # objects are resolved by exact ID from owner state, as Deployment does.
    assert not hasattr(plan, "registry_entry")
    assert not hasattr(plan, "approval_decision")
    assert not hasattr(dispatch, "registry_entry")
    owner_registry_entry = transport._owner_registry_entry(plan.registry_id)
    owner_approval_decision = transport.objects[
        ("governance", f"/api/governance/approvals/{plan.approval_decision_id}")
    ]
    assert owner_registry_entry["artifact_type"] == "execution_bundle"
    assert owner_registry_entry["approval_decision_id"] == plan.approval_decision_id
    assert owner_approval_decision["decision"] == "approved"
    assert owner_approval_decision["target_id"] == ids.strategy_artifact_id

    from services.control_plane.governance.deployment_plan import (
        DeploymentScale,
        RollbackRef,
        StagePlanner,
    )

    domain_plan = StagePlanner().create_plan(
        plan_id=plan.plan_id,
        approval_decision_id=plan.approval_decision_id,
        approval_decision=owner_approval_decision,
        registry_entry=owner_registry_entry,
        capital_pool_id=plan.capital_pool_id,
        target_stage=plan.target_stage.value,
        current_stage=plan.current_stage.value if plan.current_stage else None,
        created_by=plan.created_by,
        sponsor_persona_id=plan.sponsor_persona_id,
        binding_id=plan.binding_id,
        scale=(
            DeploymentScale(**plan.scale.model_dump()) if plan.scale is not None else None
        ),
        rollback=(
            RollbackRef(**plan.rollback.model_dump(mode="json"))
            if plan.rollback is not None
            else None
        ),
        pre_checks=list(plan.pre_checks),
        post_checks=list(plan.post_checks),
        metadata=plan.metadata,
        status=plan.status.value,
    )
    assert domain_plan.validate() == []


def test_response_loss_restart_and_duplicate_converge_without_duplicate_mutations() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    dispatch_path = f"/api/deployment/plans/{ids.deployment_plan_id}/dispatch"
    transport = FakeOwnerTransport(
        response_loss={
            "/api/capital-pools",
            "/api/registry/strategy-specs",
            "/api/registry/strategy-artifacts",
            "/api/governance/approvals",
            f"/api/registry/strategy-specs/{ids.baseline_registry_id}/advance",
            (
                "/api/registry/strategy-artifacts/"
                f"{ids.baseline_strategy_artifact_id}/advance"
            ),
            "/api/bindings",
            dispatch_path,
        }
    )
    schedule_calls: list[tuple[str, str, str]] = []

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        schedule_calls.append((persona_id, pool_id, binding_id))
        return _schedule_receipt(persona_id, pool_id, binding_id)

    first = _coordinator(store, transport, registrar).coordinate(record)
    readback_started_at = first.references["provisioning_readback_started_at"]
    first_mutations = transport.mutations.copy()
    restarted = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="restarted-worker",
    )
    second = restarted.coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert first.state == second.state == "provisioning"
    assert first.result == second.result
    assert second.references["provisioning_readback_started_at"] == readback_started_at
    assert schedule_calls == [
        (record.persona_id, ids.capital_pool_id, ids.persona_capital_binding_id)
    ]
    assert transport.mutations == first_mutations
    assert transport.mutations[("deployment", dispatch_path)] == 1


def test_safe_early_failure_retries_without_replaying_committed_owner_writes() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    proposal_path = "/api/governance/approvals"
    transport = FakeOwnerTransport(mutation_failure={proposal_path})
    schedule_calls = 0

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        nonlocal schedule_calls
        schedule_calls += 1
        return _schedule_receipt(persona_id, pool_id, binding_id)

    first = _coordinator(store, transport, registrar).coordinate(record)

    assert first.state == "failed"
    assert first.error["failed_step"] == "baseline_approval_proposed"
    assert first.compensation is None
    assert "persona_capital_binding_created" not in first.references
    assert "deployment_plan" not in first.references

    transport.mutation_failure.remove(proposal_path)
    second = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="safe-retry-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert second.state == "provisioning"
    assert second.current_step == "schedule_registered"
    assert second.attempt_count == 2
    assert schedule_calls == 1
    assert transport.mutations[("capital", "/api/capital-pools")] == 1
    baseline_posts = [
        call
        for call in transport.calls
        if call[0] == "POST"
        and call[2] == "/api/registry/strategy-specs"
        and call[3]["registry_id"] == ids.baseline_registry_id
    ]
    assert len(baseline_posts) == 1


def test_compensation_reconciler_never_restarts_safe_early_failure() -> None:
    store, record = _record_and_store()
    proposal_path = "/api/governance/approvals"
    transport = FakeOwnerTransport(mutation_failure={proposal_path})

    first = _coordinator(store, transport, lambda *_args: {}).coordinate(record)
    mutations_after_failure = transport.mutations.copy()
    transport.mutation_failure.remove(proposal_path)

    reconciled = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=lambda *_args: {},
        lease_owner="compensation-only-worker",
    ).reconcile_failure_compensation(first)

    assert reconciled.state == "failed"
    assert reconciled.error["failed_step"] == "baseline_approval_proposed"
    assert reconciled.attempt_count == 1
    assert transport.mutations == mutations_after_failure


def test_unclassified_failed_record_stays_terminal_without_forward_replay() -> None:
    store, record = _record_and_store()
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="failed-record-writer",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "unclassified_failure"
    failed.error = {"failed_step": "unknown", "terminal_reason": "ambiguous state"}
    failed = store.checkpoint(
        failed,
        lease_owner="failed-record-writer",
        lease_seconds=60,
    )
    store.release(
        failed,
        lease_owner="failed-record-writer",
        lease_seconds=60,
    )
    transport = FakeOwnerTransport()
    schedule_called = False

    def registrar(*_args):
        nonlocal schedule_called
        schedule_called = True
        raise AssertionError("unclassified terminal failure must not replay")

    replay = _coordinator(store, transport, registrar).coordinate(record)

    assert replay.state == "failed"
    assert replay.attempt_count == 1
    assert transport.calls == []
    assert schedule_called is False


def test_compensation_response_loss_and_restart_converge_from_saga_readback() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    saga_path = f"/api/deployment/sagas/{ids.deployment_saga_id}"
    failure_path = f"{saga_path}/failure"
    transport = FakeOwnerTransport(response_loss={failure_path})
    schedule_calls = 0

    def registrar(*_args):
        nonlocal schedule_calls
        schedule_calls += 1
        raise ConnectionError("cron unavailable after dispatch")

    first = _coordinator(store, transport, registrar).coordinate(record)
    mutations_after_first = transport.mutations.copy()

    assert first.state == "failed"
    assert first.compensation["status"] == "pending"
    assert first.compensation["deployment"]["status"] == "requested"
    assert transport.mutations[("deployment", failure_path)] == 1

    restarted = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="compensation-restart-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert restarted.state == "failed"
    assert restarted.compensation["status"] == "pending"
    assert transport.mutations == mutations_after_first
    assert schedule_calls == 1

    saga = transport.objects[("deployment", saga_path)]
    saga.update(status="failed", current_step="compensated")
    terminal = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="compensation-terminal-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert terminal.state == "compensated"
    assert terminal.compensation["status"] == "completed"
    assert terminal.compensation["deployment"]["status"] == "completed"
    assert terminal.references["deployment_compensation_readback"]["current_step"] == (
        "compensated"
    )
    assert transport.mutations == mutations_after_first
    assert schedule_calls == 1


@pytest.mark.parametrize("failure_mode", ["dry_run", "unavailable"])
def test_schedule_failure_is_terminal_and_suspends_partial_admission(failure_mode: str) -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    schedule_calls = 0

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        nonlocal schedule_calls
        schedule_calls += 1
        if failure_mode == "unavailable":
            raise ConnectionError("cron gateway unavailable")
        return {
            "persona_id": persona_id,
            "capital_pool_id": pool_id,
            "binding_id": binding_id,
            "mode": "dry_run",
            "registered": [{"workflow_id": FIRST_EVALUATION_WORKFLOW_ID}],
            "skipped": [],
        }

    result = _coordinator(store, transport, registrar).coordinate(record)
    ids = deterministic_provisioning_ids(record)
    binding_path = f"/api/bindings/{ids.persona_capital_binding_id}"

    assert result.state == "failed"
    assert result.error["failed_step"] == "schedule_registration"
    assert result.error["terminal_reason"]
    assert result.compensation["status"] == "pending"
    assert result.compensation["action"] == "suspend_persona_capital_binding"
    assert result.compensation["deployment"]["status"] == "requested"
    assert result.references["deployment_compensation_readback"]["status"] == (
        "compensating"
    )
    assert result.references["persona_capital_binding_suspended"]["status"] == "suspended"
    assert transport.objects[("capital", binding_path)]["status"] == "suspended"
    assert transport.mutations[("capital", f"{binding_path}/status")] == 1

    mutations_after_failure = transport.mutations.copy()
    replay = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="terminal-replay-worker",
    ).coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert replay.state == "failed"
    assert replay.compensation["status"] == "pending"
    assert schedule_calls == 1
    assert transport.mutations == mutations_after_failure


def test_dry_run_is_pure_and_does_not_touch_store_transport_or_registrar() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    called = False

    def registrar(*_args):
        nonlocal called
        called = True
        raise AssertionError("dry-run must not call the schedule registrar")

    result = _coordinator(store, transport, registrar).coordinate(record, dry_run=True)

    assert result.current_step == "dry_run"
    assert result.result["mutations_performed"] is False
    assert result.result["ids"] == deterministic_provisioning_ids(record).to_dict()
    assert store.checkpoints == []
    assert store.get(record.tenant_id, record.idempotency_key).state == "reserved"
    assert transport.calls == []
    assert called is False


def test_activation_failure_revokes_pending_binding_fail_closed() -> None:
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)
    activation_path = f"/api/bindings/{ids.persona_capital_binding_id}/activate"
    transport = FakeOwnerTransport(mutation_failure={activation_path})

    result = _coordinator(
        store,
        transport,
        lambda *_args: pytest.fail("schedule must not run after activation failure"),
    ).coordinate(record)

    binding_path = f"/api/bindings/{ids.persona_capital_binding_id}"
    assert result.state == "compensated"
    assert result.error["failed_step"] == "persona_capital_binding_active"
    assert result.compensation["action"] == "revoke_pending_persona_capital_binding"
    assert result.compensation["resulting_status"] == "revoked"
    assert result.references["persona_capital_binding_revoked"]["status"] == "revoked"
    assert transport.objects[("capital", binding_path)]["status"] == "revoked"


def test_schedule_exact_authoritative_readback_accepts_job_name_only_skips() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        return {
            "persona_id": persona_id,
            "capital_pool_id": pool_id,
            "binding_id": binding_id,
            "mode": "gateway_rpc",
            "registered": [],
            "skipped": ["pantheon-persona-first-evaluation-persona-a"],
            "authoritative_readback": {
                "persona_id": persona_id,
                "workflow_id": FIRST_EVALUATION_WORKFLOW_ID,
                "registered": True,
            },
        }

    result = _coordinator(store, transport, registrar).coordinate(record)

    assert result.state == "provisioning"
    assert result.current_step == "schedule_registered"


def test_safe_early_failure_name_error_capital_pool_replays_successfully() -> None:
    """Verify exact historical failure (NameError at capital_pool with failed_step present,
    empty references, null compensation) safely replays through forward coordination.
    """
    store, record = _record_and_store()
    ids = deterministic_provisioning_ids(record)

    # Seed the exact historical failure structure:
    # error dict has error_type=NameError, terminal_reason, and failed_step=capital_pool
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="prior-failed-run",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "capital_pool_failed"
    failed.error = {
        "failed_at": "2026-09-04T11:52:48Z",
        "error_type": "NameError",
        "failed_step": "capital_pool",
        "terminal_reason": "name 'urllib_error' is not defined",
        "compensation_error": "name 'urllib_error' is not defined",
    }
    failed.references = {}
    failed.compensation = None
    failed.attempt_count = 4
    failed = store.checkpoint(failed, lease_owner="prior-failed-run", lease_seconds=60)
    store.release(failed, lease_owner="prior-failed-run", lease_seconds=60)

    transport = FakeOwnerTransport()
    schedule_calls: list[tuple[str, str, str]] = []

    def registrar(persona_id: str, pool_id: str, binding_id: str):
        schedule_calls.append((persona_id, pool_id, binding_id))
        return _schedule_receipt(persona_id, pool_id, binding_id)

    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=registrar,
        lease_owner="replay-coordinator-1",
    )

    replayed = coordinator.coordinate(store.get(record.tenant_id, record.idempotency_key))

    assert replayed.state == "provisioning"
    assert replayed.current_step == "schedule_registered"
    assert replayed.error is None
    assert replayed.compensation is None
    assert replayed.attempt_count == 5
    assert len(schedule_calls) == 1
    assert "capital_pool" in replayed.references
    assert "persona_capital_binding_created" in replayed.references
    assert "deployment_dispatch" in replayed.references
    assert "first_evaluation_schedule" in replayed.references


def test_safe_early_failure_rejects_unsafe_binding_side_effects() -> None:
    """Fail-closed: records with committed binding references must NEVER forward replay."""
    store, record = _record_and_store()
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="unsafe-record-writer",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "persona_capital_binding_created_failed"
    failed.error = {
        "failed_step": "persona_capital_binding_created",
        "terminal_reason": "connection timeout during binding creation",
    }
    failed.references = {
        "capital_pool": {"pool_id": "pool-1", "status": "active"},
        "persona_capital_binding_created": {"binding_id": "pcb-1", "status": "pending"},
    }
    failed.compensation = None
    failed = store.checkpoint(failed, lease_owner="unsafe-record-writer", lease_seconds=60)
    store.release(failed, lease_owner="unsafe-record-writer", lease_seconds=60)

    transport = FakeOwnerTransport()
    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=lambda *_args: pytest.fail("Must not schedule"),
        lease_owner="replay-attempt",
    )

    result = coordinator.coordinate(store.get(record.tenant_id, record.idempotency_key))

    # Must remain terminal and NOT perform forward provisioning
    assert result.state in {"failed", "compensated"}
    assert "deployment_dispatch" not in result.references
    assert "schedule_registration" not in result.references


def test_safe_early_failure_rejects_non_null_compensation() -> None:
    """Fail-closed: records with existing compensation must NOT forward replay."""
    store, record = _record_and_store()
    failed = store.acquire(
        record.tenant_id,
        record.idempotency_key,
        lease_owner="compensated-writer",
        lease_seconds=60,
    )
    assert failed is not None
    failed.state = "failed"
    failed.current_step = "capital_pool_failed"
    failed.error = {
        "failed_step": "capital_pool",
        "terminal_reason": "explicit rejection",
    }
    failed.references = {}
    failed.compensation = {"status": "completed", "action": "noop"}
    failed = store.checkpoint(failed, lease_owner="compensated-writer", lease_seconds=60)
    store.release(failed, lease_owner="compensated-writer", lease_seconds=60)

    transport = FakeOwnerTransport()
    coordinator = PersonaProvisioningCoordinator(
        store=store,
        transport=transport,
        schedule_registrar=lambda *_args: pytest.fail("Must not schedule"),
        lease_owner="replay-attempt",
    )

    result = coordinator.coordinate(store.get(record.tenant_id, record.idempotency_key))
    assert result.state == "failed"
    assert result.compensation == {"status": "completed", "action": "noop"}
    assert "capital_pool" not in result.references


def test_coordinator_propagates_explicit_market_to_artifact_and_deployment_metadata() -> None:
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-us",
        idempotency_key="create-persona-us",
        request_hash="sha256:persona-us-request",
        normalized_name="trader us",
        persona_id="persona-us",
        request_payload={
            "name": "Trader US",
            "risk": "low",
            "market": "US",
            "symbols": ["SPY"],
            "requested_by": "operator-us",
            "mandate": "US paper momentum",
        },
    )
    assert created
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    assert result.current_step == "schedule_registered"

    ids = deterministic_provisioning_ids(record)

    # Verify forward StrategyArtifact
    artifact_view = transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")]
    forward_entry = artifact_view["entry"]
    forward_artifact = forward_entry["metadata"]["strategy_artifact"]
    assert forward_artifact["parameters"]["market"] == "US"
    assert "market" in forward_artifact["mutation_surface"]["immutable_parameters"]
    assert forward_entry["metadata"]["market"] == "US"

    # Verify baseline StrategyArtifact
    baseline_view = transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids.baseline_strategy_artifact_id}")]
    baseline_entry = baseline_view["entry"]
    baseline_artifact = baseline_entry["metadata"]["strategy_artifact"]
    assert baseline_artifact["parameters"]["market"] == "US"
    assert "market" in baseline_artifact["mutation_surface"]["immutable_parameters"]
    assert baseline_entry["metadata"]["market"] == "US"

    # Verify StrategySpec
    spec_view = transport.objects[("registry", f"/api/registry/strategy-specs/{ids.registry_id}")]
    assert spec_view["entry"]["metadata"]["market"] == "US"

    # Verify PersonaCapitalBinding
    binding_view = transport.objects[("capital", f"/api/bindings/{ids.persona_capital_binding_id}")]
    assert binding_view["metadata"]["market"] == "US"

    # Verify DeploymentPlan
    plan_view = transport.objects[("deployment", f"/api/deployment/plans/{ids.deployment_plan_id}")]
    assert plan_view["metadata"]["market"] == "US"


def test_coordinator_rejects_unsupported_market_context_fail_closed() -> None:
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-invalid",
        idempotency_key="create-persona-invalid",
        request_hash="sha256:persona-invalid",
        normalized_name="trader invalid",
        persona_id="persona-invalid",
        request_payload={
            "name": "Trader Invalid",
            "market": "MARS",
            "symbols": ["SPY"],
        },
    )
    assert created
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "failed"
    assert result.error is not None
    assert result.error["failed_step"] == "capital_pool"
    assert "unsupported market context 'MARS'" in result.error["terminal_reason"]


def test_coordinator_rejects_contradictory_market_and_symbols() -> None:
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-conflict",
        idempotency_key="create-persona-conflict",
        request_hash="sha256:persona-conflict",
        normalized_name="trader conflict",
        persona_id="persona-conflict",
        request_payload={
            "name": "Trader Conflict",
            "market": "US",
            "symbols": ["2330.TW"],
        },
    )
    assert created
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "failed"
    assert result.error is not None
    assert result.error["failed_step"] == "capital_pool"
    assert "contradicts explicit market 'US'" in result.error["terminal_reason"]


def test_coordinator_dry_run_rejects_invalid_market() -> None:
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-dry",
        idempotency_key="create-persona-dry",
        request_hash="sha256:persona-dry",
        normalized_name="trader dry",
        persona_id="persona-dry",
        request_payload={
            "name": "Trader Dry",
            "market": "INVALID",
        },
    )
    assert created
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    with pytest.raises(Exception, match="unsupported market context 'INVALID'"):
        coordinator.coordinate(record, dry_run=True)


def test_coordinator_omits_market_when_unspecified_preserving_legacy_behavior() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "provisioning"

    ids = deterministic_provisioning_ids(record)
    artifact_view = transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")]
    forward_entry = artifact_view["entry"]
    forward_artifact = forward_entry["metadata"]["strategy_artifact"]
    assert "market" not in forward_artifact["parameters"]
    assert "market" not in forward_artifact["mutation_surface"]["immutable_parameters"]
    assert "market" not in forward_entry["metadata"]


def test_coordinator_accepts_bare_symbols_with_explicit_market_without_guessing() -> None:
    # ABNB / GBTC in US market
    store = TrackingStore()
    record, created = store.reserve(
        tenant_id="tenant-us-bare",
        idempotency_key="create-persona-us-bare",
        request_hash="sha256:persona-us-bare-request",
        normalized_name="trader us bare",
        persona_id="persona-us-bare",
        request_payload={
            "name": "Trader US Bare",
            "risk": "low",
            "market": "US",
            "symbols": ["ABNB", "GBTC"],
            "requested_by": "operator-us",
            "mandate": "US paper momentum bare symbols",
        },
    )
    assert created
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    ids = deterministic_provisioning_ids(record)
    artifact_view = transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")]
    forward_artifact = artifact_view["entry"]["metadata"]["strategy_artifact"]
    assert forward_artifact["parameters"]["market"] == "US"
    assert forward_artifact["parameters"]["symbols"] == ["ABNB", "GBTC"]

    # EURUSD in FX market
    record_fx, created_fx = store.reserve(
        tenant_id="tenant-fx-bare",
        idempotency_key="create-persona-fx-bare",
        request_hash="sha256:persona-fx-bare-request",
        normalized_name="trader fx bare",
        persona_id="persona-fx-bare",
        request_payload={
            "name": "Trader FX Bare",
            "risk": "low",
            "market": "FX",
            "symbols": ["EURUSD"],
            "currency": "USD",
            "requested_by": "operator-fx",
            "mandate": "FX paper momentum bare symbols",
        },
    )
    assert created_fx
    result_fx = coordinator.coordinate(record_fx)
    assert result_fx.state == "provisioning"
    ids_fx = deterministic_provisioning_ids(record_fx)
    artifact_view_fx = transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids_fx.strategy_artifact_id}")]
    forward_artifact_fx = artifact_view_fx["entry"]["metadata"]["strategy_artifact"]
    assert forward_artifact_fx["parameters"]["market"] == "FX"
    assert forward_artifact_fx["parameters"]["symbols"] == ["EURUSD"]


def test_transition_legacy_persona_market_preserves_parent_and_creates_approved_child_revision() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    # Initial coordinate runs legacy flow without market
    result = coordinator.coordinate(record)
    assert result.state == "provisioning"

    ids = deterministic_provisioning_ids(record)
    parent_path = f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}"
    parent_view_before = copy.deepcopy(transport.objects[("registry", parent_path)])
    parent_artifact_before = parent_view_before["entry"]["metadata"]["strategy_artifact"]
    assert "market" not in parent_artifact_before["parameters"]

    # Transition legacy persona artifact to explicit market 'US'
    transitioned = coordinator.transition_legacy_persona_market(result, market="US")
    assert transitioned.state == "provisioning"

    # 1. Parent artifact is byte-identical and completely unmutated
    parent_view_after = transport.objects[("registry", parent_path)]
    assert parent_view_after == parent_view_before

    # 2. Child artifact is created, approved, cites parent, and carries explicit market
    child_id = f"{ids.strategy_artifact_id}-rev1"
    child_path = f"/api/registry/strategy-artifacts/{child_id}"
    child_view = transport.objects[("registry", child_path)]
    child_entry = child_view["entry"]
    assert child_entry["artifact_state"] == "approved"
    assert child_entry["version"] == "1.0.1"

    child_artifact = child_entry["metadata"]["strategy_artifact"]
    assert child_artifact["artifact_id"] == child_id
    assert child_artifact["version"] == "1.0.1"
    assert child_artifact["parameters"]["market"] == "US"
    assert child_artifact["parameters"]["symbols"] == ["SPY"]
    assert "market" in child_artifact["mutation_surface"]["immutable_parameters"]
    assert child_artifact["lineage"]["parent_registry_ids"] == [ids.strategy_artifact_id]

    # 3. Governance ApprovalDecision exists and is approved
    child_decision_id = f"apv-transition-{ids.token}"
    decision_path = f"/api/governance/approvals/{child_decision_id}"
    decision_view = transport.objects[("governance", decision_path)]
    assert decision_view["decision_state"] == "decided"
    assert decision_view["decision"] == "approved"
    assert decision_view["target_id"] == child_id

    # 4. Record references and result updated
    assert "legacy_strategy_artifact_approved" in transitioned.references
    assert (
        transitioned.references["legacy_strategy_artifact_approved"]["entry"]["registry_id"]
        == ids.strategy_artifact_id
    )
    assert (
        transitioned.references["strategy_artifact_approved"]["entry"]["registry_id"]
        == child_id
    )
    assert transitioned.result["strategy_artifact_id"] == child_id
    assert transitioned.result["market"] == "US"


def test_transition_legacy_persona_market_fails_closed_when_market_missing_or_contradictory() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "provisioning"

    # 1. Missing market fails closed when neither request_payload nor parameter provides it
    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="No authoritative owner market context found",
    ):
        coordinator.transition_legacy_persona_market(result, market=None)

    # 2. Empty/whitespace market parameter fails closed
    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="Supplied market parameter must not be empty or whitespace",
    ):
        coordinator.transition_legacy_persona_market(result, market="   ")

    # 3. Invalid market fails closed
    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="unsupported market context 'INVALID'",
    ):
        coordinator.transition_legacy_persona_market(result, market="INVALID")

    # 4. Contradiction between caller-supplied market and persisted owner request fails closed
    record_with_us_request = store.get(record.tenant_id, record.idempotency_key)
    record_with_us_request.request_payload["market"] = "US"

    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="Contradictory market context: supplied market 'TW' differs from persisted owner request 'US'",
    ):
        coordinator.transition_legacy_persona_market(record_with_us_request, market="TW")

    # 5. Caller market matching persisted request succeeds
    transitioned_consistent = coordinator.transition_legacy_persona_market(
        record_with_us_request, market="US"
    )
    assert transitioned_consistent.result["market"] == "US"

    # 6. Idempotent return when parent artifact already has the target market
    idempotent_return = coordinator.transition_legacy_persona_market(
        transitioned_consistent, market="US"
    )
    assert idempotent_return == transitioned_consistent

    # 7. Contradiction between target market and already-set parent artifact market fails closed
    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="Contradictory market context: target market 'TW' contradicts parent artifact market 'US'",
    ):
        coordinator.transition_legacy_persona_market(
            transitioned_consistent, market="TW"
        )


def test_transition_legacy_persona_market_uses_persisted_owner_request_when_market_param_omitted() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "provisioning"

    # Add market 'US' to the persisted owner request payload
    record_with_market = store.get(record.tenant_id, record.idempotency_key)
    record_with_market.request_payload["market"] = "US"

    # Calling with market=None automatically uses persisted owner request 'US'
    transitioned = coordinator.transition_legacy_persona_market(record_with_market, market=None)
    assert transitioned.result["market"] == "US"
    ids = deterministic_provisioning_ids(record)
    child_id = f"{ids.strategy_artifact_id}-rev1"
    child_path = f"/api/registry/strategy-artifacts/{child_id}"
    child_entry = transport.objects[("registry", child_path)]["entry"]
    assert child_entry["metadata"]["strategy_artifact"]["parameters"]["market"] == "US"





def _symbolless_record(market: str) -> tuple[TrackingStore, ProvisioningRecord]:
    return _record_and_store({"market": market})


def test_coordinator_tw_without_symbols_fails_before_any_owner_write() -> None:
    store, record = _symbolless_record("TW")
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)

    assert result.state == "failed"
    assert result.error is not None
    assert result.error["failed_step"] == "capital_pool"
    assert "symbols are required for market TW" in result.error["terminal_reason"]
    assert not [key for key in transport.objects if key[0] in ("capital", "registry")]


def test_coordinator_dry_run_rejects_crypto_without_symbols() -> None:
    store, record = _symbolless_record("CRYPTO")
    coordinator = _coordinator(store, FakeOwnerTransport(), _schedule_receipt)

    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="symbols are required for market CRYPTO",
    ):
        coordinator.coordinate(record, dry_run=True)


def test_coordinator_us_without_symbols_keeps_legacy_spy_default() -> None:
    store, record = _symbolless_record("US")
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)

    assert result.state == "provisioning"
    ids = deterministic_provisioning_ids(record)
    view = transport.objects[("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}")]
    parameters = view["entry"]["metadata"]["strategy_artifact"]["parameters"]
    assert parameters["symbols"] == ["SPY"]
    assert parameters["market"] == "US"


def test_transition_legacy_spy_persona_to_tw_is_refused_without_child_revision() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)
    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    registry_before = copy.deepcopy(
        {key: value for key, value in transport.objects.items() if key[0] == "registry"}
    )

    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="symbols are required for market TW",
    ):
        coordinator.transition_legacy_persona_market(result, market="TW")

    registry_after = {key: value for key, value in transport.objects.items() if key[0] == "registry"}
    assert registry_after == registry_before
    assert not [key for key in transport.objects if key[1].endswith("-rev1")]


def test_transition_persisted_tw_market_without_symbols_fails_at_entry_call() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)
    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    stored = store.get(record.tenant_id, record.idempotency_key)
    stored.request_payload["market"] = "TW"

    for supplied in (None, "US"):
        with pytest.raises(
            PersonaProvisioningCoordinationError,
            match="symbols are required for market TW",
        ):
            coordinator.transition_legacy_persona_market(stored, market=supplied)

    assert not [key for key in transport.objects if key[1].endswith("-rev1")]


def test_coordinator_derives_settlement_currency_for_tw_and_us() -> None:
    # TW request without currency defaults pool to TWD
    store, record = _record_and_store({"market": "TW", "symbols": ["2330.TW"]})
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)
    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    assert _post_payload(transport, "/api/capital-pools")["currency"] == "TWD"
    assert result.references["capital_pool"]["currency"] == "TWD"

    # TW request with explicit currency "TWD" posts TWD
    store_tw, record_tw = _record_and_store(
        {"market": "TW", "symbols": ["2330.TW"], "currency": "TWD"}
    )
    transport_tw = FakeOwnerTransport()
    coordinator_tw = _coordinator(store_tw, transport_tw, _schedule_receipt)
    result_tw = coordinator_tw.coordinate(record_tw)
    assert result_tw.state == "provisioning"
    assert _post_payload(transport_tw, "/api/capital-pools")["currency"] == "TWD"
    assert result_tw.references["capital_pool"]["currency"] == "TWD"

    # US request with ["SPY"] posts USD
    store_us, record_us = _record_and_store({"market": "US", "symbols": ["SPY"]})
    transport_us = FakeOwnerTransport()
    coordinator_us = _coordinator(store_us, transport_us, _schedule_receipt)
    result_us = coordinator_us.coordinate(record_us)
    assert result_us.state == "provisioning"
    assert _post_payload(transport_us, "/api/capital-pools")["currency"] == "USD"
    assert result_us.references["capital_pool"]["currency"] == "USD"


def test_coordinator_rejects_currency_contradicting_market_settlement() -> None:
    store, record = _record_and_store(
        {"market": "TW", "symbols": ["2330.TW"], "currency": "USD"}
    )
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "failed"
    assert result.error["failed_step"] == "capital_pool"
    assert "contradicts market 'TW'" in result.error["terminal_reason"]
    assert transport.mutations[("capital", "/api/capital-pools")] == 0

    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="contradicts market 'TW'",
    ):
        coordinator.coordinate(record, dry_run=True)


def test_coordinator_handles_crypto_and_fx_currency_and_validates_uppercase() -> None:
    # FX without currency fails at capital_pool
    store_fx, record_fx = _record_and_store({"market": "FX", "symbols": ["EURUSD"]})
    transport_fx = FakeOwnerTransport()
    coordinator_fx = _coordinator(store_fx, transport_fx, _schedule_receipt)
    result_fx = coordinator_fx.coordinate(record_fx)
    assert result_fx.state == "failed"
    assert result_fx.error["failed_step"] == "capital_pool"
    assert "cannot be determined" in result_fx.error["terminal_reason"]
    assert transport_fx.mutations[("capital", "/api/capital-pools")] == 0

    # CRYPTO with currency "USDT" posts USDT
    store_crypto, record_crypto = _record_and_store(
        {"market": "CRYPTO", "symbols": ["BTCUSDT"], "currency": "USDT"}
    )
    transport_crypto = FakeOwnerTransport()
    coordinator_crypto = _coordinator(store_crypto, transport_crypto, _schedule_receipt)
    result_crypto = coordinator_crypto.coordinate(record_crypto)
    assert result_crypto.state == "provisioning"
    assert _post_payload(transport_crypto, "/api/capital-pools")["currency"] == "USDT"
    assert result_crypto.references["capital_pool"]["currency"] == "USDT"

    # TW with currency "twd" fails with canonical uppercase
    store_lower, record_lower = _record_and_store(
        {"market": "TW", "symbols": ["2330.TW"], "currency": "twd"}
    )
    transport_lower = FakeOwnerTransport()
    coordinator_lower = _coordinator(store_lower, transport_lower, _schedule_receipt)
    result_lower = coordinator_lower.coordinate(record_lower)
    assert result_lower.state == "failed"
    assert result_lower.error["failed_step"] == "capital_pool"
    assert "canonical uppercase" in result_lower.error["terminal_reason"]
    assert transport_lower.mutations[("capital", "/api/capital-pools")] == 0


def test_coordinator_rejects_capital_pool_readback_currency_mismatch() -> None:
    store, record = _record_and_store({"market": "TW", "symbols": ["2330.TW"]})
    ids = deterministic_provisioning_ids(record)
    transport = FakeOwnerTransport()
    transport.objects[("capital", f"/api/capital-pools/{ids.capital_pool_id}")] = {
        "pool_id": ids.capital_pool_id,
        "owner_id": record.tenant_id,
        "owner_type": "org",
        "status": "active",
        "single_runtime_enforced": True,
        "currency": "USD",
    }
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "failed"
    assert result.error["failed_step"] == "capital_pool"
    assert "readback currency 'USD'" in result.error["terminal_reason"]
    assert transport.mutations[("capital", "/api/capital-pools")] == 0


def test_transition_legacy_persona_rejects_pool_currency_mismatching_target_market() -> None:
    store, record = _record_and_store({"symbols": ["2330.TW"]})
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)

    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    ids = deterministic_provisioning_ids(record)

    with pytest.raises(
        PersonaProvisioningCoordinationError,
        match="does not settle market 'TW'",
    ):
        coordinator.transition_legacy_persona_market(result, market="TW")

    assert ("registry", f"/api/registry/strategy-artifacts/{ids.strategy_artifact_id}-rev1") not in transport.objects


# --- case retirement (PROVISIONING-CASE-RETIRE-20261019) ---


class RetireOwners:
    """Owner routes exercised by ``retire_case`` with real readback semantics."""

    def __init__(self, record: ProvisioningRecord, *, saga: dict[str, Any] | None,
                 pcb_status: str | None = "active", pool_status: str | None = "active",
                 binding_status: str = "retired") -> None:
        ids = deterministic_provisioning_ids(record)
        self.ids = ids
        self.record = record
        self.saga = saga
        self.pcb_status = pcb_status
        self.pool_status = pool_status
        self.binding_status = binding_status
        self.persona_status = "active"
        self.cron_registered = True
        self.fail_once: set[str] = set()
        self.calls: list[tuple[str, str, str]] = []
        self.safe_mode_writes = 0
        if saga is not None:
            saga.setdefault("saga_id", ids.deployment_saga_id)
            saga.setdefault("plan_id", ids.deployment_plan_id)

    def _path(self, suffix: str) -> str:
        return suffix

    def get(self, owner: str, path: str):
        saga_path = f"/api/deployment/sagas/{self.ids.deployment_saga_id}"
        if path == saga_path:
            return deepcopy(self.saga)
        if path == f"/api/bindings/{self.ids.persona_capital_binding_id}":
            if self.pcb_status is None:
                return None
            return {
                "binding_id": self.ids.persona_capital_binding_id,
                "persona_id": self.record.persona_id,
                "capital_pool_id": self.ids.capital_pool_id,
                "role": "paper_owner",
                "allowed_deployment_scope": "paper",
                "status": self.pcb_status,
            }
        if path == f"/api/capital-pools/{self.ids.capital_pool_id}":
            if self.pool_status is None:
                return None
            return {"pool_id": self.ids.capital_pool_id, "status": self.pool_status}
        raise AssertionError(f"unexpected GET {owner} {path}")

    def post(self, owner: str, path: str, payload: Mapping[str, Any]):
        self.calls.append(("POST", owner, path))
        if path.endswith("/failure"):
            self.saga["status"] = "compensating"
            self.saga["current_step"] = "compensation_requested"
        elif path.endswith("/retire"):
            self.saga["status"] = "retiring"
            self.saga["current_step"] = "retirement_requested"
        else:
            raise AssertionError(path)
        return {}

    def patch(self, owner: str, path: str, payload: Mapping[str, Any]):
        self.calls.append(("PATCH", owner, path))
        if path in self.fail_once:
            self.fail_once.remove(path)
            raise ConnectionError(f"owner rejected {path}")
        if "safe-mode" in path:
            self.safe_mode_writes += 1
        if path.startswith("/api/bindings/"):
            assert payload["status"] == "revoked"
            self.pcb_status = "revoked"
        elif path.startswith("/api/capital-pools/"):
            assert payload["status"] == "archived"
            assert payload["actor_role"] == "capital.admin"
            self.pool_status = "archived"
        else:
            raise AssertionError(path)
        return {}

    # injected single-owner collaborators
    def remove_cron(self, persona_id: str):
        assert persona_id == self.record.persona_id
        if "cron" in self.fail_once:
            self.fail_once.remove("cron")
            raise RuntimeError("cron owner unavailable")
        self.cron_registered = False
        return {"registered": False}

    def bindings(self, plan_id: str, pool_id: str):
        assert (plan_id, pool_id) == (self.ids.deployment_plan_id, self.ids.capital_pool_id)
        return [{"binding_id": "rb-1", "status": self.binding_status}]

    def archive(self, persona_id: str):
        self.persona_status = "archived"
        return {"persona_id": persona_id, "status": self.persona_status}


def _retire_coordinator(store, owners: RetireOwners, lease: str = "retire-worker"):
    return PersonaProvisioningCoordinator(
        store=store,
        transport=owners,
        schedule_registrar=lambda *_a: {},
        lease_owner=lease,
        schedule_remover=owners.remove_cron,
        runtime_binding_reader=owners.bindings,
        persona_archiver=owners.archive,
    )


def _seed_state(store, record, *, state, current_step="x", error=None, compensation=None):
    active = store.acquire(record.tenant_id, record.idempotency_key,
                           lease_owner="seed", lease_seconds=60)
    active.state = state
    active.current_step = current_step
    active.error = error
    active.compensation = compensation
    active = store.checkpoint(active, lease_owner="seed", lease_seconds=60)
    return store.release(active, lease_owner="seed", lease_seconds=60)


def test_retire_case_succeeded_case_retires_every_owner_and_marks_ledger_retired() -> None:
    store, record = _record_and_store()
    _seed_state(store, record, state="succeeded")
    owners = RetireOwners(record, saga={"status": "completed", "current_step": "runtime_active"})
    coordinator = _retire_coordinator(store, owners)

    first = coordinator.retire_case(record, reason="release r1")
    # Saga retire is asynchronous: first pass proves the request, stays pending.
    assert first.state == "succeeded"
    assert first.references["retirement"]["status"] == "pending"
    assert first.references["retirement"]["pending_step"] == "deployment_saga"
    assert owners.cron_registered is False
    assert owners.pcb_status == "active" and owners.pool_status == "active"

    owners.saga["status"] = "retired"
    done = coordinator.retire_case(record, reason="other reason ignored on replay")

    assert done.state == "retired"
    assert done.current_step == "retired"
    assert done.references["retirement"]["reason"] == "release r1"
    assert owners.pcb_status == "revoked"
    assert owners.pool_status == "archived"
    assert owners.persona_status == "archived"
    assert owners.safe_mode_writes == 0
    assert all("registry" != c[1] and "governance" != c[1] for c in owners.calls)
    for step in ("first_evaluation_schedule", "deployment_saga", "runtime_bindings",
                 "persona_capital_binding", "capital_pool", "persona"):
        assert step in done.references["retirement"]

    # retired is terminal: acquire never reopens it and replay is a no-op.
    calls = list(owners.calls)
    again = coordinator.retire_case(record)
    assert again.state == "retired"
    assert owners.calls == calls
    reacquired = store.acquire(record.tenant_id, record.idempotency_key,
                               lease_owner="x", lease_seconds=5)
    assert reacquired.state == "retired"


def test_retire_case_provisioning_stuck_at_schedule_registered_without_saga_runtime() -> None:
    store, record = _record_and_store()
    _seed_state(store, record, state="provisioning", current_step="schedule_registered_readback")
    owners = RetireOwners(record, saga={"status": "awaiting_runtime_load",
                                        "current_step": "runtime_load_requested"})
    owners.binding_status = "pending"
    coordinator = _retire_coordinator(store, owners)

    first = coordinator.retire_case(record)
    assert first.state == "provisioning"
    assert ("POST", "deployment",
            f"/api/deployment/sagas/{owners.ids.deployment_saga_id}/failure") in owners.calls
    assert first.references["retirement"]["pending_step"] == "deployment_saga"

    # Compensation finishes (saga failed + compensated) and the binding is failed.
    owners.saga.update(status="failed", current_step="compensated",
                       compensation={"command_type": "abort_plan"})
    owners.binding_status = "failed"
    coordinator.retire_case(record)
    assert owners.saga["status"] == "retiring"  # /retire requested once compensated
    owners.saga["status"] = "retired"
    done = coordinator.retire_case(record)

    assert done.state == "retired"


def test_retire_case_failed_and_compensated_saga_is_retired_not_failed_inactive() -> None:
    store, record = _record_and_store()
    _seed_state(store, record, state="compensated",
                error={"failed_step": "schedule_registration"},
                compensation={"status": "completed"})
    owners = RetireOwners(
        record,
        saga={"status": "failed", "current_step": "compensated",
              "compensation": {"command_type": "request_rollback"}},
        pcb_status="suspended",
    )
    coordinator = _retire_coordinator(store, owners)
    coordinator.retire_case(record)
    assert owners.saga["status"] == "retiring"
    owners.saga["status"] = "retired"
    done = coordinator.retire_case(record)
    assert done.state == "retired"
    assert owners.pcb_status == "revoked"


def _failed_uncompensated_case():
    store, record = _record_and_store()
    _seed_state(store, record, state="failed", current_step="schedule_registration_failed",
                error={"failed_step": "schedule_registration", "terminal_reason": "boom"},
                compensation={"status": "failed",
                              "terminal_reason": "deployment compensation did not reach "
                                                 "a recognized authoritative state"})
    return store, record


def test_retire_case_failed_case_with_already_retired_saga_converges_without_posts() -> None:
    store, record = _failed_uncompensated_case()
    owners = RetireOwners(
        record,
        saga={"status": "retired", "current_step": "retired"},
        pcb_status="suspended",
    )
    done = _retire_coordinator(store, owners).retire_case(record)
    assert done.state == "retired"
    assert done.current_step == "retired"
    assert owners.pcb_status == "revoked"
    assert owners.pool_status == "archived"
    assert owners.persona_status == "archived"
    assert not any(c[0] == "POST" for c in owners.calls)


def test_retire_case_failed_case_with_retiring_saga_stays_pending_without_posts() -> None:
    store, record = _failed_uncompensated_case()
    owners = RetireOwners(
        record,
        saga={"status": "retiring", "current_step": "retirement_requested"},
        pcb_status="suspended",
    )
    pending = _retire_coordinator(store, owners).retire_case(record)
    assert pending.state != "retired"
    assert pending.references["retirement"]["status"] == "pending"
    assert not any(c[0] == "POST" for c in owners.calls)


def test_retire_case_failed_inactive_saga_is_terminal_and_never_retire_posted() -> None:
    store, record = _record_and_store()
    _seed_state(store, record, state="compensated", compensation={"status": "completed"})
    owners = RetireOwners(
        record,
        saga={"status": "failed", "current_step": "compensated",
              "compensation": {"command_type": "mark_binding_failed_inactive"}},
        binding_status="failed_inactive",
    )
    done = _retire_coordinator(store, owners).retire_case(record)
    assert done.state == "retired"
    assert not any(c[2].endswith("/retire") for c in owners.calls)


def test_retire_case_replay_after_midway_failure_continues_from_failed_step() -> None:
    store, record = _record_and_store()
    _seed_state(store, record, state="succeeded")
    owners = RetireOwners(record, saga={"status": "retired", "current_step": "retired"})
    pool_path = f"/api/capital-pools/{owners.ids.capital_pool_id}/status"
    owners.fail_once.add(pool_path)
    coordinator = _retire_coordinator(store, owners)

    first = coordinator.retire_case(record)
    assert first.state == "succeeded"
    assert first.references["retirement"]["pending_step"] == "capital_pool"
    assert owners.pcb_status == "revoked"  # earlier step stays done
    assert owners.persona_status == "active"  # later step not attempted

    done = coordinator.retire_case(record)
    assert done.state == "retired"
    assert owners.pool_status == "archived" and owners.persona_status == "archived"
    pcb_patches = [c for c in owners.calls if c[0] == "PATCH" and "/api/bindings/" in c[2]]
    assert len(pcb_patches) == 1  # not re-revoked on replay


def test_retire_case_unconfirmed_step_never_marks_retired() -> None:
    store, record = _record_and_store()
    _seed_state(store, record, state="succeeded")
    owners = RetireOwners(record, saga=None, binding_status="active")
    pending = _retire_coordinator(store, owners).retire_case(record)
    assert pending.state == "succeeded"
    assert pending.references["retirement"]["pending_step"] == "runtime_bindings"
    assert owners.pool_status == "active"

    owners.fail_once.add("cron")
    pending = _retire_coordinator(store, owners).retire_case(record)
    assert pending.references["retirement"]["pending_step"] == "first_evaluation_schedule"


def test_retired_ledger_projects_as_retired_not_paper_running(monkeypatch) -> None:
    from types import SimpleNamespace

    from services.control_plane.bff.personas import service as personas_service
    from services.control_plane.bff.tests.conftest import make_composed_persona_service

    monkeypatch.setattr(
        personas_service,
        "_composed_persona_service",
        make_composed_persona_service(
            read_store=SimpleNamespace(get_persona=lambda _id: None, list_personas=lambda **_k: []),
            write_owner=SimpleNamespace(),
        ),
    )

    store, record = _record_and_store()
    record.state = "retired"
    record.references = {
        "runtime_binding_id": "rb-1",
        "runtime_id": "rt-1",
        "authoritative_readback": {"status": "paper_running"},
    }
    record.result = {"paper_running": True, "status": "paper_running"}
    persona, metadata = personas_service._persona_record_for_provisioning(
        record,
        payload={"name": "Trader A"},
        owner="operator-a",
        existing={"persona_id": "persona-a", "name": "Trader A",
                  "metadata": {"tenant_id": "tenant-a"}, "lifecycle_state": "research_only"},
    )
    assert persona["lifecycle_state"] == "retired"
    assert metadata["provisioning_state"] == "retired"
    assert metadata["paper_runtime_state"] == "retired"


# --- chain paper capital entry (capital-only; no approvals) ---------------------

_CHAIN = {
    "tenant_id": "tenant-a",
    "persona_id": "persona-chain",
    "capital_pool_id": "hosted-all12-chain-1-pool",
    "binding_id": "hosted-all12-chain-1-pcb",
}


def _chain_coordinator(
    transport: FakeOwnerTransport,
    runtime_bindings: list[dict[str, Any]] | None = None,
) -> PersonaProvisioningCoordinator:
    store, _ = _record_and_store()
    coordinator = _coordinator(store, transport, _schedule_receipt, actor_id="control-plane-bff")
    coordinator.runtime_binding_reader = lambda plan_id, pool_id: list(
        runtime_bindings or []
    )
    return coordinator


def _seed_approved_registry(
    transport: FakeOwnerTransport,
    *,
    tenant: str = "tenant-a",
    state: str = "approved",
    checksum: str = "sha256:frozen",
) -> None:
    for kind, artifact_type, registry_id in (
        ("strategy-specs", "strategy_spec", "reg-spec-frozen"),
        ("strategy-artifacts", "execution_bundle", "artifact-frozen"),
    ):
        transport.objects[("registry", f"/api/registry/{kind}/{registry_id}")] = {
            "entry": {
                "registry_id": registry_id,
                "artifact_type": artifact_type,
                "artifact_state": state,
                "owner_tenant": tenant,
                "version": "1.0.0",
                "checksum": checksum,
            }
        }


def _ensure(coordinator: PersonaProvisioningCoordinator, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        **_CHAIN,
        "requested_by": "operator-a",
        "currency": "USD",
        "budget": 25000,
        "strategy_spec_registry_id": "reg-spec-frozen",
        "strategy_artifact_id": "artifact-frozen",
        "strategy_artifact_checksum": "sha256:frozen",
    }
    kwargs.update(overrides)
    return coordinator.ensure_chain_paper_capital(**kwargs)


def test_chain_paper_capital_writes_only_capital_as_bff_with_requester_audit() -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)

    result = _ensure(_chain_coordinator(transport))

    writes = [(m, o, p) for m, o, p, _ in transport.calls if m != "GET"]
    pool_path = f"/api/capital-pools/{_CHAIN['capital_pool_id']}"
    pcb_path = f"/api/bindings/{_CHAIN['binding_id']}"
    assert writes == [
        ("POST", "capital", "/api/capital-pools"),
        ("POST", "capital", "/api/bindings"),
        ("POST", "capital", f"{pcb_path}/activate"),
    ]
    assert {o for _, o, _ in writes} == {"capital"}
    bodies = {p: b for m, _, p, b in transport.calls if m == "POST"}
    for path in ("/api/capital-pools", "/api/bindings"):
        assert bodies[path]["actor_id"] == "control-plane-bff"
        assert bodies[path]["metadata"]["requested_by"] == "operator-a"
        assert bodies[path]["metadata"]["tenant_id"] == "tenant-a"
    assert bodies["/api/bindings"]["created_by"] == "control-plane-bff"
    assert bodies["/api/bindings"]["allowed_deployment_scope"] == "paper"
    assert bodies["/api/bindings"]["metadata"]["registry_id"] == "artifact-frozen"
    assert result["writer_actor_id"] == "control-plane-bff"
    assert result["requested_by"] == "operator-a"
    assert result["persona_capital_binding"]["status"] == "active"
    assert transport.objects[("capital", pool_path)]["pool_id"] == _CHAIN["capital_pool_id"]
    assert result["registry"]["strategy_artifact"]["checksum"] == "sha256:frozen"
    assert not any(o in {"governance", "deployment"} for _, o, _, _ in transport.calls)


def test_chain_paper_capital_adopts_existing_records_without_rewriting_writer() -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)
    transport.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")] = {
        "pool_id": _CHAIN["capital_pool_id"],
        "owner_id": "tenant-a",
        "tenant_id": "tenant-a",
        "owner_type": "org",
        "status": "active",
        "single_runtime_enforced": True,
        "metadata": {"execution_context": "paper", "chain_id": "chain-1"},
    }
    transport.objects[("capital", f"/api/bindings/{_CHAIN['binding_id']}")] = {
        "binding_id": _CHAIN["binding_id"],
        "persona_id": "persona-chain",
        "capital_pool_id": _CHAIN["capital_pool_id"],
        "role": "paper_owner",
        "allowed_deployment_scope": "paper",
        "status": "active",
        "created_by": "HOSTED-ALL12-CHAIN-DESIGN-20261023",
    }

    labels = {"chain_id": "chain-1"}
    result = _ensure(_chain_coordinator(transport), labels=labels)
    again = _ensure(_chain_coordinator(transport), labels=labels)

    assert [c for c in transport.calls if c[0] != "GET"] == []
    assert result["persona_capital_binding"]["created_by"] == "HOSTED-ALL12-CHAIN-DESIGN-20261023"
    assert again["persona_capital_binding"] == result["persona_capital_binding"]


@pytest.mark.parametrize(
    "seed",
    [
        {"tenant": "tenant-b"},
        {"state": "candidate"},
        {"checksum": "sha256:rederived"},
    ],
)
def test_chain_paper_capital_rejects_unapproved_foreign_or_rederived_artifact(
    seed: dict[str, str],
) -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport, **seed)

    with pytest.raises(PersonaProvisioningCoordinationError):
        _ensure(_chain_coordinator(transport))

    assert [c for c in transport.calls if c[0] != "GET"] == []


def test_chain_paper_capital_rejects_foreign_tenant_or_non_paper_existing_records() -> None:
    foreign = FakeOwnerTransport()
    _seed_approved_registry(foreign)
    foreign.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")] = {
        "pool_id": _CHAIN["capital_pool_id"],
        "owner_id": "tenant-b",
        "tenant_id": "tenant-b",
        "owner_type": "org",
        "status": "active",
        "single_runtime_enforced": True,
        "metadata": {"execution_context": "paper"},
    }
    with pytest.raises(PersonaProvisioningCoordinationError, match="tenant-owned paper"):
        _ensure(_chain_coordinator(foreign))

    live = FakeOwnerTransport()
    _seed_approved_registry(live)
    live.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")] = {
        "pool_id": _CHAIN["capital_pool_id"],
        "owner_id": "tenant-a",
        "tenant_id": "tenant-a",
        "owner_type": "org",
        "status": "active",
        "single_runtime_enforced": True,
        "metadata": {"execution_context": "live"},
    }
    with pytest.raises(PersonaProvisioningCoordinationError, match="tenant-owned paper"):
        _ensure(_chain_coordinator(live))
    assert [c for c in live.calls if c[0] != "GET"] == []


def test_chain_paper_capital_retires_binding_before_pool_and_only_chain_records() -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)
    coordinator = _chain_coordinator(transport)
    _ensure(coordinator)
    transport.calls.clear()

    retired = coordinator.retire_chain_paper_capital(
        tenant_id="tenant-a",
        persona_id="persona-chain",
        capital_pool_id=_CHAIN["capital_pool_id"],
        binding_id=_CHAIN["binding_id"],
    )

    patches = [(p, b["status"]) for m, _, p, b in transport.calls if m == "PATCH"]
    assert patches == [
        (f"/api/bindings/{_CHAIN['binding_id']}/status", "revoked"),
        (f"/api/capital-pools/{_CHAIN['capital_pool_id']}/status", "archived"),
    ]
    assert retired["capital_pool"]["status"] == "archived"
    assert all(b["actor_id"] == "control-plane-bff" for m, _, _, b in transport.calls if m == "PATCH")

    other = FakeOwnerTransport()
    other.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")] = {
        "pool_id": _CHAIN["capital_pool_id"],
        "owner_id": "tenant-b",
        "tenant_id": "tenant-b",
        "status": "active",
        "single_runtime_enforced": True,
        "metadata": {"execution_context": "paper", "persona_id": "persona-chain"},
    }
    with pytest.raises(PersonaProvisioningCoordinationError, match="another tenant"):
        _chain_coordinator(other).retire_chain_paper_capital(
            tenant_id="tenant-a",
            persona_id="persona-chain",
            capital_pool_id=_CHAIN["capital_pool_id"],
            binding_id=_CHAIN["binding_id"],
        )
    assert [c for c in other.calls if c[0] != "GET"] == []


def test_chain_paper_capital_labels_are_allow_listed_and_new_pool_needs_currency() -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)
    with pytest.raises(PersonaProvisioningCoordinationError, match="currency is required"):
        _ensure(_chain_coordinator(transport), currency=None)
    assert [c for c in transport.calls if c[0] != "GET"] == []

    _ensure(
        _chain_coordinator(transport),
        labels={"chain_id": "chain-1", "paper_only": True, "actor_id": "spoofed", "tenant_id": "tenant-b"},
    )
    bodies = {p: b for m, _, p, b in transport.calls if m == "POST"}
    for path in ("/api/capital-pools", "/api/bindings"):
        metadata = bodies[path]["metadata"]
        assert metadata["chain_id"] == "chain-1" and metadata["paper_only"] is True
        assert metadata["tenant_id"] == "tenant-a" and "actor_id" not in metadata


_RETIRE = {
    "tenant_id": "tenant-a",
    "persona_id": "persona-chain",
    "capital_pool_id": _CHAIN["capital_pool_id"],
    "binding_id": _CHAIN["binding_id"],
}


def _seed_pool(
    transport: FakeOwnerTransport, *, metadata: dict[str, Any], **overrides: Any
) -> None:
    transport.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")] = {
        "pool_id": _CHAIN["capital_pool_id"],
        "owner_id": "tenant-a",
        "tenant_id": "tenant-a",
        "owner_type": "org",
        "status": "active",
        "single_runtime_enforced": True,
        "metadata": metadata,
        **overrides,
    }


def _seed_pcb(transport: FakeOwnerTransport, binding_id: str, **overrides: Any) -> None:
    transport.objects[("capital", f"/api/bindings/{binding_id}")] = {
        "binding_id": binding_id,
        "persona_id": "persona-chain",
        "capital_pool_id": _CHAIN["capital_pool_id"],
        "role": "paper_owner",
        "allowed_deployment_scope": "paper",
        "status": "active",
        **overrides,
    }


@pytest.mark.parametrize(
    "pool",
    [
        {"metadata": {"execution_context": "live", "persona_id": "persona-chain"}},
        {"metadata": {"execution_context": "paper", "persona_id": "persona-someone-else"}},
        {"metadata": {"execution_context": "paper", "persona_id": "persona-chain"},
         "single_runtime_enforced": False},
    ],
)
def test_chain_paper_capital_retire_refuses_unproven_or_non_paper_pool(
    pool: dict[str, Any],
) -> None:
    transport = FakeOwnerTransport()
    _seed_pool(transport, **pool)

    with pytest.raises(PersonaProvisioningCoordinationError, match="paper pool|not proven"):
        _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE)

    assert [c for c in transport.calls if c[0] != "GET"] == []


def test_chain_paper_capital_retire_refuses_other_chain_pool_despite_matching_binding() -> None:
    transport = FakeOwnerTransport()
    _seed_pool(
        transport,
        metadata={"execution_context": "paper", "persona_id": "persona-chain", "chain_id": "chain-other"},
    )
    _seed_pcb(transport, _CHAIN["binding_id"])

    with pytest.raises(PersonaProvisioningCoordinationError, match="another chain"):
        _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE, chain_id="chain-1")

    assert [c for c in transport.calls if c[0] != "GET"] == []
    assert transport.objects[("capital", f"/api/bindings/{_CHAIN['binding_id']}")]["status"] == "active"
    assert transport.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")]["status"] == "active"


def test_chain_paper_capital_retire_refuses_other_chain_binding_before_any_write() -> None:
    transport = FakeOwnerTransport()
    _seed_pool(transport, metadata={"execution_context": "paper", "chain_id": "chain-1"})
    _seed_pcb(transport, _CHAIN["binding_id"], metadata={"chain_id": "chain-other"})

    with pytest.raises(PersonaProvisioningCoordinationError, match="PersonaCapitalBinding belongs to another chain"):
        _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE, chain_id="chain-1")

    assert [c for c in transport.calls if c[0] != "GET"] == []


def test_chain_paper_capital_retire_keeps_legacy_pool_without_persona_metadata() -> None:
    transport = FakeOwnerTransport()
    _seed_pool(
        transport,
        metadata={"execution_context": "paper", "chain_id": "chain-1"},
        created_by="HOSTED-ALL12-CHAIN-DESIGN-20261023",
    )
    _seed_pcb(transport, _CHAIN["binding_id"], metadata={"chain_id": "chain-1"})

    retired = _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE, chain_id="chain-1")

    patches = [(p, b["status"]) for m, _, p, b in transport.calls if m == "PATCH"]
    assert patches == [
        (f"/api/bindings/{_CHAIN['binding_id']}/status", "revoked"),
        (f"/api/capital-pools/{_CHAIN['capital_pool_id']}/status", "archived"),
    ]
    assert retired["capital_pool"]["status"] == "archived"
    pool = transport.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")]
    assert pool["created_by"] == "HOSTED-ALL12-CHAIN-DESIGN-20261023"


_OPERATOR_OWNER = {
    "owner_id": "HOSTED-ALL12-CHAIN-DESIGN-20261023",
    "owner_type": "operator",
    "created_by": "HOSTED-ALL12-CHAIN-DESIGN-20261023",
}


def _seed_operator_pool(transport: FakeOwnerTransport, **overrides: Any) -> None:
    _seed_pool(
        transport,
        metadata={"execution_context": "paper", "chain_id": "chain-1"},
        **{**_OPERATOR_OWNER, "tenant_id": "tenant-a", **overrides},
    )
    _seed_pcb(transport, _CHAIN["binding_id"], metadata={"chain_id": "chain-1"})


def test_chain_paper_capital_operator_owned_formal_tenant_pool_is_adopted_and_retired() -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)
    _seed_operator_pool(transport)

    _ensure(_chain_coordinator(transport), labels={"chain_id": "chain-1"})
    assert [c for c in transport.calls if c[0] != "GET"] == []

    _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE, chain_id="chain-1")
    patches = [(p, b["status"]) for m, _, p, b in transport.calls if m == "PATCH"]
    assert patches == [
        (f"/api/bindings/{_CHAIN['binding_id']}/status", "revoked"),
        (f"/api/capital-pools/{_CHAIN['capital_pool_id']}/status", "archived"),
    ]
    pool = transport.objects[("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")]
    for key, value in _OPERATOR_OWNER.items():
        assert pool[key] == value


@pytest.mark.parametrize("formal", [None, "", "  ", "tenant-b"])
def test_chain_paper_capital_missing_or_foreign_formal_tenant_refuses_before_writes(
    formal: str | None,
) -> None:
    claims = {"owner_id": "tenant-a", "owner_type": "org"}
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)
    _seed_operator_pool(transport, **claims)
    pool_key = ("capital", f"/api/capital-pools/{_CHAIN['capital_pool_id']}")
    transport.objects[pool_key]["metadata"]["tenant_id"] = "tenant-a"
    if formal is None:
        del transport.objects[pool_key]["tenant_id"]
    else:
        transport.objects[pool_key]["tenant_id"] = formal

    with pytest.raises(PersonaProvisioningCoordinationError, match="tenant-owned paper"):
        _ensure(_chain_coordinator(transport), labels={"chain_id": "chain-1"})
    with pytest.raises(PersonaProvisioningCoordinationError, match="another tenant"):
        _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE, chain_id="chain-1")
    assert [c for c in transport.calls if c[0] != "GET"] == []


def test_chain_paper_capital_retire_refuses_live_runtime_dependants() -> None:
    transport = FakeOwnerTransport()
    _seed_pool(transport, metadata={"execution_context": "paper", "persona_id": "persona-chain"})
    _seed_pcb(transport, _CHAIN["binding_id"])

    with pytest.raises(PersonaProvisioningCoordinationError, match="RuntimeBindings are not retired"):
        _chain_coordinator(
            transport, [{"binding_id": "rb-1", "status": "active"}]
        ).retire_chain_paper_capital(**_RETIRE)
    assert [c for c in transport.calls if c[0] != "GET"] == []

    retired = _chain_coordinator(
        transport, [{"binding_id": "rb-1", "status": "retired"}]
    ).retire_chain_paper_capital(**_RETIRE)
    assert retired["capital_pool"]["status"] == "archived"


def test_chain_paper_capital_retire_refuses_other_live_pool_bindings() -> None:
    transport = FakeOwnerTransport()
    _seed_pool(transport, metadata={"execution_context": "paper", "persona_id": "persona-chain"})
    _seed_pcb(transport, _CHAIN["binding_id"])
    _seed_pcb(transport, "pcb-other", persona_id="persona-someone-else")

    with pytest.raises(PersonaProvisioningCoordinationError, match="other PersonaCapitalBindings"):
        _chain_coordinator(transport).retire_chain_paper_capital(**_RETIRE)

    assert [c for c in transport.calls if c[0] != "GET"] == []


def test_chain_paper_capital_ensure_refuses_pool_of_another_persona_or_chain() -> None:
    transport = FakeOwnerTransport()
    _seed_approved_registry(transport)
    _seed_pool(
        transport,
        metadata={
            "execution_context": "paper",
            "persona_id": "persona-someone-else",
            "chain_id": "chain-other",
        },
    )

    with pytest.raises(PersonaProvisioningCoordinationError, match="does not belong"):
        _ensure(_chain_coordinator(transport), labels={"chain_id": "chain-1"})

    assert [c for c in transport.calls if c[0] != "GET"] == []


def test_non_baseline_paper_persona_writes_expression_policy_controls_and_pool_risk_policy_leaving_baseline_and_legacy_unchanged() -> None:
    from services.capital.risk_policy import RiskPolicy

    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)
    result = coordinator.coordinate(record)
    assert result.state == "provisioning"
    ids = deterministic_provisioning_ids(record)

    # 1. Pool risk policy
    pool = _post_payload(transport, "/api/capital-pools")
    drafted = dict(pool["metadata"]["risk_policy"])
    drafted.pop("metadata", None)
    assert drafted == _fixture_paper_policy(ids.capital_pool_id)
    assert drafted["max_single_name_weight"] < 1.0 and drafted["min_cash_reserve_pct"] > 0
    parsed_policy = RiskPolicy.from_mapping(pool["metadata"]["risk_policy"])
    assert parsed_policy.max_leverage == 2.0
    assert parsed_policy.gross_limit == 2.0
    assert parsed_policy.allowed_asset_classes == ("equity", "future")

    # 2. Baseline spec & artifact are unchanged
    baseline_spec = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.baseline_registry_id),
    )["strategy_spec"]
    assert baseline_spec["market_scope"]["asset_classes"] == ["equity"]

    baseline_artifact = _post_payload(
        transport,
        "/api/registry/strategy-artifacts",
        identity=("registry_id", ids.baseline_strategy_artifact_id),
    )["strategy_artifact"]
    assert "expression_policy" not in baseline_artifact
    assert "strength_scale_k" not in baseline_artifact["parameters"]
    assert "strength_floor_momentum" not in baseline_artifact["parameters"]
    assert [
        c["parameter_key"] for c in baseline_artifact["mutation_surface"]["controls"]
    ] == ["lookback_bars", "momentum_threshold"]

    # 3. Non-baseline paper persona spec & artifact include expression_policy and 4 controls
    candidate_spec = _post_payload(
        transport,
        "/api/registry/strategy-specs",
        identity=("registry_id", ids.registry_id),
    )["strategy_spec"]
    assert candidate_spec["market_scope"]["asset_classes"] == ["equity", "future"]

    forward_artifact = _post_payload(
        transport,
        "/api/registry/strategy-artifacts",
        identity=("registry_id", ids.strategy_artifact_id),
    )["strategy_artifact"]
    assert forward_artifact["expression_policy"] == {
        "version": "1.0",
        "kind": "notional_target_v1",
        "parameters": {
            "lever_min_strength": 0.5,
            "notional_fraction_at_full_strength": 1.0,
            "allow_pyramiding": False,
            "close_before_reverse": True,
        },
    }
    assert forward_artifact["parameters"]["strength_scale_k"] == 1.0
    assert forward_artifact["parameters"]["strength_floor_momentum"] == 0.01
    controls_by_key = {
        c["parameter_key"]: c for c in forward_artifact["mutation_surface"]["controls"]
    }
    assert list(controls_by_key) == [
        "lookback_bars",
        "momentum_threshold",
        "strength_scale_k",
        "strength_floor_momentum",
        "lever_min_strength",
        "notional_fraction_at_full_strength",
    ]
    assert controls_by_key["strength_scale_k"] == {
        "parameter_key": "strength_scale_k",
        "value_type": "number",
        "current_value": 1.0,
        "allowed_range": {"min": 0.25, "max": 4.0},
        "step": 0.05,
    }
    assert controls_by_key["strength_floor_momentum"] == {
        "parameter_key": "strength_floor_momentum",
        "value_type": "number",
        "current_value": 0.01,
        "allowed_range": {"min": 0.002, "max": 0.05},
        "step": 0.001,
    }
    assert controls_by_key["lever_min_strength"] == {
        "parameter_key": "lever_min_strength",
        "value_type": "number",
        "current_value": 0.5,
        "allowed_range": {"min": 0.0, "max": 1.0},
        "step": 0.05,
    }
    assert controls_by_key["notional_fraction_at_full_strength"] == {
        "parameter_key": "notional_fraction_at_full_strength",
        "value_type": "number",
        "current_value": 1.0,
        "allowed_range": {"min": 0.1, "max": 1.0},
        "step": 0.05,
    }

    # 4. Legacy persona artifact without expression_policy stays unchanged when adopted/transitioned
    legacy_transport = FakeOwnerTransport()
    _seed_approved_registry(legacy_transport)
    legacy_result = _ensure(_chain_coordinator(legacy_transport))
    assert legacy_result["registry"]["strategy_artifact"]["checksum"] == "sha256:frozen"

    legacy_store, legacy_record = _record_and_store()
    legacy_tx = FakeOwnerTransport()
    legacy_coord = _coordinator(legacy_store, legacy_tx, _schedule_receipt)
    legacy_coordinated = legacy_coord.coordinate(legacy_record)
    legacy_ids = deterministic_provisioning_ids(legacy_record)
    parent_path = f"/api/registry/strategy-artifacts/{legacy_ids.strategy_artifact_id}"
    for target_artifact in (
        legacy_tx.objects[("registry", parent_path)]["entry"]["metadata"]["strategy_artifact"],
        legacy_coordinated.references["strategy_artifact_approved"]["entry"]["metadata"]["strategy_artifact"],
        legacy_store.get(legacy_record.tenant_id, legacy_record.idempotency_key).references["strategy_artifact_approved"]["entry"]["metadata"]["strategy_artifact"],
    ):
        target_artifact.pop("expression_policy", None)
        target_artifact["parameters"].pop("strength_scale_k", None)
        target_artifact["parameters"].pop("strength_floor_momentum", None)
        target_artifact["mutation_surface"]["controls"] = [
            c
            for c in target_artifact["mutation_surface"]["controls"]
            if c["parameter_key"] in {"lookback_bars", "momentum_threshold"}
        ]
    transitioned = legacy_coord.transition_legacy_persona_market(
        legacy_coordinated, market="US"
    )
    child_artifact = transitioned.references["strategy_artifact_approved"]["entry"][
        "metadata"
    ]["strategy_artifact"]
    assert "expression_policy" not in child_artifact
    assert [
        c["parameter_key"] for c in child_artifact["mutation_surface"]["controls"]
    ] == ["lookback_bars", "momentum_threshold"]




def _template_artifact_field(key: str) -> Any:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    _coordinator(store, transport, _schedule_receipt).coordinate(record)
    ids = deterministic_provisioning_ids(record)
    entry = _owner_entry(transport, "strategy-artifacts", ids.strategy_artifact_id)
    return deepcopy(entry["metadata"]["strategy_artifact"][key])


def _admitted_artifact_view(ref: str, *, state: str = "approved") -> dict[str, Any]:
    return {
        "entry": {
            "registry_id": ref,
            "artifact_type": "execution_bundle",
            "artifact_state": state,
            "strategy_id": "discovered-strategy",
            "version": "9",
            "metadata": {
                "strategy_artifact": {
                    "artifact_id": ref,
                    "strategy_id": "discovered-strategy",
                    "version": "9",
                    "algorithm_ref": {
                        **_template_artifact_field("algorithm_ref"),
                        "commit": "a" * 40,
                    },
                    "strategy_logic": _template_artifact_field("strategy_logic"),
                    "parameters": {
                        **_template_artifact_field("parameters"),
                        "symbols": ["QQQ"],
                    },
                    "mutation_surface": _template_artifact_field("mutation_surface"),
                    "expression_policy": _template_artifact_field("expression_policy"),
                }
            },
        }
    }


def test_strategy_artifact_ref_replaces_template_content_only() -> None:
    ref = "admitted-artifact-1"
    path = f"/api/registry/strategy-artifacts/{ref}"
    store, record = _record_and_store({"strategy_artifact_ref": ref})
    transport = FakeOwnerTransport()
    transport.objects[("registry", path)] = _admitted_artifact_view(ref)
    result = _coordinator(store, transport, _schedule_receipt).coordinate(record)
    assert result.state == "provisioning", result.error
    assert result.current_step == "schedule_registered"
    ids = deterministic_provisioning_ids(record)
    entry = _owner_entry(transport, "strategy-artifacts", ids.strategy_artifact_id)
    artifact = entry["metadata"]["strategy_artifact"]
    assert artifact["algorithm_ref"]["commit"] == "a" * 40
    assert artifact["parameters"]["symbols"] == ["QQQ"]
    assert artifact["artifact_id"] == ids.strategy_artifact_id
    assert artifact["strategy_id"] == ids.strategy_id
    assert entry["metadata"]["source_strategy_artifact_ref"] == ref
    # The baseline artifact is untouched by the reference.
    baseline = _owner_entry(transport, "strategy-artifacts", ids.baseline_strategy_artifact_id)
    assert baseline["metadata"]["strategy_artifact"]["strategy_logic"]["kind"] == (
        "close_to_close_momentum"
    )


def test_strategy_artifact_ref_requires_approved_artifact() -> None:
    ref = "candidate-artifact"
    store, record = _record_and_store({"strategy_artifact_ref": ref})
    transport = FakeOwnerTransport()
    transport.objects[("registry", f"/api/registry/strategy-artifacts/{ref}")] = (
        _admitted_artifact_view(ref, state="candidate")
    )
    result = _coordinator(store, transport, _schedule_receipt).coordinate(record)
    assert result.state != "provisioning" or result.current_step != "schedule_registered"


def test_without_strategy_artifact_ref_behavior_is_unchanged() -> None:
    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    _coordinator(store, transport, _schedule_receipt).coordinate(record)
    ids = deterministic_provisioning_ids(record)
    entry = _owner_entry(transport, "strategy-artifacts", ids.strategy_artifact_id)
    assert "source_strategy_artifact_ref" not in entry["metadata"]
    assert entry["metadata"]["strategy_artifact"]["strategy_logic"]["kind"] == (
        "close_to_close_momentum"
    )


def test_paper_enrollment_problem_is_a_readback_and_never_fails_paper_admission() -> None:
    """Enrolment sits outside the saga: an unavailable owner is a readable reason."""

    store, record = _record_and_store()
    transport = FakeOwnerTransport()
    coordinator = _coordinator(store, transport, _schedule_receipt)
    coordinator.persona_owner_provider = lambda: None

    done = coordinator.coordinate(record)

    assert done.state == "provisioning"
    assert done.current_step == "schedule_registered"
    assert done.error is None and done.compensation is None
    readback = done.references["paper_enrollment"]
    assert readback["status"] == "unavailable"
    assert readback["reason"] == "RuntimeError: persona_owner_unavailable_for_paper_enrollment"
    assert readback["lifecycle_state"] is None


def test_paper_enrollment_reconcile_skips_terminal_ledger_records() -> None:
    store, record = _record_and_store()
    coordinator = _coordinator(store, FakeOwnerTransport(), _schedule_receipt)
    coordinator.persona_owner_provider = lambda: pytest.fail("terminal records are never enrolled")
    for state in ("failed", "compensated", "retired", "reserved"):
        record.state = state
        assert coordinator.reconcile_paper_enrollment(record).state == state
        assert "paper_enrollment" not in record.references


def test_canonical_payload_and_active_runtime_binding_overlay_projection() -> None:
    from services.control_plane.bff.personas.service import (
        _persona_create_canonical_payload,
        _project_persona_dto,
        _resolve_active_runtime_binding_overlay,
    )

    canonical = _persona_create_canonical_payload(
        {
            "name": "Alpha Trader",
            "risk_policy_ref": None,
            "riskPolicyRef": "rp-explicit-01",
            "risk_profile": {"risk_level": "aggressive"},
            "paperBudget": 100000,
        },
        name="Alpha Trader",
        tenant_id="tenant-a",
        requested_by="operator-a",
    )
    assert canonical["risk_policy_ref"] == "rp-explicit-01"
    assert "riskPolicyRef" not in canonical
    assert canonical["risk"] == "aggressive"
    assert canonical["budget"] == 100000

    raw_persona = {
        "persona_id": "persona-tw-202611161257-6863c3",
        "tenant_id": "tenant-dev",
        "name": "TW Momentum",
        "lifecycle_state": "active",
        "metadata": {
            "tenant_id": "tenant-dev",
            "capital_mode": "paper",
            "internal_paper_capital_pool_id": "pool-persona-6863c3",
            "persona_capital_binding_id": "pcb-persona-6863c3",
            "runtime_binding_id": "rb-1dc692fd72aa57eb",
            "runtime_id": "rt-6863c3",
            "deployment_plan_id": "plan-v1",
        },
    }
    runtime_bindings = [
        {
            "binding_id": "rb-1dc692fd72aa57eb",
            "runtime_id": "rt-6863c3",
            "plan_id": "plan-v1",
            "capital_pool_id": "pool-persona-6863c3",
            "persona_capital_binding_id": "pcb-persona-6863c3",
            "deployment_mode": "paper",
            "status": "retired",
            "metadata": {"tenant_id": "tenant-dev", "persona_id": "persona-tw-202611161257-6863c3"},
        },
        {
            "binding_id": "rb-a224d8623d1eb191",
            "runtime_id": "rt-6863c3",
            "plan_id": "plan-v2",
            "capital_pool_id": "pool-persona-6863c3",
            "persona_capital_binding_id": "pcb-persona-6863c3",
            "replaces_binding_id": "rb-1dc692fd72aa57eb",
            "deployment_mode": "paper",
            "status": "active",
            "metadata": {"tenant_id": "tenant-dev", "persona_id": "persona-tw-202611161257-6863c3"},
        },
    ]
    overlay = _resolve_active_runtime_binding_overlay(
        raw_persona, all_runtime_bindings=runtime_bindings
    )
    assert overlay == {
        "runtimeBindingId": "rb-a224d8623d1eb191",
        "runtimeId": "rt-6863c3",
        "deploymentPlanId": "plan-v2",
    }
    dto = _project_persona_dto(raw_persona, overlay=overlay)
    assert dto["runtimeBindingId"] == "rb-a224d8623d1eb191"
    assert dto["deploymentPlanId"] == "plan-v2"


def test_coordinator_pool_risk_policy_covers_new_existing_retries_rebinding_version_changes_and_repair() -> None:
    from services.capital.risk_policy import capital_pool_approval_digest, risk_policy_digest
    from services.governance.test_approval_authority import approval_snapshot

    # 1. Minimal direct call to _resolve_requested_pool_risk_policy and ordinary risk="low", risk_policy_ref=None fail closed without any opt-in flag
    store_min, record_min = _record_and_store(seed_pool_approval=False)
    ids_min = deterministic_provisioning_ids(record_min)
    coord_min = _coordinator(store_min, FakeOwnerTransport(), _schedule_receipt, seed_pool_approval=False)
    with pytest.raises(PersonaProvisioningCoordinationError, match="risk_policy_profile_mapping_unapproved"):
        coord_min._resolve_requested_pool_risk_policy(record_min, ids_min)
    res_min_coord = coord_min.coordinate(record_min)
    assert res_min_coord.state == "failed"
    assert res_min_coord.error["failed_step"] == "capital_pool"
    assert "risk_policy_profile_mapping_unapproved" in res_min_coord.error["terminal_reason"]

    # 2. Explicit policy contract on new pool creation is validated and preserved
    explicit_policy = {
        "risk_policy_id": "rp-custom-paper-v2",
        "version": "2.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 1.75,
        "net_limit": 1.25,
        "max_leverage": 1.75,
        "max_single_name_weight": 0.3,
        "allowed_asset_classes": ["equity"],
    }
    store_exp, record_exp = _record_and_store({"risk_policy": explicit_policy})
    tx_exp = FakeOwnerTransport()
    res_exp = _coordinator(store_exp, tx_exp, _schedule_receipt, seed_pool_approval=False).coordinate(record_exp)
    assert res_exp.state == "provisioning"
    created_pool = _post_payload(tx_exp, "/api/capital-pools")
    assert created_pool["metadata"]["risk_policy"] == explicit_policy

    # 3. Invalid, stale, wrong-tenant, or unapproved ordinary profile mapping (without opt-in flag!) fails closed at capital_pool step
    for bad_payload, expected_err in [
        ({"risk_policy": {**explicit_policy, "gross_limit": -1.0}}, "risk_policy_invalid_limit"),
        ({"risk_policy": {**explicit_policy, "allowed_stages": ["live"]}}, "risk_policy_stage_not_allowed"),
        ({"risk_policy": {**explicit_policy, "tenant_id": "tenant-foreign"}}, "risk_policy_wrong_tenant"),
        ({"risk_policy": {**explicit_policy, "status": "superseded"}}, "risk_policy_stale"),
        ({"risk": "low", "risk_policy_ref": None}, "risk_policy_profile_mapping_unapproved"),
        ({"risk": "aggressive"}, "requested_profile_source_invalid"),
    ]:
        s_bad, r_bad = _record_and_store(bad_payload, seed_pool_approval=False)
        tx_bad = FakeOwnerTransport()
        res_bad = _coordinator(s_bad, tx_bad, _schedule_receipt, seed_pool_approval=False).coordinate(r_bad)
        assert res_bad.state == "failed"
        assert res_bad.error["failed_step"] == "capital_pool"
        assert expected_err in res_bad.error["terminal_reason"]
        assert tx_bad.mutations[("capital", "/api/capital-pools")] == 0

    # 4. Ordinary risk="low", risk_policy_ref=None succeeds when canonical owner ApprovalDecision exists in Governance and persists profile + mapping requirement
    store_ord, record_ord = _record_and_store(
        {
            "risk": "low",
            "risk_policy_ref": None,
            "profile_mapping_approval_id": "apv-pool-risk-low-v1",
        }
    )
    ids_ord = deterministic_provisioning_ids(record_ord)
    tx_ord = FakeOwnerTransport()
    coord_ord = _coordinator(store_ord, tx_ord, _schedule_receipt)
    baseline_v1 = _fixture_paper_policy(ids_ord.capital_pool_id, version="1.0")
    ord_candidate_pool_digest = capital_pool_approval_digest(
        {
            "pool_id": ids_ord.capital_pool_id,
            "owner_id": record_ord.tenant_id,
            "owner_type": "org",
            "currency": coord_ord._capital_pool_currency(record_ord),
            "budget": record_ord.request_payload.get("budget"),
            "risk_policy_ref": None,
            "single_runtime_enforced": True,
            "metadata": {"risk_policy": baseline_v1},
        }
    )
    # Wrong target_version fails preflight before any POST /api/capital-pools write
    tx_ord.objects[("governance", "/api/governance/approvals/apv-pool-risk-low-v1")] = approval_snapshot(
        decision_id="apv-pool-risk-low-v1",
        tenant_id=record_ord.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_ord.capital_pool_id,
        target_version="wrong-create-pool-digest",
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": ids_ord.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": baseline_v1["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(baseline_v1),
                "risk_officer_draft": {"policy": baseline_v1},
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    res_ord_wrong_digest = coord_ord.coordinate(record_ord)
    assert res_ord_wrong_digest.state == "failed"
    assert res_ord_wrong_digest.error["failed_step"] == "capital_pool"
    assert "risk_policy_profile_mapping_unapproved" in res_ord_wrong_digest.error["terminal_reason"]
    assert tx_ord.mutations[("capital", "/api/capital-pools")] == 0

    store_ord, record_ord = _record_and_store(
        {
            "risk": "low",
            "risk_policy_ref": None,
            "profile_mapping_approval_id": "apv-pool-risk-low-v1",
        }
    )
    coord_ord = _coordinator(store_ord, tx_ord, _schedule_receipt)
    tx_ord.objects[("governance", "/api/governance/approvals/apv-pool-risk-low-v1")] = approval_snapshot(
        decision_id="apv-pool-risk-low-v1",
        tenant_id=record_ord.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_ord.capital_pool_id,
        target_version=ord_candidate_pool_digest,
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": ids_ord.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": baseline_v1["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(baseline_v1),
                "risk_officer_draft": {"policy": baseline_v1},
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    res_ord = coord_ord.coordinate(record_ord)
    assert res_ord.state == "provisioning", res_ord.error
    created_ord_pool = _post_payload(tx_ord, "/api/capital-pools")
    assert created_ord_pool["metadata"]["requested_risk_profile"] == "low"
    assert created_ord_pool["metadata"]["require_approved_profile_mapping"] is True
    assert created_ord_pool["metadata"]["profile_mapping_approval_id"] == "apv-pool-risk-low-v1"
    assert created_ord_pool["metadata"]["risk_policy"] == baseline_v1
    assert res_ord.references["capital_pool"]["approval_digest"] == ord_candidate_pool_digest

    # 5. Repair defaults to require_approved_profile_mapping=True: missing or unapproved pool surfaces owner_decision_request without mutating
    store_rep, record_rep = _record_and_store(seed_pool_approval=False)
    ids_rep = deterministic_provisioning_ids(record_rep)
    tx_rep = FakeOwnerTransport()
    pool_path = f"/api/capital-pools/{ids_rep.capital_pool_id}"
    tx_rep.objects[("capital", pool_path)] = {
        "pool_id": ids_rep.capital_pool_id,
        "owner_id": record_rep.tenant_id,
        "tenant_id": record_rep.tenant_id,
        "owner_type": "org",
        "status": "active",
        "currency": "USD",
        "budget": 25000,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": record_rep.tenant_id,
            "persona_id": record_rep.persona_id,
        },
    }
    coord_rep = _coordinator(store_rep, tx_rep, _schedule_receipt, seed_pool_approval=False)
    unapproved_repair = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
    )
    assert unapproved_repair["repaired"] is False
    assert unapproved_repair["before_resolution"]["status"] == "missing"
    assert unapproved_repair["after_resolution"]["status"] == "unresolved_profile_mapping"
    assert unapproved_repair["owner_decision_request"]["policy_owner"] == "services/capital/risk_policy.py"
    v1_candidate_pool_digest = unapproved_repair["owner_decision_request"]["candidate_pool_approval_digest"]
    assert v1_candidate_pool_digest
    assert tx_rep.mutations[("capital", f"{pool_path}/status")] == 1

    # Once canonical owner ApprovalDecision bound to candidate v1 pool approval_digest is in Governance, repair converges
    tx_rep.objects[("governance", "/api/governance/approvals/apv-repair-low-v1")] = approval_snapshot(
        decision_id="apv-repair-low-v1",
        tenant_id=record_rep.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_rep.capital_pool_id,
        target_version=v1_candidate_pool_digest,
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": ids_rep.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": f"rp-{ids_rep.capital_pool_id}",
                "risk_policy_version": "1.0",
                "risk_policy_digest": unapproved_repair["owner_decision_request"]["candidate_risk_policy_digest"],
                "risk_officer_draft": _draft_embed(unapproved_repair["owner_decision_request"]),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    repair_receipt = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
        approval_decision_id="apv-repair-low-v1",
    )
    assert repair_receipt["repaired"] is True
    assert repair_receipt["idempotent_noop"] is False
    assert repair_receipt["before_resolution"]["status"] == "valid"
    assert repair_receipt["after_resolution"]["resolved"] is True
    assert repair_receipt["after_resolution"]["status"] == "valid"
    assert repair_receipt["after_resolution"]["profile_mapping_status"] == "approved_profile_mapping"
    assert repair_receipt["after_resolution"]["profile_mapping_approval_id"] == "apv-repair-low-v1"
    assert repair_receipt["after_resolution"]["risk_policy_id"] == f"rp-{ids_rep.capital_pool_id}"
    assert repair_receipt["capital_pool"]["approval_digest"] == v1_candidate_pool_digest
    assert repair_receipt["capital_pool"]["metadata"]["requested_risk_profile"] == "low"
    assert repair_receipt["capital_pool"]["metadata"]["require_approved_profile_mapping"] is True
    assert repair_receipt["capital_pool"]["metadata"]["profile_mapping_approval_id"] == "apv-repair-low-v1"

    # Idempotent replay performs zero additional PATCH calls
    patches_before = tx_rep.mutations[("capital", f"{pool_path}/status")]
    noop_receipt = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
    )
    assert noop_receipt["repaired"] is False
    assert noop_receipt["idempotent_noop"] is True
    assert tx_rep.mutations[("capital", f"{pool_path}/status")] == patches_before

    # Policy version upgrade / rebinding requires owner approval for v2.0 candidate pool digest (not old v1 pool digest)
    unapproved_v2 = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
        risk_policy_version="2.0",
    )
    assert unapproved_v2["repaired"] is False
    assert unapproved_v2["after_resolution"]["status"] == "unresolved_profile_mapping"
    v2_candidate_pool_digest = unapproved_v2["owner_decision_request"]["candidate_pool_approval_digest"]
    assert v2_candidate_pool_digest and v2_candidate_pool_digest != v1_candidate_pool_digest

    # Binding v2 approval to the OLD v1 pool approval_digest fails preflight before any write
    tx_rep.objects[("governance", "/api/governance/approvals/apv-repair-low-v2-old-digest")] = approval_snapshot(
        decision_id="apv-repair-low-v2-old-digest",
        tenant_id=record_rep.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_rep.capital_pool_id,
        target_version=v1_candidate_pool_digest,
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": ids_rep.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": f"rp-{ids_rep.capital_pool_id}",
                "risk_policy_version": "2.0",
                "risk_policy_digest": unapproved_v2["owner_decision_request"]["candidate_risk_policy_digest"],
                "risk_officer_draft": _draft_embed(unapproved_v2["owner_decision_request"]),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    stale_digest_v2 = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
        risk_policy_version="2.0",
        approval_decision_id="apv-repair-low-v2-old-digest",
    )
    assert stale_digest_v2["repaired"] is False
    assert stale_digest_v2["after_resolution"]["status"] == "unresolved_profile_mapping"
    assert tx_rep.mutations[("capital", f"{pool_path}/status")] == patches_before
    assert tx_rep.get("capital", pool_path)["metadata"]["risk_policy"]["version"] == "1.0"

    # Binding v2 approval to the candidate v2 pool approval_digest passes preflight, writes v2, and passes GET readback
    tx_rep.objects[("governance", "/api/governance/approvals/apv-repair-low-v2")] = approval_snapshot(
        decision_id="apv-repair-low-v2",
        tenant_id=record_rep.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_rep.capital_pool_id,
        target_version=v2_candidate_pool_digest,
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": ids_rep.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": f"rp-{ids_rep.capital_pool_id}",
                "risk_policy_version": "2.0",
                "risk_policy_digest": unapproved_v2["owner_decision_request"]["candidate_risk_policy_digest"],
                "risk_officer_draft": _draft_embed(unapproved_v2["owner_decision_request"]),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    upgraded_receipt = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
        risk_policy_version="2.0",
        approval_decision_id="apv-repair-low-v2",
    )
    assert upgraded_receipt["repaired"] is True
    assert upgraded_receipt["capital_pool"]["approval_digest"] == v2_candidate_pool_digest
    assert upgraded_receipt["after_resolution"]["risk_policy_version"] == "2.0"
    assert upgraded_receipt["after_resolution"]["profile_mapping_approval_id"] == "apv-repair-low-v2"
    assert (
        upgraded_receipt["after_resolution"]["risk_policy_digest"]
        != noop_receipt["after_resolution"]["risk_policy_digest"]
    )
    # Idempotent replay on v2 pool with genuine approval_digest passes without additional writes
    patches_after_v2 = tx_rep.mutations[("capital", f"{pool_path}/status")]
    v2_noop_receipt = coord_rep.repair_paper_pool_risk_policy(
        tenant_id=record_rep.tenant_id,
        persona_id=record_rep.persona_id,
    )
    assert v2_noop_receipt["repaired"] is False
    assert v2_noop_receipt["idempotent_noop"] is True
    assert v2_noop_receipt["after_resolution"]["resolved"] is True
    assert v2_noop_receipt["after_resolution"]["risk_policy_version"] == "2.0"
    assert v2_noop_receipt["after_resolution"]["profile_mapping_approval_id"] == "apv-repair-low-v2"
    assert tx_rep.mutations[("capital", f"{pool_path}/status")] == patches_after_v2

    # Invalid or contradictory provenance cannot replace the original request.
    with pytest.raises(PersonaProvisioningCoordinationError, match="requested_profile_source_invalid"):
        coord_rep.repair_paper_pool_risk_policy(
            tenant_id=record_rep.tenant_id, persona_id=record_rep.persona_id,
            requested_profile="aggressive",
        )
    assert tx_rep.mutations[("capital", f"{pool_path}/status")] == patches_after_v2

    # Tenant isolation and paper boundary preserved on repair
    with pytest.raises(PersonaProvisioningCoordinationError, match="another tenant"):
        coord_rep.repair_paper_pool_risk_policy(
            tenant_id="tenant-foreign",
            persona_id=record_rep.persona_id,
            capital_pool_id=ids_rep.capital_pool_id,
        )


def test_coordinator_ref_only_creation_and_stale_custom_policy_repair_preserve_authoritative_contract(
    tmp_path, monkeypatch
) -> None:
    import json

    # 1. Ref-only creation resolves rp-custom v2/gross=0.5 through CAPITAL_RISK_POLICY_DIR owner without synthetic v1 snapshot
    policy_dir = tmp_path / "risk_policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    custom_ref_policy = {
        "risk_policy_id": "rp-custom",
        "version": "2.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity"],
        "gross_limit": 0.5,
        "net_limit": 0.5,
        "max_leverage": 1.0,
        "max_single_name_weight": 0.2,
        "max_sector_exposure": {"tech": 0.1},
        "metadata": {"tenant_id": "tenant-a"},
    }
    (policy_dir / "rp-custom.json").write_text(json.dumps(custom_ref_policy), encoding="utf-8")
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(policy_dir))

    store_ref, record_ref = _record_and_store({"risk_policy_ref": "rp-custom"})
    tx_ref = FakeOwnerTransport()
    res_ref = _coordinator(store_ref, tx_ref, _schedule_receipt).coordinate(record_ref)
    assert res_ref.state == "provisioning", res_ref.error
    created_ref_pool = _post_payload(tx_ref, "/api/capital-pools")
    assert created_ref_pool["risk_policy_ref"] == "rp-custom"
    assert created_ref_pool["metadata"]["risk_policy"]["risk_policy_id"] == "rp-custom"
    assert created_ref_pool["metadata"]["risk_policy"]["version"] == "2.0"
    assert created_ref_pool["metadata"]["risk_policy"]["gross_limit"] == 0.5
    assert created_ref_pool["metadata"]["risk_policy"]["max_sector_exposure"] == {"tech": 0.1}

    # 2. Repair of an expired custom inline policy WITHOUT a supplied replacement fails closed and does not replace with 2.0 default
    store_stale, record_stale = _record_and_store(seed_pool_approval=False)
    ids_stale = deterministic_provisioning_ids(record_stale)
    tx_stale = FakeOwnerTransport()
    stale_pool_path = f"/api/capital-pools/{ids_stale.capital_pool_id}"
    expired_custom_inline = {
        "risk_policy_id": "rp-custom-restrictive",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity"],
        "gross_limit": 0.5,
        "net_limit": 0.5,
        "max_leverage": 1.0,
        "max_sector_exposure": {"tech": 0.1},
        "expires_at": "2020-01-01T00:00:00Z",
    }
    tx_stale.objects[("capital", stale_pool_path)] = {
        "pool_id": ids_stale.capital_pool_id,
        "owner_id": record_stale.tenant_id,
        "tenant_id": record_stale.tenant_id,
        "owner_type": "org",
        "status": "active",
        "currency": "USD",
        "budget": 25000,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": record_stale.tenant_id,
            "persona_id": record_stale.persona_id,
            "risk_policy_stale": True,
            "risk_policy": dict(expired_custom_inline),
        },
    }
    coord_stale = _coordinator(store_stale, tx_stale, _schedule_receipt, seed_pool_approval=False)
    with pytest.raises(PersonaProvisioningCoordinationError, match="risk_policy_stale"):
        coord_stale.repair_paper_pool_risk_policy(
            tenant_id=record_stale.tenant_id,
            persona_id=record_stale.persona_id,
        )
    assert tx_stale.mutations[("capital", f"{stale_pool_path}/status")] == 0
    persisted_after_reject = tx_stale.objects[("capital", stale_pool_path)]
    assert persisted_after_reject["metadata"]["risk_policy"]["risk_policy_id"] == "rp-custom-restrictive"
    assert persisted_after_reject["metadata"]["risk_policy"]["gross_limit"] == 0.5
    assert persisted_after_reject["metadata"]["risk_policy_stale"] is True

    # 3. Repair with an authoritative replacement (via owner risk_policy_ref or explicit policy) succeeds and preserves restrictive limits
    repaired_via_ref = coord_stale.repair_paper_pool_risk_policy(
        tenant_id=record_stale.tenant_id,
        persona_id=record_stale.persona_id,
        risk_policy_ref="rp-custom",
    )
    assert repaired_via_ref["repaired"] is True
    assert repaired_via_ref["after_resolution"]["resolved"] is True
    assert repaired_via_ref["after_resolution"]["risk_policy_id"] == "rp-custom"
    assert repaired_via_ref["after_resolution"]["risk_policy_version"] == "2.0"
    assert repaired_via_ref["after_resolution"]["effective_limits"]["gross_limit"] == 0.5
    assert repaired_via_ref["after_resolution"]["effective_limits"]["max_sector_exposure"] == {"tech": 0.1}

    # 4. Changing max_sector_exposure tech 0.1 -> 0.9 changes digest so repair detects non-convergence and updates
    updated_sector_policy = {
        **custom_ref_policy,
        "max_sector_exposure": {"tech": 0.9},
    }
    (policy_dir / "rp-custom.json").write_text(json.dumps(updated_sector_policy), encoding="utf-8")
    repaired_sector = coord_stale.repair_paper_pool_risk_policy(
        tenant_id=record_stale.tenant_id,
        persona_id=record_stale.persona_id,
        risk_policy_ref="rp-custom",
    )
    assert repaired_sector["repaired"] is True
    assert repaired_sector["idempotent_noop"] is False
    assert (
        repaired_sector["after_resolution"]["risk_policy_digest"]
        != repaired_via_ref["after_resolution"]["risk_policy_digest"]
    )
    assert repaired_sector["after_resolution"]["effective_limits"]["max_sector_exposure"] == {"tech": 0.9}


def test_coordinator_ref_only_policies_validated_on_deployment_rebinding_and_publish_pool_envelope_renewal(
    tmp_path, monkeypatch
) -> None:
    import json

    policy_dir = tmp_path / "ref_only_risk_policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    valid_ref_policy = {
        "risk_policy_id": "rp-valid-ref-only",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity"],
        "gross_limit": 0.75,
        "net_limit": 0.75,
        "max_leverage": 1.0,
        "metadata": {"tenant_id": "tenant-a"},
    }
    stale_ref_policy = {
        **valid_ref_policy,
        "risk_policy_id": "rp-stale-ref-only",
        "status": "stale",
    }
    expired_ref_policy = {
        **valid_ref_policy,
        "risk_policy_id": "rp-expired-ref-only",
        "expires_at": "2020-01-01T00:00:00Z",
    }
    wrong_tenant_ref_policy = {
        **valid_ref_policy,
        "risk_policy_id": "rp-wrong-tenant-ref-only",
        "metadata": {"tenant_id": "tenant-foreign"},
    }
    for pol in (valid_ref_policy, stale_ref_policy, expired_ref_policy, wrong_tenant_ref_policy):
        (policy_dir / f"{pol['risk_policy_id']}.json").write_text(json.dumps(pol), encoding="utf-8")
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(policy_dir))

    # 1. Deployment creation rejects missing, stale, expired, and wrong-tenant ref-only policies
    for bad_ref, expected_code in [
        ("rp-missing-ref-only", "risk_policy_missing"),
        ("rp-stale-ref-only", "risk_policy_stale"),
        ("rp-expired-ref-only", "risk_policy_stale"),
        ("rp-wrong-tenant-ref-only", "risk_policy_wrong_tenant"),
    ]:
        store_bad, record_bad = _record_and_store({"risk_policy_ref": bad_ref}, seed_pool_approval=False)
        tx_bad = FakeOwnerTransport()
        res_bad = _coordinator(store_bad, tx_bad, _schedule_receipt, seed_pool_approval=False).coordinate(record_bad)
        assert res_bad.state == "failed"
        assert expected_code in str(res_bad.error or "")

    # 2. Rebinding / retry against an existing ref-only pool (no inline metadata.risk_policy) rejects missing, stale, and wrong-tenant refs
    for bad_ref, expected_code in [
        ("rp-missing-ref-only", "risk_policy_missing"),
        ("rp-stale-ref-only", "risk_policy_stale"),
        ("rp-expired-ref-only", "risk_policy_stale"),
        ("rp-wrong-tenant-ref-only", "risk_policy_wrong_tenant"),
    ]:
        store_reb, record_reb = _record_and_store(seed_pool_approval=False)
        ids_reb = deterministic_provisioning_ids(record_reb)
        tx_reb = FakeOwnerTransport()
        pool_path = f"/api/capital-pools/{ids_reb.capital_pool_id}"
        tx_reb.objects[("capital", pool_path)] = {
            "pool_id": ids_reb.capital_pool_id,
            "owner_id": record_reb.tenant_id,
            "tenant_id": record_reb.tenant_id,
            "owner_type": "org",
            "status": "active",
            "currency": "USD",
            "budget": 25000,
            "risk_policy_ref": bad_ref,
            "single_runtime_enforced": True,
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "tenant_id": record_reb.tenant_id,
                "persona_id": record_reb.persona_id,
            },
        }
        coord_reb = _coordinator(store_reb, tx_reb, _schedule_receipt, seed_pool_approval=False)
        res_reb = coord_reb.coordinate(record_reb)
        assert res_reb.state == "failed"
        assert expected_code in str(res_reb.error or "")

    # 3. publish_pool_envelope renewal validates ref-only bound CapitalPools (no inline metadata.risk_policy)
    from services.control_plane.bff.tests.test_persona_pool_publish_route import (
        FakeRuntimeManager,
        PublishOwnerTransport,
    )

    store_pub, record_pub = _record_and_store({"risk_policy_ref": "rp-valid-ref-only"}, seed_pool_approval=False)
    ids_pub = deterministic_provisioning_ids(record_pub)
    runtime_pub = FakeRuntimeManager()
    tx_pub = PublishOwnerTransport(runtime_pub)
    pool_path_pub = f"/api/capital-pools/{ids_pub.capital_pool_id}"
    coord_pub = PersonaProvisioningCoordinator(
        store=store_pub,
        transport=tx_pub,
        schedule_registrar=_schedule_receipt,
        lease_owner="worker-test",
        actor_id="pantheon-persona-provisioner",
        runtime_binding_reader=runtime_pub.reader,
    )
    res_init = coord_pub.coordinate(record_pub)
    assert res_init.state == "provisioning", res_init.error

    base_artifact = tx_pub.objects[
        ("registry", f"/api/registry/strategy-artifacts/{ids_pub.strategy_artifact_id}")
    ]["entry"]["metadata"]["strategy_artifact"]
    next_envelope = {
        **base_artifact,
        "artifact_id": "artifact-pool-envelope-v2",
        "version": "2.0.0",
    }
    pub_request = {
        "tenant_id": record_pub.tenant_id,
        "persona_id": record_pub.persona_id,
        "pool_version": 2,
        "pool_digest": "sha256:" + "a" * 64,
        "request_hash": record_pub.request_hash,
        "requested_by": "operator-a",
        "stage": "paper",
        "envelope": next_envelope,
        "previous": {
            "artifact_id": ids_pub.strategy_artifact_id,
            "version": ids_pub.version,
            "plan_id": ids_pub.deployment_plan_id,
        },
    }

    for bad_ref, expected_code in [
        ("rp-missing-ref-only", "risk_policy_missing"),
        ("rp-stale-ref-only", "risk_policy_stale"),
        ("rp-expired-ref-only", "risk_policy_stale"),
        ("rp-wrong-tenant-ref-only", "risk_policy_wrong_tenant"),
    ]:
        tx_pub.objects[("capital", pool_path_pub)] = {
            "pool_id": ids_pub.capital_pool_id,
            "owner_id": record_pub.tenant_id,
            "tenant_id": record_pub.tenant_id,
            "owner_type": "org",
            "status": "active",
            "currency": "USD",
            "budget": 25000,
            "risk_policy_ref": bad_ref,
            "single_runtime_enforced": True,
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "tenant_id": record_pub.tenant_id,
                "persona_id": record_pub.persona_id,
            },
        }
        assert "risk_policy" not in tx_pub.objects[("capital", pool_path_pub)]["metadata"]
        with pytest.raises(PersonaProvisioningCoordinationError, match=expected_code):
            coord_pub.publish_pool_envelope(pub_request)

    # Valid ref-only pool (no inline metadata.risk_policy) passes publish_pool_envelope renewal
    tx_pub.objects[("capital", pool_path_pub)] = {
        "pool_id": ids_pub.capital_pool_id,
        "owner_id": record_pub.tenant_id,
        "tenant_id": record_pub.tenant_id,
        "owner_type": "org",
        "status": "active",
        "currency": "USD",
        "budget": 25000,
        "risk_policy_ref": "rp-valid-ref-only",
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": record_pub.tenant_id,
            "persona_id": record_pub.persona_id,
        },
    }
    assert "risk_policy" not in tx_pub.objects[("capital", pool_path_pub)]["metadata"]
    receipt_pub = coord_pub.publish_pool_envelope(pub_request)
    assert receipt_pub["envelope_artifact_id"] == "artifact-pool-envelope-v2"
    assert receipt_pub["pool_version"] == 2


def test_coordinator_autoresolves_profile_policy_without_internal_ids_and_routes_idempotent_owner_disposition(
    tmp_path, monkeypatch
) -> None:
    import json
    from services.capital.risk_policy import capital_pool_approval_digest, risk_policy_digest
    from services.governance.test_approval_authority import approval_snapshot

    policy_dir = tmp_path / "autoresolve_risk_policies"
    policy_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(policy_dir))

    # 1. Clean create with ordinary risk="low" and NO internal IDs (risk_policy_ref=None, no risk_policy,
    # no profile_mapping_approval_id) auto-resolves an approved pool-targeted decision whose ID is an
    # arbitrary UUID (NOT apv-capital-pool-{pool_id}) and recovers across POST /api/capital-pools response loss.
    store_arb, record_arb = _record_and_store({"risk": "low"}, seed_pool_approval=False)
    assert "profile_mapping_approval_id" not in record_arb.request_payload
    assert "risk_policy_ref" not in record_arb.request_payload
    assert "risk_policy" not in record_arb.request_payload
    ids_arb = deterministic_provisioning_ids(record_arb)
    tx_arb = FakeOwnerTransport(response_loss={"/api/capital-pools"}, seed_pool_policy_approval=False)
    coord_arb = _coordinator(store_arb, tx_arb, _schedule_receipt, seed_pool_approval=False)
    baseline_arb = _fixture_paper_policy(ids_arb.capital_pool_id, version="1.0")
    arb_pool_digest = capital_pool_approval_digest(
        {
            "pool_id": ids_arb.capital_pool_id,
            "owner_id": record_arb.tenant_id,
            "owner_type": "org",
            "currency": "USD",
            "budget": 25000,
            "risk_policy_ref": None,
            "single_runtime_enforced": True,
            "metadata": {"risk_policy": baseline_arb},
        }
    )
    arbitrary_dec_id = "apv-arbitrary-uuid-7729c10e"
    tx_arb.objects[("governance", f"/api/governance/approvals/{arbitrary_dec_id}")] = approval_snapshot(
        decision_id=arbitrary_dec_id,
        tenant_id=record_arb.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_arb.capital_pool_id,
        target_version=arb_pool_digest,
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": ids_arb.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": baseline_arb["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(baseline_arb),
                "risk_officer_draft": {"policy": baseline_arb},
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    res_arb = coord_arb.coordinate(record_arb)
    assert res_arb.state == "provisioning", res_arb.error
    persisted_arb_pool = tx_arb.get("capital", f"/api/capital-pools/{ids_arb.capital_pool_id}")
    assert persisted_arb_pool is not None
    assert persisted_arb_pool["metadata"]["requested_risk_profile"] == "low"
    assert persisted_arb_pool["metadata"]["require_approved_profile_mapping"] is True
    assert persisted_arb_pool["metadata"]["profile_mapping_approval_id"] == arbitrary_dec_id
    assert res_arb.references["capital_pool_risk_policy_resolution"]["resolved"] is True
    assert res_arb.references["capital_pool_risk_policy_resolution"]["profile_mapping_approval_id"] == arbitrary_dec_id
    assert tx_arb.mutations[("capital", "/api/capital-pools")] == 1

    # 2. Clean create with ordinary risk="low" and NO internal IDs auto-resolves an approved catalog policy
    # from CAPITAL_RISK_POLICY_DIR (with policy-scoped Governance decision apv-catalog-low-881) and persists
    # risk_policy_ref, version, limits, and profile_mapping_approval_id.
    catalog_low_policy = {
        "risk_policy_id": "rp-tenant-a-paper-low-v1",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "allowed_asset_classes": ["equity", "future"],
        "gross_limit": 0.85,
        "net_limit": 0.65,
        "max_leverage": 1.0,
        "max_single_name_weight": 0.2,
        "metadata": {"tenant_id": "tenant-a", "requested_profile": "low"},
    }
    (policy_dir / "rp-tenant-a-paper-low-v1.json").write_text(json.dumps(catalog_low_policy), encoding="utf-8")
    store_cat, record_cat = _record_and_store({"risk": "low"}, seed_pool_approval=False)
    tx_cat = FakeOwnerTransport(seed_pool_policy_approval=False)
    tx_cat.objects[("governance", "/api/governance/approvals/apv-catalog-low-881")] = approval_snapshot(
        decision_id="apv-catalog-low-881",
        tenant_id="tenant-a",
        target_type="capital_pool_activation",
        target_id="rp-tenant-a-paper-low-v1",
        target_version=risk_policy_digest(catalog_low_policy),
        owner_user_id="proposer-1",
        risk_level="low",
        metadata={
            "subject": {
                "pool_id": "rp-tenant-a-paper-low-v1",
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": "rp-tenant-a-paper-low-v1",
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(catalog_low_policy),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
            ],
        },
    )
    res_cat = _coordinator(store_cat, tx_cat, _schedule_receipt, seed_pool_approval=False).coordinate(record_cat)
    assert res_cat.state == "provisioning", res_cat.error
    created_cat_pool = _post_payload(tx_cat, "/api/capital-pools")
    assert created_cat_pool["risk_policy_ref"] == "rp-tenant-a-paper-low-v1"
    assert created_cat_pool["metadata"]["risk_policy"]["risk_policy_id"] == "rp-tenant-a-paper-low-v1"
    assert created_cat_pool["metadata"]["risk_policy"]["gross_limit"] == 0.85
    assert created_cat_pool["metadata"]["risk_policy"]["max_single_name_weight"] == 0.2
    assert created_cat_pool["metadata"]["profile_mapping_approval_id"] == "apv-catalog-low-881"

    # 2b. Authority loss on the owner ApprovalDecision after normal coordinator persistence
    # (revoked, expired, rejected, cross-tenant, or removed) must fail closed on bound runtime,
    # direct coordinator readback resolution, repair_paper_pool_risk_policy, and resumed coordinate().
    from services.capital.risk_policy import (
        RiskPolicyError,
        resolve_bound_pool_risk_policy,
        resolve_pool_risk_policy_binding,
    )

    ids_cat = deterministic_provisioning_ids(record_cat)
    cat_pool_path = f"/api/capital-pools/{ids_cat.capital_pool_id}"
    persisted_cat_pool_snapshot = deepcopy(tx_cat.get("capital", cat_pool_path))
    assert persisted_cat_pool_snapshot is not None
    assert "profile_auto_resolved" not in persisted_cat_pool_snapshot["metadata"]
    valid_cat_dec_snapshot = deepcopy(
        tx_cat.objects[("governance", "/api/governance/approvals/apv-catalog-low-881")]
    )
    coord_cat_check = PersonaProvisioningCoordinator(
        store=store_cat,
        transport=tx_cat,
        schedule_registrar=_schedule_receipt,
        lease_owner="test-worker",
        actor_id="pantheon-persona-provisioner",
        governance_proposer=tx_cat,
        governance_proposer_actor_id="pantheon-dev-persona-enrollment-proposer",
    )
    tx_cat.governance_subject = "pantheon-dev-persona-enrollment-proposer"
    tx_cat.governance_roles = {"approval_proposer"}

    initial_tx_objects = deepcopy(tx_cat.objects)

    for loss_case, dec_override, expected_absence in [
        ("revoked", {"revoked_at": "2026-11-22T00:00:00Z"}, "policy_approval_revoked"),
        ("expired", {"expires_at": "2000-01-01T00:00:00Z"}, "policy_approval_expired"),
        ("rejected", {"decision": "rejected"}, "policy_approval_rejected"),
        ("cross_tenant", {"tenant_id": "tenant-other"}, "cross_tenant_policy_or_approval_rejected"),
        ("missing", None, "policy_approval_missing"),
    ]:
        tx_cat.objects = deepcopy(initial_tx_objects)
        tx_cat.objects[("capital", cat_pool_path)] = deepcopy(persisted_cat_pool_snapshot)
        if dec_override is None:
            tx_cat.objects.pop(("governance", "/api/governance/approvals/apv-catalog-low-881"), None)
        else:
            tx_cat.objects[("governance", "/api/governance/approvals/apv-catalog-low-881")] = {
                **deepcopy(valid_cat_dec_snapshot),
                **dec_override,
            }
        tx_reader = coord_cat_check._governance_approval_reader()
        readback_pool = tx_cat.get("capital", cat_pool_path)
        bind_res = resolve_pool_risk_policy_binding(
            pool=readback_pool,
            expected_tenant_id="tenant-a",
            expected_stage="paper",
            approval_reader=tx_reader,
        )
        assert bind_res.resolved is False, loss_case
        assert bind_res.status == "unresolved_profile_mapping", loss_case
        assert bind_res.profile_mapping_status == "unresolved_owner_decision_required", loss_case
        assert bind_res.profile_mapping_approval_id is None, loss_case
        assert bind_res.allow_new_or_increased_risk is False, loss_case
        assert bind_res.owner_decision_request["absence_source"] == expected_absence, loss_case
        with pytest.raises(RiskPolicyError, match="risk_policy_profile_mapping_unapproved"):
            resolve_bound_pool_risk_policy(
                pool=readback_pool,
                expected_tenant_id="tenant-a",
                expected_stage="paper",
                approval_reader=tx_reader,
            )

        rep_loss = coord_cat_check.repair_paper_pool_risk_policy(
            tenant_id=record_cat.tenant_id,
            persona_id=record_cat.persona_id,
            dry_run=True,
        )
        assert rep_loss["repaired"] is False, loss_case
        assert rep_loss["after_resolution"]["resolved"] is False, loss_case
        assert rep_loss["after_resolution"]["allow_new_or_increased_risk"] is False, loss_case
        assert rep_loss["owner_decision_request"]["absence_source"] == expected_absence, loss_case

        store_resume, resume_record = _record_and_store({"risk": "low"}, seed_pool_approval=False)
        coord_resume = PersonaProvisioningCoordinator(
            store=store_resume,
            transport=tx_cat,
            schedule_registrar=_schedule_receipt,
            lease_owner="test-worker",
            actor_id="pantheon-persona-provisioner",
            governance_proposer=tx_cat,
            governance_proposer_actor_id="pantheon-dev-persona-enrollment-proposer",
        )
        resume_res = coord_resume.coordinate(resume_record)
        assert resume_res.state in {"failed", "compensated"}, loss_case
        assert resume_res.error["failed_step"] == "capital_pool", loss_case
        assert resume_res.error["reason_code"] == "risk_policy_profile_mapping_unapproved", loss_case
        assert resume_res.error["absence_source"] == expected_absence, loss_case
        assert tx_cat.get("capital", cat_pool_path)["status"] == "suspended", loss_case
        assert tx_cat.get("capital", cat_pool_path)["metadata"]["provisional_unresolved_risk_policy"] is True, loss_case

    (policy_dir / "rp-tenant-a-paper-low-v1.json").unlink()

    # 3. Provisional active-looking unresolved pool record is suspended on unresolved coordinate(),
    # routes a deterministic Governance proposal without duplicates on retry (including response loss),
    # and recovers to active once the owner approves the proposal.
    store_prov, record_prov = _record_and_store({"risk": "low"}, seed_pool_approval=False)
    ids_prov = deterministic_provisioning_ids(record_prov)
    tx_prov = FakeOwnerTransport(
        response_loss={"/api/governance/approvals"},
        governance_subject="pantheon-dev-persona-enrollment-proposer",
        governance_roles=("approval_proposer",),
        seed_pool_policy_approval=False,
    )
    prov_pool_path = f"/api/capital-pools/{ids_prov.capital_pool_id}"
    tx_prov.objects[("capital", prov_pool_path)] = {
        "pool_id": ids_prov.capital_pool_id,
        "owner_id": record_prov.tenant_id,
        "tenant_id": record_prov.tenant_id,
        "owner_type": "org",
        "status": "active",
        "currency": "USD",
        "budget": 25000,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": record_prov.tenant_id,
            "persona_id": record_prov.persona_id,
        },
    }
    coord_prov = PersonaProvisioningCoordinator(
        store=store_prov,
        transport=tx_prov,
        schedule_registrar=_schedule_receipt,
        lease_owner="test-worker",
        actor_id="pantheon-persona-provisioner",
        governance_proposer=tx_prov,
        governance_proposer_actor_id="pantheon-dev-persona-enrollment-proposer",
    )
    res_prov_fail = coord_prov.coordinate(record_prov)
    assert res_prov_fail.state == "failed"
    assert res_prov_fail.error["failed_step"] == "capital_pool"
    assert res_prov_fail.error["reason_code"] == "risk_policy_profile_mapping_unapproved"
    assert res_prov_fail.error["absence_source"] == "policy_definition_and_approval_absent"
    routed_req = res_prov_fail.error["owner_decision_request"]
    prop_id = routed_req["deterministic_proposal_id"]
    assert routed_req["governance_proposal_id"] == prop_id
    assert routed_req["governance_proposal_status"] == "proposed_to_governance_human_inbox"
    assert tx_prov.mutations[("governance", "/api/governance/approvals")] == 1
    # Direction is computed against the pool's effective policy (here identical to the draft) and stored with
    # the per-limit comparison; paper pool proposals are low risk with the bounded expiry the paper gate needs.
    proposed_decision = tx_prov.objects[("governance", f"/api/governance/approvals/{prop_id}")]
    assert proposed_decision["metadata"]["subject"]["risk_direction"] == "neutral"
    assert set(proposed_decision["metadata"]["subject"]["risk_comparison"].values()) == {"equal"}
    assert proposed_decision["risk_level"] == "low" and proposed_decision["expires_at"]
    # Provisional pool was suspended and marked provisional_unresolved_risk_policy=True so it cannot add risk
    assert tx_prov.objects[("capital", prov_pool_path)]["status"] == "suspended"
    assert tx_prov.objects[("capital", prov_pool_path)]["metadata"]["provisional_unresolved_risk_policy"] is True

    # Retry while proposal is still pending reuses the existing proposal with 0 duplicate POSTs
    res_prov_retry = coord_prov.coordinate(record_prov)
    assert res_prov_retry.state == "failed"
    assert res_prov_retry.error["owner_decision_request"]["governance_proposal_status"] == "reused_existing_pending_proposal"
    assert res_prov_retry.error["owner_decision_request"]["governance_proposal_id"] == prop_id
    assert tx_prov.mutations[("governance", "/api/governance/approvals")] == 1

    # Now approve the proposal in Governance (and switch transport principal back to automated gate for registry admission)
    tx_prov.governance_subject = "pantheon-persona-provisioner"
    tx_prov.governance_roles = {"approval_proposer", "automated_gate"}
    tx_prov.objects[("governance", f"/api/governance/approvals/{prop_id}")] = approval_snapshot(
        decision_id=prop_id,
        tenant_id=record_prov.tenant_id,
        target_type="capital_pool_activation",
        target_id=ids_prov.capital_pool_id,
        target_version=routed_req["candidate_pool_approval_digest"],
        owner_user_id="pantheon-dev-persona-enrollment-proposer",
        risk_level="medium",
        metadata={
            "subject": {
                "pool_id": ids_prov.capital_pool_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": routed_req["candidate_risk_policy_id"],
                "risk_policy_version": routed_req["candidate_risk_policy_version"],
                "risk_policy_digest": routed_req["candidate_risk_policy_digest"],
                "risk_officer_draft": _draft_embed(routed_req),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "risk_owner"},
            ],
        },
    )
    res_prov_ok = coord_prov.coordinate(record_prov)
    assert res_prov_ok.state == "provisioning", res_prov_ok.error
    converged_prov_pool = tx_prov.get("capital", prov_pool_path)
    assert converged_prov_pool["status"] == "active"
    assert converged_prov_pool["metadata"].get("provisional_unresolved_risk_policy") is False
    assert converged_prov_pool["metadata"]["profile_mapping_approval_id"] == prop_id

    # 4. Existing three dev paper pools (06d4775a9b6a8f63b311, 039ad01f8bcd2b4aa466, a81b4fd15944698341aa):
    # reproduce actual absence, idempotent proposal routing, explicit non-approval, and auto-resolved repair without internal IDs.
    three_pools = [
        ("persona-2448c7053e33ea48da47", "pool-persona-paper-06d4775a9b6a8f63b311", "pcb-persona-paper-06d4775a9b6a8f63b311", "TWD"),
        ("persona-6507750312870d03a241", "pool-persona-paper-039ad01f8bcd2b4aa466", "pcb-persona-paper-039ad01f8bcd2b4aa466", "TWD"),
        ("persona-ee7112a829d38b9135ac", "pool-persona-paper-a81b4fd15944698341aa", "pcb-persona-paper-a81b4fd15944698341aa", "TWD"),
    ]
    store_3 = TrackingStore()
    tx_3 = FakeOwnerTransport(
        governance_subject="pantheon-dev-persona-enrollment-proposer",
        governance_roles=("approval_proposer",),
        seed_pool_policy_approval=False,
    )
    coord_3 = PersonaProvisioningCoordinator(
        store=store_3,
        transport=tx_3,
        schedule_registrar=_schedule_receipt,
        lease_owner="test-worker",
        actor_id="pantheon-persona-provisioner",
        governance_proposer=tx_3,
        governance_proposer_actor_id="pantheon-dev-persona-enrollment-proposer",
    )
    for idx, (p_id, cp_id, b_id, curr) in enumerate(three_pools):
        p_path = f"/api/capital-pools/{cp_id}"
        tx_3.objects[("capital", p_path)] = {
            "pool_id": cp_id,
            "owner_id": "tenant-dev",
            "tenant_id": "tenant-dev",
            "owner_type": "org",
            "status": "active",
            "currency": curr,
            "budget": 100000.0,
            "single_runtime_enforced": True,
            "metadata": {
                "internal": True,
                "execution_context": "paper",
                "tenant_id": "tenant-dev",
                "persona_id": p_id,
                "risk_policy": _fixture_paper_policy(cp_id, version="1.0"),
            },
        }
        first_rep = coord_3.repair_paper_pool_risk_policy(
            tenant_id="tenant-dev",
            persona_id=p_id,
            capital_pool_id=cp_id,
            binding_id=b_id,
            requested_profile="low",  # Verified hosted source represented explicitly in this fixture.
        )
        assert first_rep["repaired"] is False
        assert first_rep["after_resolution"]["status"] == "unresolved_profile_mapping"
        assert first_rep["after_resolution"]["allow_new_or_increased_risk"] is False
        assert first_rep["after_resolution"]["allow_risk_reducing_actions"] is True
        od_req = first_rep["owner_decision_request"]
        assert od_req["disposition_task_id"] == "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122"
        assert od_req["hosted_proof_task_id"] == "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121"
        assert od_req["governance_proposal_status"] == "proposed_to_governance_human_inbox"
        assert tx_3.mutations[("governance", "/api/governance/approvals")] == idx + 1

        # Second call reuses existing pending proposal with 0 duplicate POSTs
        second_rep = coord_3.repair_paper_pool_risk_policy(
            tenant_id="tenant-dev",
            persona_id=p_id,
            capital_pool_id=cp_id,
            binding_id=b_id,
            requested_profile="low",  # Verified hosted source represented explicitly in this fixture.
        )
        assert second_rep["repaired"] is False
        assert second_rep["owner_decision_request"]["governance_proposal_status"] == "reused_existing_pending_proposal"
        assert tx_3.mutations[("governance", "/api/governance/approvals")] == idx + 1

        # Once owner approves the routed proposal (with its deterministic apv-risk-profile-* ID),
        # repair_paper_pool_risk_policy with NO approval_decision_id argument auto-resolves and converges
        routed_prop_id = od_req["deterministic_proposal_id"]
        assert routed_prop_id == [
            "apv-risk-profile-550ae83ccfeaafa69ec1",
            "apv-risk-profile-a1cdc8408fe251534581",
            "apv-risk-profile-6bf0cafe806a1e89fb34",
        ][idx]
        tx_3.objects[("governance", f"/api/governance/approvals/{routed_prop_id}")] = approval_snapshot(
            decision_id=routed_prop_id,
            tenant_id="tenant-dev",
            target_type="capital_pool_activation",
            target_id=cp_id,
            target_version=od_req["candidate_pool_approval_digest"],
            owner_user_id="pantheon-dev-persona-enrollment-proposer",
            risk_level="medium",
            metadata={
                "subject": {
                    "pool_id": cp_id,
                    "risk_direction": "increase",
                    "requested_risk": "low",
                    "risk_policy_id": od_req["candidate_risk_policy_id"],
                    "risk_policy_version": "1.0",
                    "risk_policy_digest": od_req["candidate_risk_policy_digest"],
                    "risk_officer_draft": _draft_embed(od_req),
                },
                "approvals": [
                    {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                    {"actor_id": "reviewer-2", "actor_role": "risk_owner"},
                ],
            },
        )
        converged_rep = coord_3.repair_paper_pool_risk_policy(
            tenant_id="tenant-dev",
            persona_id=p_id,
            capital_pool_id=cp_id,
            binding_id=b_id,
            requested_profile="low",  # Verified hosted source represented explicitly in this fixture.
        )
        assert converged_rep["repaired"] is True
        assert converged_rep["after_resolution"]["resolved"] is True
        assert converged_rep["after_resolution"]["profile_mapping_approval_id"] == routed_prop_id
        assert converged_rep["capital_pool"]["metadata"]["profile_mapping_approval_id"] == routed_prop_id

    # 5. Version upgrade (1.0 -> 2.0) auto-discovers v2 approval without caller passing approval_decision_id,
    # and explicit non-approval (decision="rejected") fails closed without re-proposing.
    p0_id, cp0_id, b0_id, _ = three_pools[0]
    v2_unapproved = coord_3.repair_paper_pool_risk_policy(
        tenant_id="tenant-dev",
        persona_id=p0_id,
        capital_pool_id=cp0_id,
        binding_id=b0_id,
        risk_policy_version="2.0",
    )
    assert v2_unapproved["repaired"] is False
    v2_prop_id = v2_unapproved["owner_decision_request"]["deterministic_proposal_id"]
    proposals_after_v2_req = tx_3.mutations[("governance", "/api/governance/approvals")]

    # Simulate explicit non-approval (decision="rejected") by risk_owner
    tx_3.objects[("governance", f"/api/governance/approvals/{v2_prop_id}")] = {
        **tx_3.objects[("governance", f"/api/governance/approvals/{v2_prop_id}")],
        "decision_state": "decided",
        "decision": "rejected",
        "actor_id": "risk-owner-1",
        "actor_role": "risk_owner",
        "decided_at": _owner_now(),
        "recorded_at": _owner_now(),
        "authority_status": "authoritative",
        "controller_record_ref": f"governance-controller://approval-{v2_prop_id}",
    }
    v2_rejected_retry = coord_3.repair_paper_pool_risk_policy(
        tenant_id="tenant-dev",
        persona_id=p0_id,
        capital_pool_id=cp0_id,
        binding_id=b0_id,
        risk_policy_version="2.0",
    )
    assert v2_rejected_retry["repaired"] is False
    assert v2_rejected_retry["owner_decision_request"]["governance_proposal_status"] == "explicit_non_approval_recorded"
    assert v2_rejected_retry["owner_decision_request"]["absence_source"] == "policy_approval_rejected"
    assert tx_3.mutations[("governance", "/api/governance/approvals")] == proposals_after_v2_req

    # Once a separate v2 approval decision with an arbitrary ID is approved, repair with risk_policy_version="2.0"
    # and NO approval_decision_id auto-discovers it and upgrades the pool.
    tx_3.objects.pop(("governance", f"/api/governance/approvals/{v2_prop_id}"), None)
    tx_3.objects[("governance", "/api/governance/approvals/apv-arbitrary-v2-upgrade-551")] = approval_snapshot(
        decision_id="apv-arbitrary-v2-upgrade-551",
        tenant_id="tenant-dev",
        target_type="capital_pool_activation",
        target_id=cp0_id,
        target_version=v2_unapproved["owner_decision_request"]["candidate_pool_approval_digest"],
        owner_user_id="pantheon-dev-persona-enrollment-proposer",
        risk_level="medium",
        metadata={
            "subject": {
                "pool_id": cp0_id,
                "risk_direction": "increase",
                "requested_risk": "low",
                "risk_policy_id": v2_unapproved["owner_decision_request"]["candidate_risk_policy_id"],
                "risk_policy_version": "2.0",
                "risk_policy_digest": v2_unapproved["owner_decision_request"]["candidate_risk_policy_digest"],
                "risk_officer_draft": _draft_embed(v2_unapproved["owner_decision_request"]),
            },
            "approvals": [
                {"actor_id": "reviewer-1", "actor_role": "governance_reviewer"},
                {"actor_id": "reviewer-2", "actor_role": "risk_owner"},
            ],
        },
    )
    v2_converged = coord_3.repair_paper_pool_risk_policy(
        tenant_id="tenant-dev",
        persona_id=p0_id,
        capital_pool_id=cp0_id,
        binding_id=b0_id,
        risk_policy_version="2.0",
    )
    assert v2_converged["repaired"] is True
    assert v2_converged["after_resolution"]["resolved"] is True
    assert v2_converged["after_resolution"]["risk_policy_version"] == "2.0"
    assert v2_converged["after_resolution"]["profile_mapping_approval_id"] == "apv-arbitrary-v2-upgrade-551"

    # 6. Observable BFF UI/API metadata projection on unresolved vs resolved provisioning records
    from services.control_plane.bff.personas import service as personas_service
    from services.control_plane.bff.personas.service import (
        _persona_create_response,
        _persona_provisioning_authoritative_meta,
        _persona_provisioning_metadata,
    )

    monkeypatch.setattr(
        personas_service,
        "_overlay_source_health_truth",
        lambda status, sources, **kw: (status, sources, []),
    )

    unresolved_meta = _persona_provisioning_metadata(
        res_prov_retry,
        ids=ids_prov,
        payload=res_prov_retry.request_payload,
        owner="operator-a",
        archetype="generalist",
        risk="low",
        mandate="Paper-only momentum research",
        strategy_family="momentum",
        traits={"risk_appetite": "low"},
        lifecycle_state="provisioning_failed",
    )
    assert unresolved_meta["provisioning_waiting_reason"] == "policy_approval_pending"
    assert unresolved_meta["recommended_governance_action"] == "approve_capital_pool_risk_policy_mapping"
    assert unresolved_meta["risk_policy_resolution"]["resolved"] is False
    assert unresolved_meta["risk_policy_owner_decision_request"]["governance_proposal_id"] == prop_id
    auth_meta_unresolved = _persona_provisioning_authoritative_meta({"metadata": unresolved_meta})
    assert auth_meta_unresolved["provisioning_waiting_reason"] == "policy_approval_pending"
    assert auth_meta_unresolved["risk_policy_owner_decision_request"]["governance_proposal_id"] == prop_id

    resolved_meta = _persona_provisioning_metadata(
        res_prov_ok,
        ids=ids_prov,
        payload=res_prov_ok.request_payload,
        owner="operator-a",
        archetype="generalist",
        risk="low",
        mandate="Paper-only momentum research",
        strategy_family="momentum",
        traits={"risk_appetite": "low"},
        lifecycle_state="provisioning",
    )
    assert resolved_meta["risk_policy_resolution"]["resolved"] is True
    assert resolved_meta["risk_policy_resolution"]["profile_mapping_approval_id"] == prop_id
    create_resp = _persona_create_response(
        res_prov_ok,
        persona={
            "id": res_prov_ok.persona_id,
            "persona_id": res_prov_ok.persona_id,
            "name": "Trader A",
            "lifecycle_state": "provisioning",
            "metadata": resolved_meta,
        },
        metadata=resolved_meta,
        payload=res_prov_ok.request_payload,
        snapshot_at="2026-11-22T00:00:00Z",
    )
    assert create_resp["meta"]["risk_policy_resolution"]["resolved"] is True
    assert create_resp["meta"]["risk_policy_resolution"]["profile_mapping_approval_id"] == prop_id

    # 7. Two approved catalog policies (ambiguous_profile_mapping) preserve ambiguity across create, resume
    # (including provisional active pool suspension), and repair — never promoting an arbitrary candidate.
    # Also verify lesson-shaped pool_policy_parameter_proposal is rejected as activation authority.
    store_amb, rec_amb = _record_and_store({"risk": "low"}, seed_pool_approval=False)
    ids_amb = deterministic_provisioning_ids(rec_amb)
    tx_amb = FakeOwnerTransport(
        governance_subject="pantheon-dev-persona-enrollment-proposer",
        governance_roles=("approval_proposer",),
        seed_pool_policy_approval=False,
    )
    coord_amb = PersonaProvisioningCoordinator(
        store=store_amb,
        transport=tx_amb,
        schedule_registrar=_schedule_receipt,
        lease_owner="test-worker",
        actor_id="pantheon-persona-provisioner",
        governance_proposer=tx_amb,
        governance_proposer_actor_id="pantheon-dev-persona-enrollment-proposer",
    )
    amb_policies = []
    for n in (1, 2):
        pol = {
            "risk_policy_id": f"rp-review-low-{n}",
            "version": "1.0",
            "status": "active",
            "allowed_stages": ["paper"],
            "gross_limit": n * 0.4,
            "net_limit": 0.6,
            "max_leverage": 1.0,
            "metadata": {"tenant_id": rec_amb.tenant_id, "requested_profile": "low"},
        }
        (policy_dir / f"{pol['risk_policy_id']}.json").write_text(json.dumps(pol), encoding="utf-8")
        dec = approval_snapshot(
            decision_id=f"apv-review-{n}",
            tenant_id=rec_amb.tenant_id,
            target_type="capital_pool_activation",
            target_id=pol["risk_policy_id"],
            target_version=risk_policy_digest(pol),
            owner_user_id="proposer",
            risk_level="medium",
            metadata={
                "subject": {
                    "pool_id": pol["risk_policy_id"],
                    "risk_direction": "increase",
                    "requested_risk": "low",
                    "risk_policy_id": pol["risk_policy_id"],
                    "risk_policy_version": "1.0",
                    "risk_policy_digest": risk_policy_digest(pol),
                },
                "approvals": [
                    {"actor_id": "reviewer-1", "actor_role": "risk_owner"},
                    {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
                ],
            },
        )
        tx_amb.objects[("governance", f"/api/governance/approvals/{dec['decision_id']}")] = dec
        amb_policies.append(pol)

    with pytest.raises(PersonaProvisioningCoordinationError) as exc_amb:
        coord_amb._resolve_requested_pool_risk_policy_with_provenance(rec_amb, ids_amb)
    assert exc_amb.value.reason_code == "risk_policy_profile_mapping_ambiguous"
    assert exc_amb.value.resolution_status == "ambiguous_profile_mapping"
    assert exc_amb.value.absence_source == "policy_candidates_ambiguous"

    # Full coordinate (create) fails closed with ambiguous_profile_mapping and creates no pool in Capital
    full_amb_create = coord_amb.coordinate(rec_amb)
    assert full_amb_create.state == "failed"
    assert full_amb_create.error["failed_step"] == "capital_pool"
    assert full_amb_create.error["reason_code"] == "risk_policy_profile_mapping_ambiguous"
    assert full_amb_create.error["resolution_status"] == "ambiguous_profile_mapping"
    assert full_amb_create.error["absence_source"] == "policy_candidates_ambiguous"
    assert full_amb_create.error["owner_decision_request"]["ambiguous_policy_ids"] == [
        "rp-review-low-1",
        "rp-review-low-2",
    ]
    assert full_amb_create.error["owner_decision_request"]["ambiguous_approval_decision_ids"] == [
        "apv-review-1",
        "apv-review-2",
    ]
    amb_pool_path = f"/api/capital-pools/{ids_amb.capital_pool_id}"
    assert tx_amb.get("capital", amb_pool_path) is None
    assert tx_amb.mutations[("governance", "/api/governance/approvals")] == 0

    # Resume when a provisional active pool record exists suspends the provisional pool and preserves ambiguity
    tx_amb.objects[("capital", amb_pool_path)] = {
        "pool_id": ids_amb.capital_pool_id,
        "owner_id": rec_amb.tenant_id,
        "tenant_id": rec_amb.tenant_id,
        "owner_type": "org",
        "status": "active",
        "currency": "USD",
        "budget": 25000,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": rec_amb.tenant_id,
            "persona_id": rec_amb.persona_id,
            "risk_policy": amb_policies[0],
            "profile_mapping_approval_id": "apv-review-1",
        },
    }
    full_amb_resume = coord_amb.coordinate(rec_amb)
    assert full_amb_resume.state == "failed"
    assert full_amb_resume.error["reason_code"] == "risk_policy_profile_mapping_ambiguous"
    assert full_amb_resume.error["resolution_status"] == "ambiguous_profile_mapping"
    assert tx_amb.get("capital", amb_pool_path)["status"] == "suspended"
    assert tx_amb.get("capital", amb_pool_path)["metadata"]["provisional_unresolved_risk_policy"] is True

    # Repair on the pool also preserves ambiguous_profile_mapping and never promotes an arbitrary candidate
    rep_amb = coord_amb.repair_paper_pool_risk_policy(
        tenant_id=rec_amb.tenant_id,
        persona_id=rec_amb.persona_id,
    )
    assert rep_amb["repaired"] is False
    assert rep_amb["after_resolution"]["resolved"] is False
    assert rep_amb["after_resolution"]["status"] == "ambiguous_profile_mapping"
    assert rep_amb["after_resolution"]["reason_code"] == "risk_policy_profile_mapping_ambiguous"
    assert rep_amb["owner_decision_request"]["absence_source"] == "policy_candidates_ambiguous"
    assert rep_amb["owner_decision_request"]["ambiguous_policy_ids"] == [
        "rp-review-low-1",
        "rp-review-low-2",
    ]

    # Remove the second policy and replace apv-review-1 with a lesson-shaped pool_policy_parameter_proposal:
    # coordinator must reject it as activation authority.
    (policy_dir / "rp-review-low-2.json").unlink()
    tx_amb.objects.pop(("governance", "/api/governance/approvals/apv-review-1"), None)
    tx_amb.objects.pop(("governance", "/api/governance/approvals/apv-review-2"), None)
    tx_amb.objects[("governance", "/api/governance/approvals/apv-parameter-only")] = approval_snapshot(
        decision_id="apv-parameter-only",
        tenant_id=rec_amb.tenant_id,
        target_type="pool_policy_parameter_proposal",
        target_id=amb_policies[0]["risk_policy_id"],
        target_version="1.0",
        owner_user_id="proposer",
        persona_id=rec_amb.persona_id,
        risk_level="high",
        actor_role="risk_owner",
        metadata={
            "subject": {
                "proposal_kind": "pool_policy",
                "lesson_ids": "lesson-review",
                "case_file_id": "case-review",
                "proposal_digest": "a" * 64,
                "requested_risk": "low",
                "risk_policy_id": amb_policies[0]["risk_policy_id"],
                "risk_policy_version": "1.0",
                "risk_policy_digest": risk_policy_digest(amb_policies[0]),
            }
        },
    )
    with pytest.raises(PersonaProvisioningCoordinationError) as exc_param:
        coord_amb._resolve_requested_pool_risk_policy_with_provenance(rec_amb, ids_amb)
    assert exc_param.value.reason_code == "risk_policy_profile_mapping_unapproved"
    rep_param = coord_amb.repair_paper_pool_risk_policy(
        tenant_id=rec_amb.tenant_id,
        persona_id=rec_amb.persona_id,
    )
    assert rep_param["repaired"] is False
    assert rep_param["after_resolution"]["status"] == "unresolved_profile_mapping"
    (policy_dir / "rp-review-low-1.json").unlink()

    # 8. Full-path credentials-scoped HTTP route requests (POST /bff/personas, GET /bff/personas/{id},
    # POST /bff/personas/{id}/provisioning/paper-capital/risk-policy) via mounted FastAPI router.
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.command_queue import CommandStore
    from services.control_plane.bff.personas.router import create_personas_router
    from services.control_plane.bff.tests.conftest import make_composed_persona_service

    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    AUTH_HEADERS = {"Authorization": "Bearer test-operator:operator,reviewer,admin"}

    route_personas: dict[str, dict[str, Any]] = {}
    route_store = TrackingStore()
    route_tx = FakeOwnerTransport(
        governance_subject="pantheon-dev-persona-enrollment-proposer",
        governance_roles=("approval_proposer",),
        seed_pool_policy_approval=False,
    )
    route_coord = PersonaProvisioningCoordinator(
        store=route_store,
        transport=route_tx,
        schedule_registrar=_schedule_receipt,
        lease_owner="route-test-worker",
        actor_id="pantheon-persona-provisioner",
        governance_proposer=route_tx,
        governance_proposer_actor_id="pantheon-dev-persona-enrollment-proposer",
    )

    class _RouteWriteOwner:
        def create_persona(self, record_in: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
            saved = deepcopy(dict(record_in or kwargs))
            pid = str(saved.get("persona_id") or saved.get("id"))
            saved.setdefault("id", pid)
            saved.setdefault("persona_id", pid)
            route_personas[pid] = saved
            return deepcopy(saved)

        def update_persona(self, pid: str, **kwargs: Any) -> dict[str, Any]:
            cur = deepcopy(route_personas.get(pid) or {"id": pid, "persona_id": pid, "metadata": {}})
            if "lifecycle_state" in kwargs and kwargs["lifecycle_state"] is not None:
                cur["lifecycle_state"] = kwargs["lifecycle_state"]
            if isinstance(kwargs.get("metadata"), Mapping):
                cur["metadata"] = {**(cur.get("metadata") or {}), **dict(kwargs["metadata"])}
            route_personas[pid] = cur
            return deepcopy(cur)

        def patch_persona(self, pid: str, patch_in: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
            return self.update_persona(pid, **(dict(patch_in or {}) | kwargs))

    from services.control_plane.bff.ports import ReadSurfacePorts

    class _RouteReadStore(ReadSurfacePorts):
        def get_persona(self, pid: str) -> dict[str, Any] | None:
            found = route_personas.get(pid)
            return deepcopy(found) if found is not None else None

        def list_personas(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return [deepcopy(v) for v in route_personas.values()]

        def list_strategy_specs(self, **_kwargs: Any) -> list[dict[str, Any]]:
            return []

        def get_persona_containment(self, _pid: str) -> dict[str, Any] | None:
            return None

    composed_svc = make_composed_persona_service(
        write_owner=_RouteWriteOwner(),
        read_store=_RouteReadStore(),
        command_store=CommandStore(str(tmp_path / "route_commands.jsonl")),
    )
    monkeypatch.setattr(personas_service, "_composed_persona_service", composed_svc)
    monkeypatch.setattr(personas_service, "_persona_provisioning_store", lambda: route_store)
    monkeypatch.setattr(personas_service, "PersonaProvisioningCoordinator", lambda **_kw: route_coord)
    monkeypatch.setattr(
        personas_service,
        "_get_persona_directory_snapshot",
        lambda tenant_id=None: SimpleNamespace(
            records_by_id=dict(route_personas),
            records=list(route_personas.values()),
        ),
    )

    app = FastAPI()
    app.include_router(create_personas_router(service=composed_svc))
    route_client = TestClient(app)

    # 8a. Credentials-scoped POST /bff/personas with risk="low" and no internal policy/approval IDs when unapproved
    unapproved_headers = {**AUTH_HEADERS, "Idempotency-Key": "idem-route-low-unapproved-1"}
    resp_unapproved = route_client.post(
        "/bff/personas",
        headers=unapproved_headers,
        json={
            "name": "Route Low Trader",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Route paper low profile test",
            "market": "TW",
            "symbols": ["2330.TW"],
            "budget": 100000,
        },
    )
    assert resp_unapproved.status_code == 502, resp_unapproved.text
    raw_unapproved = resp_unapproved.json()
    err_unapproved = raw_unapproved.get("detail", raw_unapproved)["error"]
    assert err_unapproved["details"]["reasonCode"] == "risk_policy_profile_mapping_unapproved"
    assert err_unapproved["details"]["absenceSource"] == "policy_definition_and_approval_absent"
    routed_from_post = err_unapproved["details"]["riskPolicyOwnerDecisionRequest"]
    created_pid = err_unapproved["details"]["personaId"]
    created_pool_id = routed_from_post["capital_pool_id"]
    routed_gov_id = routed_from_post["governance_proposal_id"]
    assert routed_from_post["governance_proposal_status"] == "proposed_to_governance_human_inbox"
    assert routed_from_post["disposition_task_id"] == "PAPER-RISK-LOW-POLICY-OWNER-DISPOSITION-20261122"
    assert routed_from_post["hosted_proof_task_id"] == "PAPER-RISK-JOURNEY-HOSTED-PROOF-20261121"

    # 8b. Credentials-scoped POST /bff/personas/{persona_id}/provisioning/paper-capital/risk-policy when ambiguous
    route_tenant_id = routed_from_post["tenant_id"]
    route_tx.objects[("capital", f"/api/capital-pools/{created_pool_id}")] = {
        "pool_id": created_pool_id,
        "owner_id": route_tenant_id,
        "tenant_id": route_tenant_id,
        "owner_type": "org",
        "status": "active",
        "currency": "TWD",
        "budget": 100000.0,
        "single_runtime_enforced": True,
        "metadata": {
            "internal": True,
            "execution_context": "paper",
            "tenant_id": route_tenant_id,
            "persona_id": created_pid,
            "risk_policy": _fixture_paper_policy(created_pool_id, version="1.0"),
        },
    }
    for n in (1, 2):
        r_pol = {
            "risk_policy_id": f"rp-route-low-{n}",
            "version": "1.0",
            "status": "active",
            "allowed_stages": ["paper"],
            "gross_limit": n * 0.4,
            "net_limit": 0.6,
            "max_leverage": 1.0,
            "metadata": {"tenant_id": route_tenant_id, "requested_profile": "low"},
        }
        (policy_dir / f"{r_pol['risk_policy_id']}.json").write_text(json.dumps(r_pol), encoding="utf-8")
        r_dec = approval_snapshot(
            decision_id=f"apv-route-low-{n}",
            tenant_id=route_tenant_id,
            target_type="capital_pool_activation",
            target_id=r_pol["risk_policy_id"],
            target_version=risk_policy_digest(r_pol),
            owner_user_id="proposer",
            risk_level="medium",
            metadata={
                "subject": {
                    "pool_id": r_pol["risk_policy_id"],
                    "risk_direction": "increase",
                    "requested_risk": "low",
                    "risk_policy_id": r_pol["risk_policy_id"],
                    "risk_policy_version": "1.0",
                    "risk_policy_digest": risk_policy_digest(r_pol),
                },
                "approvals": [
                    {"actor_id": "reviewer-1", "actor_role": "risk_owner"},
                    {"actor_id": "reviewer-2", "actor_role": "governance_reviewer"},
                ],
            },
        )
        route_tx.objects[("governance", f"/api/governance/approvals/{r_dec['decision_id']}")] = r_dec

    resp_repair_ambig = route_client.post(
        f"/bff/personas/{created_pid}/provisioning/paper-capital/risk-policy",
        headers=AUTH_HEADERS,
        json={},
    )
    assert resp_repair_ambig.status_code == 200, resp_repair_ambig.text
    repair_ambig_data = resp_repair_ambig.json()["data"]
    assert repair_ambig_data["repaired"] is False
    assert repair_ambig_data["after_resolution"]["status"] == "ambiguous_profile_mapping"
    assert repair_ambig_data["owner_decision_request"]["ambiguous_policy_ids"] == [
        "rp-route-low-1",
        "rp-route-low-2",
    ]
    # Also verify POST /bff/personas returns 502 with ambiguous_profile_mapping when catalog is ambiguous
    resp_create_ambig = route_client.post(
        "/bff/personas",
        headers=unapproved_headers,
        json={
            "name": "Route Low Trader",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Route paper low profile test",
            "market": "TW",
            "symbols": ["2330.TW"],
            "budget": 100000,
        },
    )
    assert resp_create_ambig.status_code == 502, resp_create_ambig.text
    err_create_ambig = resp_create_ambig.json().get("detail", resp_create_ambig.json())["error"]
    assert err_create_ambig["details"]["reasonCode"] == "risk_policy_profile_mapping_ambiguous"
    assert err_create_ambig["details"]["resolutionStatus"] == "ambiguous_profile_mapping"
    assert err_create_ambig["details"]["riskPolicyOwnerDecisionRequest"]["ambiguous_policy_ids"] == [
        "rp-route-low-1",
        "rp-route-low-2",
    ]

    # 8c. Once ambiguity is resolved to a single approved policy, both repair route and POST /bff/personas succeed
    (policy_dir / "rp-route-low-2.json").unlink()
    route_tx.objects.pop(("governance", "/api/governance/approvals/apv-route-low-2"), None)
    route_tx.objects.pop(("governance", f"/api/governance/approvals/{routed_gov_id}"), None)
    route_tx.governance_subject = "pantheon-persona-provisioner"
    route_tx.governance_roles = {"approval_proposer", "automated_gate"}

    resp_repair_ok = route_client.post(
        f"/bff/personas/{created_pid}/provisioning/paper-capital/risk-policy",
        headers=AUTH_HEADERS,
        json={},
    )
    assert resp_repair_ok.status_code == 200, resp_repair_ok.text
    repair_ok_data = resp_repair_ok.json()["data"]
    assert repair_ok_data["repaired"] is True
    assert repair_ok_data["after_resolution"]["resolved"] is True
    assert repair_ok_data["after_resolution"]["risk_policy_id"] == "rp-route-low-1"
    assert repair_ok_data["after_resolution"]["profile_mapping_approval_id"] == "apv-route-low-1"

    resp_create_ok = route_client.post(
        "/bff/personas",
        headers=unapproved_headers,
        json={
            "name": "Route Low Trader",
            "archetype": "momentum",
            "risk": "low",
            "mandate": "Route paper low profile test",
            "market": "TW",
            "symbols": ["2330.TW"],
            "budget": 100000,
        },
    )
    assert resp_create_ok.status_code == 201, resp_create_ok.text
    create_ok_json = resp_create_ok.json()
    assert create_ok_json["meta"]["risk_policy_resolution"]["resolved"] is True
    assert create_ok_json["meta"]["risk_policy_resolution"]["risk_policy_id"] == "rp-route-low-1"
    assert create_ok_json["meta"]["risk_policy_resolution"]["profile_mapping_approval_id"] == "apv-route-low-1"

    resp_get_ok = route_client.get(
        f"/bff/personas/{created_pid}",
        headers=AUTH_HEADERS,
    )
    assert resp_get_ok.status_code == 200, resp_get_ok.text
    get_ok_data = resp_get_ok.json()["data"]
    assert get_ok_data["riskPolicyResolution"]["resolved"] is True
    assert get_ok_data["riskPolicyResolution"]["risk_policy_id"] == "rp-route-low-1"
    assert get_ok_data["riskPolicyResolution"]["profile_mapping_approval_id"] == "apv-route-low-1"
    assert get_ok_data.get("riskPolicyOwnerDecisionRequest") is None

    # 8d. Pre-existing approval then policy revocation on catalog policy fails closed on coordinator repair
    revoked_route_pol = {
        "risk_policy_id": "rp-route-low-1",
        "version": "1.0",
        "status": "active",
        "allowed_stages": ["paper"],
        "gross_limit": 0.4,
        "net_limit": 0.6,
        "max_leverage": 1.0,
        "metadata": {
            "tenant_id": route_tenant_id,
            "requested_profile": "low",
            "approval_status": "revoked",
        },
    }
    (policy_dir / "rp-route-low-1.json").write_text(json.dumps(revoked_route_pol), encoding="utf-8")
    resp_repair_revoked = route_client.post(
        f"/bff/personas/{created_pid}/provisioning/paper-capital/risk-policy",
        headers=AUTH_HEADERS,
        json={},
    )
    assert resp_repair_revoked.status_code == 200, resp_repair_revoked.text
    repair_revoked_data = resp_repair_revoked.json()["data"]
    assert repair_revoked_data["repaired"] is False
    assert repair_revoked_data["after_resolution"]["resolved"] is False
    assert repair_revoked_data["after_resolution"]["allow_new_or_increased_risk"] is False
    assert repair_revoked_data["owner_decision_request"]["absence_source"] == "policy_approval_revoked"
    (policy_dir / "rp-route-low-1.json").unlink()

    # 8e. Transport approval reader signals overflow when > 16 target decisions exist and repair fails closed
    for idx in range(17):
        over_dec = approval_snapshot(
            decision_id=f"apv-route-over-{idx:02d}",
            tenant_id=route_tenant_id,
            target_type="capital_pool_activation",
            target_id=created_pool_id,
            target_version="1.0",
            decision="rejected",
            owner_user_id="proposer",
            risk_level="medium",
        )
        route_tx.objects[("governance", f"/api/governance/approvals/{over_dec['decision_id']}")] = over_dec
    tx_list = route_coord._governance_approval_reader().list_for_target(
        target_type="capital_pool_activation",
        target_id=created_pool_id,
        decision_state=None,
        max_items=16,
    )
    assert len(tx_list) == 16
    assert getattr(tx_list, "overflow", False) is True
    resp_repair_over = route_client.post(
        f"/bff/personas/{created_pid}/provisioning/paper-capital/risk-policy",
        headers=AUTH_HEADERS,
        json={},
    )
    assert resp_repair_over.status_code == 200, resp_repair_over.text
    assert resp_repair_over.json()["data"]["after_resolution"]["status"] == "ambiguous_profile_mapping"






@pytest.fixture()
def pending_profile_owner(tmp_path, monkeypatch):
    """Real Capital model/store/service/mapper behind the coordinator transport."""
    monkeypatch.setenv("CAPITAL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CAPITAL_STORE_BACKEND", "json")
    monkeypatch.setenv("CAPITAL_AUDIT_BACKEND", "jsonl")
    monkeypatch.setenv("CAPITAL_RISK_POLICY_DIR", str(tmp_path / "no-policies"))
    from services.capital import main as capital
    from services.capital.pg_store import PersistentCapitalPoolStore
    from services.capital.models import UpdateCapitalPoolStatusRequest

    class RealCapitalTransport(FakeOwnerTransport):
        def __init__(self):
            super().__init__(seed_pool_policy_approval=False,
                             governance_subject="pantheon-dev-persona-enrollment-proposer",
                             governance_roles=("approval_proposer",))
            self.service = capital.CapitalBoundaryService(
                pool_store=PersistentCapitalPoolStore(path=tmp_path / "pools.json"),
                binding_store=capital.binding_store, allocation_store=capital.allocation_authority_store,
                audit_store=capital.build_capital_audit_store(tmp_path / "audit.jsonl"),
                audit_log_path=tmp_path / "audit.jsonl",
            )
            self.lose_response = False
            self.before_patch = None

        def get(self, owner, path):
            if owner == "capital" and path.startswith("/api/capital-pools/"):
                pool = self.service.pool_store.get(path.rsplit("/", 1)[-1])
                return capital._pool_body(pool).model_dump() if pool else None
            return super().get(owner, path)

        def patch(self, owner, path, payload):
            if owner != "capital" or not path.startswith("/api/capital-pools/"):
                return super().patch(owner, path, payload)
            self.calls.append(("PATCH", owner, path, deepcopy(dict(payload))))
            if self.before_patch:
                callback, self.before_patch = self.before_patch, None
                callback()
            pool_id = path.split("/")[-2]
            result = self.service.update_pool_status(pool_id, UpdateCapitalPoolStatusRequest(**payload))
            if self.lose_response:
                self.lose_response = False
                raise ConnectionError("committed response lost")
            return capital._pool_body(result).model_dump()

    return RealCapitalTransport(), capital


def test_legacy_hardcoded_inline_policy_gets_next_version_drafted_by_risk_officer_once(pending_profile_owner):
    """The three dev `low` pools carry inline v1.0 (2x, no single-name cap, no cash reserve).

    The next version (2.0) is drafted by the risk officer, proposed to Governance with the digest + draft
    provenance, idempotently (one turn, one proposal), and nothing is approved or bound by the repair.
    """
    tx, capital = pending_profile_owner
    store, record = _record_and_store({"risk": "low"}, seed_pool_approval=False)
    ids = deterministic_provisioning_ids(record)
    legacy = {"risk_policy_id": f"rp-{ids.capital_pool_id}", "version": "1.0", "status": "active",
              "allowed_stages": ["paper"], "gross_limit": 2.0, "net_limit": 2.0, "max_leverage": 2.0,
              "allowed_asset_classes": ["equity", "future"]}
    tx.service.pool_store.create(capital.CapitalPool(
        pool_id=ids.capital_pool_id, name="legacy low", owner_id=record.tenant_id, owner_type="org",
        status="active", created_at=_owner_now(), budget=100000.0, currency="TWD",
        metadata={"tenant_id": record.tenant_id, "persona_id": record.persona_id, "execution_context": "paper",
                  "risk_policy": legacy,
                  "recent_paper_evidence": ["2026-11-19 buy 452 x 2317 @ 221 vs 100,000 TWD"]},
    ))
    coord = PersonaProvisioningCoordinator(
        store=store, transport=tx, schedule_registrar=_schedule_receipt, lease_owner="test-worker",
        governance_proposer=tx, governance_proposer_actor_id=tx.governance_subject,
        risk_policy_drafter=_fake_risk_officer_drafter,
    )
    tx.service.guard._approval_reader = coord._governance_approval_reader()
    kwargs = dict(tenant_id=record.tenant_id, persona_id=record.persona_id)
    first = coord.repair_paper_pool_risk_policy(**kwargs)
    second = coord.repair_paper_pool_risk_policy(**kwargs)
    assert len(DRAFTER_CALLS) == 1
    assert DRAFTER_CALLS[0].version == "2.0" and DRAFTER_CALLS[0].profile == "low"
    assert DRAFTER_CALLS[0].currency == "TWD" and DRAFTER_CALLS[0].budget == 100000.0
    assert any("452" in e for e in DRAFTER_CALLS[0].evidence)
    request = first["owner_decision_request"]
    assert request["candidate_risk_policy_version"] == "2.0"
    assert request["risk_officer_draft"]["provenance"]["source"] == "risk_officer_draft"
    assert request["risk_officer_draft"]["policy"]["max_single_name_weight"] < 1.0
    assert request["candidate_risk_policy_digest"] == risk_policy_digest(request["risk_officer_draft"]["policy"])
    assert request["governance_proposal_id"] == second["owner_decision_request"]["governance_proposal_id"]
    assert tx.mutations[("governance", "/api/governance/approvals")] == 1
    assert first["repaired"] is False and second["repaired"] is False
    stored = tx.service.pool_store.require(ids.capital_pool_id).metadata["risk_policy"]
    assert stored["version"] == "1.0" and "max_single_name_weight" not in stored  # nothing bound without approval


def test_no_drafter_or_failed_draft_leaves_pool_pending_without_invented_limits(monkeypatch) -> None:
    monkeypatch.delenv("OPENCLAW_GATEWAY_URL", raising=False)
    store, record = _record_and_store(seed_pool_approval=False)
    transport = FakeOwnerTransport()
    transport.seed_pool_policy_approval = False
    unconfigured = PersonaProvisioningCoordinator(
        store=store, transport=transport, schedule_registrar=_schedule_receipt, lease_owner="test-worker")
    result = unconfigured.coordinate(record)
    assert result.state == "failed" and result.error["reason_code"] == "risk_officer_draft_unavailable"
    assert "not configured" in result.error["terminal_reason"]
    assert not [c for c in transport.calls if c[0] != "GET" and "/api/capital-pools" in str(c[2])]

    store2, record2 = _record_and_store(seed_pool_approval=False)
    transport2 = FakeOwnerTransport()
    transport2.seed_pool_policy_approval = False
    bad = PersonaProvisioningCoordinator(
        store=store2, transport=transport2, schedule_registrar=_schedule_receipt, lease_owner="test-worker",
        risk_policy_drafter=lambda inputs: DraftResult(
            False, reason="risk_policy_draft_outside_bounds:max_single_name_weight=0.5 exceeds low bound 0.2"))
    result2 = bad.coordinate(record2)
    assert result2.state == "failed" and "outside_bounds" in result2.error["terminal_reason"]
    assert not [c for c in transport2.calls if c[0] != "GET" and "/api/capital-pools" in str(c[2])]


@pytest.mark.parametrize("profile", ["low", "medium", "high"])
def test_pending_profile_real_owner_readback_retry_and_response_loss(pending_profile_owner, profile):
    tx, capital = pending_profile_owner
    store, record = _record_and_store({"risk": profile}, seed_pool_approval=False)
    ids = deterministic_provisioning_ids(record)
    pool = capital.CapitalPool(
        pool_id=ids.capital_pool_id, name="pending paper", owner_id=record.tenant_id,
        owner_type="org", status="active", created_at=_owner_now(), budget=25000.0,
        metadata={"tenant_id": record.tenant_id, "persona_id": record.persona_id,
                  "execution_context": "paper", "operator_note": "keep",
                  "risk_policy": _fixture_paper_policy(ids.capital_pool_id)},
    )
    tx.service.pool_store.create(pool)
    coord = PersonaProvisioningCoordinator(store=store, transport=tx, schedule_registrar=_schedule_receipt, lease_owner="test-worker", governance_proposer=tx,
                                          governance_proposer_actor_id=tx.governance_subject)
    tx.service.guard._approval_reader = coord._governance_approval_reader()
    kwargs = dict(tenant_id=record.tenant_id, persona_id=record.persona_id)
    preview = coord.repair_paper_pool_risk_policy(**kwargs, dry_run=True)
    assert preview["after_resolution"]["requested_profile"] == profile
    assert not tx.calls or all(call[0] in {"GET", "LIST"} for call in tx.calls)
    assert "requested_risk_profile" not in tx.service.pool_store.require(ids.capital_pool_id).metadata
    assert not tx.mutations

    # A concurrent owner update after BFF read must survive provenance persistence.
    def concurrent_update():
        tx.service.pool_store.patch(ids.capital_pool_id, patch={
            "status": "suspended", "metadata": {**pool.metadata, "concurrent_note": "retain"}
        }, updated_at=_owner_now())
    tx.before_patch = concurrent_update
    tx.lose_response = True
    first = coord.repair_paper_pool_risk_policy(**kwargs)
    second = coord.repair_paper_pool_risk_policy(**kwargs)
    readback = tx.service.resolve_pool_risk_policy(ids.capital_pool_id)
    body = capital._pool_body(tx.service.pool_store.require(ids.capital_pool_id)).model_dump()
    assert body["metadata"]["requested_risk_profile"] == profile
    assert body["metadata"]["operator_note"] == "keep"
    assert body["metadata"]["concurrent_note"] == "retain"
    assert body["status"] == "suspended"
    assert body["budget"] == pool.budget
    assert body["metadata"]["risk_policy"] == pool.metadata["risk_policy"]
    assert body["metadata"].get("profile_mapping_approval_id") is None
    assert readback["requested_profile"] == first["after_resolution"]["requested_profile"] == profile
    assert readback["status"] == first["after_resolution"]["status"] == "unresolved_profile_mapping"
    assert readback["allow_new_or_increased_risk"] is False
    assert readback["allow_risk_reducing_actions"] is True
    assert first["owner_decision_request"]["governance_proposal_id"] == second["owner_decision_request"]["governance_proposal_id"]
    assert len([c for c in tx.calls if c[0] == "PATCH"]) == 1
    assert tx.mutations[("governance", "/api/governance/approvals")] == 1
    import runpy
    from pathlib import Path
    probe = runpy.run_path(str(Path(__file__).resolve().parents[3] / "docs/deployment/evidence/PAPER-RISK-PENDING-PROFILE-PERSISTENCE-20261122/check_readback.py"))["check"]
    proposal_id = first["owner_decision_request"]["governance_proposal_id"]
    receipts = [{"tenant_id": record.tenant_id, "persona_id": record.persona_id,
                 "request_payload": record.request_payload, "capital_pool": body,
                 "risk_policy_resolution": readback,
                 "governance_proposal": tx.objects[("governance", f"/api/governance/approvals/{proposal_id}")]}]
    assert probe(receipts, expected_count=1)["passed"], receipts
    receipts[0]["risk_policy_resolution"]["requested_profile"] = None
    assert not probe(receipts, expected_count=1)["passed"]


def test_profile_missing_conflict_and_identity_fail_without_mutation(pending_profile_owner):
    tx, capital = pending_profile_owner
    store, record = _record_and_store({"risk": "high"}, seed_pool_approval=False)
    ids = deterministic_provisioning_ids(record)
    original = capital.CapitalPool(
        pool_id=ids.capital_pool_id, name="paper", owner_id=record.tenant_id,
        owner_type="org", status="active", created_at=_owner_now(),
        metadata={"tenant_id": record.tenant_id, "persona_id": record.persona_id, "execution_context": "paper"},
    )
    tx.service.pool_store.create(original)
    coord = PersonaProvisioningCoordinator(store=store, transport=tx, schedule_registrar=_schedule_receipt, lease_owner="test-worker", governance_proposer=tx)
    kwargs = dict(tenant_id=record.tenant_id, persona_id=record.persona_id)
    with pytest.raises(PersonaProvisioningCoordinationError, match="requested_profile_source_conflict"):
        coord.repair_paper_pool_risk_policy(**kwargs, requested_profile="low")
    stored = store._backend.records[(record.tenant_id, record.idempotency_key)]
    stored.request_payload.pop("risk")
    stored.request_payload.pop("traits")  # Remove the original supplied legacy risk source too.
    with pytest.raises(PersonaProvisioningCoordinationError, match="requested_profile_source_missing"):
        coord.repair_paper_pool_risk_policy(**kwargs)
    stored.request_payload.update(risk="medium", metadata={"requested_risk_profile": "high"})
    with pytest.raises(PersonaProvisioningCoordinationError, match="requested_profile_source_conflict"):
        coord.repair_paper_pool_risk_policy(**kwargs)
    assert tx.service.pool_store.require(ids.capital_pool_id) == original
    assert not tx.mutations


@pytest.mark.parametrize("state", ["proposed", "rejected"])
@pytest.mark.parametrize("mismatch", ["tenant", "profile", "profile_alias", "policy", "version", "digest", "pool_digest", "target"])
def test_pending_or_rejected_proposal_must_match_entire_request(state, mismatch):
    store, record = _record_and_store({"risk": "medium"}, seed_pool_approval=False)
    tx = FakeOwnerTransport(seed_pool_policy_approval=False,
                            governance_subject="pantheon-dev-persona-enrollment-proposer",
                            governance_roles=("approval_proposer",))
    coord = PersonaProvisioningCoordinator(store=store, transport=tx, schedule_registrar=_schedule_receipt,
                                          lease_owner="test-worker", governance_proposer=tx)
    ids = deterministic_provisioning_ids(record)
    first = coord.coordinate(record)
    request = first.error["owner_decision_request"]
    proposal_id = request["governance_proposal_id"]
    proposal = tx.objects[("governance", f"/api/governance/approvals/{proposal_id}")]
    if state == "rejected":
        proposal.update(decision_state="decided", decision="rejected")
    if mismatch == "tenant":
        proposal["tenant_id"] = "foreign"
    elif mismatch == "pool_digest":
        proposal["target_version"] = "wrong"
    elif mismatch == "target":
        proposal["target_id"] = "other-pool"
    elif mismatch == "profile_alias":
        proposal["metadata"]["subject"]["requested_profile"] = "high"
    else:
        key = {"profile": "requested_risk", "policy": "risk_policy_id",
               "version": "risk_policy_version", "digest": "risk_policy_digest"}[mismatch]
        proposal["metadata"]["subject"][key] = "different"
    retry = coord.coordinate(record)
    routed = retry.error["owner_decision_request"]
    assert routed["governance_proposal_status"] == "existing_proposal_request_mismatch"
    assert "governance_proposal_id" not in routed
    assert tx.mutations[("governance", "/api/governance/approvals")] == 1
    # List fallback cannot substitute the same mismatch under another ID either.
    tx.objects[("governance", "/api/governance/approvals/unrelated-case")] = {
        **tx.objects.pop(("governance", f"/api/governance/approvals/{proposal_id}")),
        "decision_id": "unrelated-case",
    }
    retry = coord.coordinate(record)
    assert retry.error["owner_decision_request"]["governance_proposal_id"] == proposal_id
    assert tx.mutations[("governance", "/api/governance/approvals")] == 2


@pytest.fixture
def profile_ingress(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.control_plane.bff.personas import service
    from services.control_plane.bff.personas.router import create_personas_router
    from services.control_plane.bff.ports import ReadSurfacePorts
    from services.control_plane.bff.tests.conftest import make_composed_persona_service

    class Owner(ReadSurfacePorts):
        def __init__(self):
            self.rows = {}

        def get_persona(self, pid):
            return deepcopy(self.rows.get(pid))

        def create_persona(self, record_in=None, **kwargs):
            row = deepcopy(dict(record_in or kwargs))
            pid = row.get("persona_id") or row["id"]
            self.rows[pid] = {**row, "id": pid, "persona_id": pid}
            return self.get_persona(pid)

        def update_persona(self, pid, **kwargs):
            row = self.rows[pid]
            for key, value in kwargs.items():
                if key == "metadata":
                    row[key] = {**row.get(key, {}), **value}
                elif value is not None:
                    row[key] = value
            return self.get_persona(pid)

        def list_strategy_specs(self, **kwargs):
            return []

        def get_persona_containment(self, pid):
            return None

    owner = Owner()
    store = TrackingStore()
    tx = FakeOwnerTransport()
    composed = make_composed_persona_service(write_owner=owner, read_store=owner)
    monkeypatch.setenv("PANTHEON_BFF_AUTH_STUB", "true")
    monkeypatch.setenv("PANTHEON_BFF_AUTH_MODE", "permissive")
    monkeypatch.setenv("PANTHEON_PERSONA_GOVERNANCE_ACTOR_ID", "pantheon-persona-provisioner")
    monkeypatch.setattr(service, "_composed_persona_service", composed)
    monkeypatch.setattr(service, "_persona_provisioning_store", lambda: store)
    monkeypatch.setattr(service, "_PersonaOwnerHttpTransport", lambda **kw: tx)
    monkeypatch.setattr(service, "_register_persona_cron_required", _schedule_receipt)
    monkeypatch.setattr(service, "_ensure_persona_ooda_packet", lambda *a, **kw: None)
    app = FastAPI()
    app.include_router(create_personas_router(service=composed))
    return SimpleNamespace(client=TestClient(app), store=store, tx=tx, owner=owner, service=service,
                           headers={"Authorization": "Bearer test-operator:operator,reviewer,admin",
                                    "Idempotency-Key": "name-only-contract"})


def test_name_only_create_labels_default_preserves_hash_and_retry_contract(profile_ingress):
    h = profile_ingress
    payload = {"name": "Name Only Trader"}
    response = h.client.post("/bff/personas", headers=h.headers, json=payload)
    assert response.status_code == 201, response.text
    record = h.store.list_all()[0]
    original = h.service._persona_create_canonical_payload(
        payload, name=payload["name"], tenant_id=record.tenant_id, requested_by="test-operator")
    assert record.request_hash == h.service._stable_json_hash({
        "route": "POST /bff/personas", "tenant_id": record.tenant_id, "payload": original})
    assert "risk" not in original
    assert record.request_payload["risk"] == "low"
    assert record.request_payload["metadata"]["requested_risk_profile_provenance"] == "persona_create_default"
    ids = deterministic_provisioning_ids(record)
    pool = h.tx.objects[("capital", f"/api/capital-pools/{ids.capital_pool_id}")]
    assert pool["metadata"]["requested_risk_profile"] == "low"
    assert h.owner.get_persona(record.persona_id)["metadata"]["risk_level"] == "low"
    mutations = h.tx.mutations.copy()
    replay = h.client.post("/bff/personas", headers=h.headers, json=payload)
    assert replay.status_code == 201, replay.text
    assert h.tx.mutations == mutations
    changed = h.client.post("/bff/personas", headers=h.headers, json={**payload, "risk": "low"})
    assert changed.status_code == 409


def test_name_only_create_without_approved_policy_holds_and_reuses_case(profile_ingress):
    h = profile_ingress
    h.tx.seed_pool_policy_approval = False
    payload = {"name": "Unapproved Name Only Trader"}
    first = h.client.post("/bff/personas", headers=h.headers, json=payload)
    assert first.status_code == 502, first.text
    record = h.store.list_all()[0]
    assert record.error["reason_code"] == "risk_policy_profile_mapping_unapproved"
    request = record.error["owner_decision_request"]
    assert request["requested_profile"] == "low"
    assert record.request_payload["risk"] == "low"
    # A new pool has no authority to exist/activate until policy admission.
    assert not any(owner == "capital" for owner, path in h.tx.objects)
    second = h.client.post("/bff/personas", headers=h.headers, json=payload)
    assert second.status_code == 502, second.text
    retried = h.store.list_all()[0]
    assert retried.error["owner_decision_request"]["deterministic_proposal_id"] == request["deterministic_proposal_id"]


@pytest.mark.parametrize("profile", ["low", "medium", "high"])
def test_original_name_only_reservation_replays_from_verified_owner_without_rehash(profile_ingress, profile):
    h = profile_ingress
    assert h.client.post("/bff/personas", headers=h.headers, json={"name": "Seed Tenant"}).status_code == 201
    tenant_id = h.store.list_all()[0].tenant_id
    name = "Legacy Name Only Trader"
    original = h.service._persona_create_canonical_payload(
        {"name": name}, name=name, tenant_id=tenant_id, requested_by="test-operator")
    request_hash = h.service._stable_json_hash({"route": "POST /bff/personas", "tenant_id": tenant_id, "payload": original})
    record, _ = h.store.reserve(
        tenant_id=tenant_id, idempotency_key="legacy-key", request_hash=request_hash,
        normalized_name=h.service._normalize_persona_create_name(name),
        persona_id=h.service._persona_create_identity(tenant_id, h.service._normalize_persona_create_name(name)),
        request_payload=original)
    h.owner.create_persona({"persona_id": record.persona_id, "tenant_id": tenant_id,
                            "name": name, "lifecycle_state": "research_only",
                            "metadata": {"risk_level": profile, "risk_profile": {"risk_level": profile}, "tenant_id": tenant_id,
                                         "provisioning_request_hash": request_hash,
                                         "internal_paper_capital_pool_id": deterministic_provisioning_ids(record).capital_pool_id}})
    response = h.client.post("/bff/personas", headers={**h.headers, "Idempotency-Key": "legacy-key"}, json={"name": name})
    assert response.status_code == (201 if profile == "low" else 502), response.text
    saved = h.store.get(tenant_id, "legacy-key")
    assert saved.request_hash == request_hash
    assert saved.request_payload == original
    assert saved.references["requested_risk_profile_provenance"]["source"] == "verified_persona_owner"
    assert h.owner.get_persona(record.persona_id)["metadata"]["risk_level"] == profile
    if profile != "low":
        assert saved.error["reason_code"] == "risk_policy_profile_mapping_unapproved"


def test_conflicting_create_profile_is_traceable_and_writes_nothing(profile_ingress):
    h = profile_ingress
    response = h.client.post("/bff/personas", headers=h.headers, json={
        "name": "Conflicting Trader", "risk": "low", "metadata": {"requested_risk_profile": "high"}})
    assert response.status_code == 422, response.text
    assert "requested_profile_source_conflict" in response.text
    assert not h.store.list_all()
    assert not h.tx.mutations


def test_legacy_missing_request_cannot_gain_provenance_from_failed_display_default(profile_ingress):
    h = profile_ingress
    assert h.client.post("/bff/personas", headers=h.headers, json={"name": "Seed Tenant"}).status_code == 201
    tenant_id = h.store.list_all()[0].tenant_id
    name = "Missing Legacy Source"
    canonical = h.service._persona_create_canonical_payload(
        {"name": name}, name=name, tenant_id=tenant_id, requested_by="test-operator")
    request_hash = h.service._stable_json_hash({"route": "POST /bff/personas", "tenant_id": tenant_id, "payload": canonical})
    record, _ = h.store.reserve(
        tenant_id=tenant_id, idempotency_key="missing-legacy-key", request_hash=request_hash,
        normalized_name=h.service._normalize_persona_create_name(name),
        persona_id=h.service._persona_create_identity(tenant_id, h.service._normalize_persona_create_name(name)),
        request_payload=canonical)
    for _ in range(2):
        response = h.client.post("/bff/personas", headers={**h.headers, "Idempotency-Key": "missing-legacy-key"}, json={"name": name})
        assert response.status_code == 502, response.text
        saved = h.store.get(tenant_id, "missing-legacy-key")
        assert saved.error["reason_code"] == "requested_profile_source_missing"
        assert saved.request_payload == canonical
        assert "requested_risk_profile_provenance" not in saved.references
    assert ("capital", f"/api/capital-pools/{deterministic_provisioning_ids(record).capital_pool_id}") not in h.tx.objects


@pytest.mark.parametrize("profile", ["low", "medium", "high"])
@pytest.mark.parametrize("alias", ["riskLevel", "risk_profile", "riskProfile", "traits"])
def test_actual_profile_aliases_retain_value_and_reject_conflicts(profile, alias):
    value = ({"riskLevel": profile} if alias in {"risk_profile", "riskProfile"}
             else {"risk_appetite": profile} if alias == "traits" else profile)
    request = {alias: value}
    extract = PersonaProvisioningCoordinator._extract_request_risk_profile_context
    assert extract(request)[0] == profile
    if alias == "traits":
        assert extract({**request, "risk": "low"})[0] == "low"
        return
    with pytest.raises(PersonaProvisioningCoordinationError, match="requested_profile_source_conflict"):
        extract({**request, "risk": "high" if profile == "low" else "low"})


@pytest.mark.parametrize("mismatch", [None, "tenant", "persona", "pool", "hash", "missing_profile", "failed_missing", "conflict"])
def test_old_request_requires_verified_matching_real_persona(mismatch):
    from types import SimpleNamespace
    store, record = _record_and_store({}, seed_pool_approval=False)
    record.request_payload = {"name": "Old Request"}
    original = deepcopy(record.request_payload)
    ids = deterministic_provisioning_ids(record)
    persona = {"persona_id": record.persona_id, "tenant_id": record.tenant_id,
               "metadata": {"risk_level": "medium", "internal_paper_capital_pool_id": ids.capital_pool_id,
                            "provisioning_request_hash": record.request_hash}}
    if mismatch == "tenant":
        persona["tenant_id"] = "foreign"
    elif mismatch == "persona":
        persona["persona_id"] = "foreign"
    elif mismatch == "pool":
        persona["metadata"]["internal_paper_capital_pool_id"] = "foreign"
    elif mismatch == "hash":
        persona["metadata"]["provisioning_request_hash"] = "foreign"
    elif mismatch == "missing_profile":
        persona["metadata"].pop("risk_level")
    elif mismatch == "failed_missing":
        persona["metadata"]["provisioning_error"] = {"reason_code": "requested_profile_source_missing"}
    elif mismatch == "conflict":
        persona["risk_level"] = "high"
    coord = PersonaProvisioningCoordinator(
        store=store, transport=FakeOwnerTransport(), schedule_registrar=_schedule_receipt,
        lease_owner="old-request-test", persona_owner_provider=lambda: SimpleNamespace(get_persona=lambda pid: persona))
    if mismatch in {"tenant", "persona", "pool", "hash", "conflict"}:
        with pytest.raises(PersonaProvisioningCoordinationError):
            coord._record_risk_profile_context(record)
    else:
        profile, required, _ = coord._record_risk_profile_context(record)
        assert required is True
        assert profile == (None if mismatch else "medium")
    assert record.request_payload == original


# --- paper automated gate for pool risk-policy proposals (operator decision P-4) ---------------------

_GATE_LOW_POLICY = {"risk_policy_id": "rp-pool", "version": "2.0", "gross_limit": 1.0, "net_limit": 1.0,
                    "max_leverage": 1.0, "max_single_name_weight": 0.20, "min_cash_reserve_pct": 0.10}


def _gate_coordinator(monkeypatch, *, fail=False):
    coordinator = PersonaProvisioningCoordinator.__new__(PersonaProvisioningCoordinator)
    coordinator.transport = object()
    decided: list[dict[str, Any]] = []

    def fake_decide(proposal, *, outcome, rationale):
        if fail:
            raise RuntimeError("gate unavailable")
        decided.append({"id": proposal["decision_id"], "outcome": outcome, "rationale": rationale})
        return {"decision_state": "decided", "decision": outcome}

    monkeypatch.setattr(coordinator, "_paper_gate_decide_risk_policy", fake_decide)
    return coordinator, decided


def _gate_proposal(policy):
    draft = {"policy": policy, "provenance": {"source": "risk_officer_draft"}} if policy else None
    return {"decision_id": "apv-risk-profile-x", "metadata": {"subject": {
        "pool_id": "pool-1", "requested_risk": "low", **({"risk_officer_draft": draft} if draft else {})}}}


def test_paper_gate_approves_a_policy_inside_the_outer_bounds(monkeypatch):
    coordinator, decided = _gate_coordinator(monkeypatch)
    req: dict[str, Any] = {}
    coordinator._apply_paper_gate_to_risk_policy_proposal(
        _gate_proposal(_GATE_LOW_POLICY), req, requested_profile="low")
    assert [d["outcome"] for d in decided] == ["approved"]
    assert req["paper_gate_status"] == "decided_approved_by_automated_gate"


def test_paper_gate_refuses_out_of_bounds_with_the_named_limit(monkeypatch):
    coordinator, decided = _gate_coordinator(monkeypatch)
    req: dict[str, Any] = {}
    coordinator._apply_paper_gate_to_risk_policy_proposal(
        _gate_proposal({**_GATE_LOW_POLICY, "max_single_name_weight": 0.5}), req, requested_profile="low")
    assert decided[0]["outcome"] == "rejected" and "max_single_name_weight" in decided[0]["rationale"]
    assert "max_single_name_weight" in req["paper_gate_reason"]


def test_paper_gate_refuses_gross_over_the_bound_and_rationale_names_all_four(monkeypatch):
    coordinator, decided = _gate_coordinator(monkeypatch)
    coordinator._apply_paper_gate_to_risk_policy_proposal(
        _gate_proposal({**_GATE_LOW_POLICY, "gross_limit": 3.0}), {}, requested_profile="low")
    assert decided[0]["outcome"] == "rejected" and "gross_limit" in decided[0]["rationale"]
    coordinator._apply_paper_gate_to_risk_policy_proposal(
        _gate_proposal(_GATE_LOW_POLICY), {}, requested_profile="low")
    assert decided[1]["outcome"] == "approved"
    for name in ("gross_limit", "max_leverage", "max_single_name_weight", "min_cash_reserve_pct"):
        assert name in decided[1]["rationale"]


def test_paper_gate_refuses_a_v1_proposal_without_the_cap_and_reserve_and_names_the_draft(monkeypatch):
    coordinator, decided = _gate_coordinator(monkeypatch)
    req: dict[str, Any] = {}
    v1 = {k: v for k, v in _GATE_LOW_POLICY.items() if k not in {"max_single_name_weight", "min_cash_reserve_pct"}}
    coordinator._apply_paper_gate_to_risk_policy_proposal(_gate_proposal(v1), req, requested_profile="low")
    assert decided[0]["outcome"] == "rejected" and "max_single_name_weight" in decided[0]["rationale"]
    req2: dict[str, Any] = {}
    coordinator._apply_paper_gate_to_risk_policy_proposal(_gate_proposal(None), req2, requested_profile="low")
    assert decided[1]["outcome"] == "rejected"
    assert "superseded by the risk-officer draft" in decided[1]["rationale"]


def test_paper_gate_unavailable_leaves_the_proposal_pending(monkeypatch):
    coordinator, decided = _gate_coordinator(monkeypatch, fail=True)
    req: dict[str, Any] = {}
    coordinator._apply_paper_gate_to_risk_policy_proposal(
        _gate_proposal(_GATE_LOW_POLICY), req, requested_profile="low")
    assert decided == [] and req["paper_gate_status"].startswith("unavailable:")
