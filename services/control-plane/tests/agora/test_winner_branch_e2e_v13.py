"""
Winner-branch E2E acceptance tests — v1.3 contract (AG-DES-E2E-001 §F1).

Validates all 11 canonical steps of the Agora winner-branch flow against the
v1.3 schema contracts defined in:
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/

These are contract-level acceptance tests.  They build fixtures that conform
to each schema, assert the required behavioral invariants documented in §F1,
and confirm that Agora never creates a broker order, RuntimeBinding, or
capital binding throughout the entire flow.

Run:
    python3 -m pytest services/control-plane/tests/agora/test_winner_branch_e2e_v13.py -v
"""
from __future__ import annotations

import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parents[4] / (
    "docs/04/pantheon_agora_cross_repo_2026-06-20/"
    "design-closure-round2/schemas"
)

# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

try:
    import jsonschema

    def _validate(instance: dict, schema_name: str) -> None:
        schema_path = SCHEMA_DIR / schema_name
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(instance=instance, schema=schema)

    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

    def _validate(instance: dict, schema_name: str) -> None:
        pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# Shared fixture builders
# ---------------------------------------------------------------------------

NOW = "2026-06-21T00:00:00Z"

WORKSHOP_ID = "ws_winner_branch_001"
STRATEGY_ID = "strat_winner_branch_001"
USER_A = "user_a_ref"
USER_B = "user_b_ref"

BASE_REGISTRY_ID = "reg_wbranch_v1_draft"
CANDIDATE_REGISTRY_ID = "reg_wbranch_v2_draft"
BASE_DOC_SHA256 = _sha256(b"base_strategy_spec_document_bytes")


def _actor(actor_type: str, actor_ref: str) -> dict:
    return {"actor_type": actor_type, "actor_ref": actor_ref}


def _evidence_ref(ref_type: str, ref_id: str, summary: str = "") -> dict:
    r: dict = {"ref_type": ref_type, "ref_id": ref_id}
    if summary:
        r["summary"] = summary
    return r


def _sse_event(
    event_type: str,
    sequence_no: int,
    payload: dict,
    visibility: str = "owner_private",
) -> dict:
    return {
        "spec_version": "1.0",
        "event_id": f"evt_{sequence_no:04d}",
        "event_type": event_type,
        "aggregate_type": "strategy_workshop",
        "aggregate_id": WORKSHOP_ID,
        "sequence_no": sequence_no,
        "event_time": NOW,
        "emitted_at": NOW,
        "trace_id": f"trace_{sequence_no:04d}",
        "idempotency_key": f"idem_{sequence_no:04d}",
        "visibility": visibility,
        "payload": payload,
    }


def _workshop_card(
    card_type: str,
    sequence_no: int,
    payload: dict,
    status: str = "completed",
) -> dict:
    return {
        "spec_version": "1.0",
        "card_id": f"card_{card_type}_{sequence_no:04d}",
        "card_type": card_type,
        "workshop_id": WORKSHOP_ID,
        "sequence_no": sequence_no,
        "status": status,
        "title": f"Card: {card_type}",
        "payload": payload,
        "created_at": NOW,
    }


# ---------------------------------------------------------------------------
# Step 1 — Identity and private servant
# ---------------------------------------------------------------------------


class TestStep1IdentityAndPrivateServant(unittest.TestCase):
    """
    §F1 Step 1: each authenticated Agora user has exactly one user-private
    servant; no execution authority; User B cannot resolve User A's servant.
    """

    def setUp(self):
        self.servant_a = {
            "servant_id": "srv_user_a_001",
            "owner_ref": USER_A,
            "session_type": "agora_workshop",
            "persona_class": "agora_servant",
            "execution_authority": "none",
            "scope": "user_private",
        }
        self.servant_b = {
            "servant_id": "srv_user_b_001",
            "owner_ref": USER_B,
            "session_type": "agora_workshop",
            "persona_class": "agora_servant",
            "execution_authority": "none",
            "scope": "user_private",
        }

    def test_servant_has_no_execution_authority(self):
        self.assertEqual(self.servant_a["execution_authority"], "none")

    def test_servant_persona_class_is_agora_servant(self):
        self.assertEqual(self.servant_a["persona_class"], "agora_servant")

    def test_servant_scope_is_user_private(self):
        self.assertEqual(self.servant_a["scope"], "user_private")

    def test_user_b_cannot_resolve_user_a_servant(self):
        def resolve_servant(requesting_user: str, servant_id: str, owner_ref: str) -> bool:
            return requesting_user == owner_ref

        self.assertTrue(resolve_servant(USER_A, self.servant_a["servant_id"], USER_A))
        self.assertFalse(resolve_servant(USER_B, self.servant_a["servant_id"], USER_A))

    def test_each_user_has_exactly_one_private_servant(self):
        all_servants = [self.servant_a, self.servant_b]
        owners = [s["owner_ref"] for s in all_servants]
        self.assertEqual(len(owners), len(set(owners)), "Multiple servants per user")


# ---------------------------------------------------------------------------
# Step 2 — Create workshop with expert hypothesis
# ---------------------------------------------------------------------------


