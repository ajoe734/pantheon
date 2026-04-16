#!/usr/bin/env python3
"""Unit tests for consultation read surfaces CS-01 to CS-06.

Tests the read_store methods that back the consultation surfaces:
- list_consultations_for_persona     (CS-01)
- get_consultation                   (CS-02)
- get_consultation_participants      (CS-03)
- get_consultation_outcome           (CS-04)
- get_consultation_evidence          (CS-05)
- get_consult_policy                 (CS-06)

Canonical basis:
  PERSONA_RUNTIME_MODEL.md §6, §13, §14
  CONSULTATION_SURFACE_CONTRACT.md
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


def test_consultation_surfaces():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        store = ReadSurfaceStore(store_path)

        # ------------------------------------------------------------------ #
        # CS-01: list consultations for persona
        # ------------------------------------------------------------------ #
        consultations = store.list_consultations_for_persona("persona-alpha")
        assert consultations is not None, "CS-01: should return a list, not None"
        assert len(consultations) >= 1, "CS-01: persona-alpha should have at least one consultation"
        for c in consultations:
            assert c.get("persona_id") == "persona-alpha", "CS-01: all sessions should belong to persona-alpha"
            assert c.get("session_type") in {"consult", "committee"}, "CS-01: only consult/committee types"
        print(f"✅ CS-01: list_consultations_for_persona returns {len(consultations)} session(s)")

        # CS-01: filter by consultation_type
        pre_dep = store.list_consultations_for_persona(
            "persona-alpha", consultation_type="pre_deployment"
        )
        assert pre_dep is not None
        assert all(
            (s.get("metadata") or {}).get("consultation", {}).get("consultation_type") == "pre_deployment"
            for s in pre_dep
        ), "CS-01: filter by consultation_type should only return matching sessions"
        print("✅ CS-01: consultation_type filter works")

        # CS-01: filter by status
        terminated = store.list_consultations_for_persona("persona-alpha", status="terminated")
        assert terminated is not None
        assert all(s.get("status") == "terminated" for s in terminated), "CS-01: status filter"
        print("✅ CS-01: status filter works")

        # CS-01: None for invalid persona_id
        assert store.list_consultations_for_persona(None) is None
        assert store.list_consultations_for_persona("") is None
        print("✅ CS-01: returns None for invalid persona_id")

        # ------------------------------------------------------------------ #
        # CS-02: consultation detail
        # ------------------------------------------------------------------ #
        session = store.get_consultation("cs-20260410-001")
        assert session is not None, "CS-02: cs-20260410-001 should exist"
        assert session["session_id"] == "cs-20260410-001", "CS-02: session_id matches"
        assert session["session_type"] in {"consult", "committee"}, "CS-02: must be consult or committee"
        assert "trace_id" in session, "CS-02: trace_id required (PERSONA_RUNTIME_MODEL §16)"
        assert "request_id" in session, "CS-02: request_id required (PERSONA_RUNTIME_MODEL §16)"
        assert "capability_snapshot_id" in session, "CS-02: capability_snapshot_id required"
        print("✅ CS-02: get_consultation returns correct session detail")

        # CS-02: None for unknown
        assert store.get_consultation("nonexistent") is None, "CS-02: unknown session returns None"
        assert store.get_consultation(None) is None, "CS-02: None id returns None"
        print("✅ CS-02: returns None for unknown session_id")

        # CS-02: regular (non-consultation) session must not be returned
        assert store.get_consultation("sess-001") is None, \
            "CS-02: non-consultation session sess-001 must not be returned"
        print("✅ CS-02: non-consultation sessions are not returned")

        # ------------------------------------------------------------------ #
        # CS-03: consultation participants
        # ------------------------------------------------------------------ #
        participants = store.get_consultation_participants("cs-20260410-001")
        assert participants is not None, "CS-03: participants should not be None for valid session"
        assert len(participants) >= 1, "CS-03: at least the requester should be present"
        roles = {p.get("consultation_role") for p in participants}
        assert "requester" in roles, "CS-03: requester should be present"
        for p in participants:
            assert "session_id" in p, "CS-03: each participant has session_id"
            assert "persona_id" in p, "CS-03: each participant has persona_id"
            assert "consultation_role" in p, "CS-03: each participant has consultation_role"
        print(f"✅ CS-03: get_consultation_participants returns {len(participants)} participant(s)")

        # CS-03: None for unknown session
        assert store.get_consultation_participants("nonexistent") is None
        assert store.get_consultation_participants(None) is None
        print("✅ CS-03: returns None for unknown session_id")

        # ------------------------------------------------------------------ #
        # CS-04: consultation outcome
        # ------------------------------------------------------------------ #
        outcome = store.get_consultation_outcome("cs-20260410-001")
        assert outcome is not None, "CS-04: outcome should not be None"
        assert outcome["session_id"] == "cs-20260410-001", "CS-04: session_id matches"
        assert "metadata" in outcome, "CS-04: outcome has metadata"
        consult_meta = outcome["metadata"]["consultation"]
        assert "outcome" in consult_meta, "CS-04: outcome field present"
        assert "actual_reviewers" in consult_meta, "CS-04: actual_reviewers present"
        print(f"✅ CS-04: get_consultation_outcome returns outcome={consult_meta['outcome']!r}")

        # CS-04: None for unknown
        assert store.get_consultation_outcome("nonexistent") is None
        assert store.get_consultation_outcome(None) is None
        print("✅ CS-04: returns None for unknown session_id")

        # ------------------------------------------------------------------ #
        # CS-05: consultation evidence
        # ------------------------------------------------------------------ #
        evidence = store.get_consultation_evidence("cs-20260410-001")
        assert evidence is not None, "CS-05: evidence should not be None"
        assert isinstance(evidence, list), "CS-05: evidence is a list"
        assert len(evidence) >= 1, "CS-05: at least one evidence ref"
        for ev in evidence:
            assert "evidence_type" in ev, "CS-05: each evidence ref has evidence_type"
            assert "link" in ev, "CS-05: each evidence ref has a link"
        print(f"✅ CS-05: get_consultation_evidence returns {len(evidence)} evidence ref(s)")

        # CS-05: None for unknown
        assert store.get_consultation_evidence("nonexistent") is None
        assert store.get_consultation_evidence(None) is None
        print("✅ CS-05: returns None for unknown session_id")

        # ------------------------------------------------------------------ #
        # CS-06: consult policy
        # ------------------------------------------------------------------ #
        policy = store.get_consult_policy("persona-alpha")
        assert policy is not None, "CS-06: consult policy should exist for persona-alpha"
        assert "required_reviewers" in policy, "CS-06: required_reviewers present"
        assert "trigger_rules" in policy, "CS-06: trigger_rules present"
        assert "forbidden_solo_actions" in policy, "CS-06: forbidden_solo_actions present"
        assert "escalation_rules" in policy, "CS-06: escalation_rules present"
        assert isinstance(policy["trigger_rules"], list), "CS-06: trigger_rules is a list"
        print(f"✅ CS-06: get_consult_policy returns policy with {len(policy['trigger_rules'])} rule(s)")

        # CS-06: None for unknown persona
        assert store.get_consult_policy("nonexistent") is None
        assert store.get_consult_policy(None) is None
        print("✅ CS-06: returns None for unknown persona_id")

        # ------------------------------------------------------------------ #
        # Invariant: responder sessions are not returned by CS-01
        # (CS-01 returns only the persona's own requester sessions)
        # ------------------------------------------------------------------ #
        resp_session = store.get_consultation("cs-resp-20260410-001")
        assert resp_session is not None, "responder session should exist in store"
        # p-risk-analyst has no requester consultations so the list is empty (not None)
        risk_analyst_consultations = store.list_consultations_for_persona("p-risk-analyst")
        assert risk_analyst_consultations is not None
        assert all(
            (s.get("metadata") or {}).get("consultation", {}).get("requester_session_id") == s["session_id"]
            for s in risk_analyst_consultations
        ), "CS-01: only requester sessions are listed"
        print("✅ CS-01 invariant: only requester sessions returned by list_consultations_for_persona")

        # ------------------------------------------------------------------ #
        # Persistence: reload and verify consultation data survives
        # ------------------------------------------------------------------ #
        store2 = ReadSurfaceStore(store_path)
        assert store2.get_consultation("cs-20260410-001") is not None, "Consultation session survives reload"
        assert store2.get_consult_policy("persona-alpha") is not None, "Consult policy survives reload"
        print("✅ Persistence: consultation data reloads correctly")

    print("\n" + "=" * 55)
    print("Consultation surface (CS-01 to CS-06) tests: ALL PASSED")


if __name__ == "__main__":
    test_consultation_surfaces()