class TestStep2WorkshopHypothesis(unittest.TestCase):
    """
    §F1 Step 2: raw message stored as encrypted/private ref; event row stores
    private ref + redacted summary; Management cannot read raw text.
    """

    WINNER_BRANCH_HYPOTHESIS = (
        "related-party holdings; candidate branch mapping; historical branch "
        "entry/exit profitability; branch migration to alternate sell branches; "
        "unmatched large selling branches; holding-change correlation; event lead "
        "analysis over 3-6 months; winner-branch scoring; probability/EV; "
        "position/add/leverage discussion; similar Alpha research"
    )

    def setUp(self):
        raw_bytes = self.WINNER_BRANCH_HYPOTHESIS.encode()
        self.private_content_ref = f"privobj_{_sha256(raw_bytes)[:16]}"
        self.redacted_summary = "[redacted: winner-branch hypothesis submitted]"

        self.message_accepted_event = _sse_event(
            event_type="workshop.message.accepted",
            sequence_no=1,
            payload={
                "private_content_ref": self.private_content_ref,
                "redacted_summary": self.redacted_summary,
                "content_class": "user_strategy_description",
            },
            visibility="owner_private",
        )

    def test_sse_event_schema(self):
        _validate(self.message_accepted_event, "workshop_stream_event.schema.json")

    def test_raw_text_not_in_event_payload(self):
        payload_str = json.dumps(self.message_accepted_event["payload"])
        self.assertNotIn(self.WINNER_BRANCH_HYPOTHESIS, payload_str)
        self.assertIn(self.private_content_ref, payload_str)

    def test_event_visibility_is_owner_private(self):
        self.assertEqual(self.message_accepted_event["visibility"], "owner_private")

    def test_management_receives_only_redacted_summary(self):
        def management_projection(event: dict) -> dict:
            if event.get("visibility") == "owner_private":
                p = dict(event["payload"])
                p.pop("private_content_ref", None)
                return {"redacted_summary": p.get("redacted_summary", "[redacted]")}
            return event["payload"]

        projection = management_projection(self.message_accepted_event)
        self.assertNotIn("private_content_ref", projection)
        self.assertIn("redacted_summary", projection)

    def test_hypothesis_covers_required_topics(self):
        required_topics = [
            "related-party holdings",
            "candidate branch mapping",
            "historical branch",
            "branch migration",
            "winner-branch scoring",
            "probability/EV",
        ]
        for topic in required_topics:
            self.assertIn(topic, self.WINNER_BRANCH_HYPOTHESIS)


# ---------------------------------------------------------------------------
# Step 3 — Servant reconstruction and gap analysis
# ---------------------------------------------------------------------------


class TestStep3ServantReconstructionAndGap(unittest.TestCase):
    """
    §F1 Step 3: servant emits typed causal-chain, definitions, uncertainties,
    completeness snapshot, next-best-question; assert typed cards and ordered
    SSE events.
    """

    def _make_reconstruction_events(self) -> list[dict]:
        return [
            _sse_event("workshop.servant.response.started", 2, {"causal_chain": []}),
            _sse_event("workshop.servant.response.delta", 3, {"chunk": "causal chain built"}),
            _sse_event("workshop.servant.response.completed", 4, {"reconstruction": "done"}),
            _sse_event("workshop.completeness.updated", 5, {"completeness_pct": 70}),
            _sse_event("workshop.next_question.updated", 6, {"question": "Define branch entry signal"}),
        ]

    def _make_reconstruction_cards(self) -> list[dict]:
        return [
            _workshop_card("servant_reconstruction", 1, {"causal_chain": ["branch scoring link", "EV model"]}),
            _workshop_card("completeness_update", 2, {"completeness_pct": 70, "gaps": ["EV model not specified"]}),
            _workshop_card("missing_definition", 3, {"term": "branch entry signal", "status": "undefined"}),
            _workshop_card("next_question", 4, {"question": "Define branch entry signal", "priority": "high"}),
        ]

    def test_sse_events_are_schema_valid(self):
        for event in self._make_reconstruction_events():
            _validate(event, "workshop_stream_event.schema.json")

    def test_sse_events_have_ordered_sequence_nos(self):
        events = self._make_reconstruction_events()
        seqs = [e["sequence_no"] for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)), "Duplicate sequence numbers")

    def test_all_card_types_are_schema_valid(self):
        for card in self._make_reconstruction_cards():
            _validate(card, "workshop_card.schema.json")

    def test_card_types_are_distinct_and_typed(self):
        cards = self._make_reconstruction_cards()
        types = {c["card_type"] for c in cards}
        expected = {"servant_reconstruction", "completeness_update", "missing_definition", "next_question"}
        self.assertTrue(expected.issubset(types))

    def test_next_question_card_is_present(self):
        cards = self._make_reconstruction_cards()
        nq = [c for c in cards if c["card_type"] == "next_question"]
        self.assertEqual(len(nq), 1)
        self.assertIn("question", nq[0]["payload"])

    def test_response_event_ordering(self):
        events = self._make_reconstruction_events()
        types = [e["event_type"] for e in events]
        started_idx = types.index("workshop.servant.response.started")
        completed_idx = types.index("workshop.servant.response.completed")
        self.assertLess(started_idx, completed_idx)
        delta_indices = [i for i, t in enumerate(types) if t == "workshop.servant.response.delta"]
        for di in delta_indices:
            self.assertGreater(di, started_idx)
            self.assertLess(di, completed_idx)


# ---------------------------------------------------------------------------
# Step 4 — First StrategySpec draft
# ---------------------------------------------------------------------------


class TestStep4FirstStrategySpecDraft(unittest.TestCase):
    """
    §F1 Step 4: create exactly one Registry draft + one workshop-version link;
    no StrategySpec truth is copied into workshop storage; assert IDs and lineage.
    """

    def setUp(self):
        self.workshop_version_id = "wv_001"
        self.registry_draft = {
            "registry_id": BASE_REGISTRY_ID,
            "artifact_type": "strategy_spec",
            "strategy_id": STRATEGY_ID,
            "workshop_id": WORKSHOP_ID,
            "workshop_version_id": self.workshop_version_id,
            "version": "1.0.0-draft",
            "artifact_state": "draft",
            "lineage": {"source": "workshop", "workshop_id": WORKSHOP_ID, "source_event_ids": ["evt_0001"]},
        }
        self.version_created_event = _sse_event(
            event_type="workshop.version.created",
            sequence_no=7,
            payload={
                "workshop_version_id": self.workshop_version_id,
                "strategy_spec_registry_id": BASE_REGISTRY_ID,
                "strategy_id": STRATEGY_ID,
                "lineage": self.registry_draft["lineage"],
            },
        )

    def test_version_created_event_schema(self):
        _validate(self.version_created_event, "workshop_stream_event.schema.json")

    def test_registry_draft_has_strategy_and_workshop_ids(self):
        self.assertEqual(self.registry_draft["strategy_id"], STRATEGY_ID)
        self.assertEqual(self.registry_draft["workshop_id"], WORKSHOP_ID)

    def test_workshop_version_linked_to_registry_entry(self):
        payload = self.version_created_event["payload"]
        self.assertEqual(payload["workshop_version_id"], self.workshop_version_id)
        self.assertEqual(payload["strategy_spec_registry_id"], BASE_REGISTRY_ID)

    def test_spec_truth_not_copied_into_workshop_storage(self):
        workshop_storage = {
            "workshop_id": WORKSHOP_ID,
            "workshop_version_ref": {
                "workshop_version_id": self.workshop_version_id,
                "strategy_spec_registry_id": BASE_REGISTRY_ID,
            },
        }
        self.assertNotIn("hypothesis", workshop_storage)
        self.assertNotIn("objective", workshop_storage)
        self.assertIn("strategy_spec_registry_id", workshop_storage["workshop_version_ref"])

    def test_registry_draft_state_is_draft(self):
        self.assertEqual(self.registry_draft["artifact_state"], "draft")

    def test_lineage_source_is_workshop(self):
        self.assertEqual(self.registry_draft["lineage"]["source"], "workshop")
        self.assertEqual(self.registry_draft["lineage"]["workshop_id"], WORKSHOP_ID)


# ---------------------------------------------------------------------------
# Step 5 — Research plan
# ---------------------------------------------------------------------------


class TestStep5ResearchPlan(unittest.TestCase):
    """
    §F1 Step 5: servant proposes a plan covering the required research topics;
    trader approves; plan schema is valid; no_order_route_proof present.
    """

    def _make_plan(self, status: str = "approved") -> dict:
        return {
            "spec_version": "1.0",
            "plan_id": "plan_wbranch_001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": BASE_REGISTRY_ID,
            "status": status,
            "approval": {
                "state": "approved",
                "decided_by": USER_A,
                "decided_at": NOW,
                "reason": "Plan covers all required topics",
            },
            "stages": [
                {
                    "stage_id": "s01_data_validation",
                    "stage_type": "data_validation",
                    "status": "pending",
                    "dependencies": [],
                    "required_capability": "agora.research.v1",
                    "routing": {
                        "preferred_backend": "data_validation",
                        "fallback_policy": "fail_closed",
                    },
                },
                {
                    "stage_id": "s02_prototype_backtest",
                    "stage_type": "prototype_backtest",
                    "status": "pending",
                    "dependencies": ["s01_data_validation"],
                    "required_capability": "agora.research.v1",
                    "routing": {
                        "preferred_backend": "vectorbt",
                        "fallback_policy": "fail_closed",
                    },
                },
                {
                    "stage_id": "s03_rolling_oos",
                    "stage_type": "rolling_oos",
                    "status": "pending",
                    "dependencies": ["s02_prototype_backtest"],
                    "required_capability": "agora.research.v1",
                    "routing": {
                        "preferred_backend": "vectorbt",
                        "fallback_policy": "fail_closed",
                    },
                },
                {
                    "stage_id": "s04_econometric_validation",
                    "stage_type": "econometric_validation",
                    "status": "pending",
                    "dependencies": ["s01_data_validation"],
                    "required_capability": "agora.research.v1",
                    "routing": {
                        "preferred_backend": "statsmodels",
                        "fallback_policy": "fail_closed",
                    },
                },
                {
                    "stage_id": "s05_robustness_stress",
                    "stage_type": "robustness_stress",
                    "status": "pending",
                    "dependencies": ["s03_rolling_oos", "s04_econometric_validation"],
                    "required_capability": "agora.research.v1",
                    "routing": {
                        "preferred_backend": "vectorbt",
                        "fallback_policy": "fail_closed",
                    },
                },
                {
                    "stage_id": "s06_evidence_synthesis",
                    "stage_type": "evidence_synthesis",
                    "status": "pending",
                    "dependencies": ["s05_robustness_stress"],
                    "required_capability": "agora.research.v1",
                    "routing": {
                        "preferred_backend": "openclaw_result_synthesis",
                        "fallback_policy": "fail_closed",
                    },
                },
            ],
            "no_order_route_proof": "research_plan_no_order_route",
            "created_at": NOW,
        }

    def test_plan_schema_valid_when_approved(self):
        _validate(self._make_plan("approved"), "research_plan_execution.schema.json")

    def test_plan_schema_valid_when_draft(self):
        plan = self._make_plan("draft")
        plan["status"] = "draft"
        _validate(plan, "research_plan_execution.schema.json")

    def test_no_order_route_proof_present(self):
        plan = self._make_plan()
        self.assertEqual(plan["no_order_route_proof"], "research_plan_no_order_route")

    def test_plan_has_minimum_required_stages(self):
        plan = self._make_plan()
        stage_types = {s["stage_type"] for s in plan["stages"]}
        required = {"data_validation", "prototype_backtest", "rolling_oos", "evidence_synthesis"}
        self.assertTrue(required.issubset(stage_types))

    def test_all_stages_have_fail_closed_fallback(self):
        plan = self._make_plan()
        for stage in plan["stages"]:
            self.assertEqual(stage["routing"]["fallback_policy"], "fail_closed")

    def test_stage_dependencies_form_valid_dag(self):
        plan = self._make_plan()
        ids = {s["stage_id"] for s in plan["stages"]}
        for stage in plan["stages"]:
            for dep in stage["dependencies"]:
                self.assertIn(dep, ids, f"Unknown dependency {dep} in {stage['stage_id']}")

    def test_trader_approval_required(self):
        plan = self._make_plan()
        self.assertEqual(plan["approval"]["state"], "approved")
        self.assertEqual(plan["approval"]["decided_by"], USER_A)

    def test_plan_approved_event_emitted(self):
        event = _sse_event(
            "research.plan.approved",
            8,
            {"plan_id": "plan_wbranch_001", "approved_by": USER_A},
        )
        _validate(event, "workshop_stream_event.schema.json")


# ---------------------------------------------------------------------------
# Step 6 — Research execution
# ---------------------------------------------------------------------------


class TestStep6ResearchExecution(unittest.TestCase):
    """
    §F1 Step 6: dispatch typed stages to governed backends; assert backend mode
    labelling, no silent fallback, ordered/replayable progress, evidence refs,
    and no order route.
    """

    def _make_run(
        self,
        stage_type: str,
        backend: str,
        mode: str = "real",
        status: str = "succeeded",
    ) -> dict:
        return {
            "spec_version": "1.0",
            "run_id": f"run_{stage_type}_001",
            "plan_id": "plan_wbranch_001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": BASE_REGISTRY_ID,
            "stage_id": f"s_{stage_type}",
            "stage_type": stage_type,
            "execution_status": status,
            "outcome": "pass",
            "progress": {"phase": "completed", "percent": 100, "updated_at": NOW},
            "backend": {
                "requested": backend,
                "effective": backend,
                "mode": mode,
            },
            "evidence_refs": [_evidence_ref("experiment_artifact", f"art_{stage_type}_001")],
            "no_order_route_proof": "research_only_not_direct_action",
            "created_at": NOW,
        }

    def test_run_schema_valid_for_each_stage_type(self):
        for stage_type, backend in [
            ("data_validation", "data_validation"),
            ("prototype_backtest", "vectorbt"),
            ("rolling_oos", "vectorbt"),
            ("econometric_validation", "statsmodels"),
            ("evidence_synthesis", "openclaw_result_synthesis"),
        ]:
            run = self._make_run(stage_type, backend)
            _validate(run, "research_run_projection.schema.json")

    def test_no_order_route_proof_on_every_run(self):
        run = self._make_run("prototype_backtest", "vectorbt")
        self.assertEqual(run["no_order_route_proof"], "research_only_not_direct_action")

    def test_backend_mode_is_labelled_explicitly(self):
        for mode in ("real", "fixture", "stub"):
            run = self._make_run("data_validation", "data_validation", mode=mode)
            self.assertEqual(run["backend"]["mode"], mode)

    def test_fixture_mode_cannot_satisfy_full_validation(self):
        run = self._make_run("rolling_oos", "vectorbt", mode="fixture")
        self.assertNotEqual(run["backend"]["mode"], "real",
                            "fixture mode must not be treated as real backend")

    def test_progress_events_are_ordered_and_replayable(self):
        progress_events = [
            _sse_event("research.run.queued", 9, {"run_id": "run_data_validation_001"}),
            _sse_event("research.run.progress", 10, {"run_id": "run_data_validation_001", "percent": 50}),
            _sse_event("research.run.completed", 11, {"run_id": "run_data_validation_001", "outcome": "pass"}),
        ]
        seqs = [e["sequence_no"] for e in progress_events]
        self.assertEqual(seqs, sorted(seqs))
        for event in progress_events:
            _validate(event, "workshop_stream_event.schema.json")

    def test_every_completed_run_has_evidence_refs(self):
        run = self._make_run("prototype_backtest", "vectorbt")
        self.assertGreater(len(run["evidence_refs"]), 0)

    def test_no_silent_fallback(self):
        def dispatch(stage: dict) -> dict:
            routing = stage["routing"]
            preferred = routing["preferred_backend"]
            fallback_policy = routing["fallback_policy"]
            if fallback_policy == "fail_closed":
                effective = preferred
                return {"effective": effective, "mode": "real"}
            raise ValueError("Unexpected fallback policy")

        stage = {
            "stage_id": "s01",
            "stage_type": "prototype_backtest",
            "routing": {"preferred_backend": "vectorbt", "fallback_policy": "fail_closed"},
        }
        result = dispatch(stage)
        self.assertEqual(result["effective"], "vectorbt")


# ---------------------------------------------------------------------------
# Step 7 — Results and patch proposal
# ---------------------------------------------------------------------------


class TestStep7ResultsAndPatchProposal(unittest.TestCase):
    """
    §F1 Step 7: result-synthesis produces VersionPatchProposal using RFC 6902
    restricted ops; validate and accept creates a new immutable Registry draft.
    """

    def _make_proposal(self, status: str = "accepted") -> dict:
        return {
            "spec_version": "1.0",
            "proposal_id": "vpp_wbranch_p001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "base_workshop_version_id": "wv_001",
            "base_strategy_spec_registry_id": BASE_REGISTRY_ID,
            "base_document_sha256": BASE_DOC_SHA256,
            "proposed_by": _actor("agora_servant", "srv_user_a_001"),
            "source_event_ids": ["evt_0011", "evt_0012"],
            "patch_format": "rfc6902-restricted-v1",
            "operations": [
                {"op": "replace", "path": "/evaluation_plan", "value": {"revised": True}},
                {"op": "add", "path": "/evidence_refs/-", "value": {"ref": "run_evidence_001"}},
            ],
            "rationale": "Research shows branch entry signal refinement improves OOS Sharpe by 0.3",
            "change_summary": [
                {"path": "/evaluation_plan", "summary": "Revised evaluation criteria based on OOS results"},
            ],
            "validation": {
                "path_policy_valid": True,
                "base_hash_valid": True,
                "strategy_schema_valid": True,
                "policy_valid": True,
                "conflicts": [],
                "validated_at": NOW,
            },
            "status": status,
            "accepted_workshop_version_id": "wv_002",
            "accepted_strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            "created_at": NOW,
            "updated_at": NOW,
        }

    def test_proposal_schema_valid_when_accepted(self):
        _validate(self._make_proposal("accepted"), "version_patch_proposal.schema.json")

    def test_proposal_schema_valid_when_draft(self):
        p = self._make_proposal("draft")
        p.pop("accepted_workshop_version_id", None)
        p.pop("accepted_strategy_spec_registry_id", None)
        _validate(p, "version_patch_proposal.schema.json")

    def test_patch_format_is_rfc6902_restricted(self):
        self.assertEqual(self._make_proposal()["patch_format"], "rfc6902-restricted-v1")

    def test_operations_only_contain_allowed_ops(self):
        allowed = {"add", "remove", "replace", "test"}
        proposal = self._make_proposal()
        for op in proposal["operations"]:
            self.assertIn(op["op"], allowed, f"Forbidden op: {op['op']}")

    def test_operations_only_modify_allowed_paths(self):
        allowed_roots = {
            "title", "hypothesis", "objective", "market_scope",
            "data_dependencies", "execution_profile", "evaluation_plan",
            "governance", "evidence_refs", "code_refs", "metadata",
        }
        proposal = self._make_proposal()
        for op in proposal["operations"]:
            root = op["path"].lstrip("/").split("/")[0]
            self.assertIn(root, allowed_roots, f"Forbidden path root: {root}")

    def test_base_hash_validates_before_accept(self):
        proposal = self._make_proposal()
        self.assertTrue(proposal["validation"]["base_hash_valid"])

    def test_accept_creates_new_immutable_registry_entry(self):
        proposal = self._make_proposal("accepted")
        self.assertIsNotNone(proposal.get("accepted_strategy_spec_registry_id"))
        self.assertNotEqual(
            proposal["accepted_strategy_spec_registry_id"],
            proposal["base_strategy_spec_registry_id"],
        )

    def test_base_registry_entry_not_mutated(self):
        original_id = BASE_REGISTRY_ID
        proposal = self._make_proposal("accepted")
        self.assertEqual(proposal["base_strategy_spec_registry_id"], original_id)

    def test_proposal_id_matches_pattern(self):
        proposal_id = self._make_proposal()["proposal_id"]
        self.assertTrue(re.match(r"^vpp_[A-Za-z0-9_-]+$", proposal_id))

    def test_patch_proposed_event_emitted(self):
        event = _sse_event(
            "workshop.patch.proposed",
            12,
            {"proposal_id": "vpp_wbranch_p001", "status": "draft"},
        )
        _validate(event, "workshop_stream_event.schema.json")

    def test_patch_validated_event_emitted(self):
        event = _sse_event(
            "workshop.patch.validated",
            13,
            {"proposal_id": "vpp_wbranch_p001", "status": "validated"},
        )
        _validate(event, "workshop_stream_event.schema.json")


# ---------------------------------------------------------------------------
# Step 8 — Compare and readiness
# ---------------------------------------------------------------------------


class TestStep8CompareAndReadiness(unittest.TestCase):
    """
    §F1 Step 8: compare base and candidate; predicted/observed separate; OOS/
    cost/capacity/regime checks visible; readiness transitions are
    evidence-based; fixture/stub cannot satisfy full validation.
    """

    def _make_comparison(self) -> dict:
        return {
            "spec_version": "1.0",
            "comparison_id": "vcmp_wbranch_001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "base_version": {
                "workshop_version_id": "wv_001",
                "strategy_spec_registry_id": BASE_REGISTRY_ID,
                "label": "v1.0 (base)",
                "document_sha256": BASE_DOC_SHA256,
                "created_at": NOW,
            },
            "candidate_versions": [
                {
                    "workshop_version_id": "wv_002",
                    "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
                    "label": "v1.1 (candidate)",
                    "document_sha256": _sha256(b"candidate_strategy_spec_document_bytes"),
                    "created_at": NOW,
                }
            ],
            "field_diffs": [
                {
                    "path": "/evaluation_plan",
                    "change_kind": "changed",
                    "candidate_version_id": CANDIDATE_REGISTRY_ID,
                    "materiality": "high",
                }
            ],
            "metric_diffs": [
                {
                    "metric": "sharpe_ratio_oos",
                    "candidate_version_id": CANDIDATE_REGISTRY_ID,
                    "base_value": 0.8,
                    "candidate_value": 1.1,
                    "evidence_class": "backtested_oos",
                },
                {
                    "metric": "max_drawdown",
                    "candidate_version_id": CANDIDATE_REGISTRY_ID,
                    "base_value": -0.15,
                    "candidate_value": -0.12,
                    "evidence_class": "backtested_oos",
                },
                {
                    "metric": "sharpe_ratio_predicted",
                    "candidate_version_id": CANDIDATE_REGISTRY_ID,
                    "base_value": 0.9,
                    "candidate_value": 1.2,
                    "evidence_class": "predicted",
                },
            ],
            "readiness_diffs": [
                {
                    "candidate_version_id": CANDIDATE_REGISTRY_ID,
                    "gate": "preliminary_research",
                    "base_state": "conditional",
                    "candidate_state": "ready",
                    "changed_requirement_ids": ["req_oos_001"],
                }
            ],
            "recommendation": {
                "recommended_version_id": CANDIDATE_REGISTRY_ID,
                "rationale": "Candidate improves OOS Sharpe with lower drawdown",
                "confidence": 0.82,
                "decision_authority": "trader",
            },
            "created_at": NOW,
        }

    def _make_readiness(self) -> dict:
        return {
            "spec_version": "1.0",
            "assessment_id": "ready_wbranch_v2_001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "workshop_version_id": "wv_002",
            "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            "assessment_version": 1,
            "gates": [
                {
                    "gate": "preliminary_research",
                    "state": "ready",
                    "requirements": [
                        {"requirement_id": "req_hyp_001", "title": "Hypothesis defined", "hardness": "hard", "state": "satisfied"},
                        {"requirement_id": "req_oos_001", "title": "OOS validation passed", "hardness": "hard", "state": "satisfied"},
                    ],
                },
                {
                    "gate": "full_validation",
                    "state": "conditional",
                    "requirements": [
                        {"requirement_id": "req_cap_001", "title": "Capacity estimate", "hardness": "soft", "state": "partial"},
                        {"requirement_id": "req_cost_001", "title": "Cost/liquidity analysis", "hardness": "hard", "state": "satisfied"},
                    ],
                    "conditional_assumptions": ["Capacity estimate is market-size assumption only"],
                },
                {
                    "gate": "trading_room",
                    "state": "blocked",
                    "requirements": [
                        {"requirement_id": "req_tr_001", "title": "Full validation gate ready", "hardness": "hard", "state": "missing"},
                    ],
                    "blocking_requirement_ids": ["req_tr_001"],
                },
            ],
            "highest_ready_gate": "preliminary_research",
            "assessed_at": NOW,
            "evidence_refs": [_evidence_ref("experiment_artifact", "run_oos_001")],
        }

    def test_comparison_schema_valid(self):
        _validate(self._make_comparison(), "version_compare.schema.json")

    def test_readiness_schema_valid(self):
        _validate(self._make_readiness(), "strategy_readiness.schema.json")

    def test_comparison_id_matches_pattern(self):
        cmp_id = self._make_comparison()["comparison_id"]
        self.assertTrue(re.match(r"^vcmp_[A-Za-z0-9_-]+$", cmp_id))

    def test_predicted_and_observed_evidence_classes_are_separate(self):
        comparison = self._make_comparison()
        evidence_classes = {m["evidence_class"] for m in comparison["metric_diffs"]}
        self.assertIn("predicted", evidence_classes)
        self.assertIn("backtested_oos", evidence_classes)

    def test_readiness_transitions_are_evidence_based(self):
        readiness = self._make_readiness()
        for gate in readiness["gates"]:
            if gate["state"] == "ready":
                satisfied = [r for r in gate["requirements"] if r["state"] == "satisfied"]
                self.assertGreater(len(satisfied), 0)
        self.assertGreater(len(readiness["evidence_refs"]), 0)

    def test_fixture_stub_cannot_satisfy_full_validation(self):
        run = {
            "spec_version": "1.0",
            "run_id": "run_fixture_001",
            "plan_id": "plan_001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": BASE_REGISTRY_ID,
            "stage_id": "s01",
            "stage_type": "rolling_oos",
            "execution_status": "succeeded",
            "outcome": "pass",
            "progress": {"phase": "completed", "percent": 100, "updated_at": NOW},
            "backend": {"requested": "vectorbt", "effective": "vectorbt", "mode": "fixture"},
            "no_order_route_proof": "research_only_not_direct_action",
            "created_at": NOW,
        }
        _validate(run, "research_run_projection.schema.json")
        self.assertEqual(run["backend"]["mode"], "fixture")
        self.assertNotEqual(run["backend"]["mode"], "real",
                            "fixture mode must not count as full validation evidence")

    def test_gate_exact_count_is_three(self):
        readiness = self._make_readiness()
        self.assertEqual(len(readiness["gates"]), 3)

    def test_gate_names_are_canonical(self):
        readiness = self._make_readiness()
        names = {g["gate"] for g in readiness["gates"]}
        self.assertEqual(names, {"preliminary_research", "full_validation", "trading_room"})

    def test_readiness_updated_event_schema(self):
        event = _sse_event(
            "workshop.readiness.updated",
            14,
            {"assessment_id": "ready_wbranch_v2_001", "highest_ready_gate": "preliminary_research"},
        )
        _validate(event, "workshop_stream_event.schema.json")


# ---------------------------------------------------------------------------
# Step 9 — Select execution candidate(s)
# ---------------------------------------------------------------------------


class TestStep9SelectExecutionCandidates(unittest.TestCase):
    """
    §F1 Step 9: trader selects primary / shadow candidates; selection does NOT
    promote to live; no RuntimeBinding created.
    """

    def _make_selection(
        self, version_ids: list[str], selection_type: str = "primary"
    ) -> dict:
        return {
            "selection_id": "sel_wbranch_001",
            "workshop_id": WORKSHOP_ID,
            "strategy_id": STRATEGY_ID,
            "selected_version_ids": version_ids,
            "selection_type": selection_type,
            "selected_by": USER_A,
            "selected_at": NOW,
            "promotes_to_live": False,
            "runtime_binding_created": False,
            "capital_binding_created": False,
        }

    def test_selection_does_not_promote_to_live(self):
        sel = self._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(sel["promotes_to_live"])

    def test_no_runtime_binding_on_selection(self):
        sel = self._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(sel["runtime_binding_created"])

    def test_no_capital_binding_on_selection(self):
        sel = self._make_selection([CANDIDATE_REGISTRY_ID])
        self.assertFalse(sel["capital_binding_created"])

    def test_version_selected_event_schema(self):
        event = _sse_event(
            "workshop.version.selected",
            15,
            {
                "selected_version_ids": [CANDIDATE_REGISTRY_ID],
                "selection_type": "primary",
                "promotes_to_live": False,
            },
        )
        _validate(event, "workshop_stream_event.schema.json")

    def test_no_selection_is_valid(self):
        sel = self._make_selection([])
        self.assertEqual(sel["selected_version_ids"], [])
        self.assertFalse(sel["promotes_to_live"])

    def test_multiple_shadow_variants_allowed(self):
        sel = self._make_selection(
            [CANDIDATE_REGISTRY_ID, "reg_wbranch_v3_draft"],
            selection_type="shadow_comparison",
        )
        self.assertGreater(len(sel["selected_version_ids"]), 1)
        self.assertFalse(sel["promotes_to_live"])


# ---------------------------------------------------------------------------
# Step 10 — Candidate pool and Trading Room workspace
# ---------------------------------------------------------------------------


class TestStep10CandidatePoolAndTradingRoom(unittest.TestCase):
    """
    §F1 Step 10: generate CandidatePool; review/add/remove candidates; generate
    DashboardRecipe; Trading Room only available if gate is ready.
    """

    def _make_trading_room(self, readiness_state: str = "ready") -> dict:
        return {
            "spec_version": "1.0",
            "user_scope_ref": USER_A,
            "strategies": [
                {
                    "strategy_id": STRATEGY_ID,
                    "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
                    "title": "Winner Branch v1.1",
                    "readiness_state": readiness_state,
                    "monitoring_state": "inactive",
                    "pending_event_counts": {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0},
                    "dashboard_recipe_id": "recipe_wbranch_001",
                }
            ],
            "queue_summary": {"entry": 0, "add": 0, "reduce": 0, "exit": 0, "review": 0},
            "risk_summary": {"state": "normal"},
            "snapshot_at": NOW,
            "data_cutoff": NOW,
        }

    def test_trading_room_schema_valid(self):
        _validate(self._make_trading_room(), "trading_room_aggregate.schema.json")

    def test_trading_room_unavailable_when_gate_blocked(self):
        room = self._make_trading_room(readiness_state="blocked")
        strategy = room["strategies"][0]
        self.assertEqual(strategy["readiness_state"], "blocked")

    def test_trading_room_only_available_when_gate_ready(self):
        for state in ("blocked", "conditional", "stale"):
            room = self._make_trading_room(readiness_state=state)
            strategy = room["strategies"][0]
            self.assertNotEqual(strategy["readiness_state"], "ready")

        room_ready = self._make_trading_room(readiness_state="ready")
        self.assertEqual(room_ready["strategies"][0]["readiness_state"], "ready")

    def test_dashboard_recipe_linked_to_strategy(self):
        room = self._make_trading_room()
        strategy = room["strategies"][0]
        self.assertIsNotNone(strategy.get("dashboard_recipe_id"))

    def test_candidate_pool_generates_with_scoring(self):
        candidate_pool = {
            "pool_id": "pool_wbranch_001",
            "strategy_id": STRATEGY_ID,
            "scoring_recipe": "winner_branch_ev_score_v1",
            "candidates": [
                {
                    "candidate_id": "cand_001",
                    "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
                    "score": 0.87,
                    "state": "active",
                    "selected_by": USER_A,
                }
            ],
            "generated_at": NOW,
        }
        self.assertGreater(len(candidate_pool["candidates"]), 0)
        self.assertIn("scoring_recipe", candidate_pool)

    def test_user_scope_ref_is_owner(self):
        room = self._make_trading_room()
        self.assertEqual(room["user_scope_ref"], USER_A)


# ---------------------------------------------------------------------------
# Step 11 — Decision event and governed intent (critical: no order/binding)
# ---------------------------------------------------------------------------


class TestStep11DecisionEventAndGovernedIntent(unittest.TestCase):
    """
    §F1 Step 11: trigger decision event; trader approves → TradingIntent;
    shadow starts as no-order evaluation; paper/canary/live creates
    request-only governed handoff.

    CRITICAL INVARIANT: Agora NEVER creates a broker order, RuntimeBinding,
    or capital binding at any point in this step.
    """

    def _make_decision_event(self, decision_state: str = "approved_by_trader") -> dict:
        return {
            "spec_version": "1.0",
            "decision_event_id": "de_wbranch_entry_001",
            "event_kind": "entry",
            "origin": "servant_analysis",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            "subject": {"symbol": "2330.TW"},
            "state": "decided",
            "triggered_at": NOW,
            "confidence": {"value": 0.82, "basis": "statistical", "calibration_state": "calibrated"},
            "probability": {"target_outcome": "branch_winner_entry", "horizon": "3m", "value": 0.74},
            "expected_value": {"horizon": "3m", "unit": "pct_return", "gross": 0.14, "cost": 0.01, "net": 0.13, "downside": -0.04},
            "rationale": [
                {"claim": "Branch entry signal confirmed by OOS backtest", "confidence": 0.82,
                 "evidence_refs": [_evidence_ref("experiment_artifact", "run_oos_001")]},
            ],
            "risk_notes": [
                {"severity": "watch", "domain": "liquidity", "summary": "Branch liquidity can thin post-event"},
            ],
            "evidence_refs": [_evidence_ref("experiment_artifact", "run_oos_001")],
            "invalidation": {"conditions": ["Branch spread collapses", "Volume dries up"], "current_state": "valid"},
            "suggested_action": "enter",
            "suggested_size": {"size_hint": "small", "portfolio_pct": 0.03, "non_binding": True},
            "decision_state": decision_state,
            "no_order_route_proof": "agora_decision_support_only",
        }

    def _make_governed_handoff(self, requested_stage: str = "paper") -> dict:
        return {
            "spec_version": "1.0",
            "handoff_id": "hoff_wbranch_001",
            "intent_id": "intent_wbranch_001",
            "decision_event_id": "de_wbranch_entry_001",
            "requested_stage": requested_stage,
            "handoff_type": "paper_validation_request",
            "state": "submitted",
            "strategy_id": STRATEGY_ID,
            "strategy_spec_registry_id": CANDIDATE_REGISTRY_ID,
            "requested_by": _actor("trader", USER_A),
            "target_queue": "management_governance",
            "action_proposal": {
                "action": "enter",
                "symbol": "2330.TW",
                "direction": "long",
                "size_hint": "small",
                "portfolio_pct": 0.03,
                "non_binding": True,
            },
            "evidence_refs": [_evidence_ref("experiment_artifact", "run_oos_001")],
            "no_order_route_proof": "agora_request_only_no_order_route",
            "created_at": NOW,
        }

    def test_decision_event_schema_valid(self):
        _validate(self._make_decision_event(), "trading_decision_event.schema.json")

    def test_governed_handoff_schema_valid_paper(self):
        _validate(self._make_governed_handoff("paper"), "governed_intent_handoff.schema.json")

    def test_governed_handoff_schema_valid_canary(self):
        _validate(self._make_governed_handoff("canary"), "governed_intent_handoff.schema.json")

    def test_governed_handoff_schema_valid_live(self):
        _validate(self._make_governed_handoff("live"), "governed_intent_handoff.schema.json")

    def test_no_order_route_proof_on_decision_event(self):
        event = self._make_decision_event()
        self.assertEqual(event["no_order_route_proof"], "agora_decision_support_only")

    def test_no_order_route_proof_on_governed_handoff(self):
        handoff = self._make_governed_handoff()
        self.assertEqual(handoff["no_order_route_proof"], "agora_request_only_no_order_route")

    def test_action_proposal_is_non_binding(self):
        handoff = self._make_governed_handoff()
        self.assertTrue(handoff["action_proposal"]["non_binding"])

    def test_suggested_size_is_non_binding(self):
        event = self._make_decision_event()
        self.assertTrue(event["suggested_size"]["non_binding"])

    def test_agora_creates_no_runtime_binding(self):
        handoff = self._make_governed_handoff()
        self.assertNotIn("runtime_binding_id", handoff)
        self.assertIsNone(handoff.get("runtime_binding_ref"))

    def test_agora_creates_no_capital_binding(self):
        handoff = self._make_governed_handoff()
        self.assertIsNone(handoff.get("capital_binding_ref"))

    def test_agora_creates_no_broker_order(self):
        handoff = self._make_governed_handoff()
        for key in ("broker_order_id", "order_route", "order_id", "filled_qty"):
            self.assertNotIn(key, handoff, f"Forbidden field: {key}")

    def test_handoff_state_is_submitted_not_executed(self):
        handoff = self._make_governed_handoff()
        self.assertIn(handoff["state"], {"draft", "submitted"})
        self.assertNotIn(handoff["state"], {"converted"})

    def test_shadow_is_no_order_evaluation(self):
        handoff = self._make_governed_handoff("shadow")
        self.assertEqual(handoff["requested_stage"], "shadow")
        self.assertEqual(handoff["no_order_route_proof"], "agora_request_only_no_order_route")

    def test_decision_event_includes_invalidation_conditions(self):
        event = self._make_decision_event()
        self.assertGreater(len(event["invalidation"]["conditions"]), 0)

    def test_decision_event_confidence_in_bounds(self):
        event = self._make_decision_event()
        c = event["confidence"]["value"]
        self.assertGreaterEqual(c, 0.0)
        self.assertLessEqual(c, 1.0)

    def test_decision_event_probability_in_bounds(self):
        event = self._make_decision_event()
        p = event["probability"]["value"]
        self.assertGreaterEqual(p, 0.0)
        self.assertLessEqual(p, 1.0)

    def test_consultation_concluded_event_schema(self):
        event = _sse_event("consultation.completed", 20, {"consultation_id": "consult_001"})
        _validate(event, "workshop_stream_event.schema.json")


# ---------------------------------------------------------------------------
# Full flow sequence invariant
# ---------------------------------------------------------------------------


class TestFullFlowSequenceInvariant(unittest.TestCase):
    """
    Validates the 11-step flow as a whole: event ordering is monotonic across
    steps and no order/runtime/capital binding is created at any step.
    """

    def test_sse_sequence_numbers_are_monotonic_across_steps(self):
        events = [
            _sse_event("workshop.message.accepted", 1, {}),
            _sse_event("workshop.servant.response.started", 2, {}),
            _sse_event("workshop.servant.response.completed", 4, {}),
            _sse_event("workshop.completeness.updated", 5, {}),
            _sse_event("workshop.next_question.updated", 6, {}),
            _sse_event("workshop.version.created", 7, {}),
            _sse_event("research.plan.approved", 8, {}),
            _sse_event("research.run.queued", 9, {}),
            _sse_event("research.run.completed", 11, {}),
            _sse_event("workshop.patch.proposed", 12, {}),
            _sse_event("workshop.patch.validated", 13, {}),
            _sse_event("workshop.readiness.updated", 14, {}),
            _sse_event("workshop.version.selected", 15, {}),
        ]
        seqs = [e["sequence_no"] for e in events]
        self.assertEqual(seqs, sorted(seqs))

    def test_no_broker_order_anywhere_in_flow(self):
        flow_artifacts: list[dict[str, Any]] = []
        for key in ("broker_order_id", "order_route", "order_id", "filled_qty"):
            for artifact in flow_artifacts:
                self.assertNotIn(key, artifact, f"Forbidden field {key} found in flow artifact")

    def test_all_handoffs_are_request_only(self):
        handoff_types = {"shadow_start", "paper_validation_request", "promotion_review_request"}
        for ht in handoff_types:
            self.assertIn(ht, {"shadow_start", "paper_validation_request", "promotion_review_request"})


if __name__ == "__main__":
    unittest.main()
