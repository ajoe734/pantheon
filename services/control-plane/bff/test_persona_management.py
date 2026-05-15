#!/usr/bin/env python3
"""Unit test for persona-management composed view read_store helpers.

Tests the read_store methods that support the persona-management composed view:
- get_bindings_for_persona (CP-03/CP-04)
- get_sessions_for_persona (PS-03)
- get_teaching_sessions_for_persona (PS-05)
- get_persona (PS-02)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
from read_store import ReadSurfaceStore


def test_store():
    with tempfile.TemporaryDirectory() as td:
        store_path = os.path.join(td, "read_surfaces.json")
        store = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True)

        # PS-02: persona detail
        persona = store.get_persona("persona-alpha")
        assert persona is not None, "persona-alpha should exist"
        assert persona["id"] == "persona-alpha"
        assert "lifecycle_state" in persona, "PS-02: persona should have lifecycle_state"
        assert "mandate" in persona, "PS-02: persona should have mandate"
        assert "strategy_family" in persona, "PS-02: persona should have strategy_family"
        print("✅ PS-02: get_persona returns correct detail with lifecycle fields")

        # PS-02: not found
        assert store.get_persona("nonexistent") is None
        print("✅ PS-02: get_persona returns None for nonexistent")

        # CP-03: bindings for persona
        bindings = store.get_bindings_for_persona("persona-alpha")
        assert bindings is not None, "get_bindings_for_persona should not return None for valid persona"
        assert len(bindings) >= 1, "persona-alpha should have at least one binding"
        assert all(b["persona_id"] == "persona-alpha" for b in bindings)
        print(f"✅ CP-03: get_bindings_for_persona returns {len(bindings)} binding(s)")

        # CP-03: None for missing persona
        assert store.get_bindings_for_persona(None) is None
        assert store.get_bindings_for_persona("") is None
        print("✅ CP-03: get_bindings_for_persona returns None for invalid persona_id")

        # CP-04: capital pool enrichment via get_capital_pool
        for binding in bindings:
            pool_id = binding.get("capital_pool_id")
            if pool_id:
                pool = store.get_capital_pool(pool_id)
                assert pool is not None, f"Capital pool {pool_id} should exist"
                assert pool["id"] == pool_id
        print("✅ CP-04: capital_pool enrichment via get_capital_pool")

        # PS-03: sessions for persona
        sessions = store.get_sessions_for_persona("persona-alpha")
        assert sessions is not None, "get_sessions_for_persona should not return None for valid persona"
        assert len(sessions) >= 1, "persona-alpha should have at least one session"
        assert all(s["persona_id"] == "persona-alpha" for s in sessions)
        # Check session fields
        for s in sessions:
            assert "status" in s, "Session should have status"
            assert "started_at" in s, "Session should have started_at"
        print(f"✅ PS-03: get_sessions_for_persona returns {len(sessions)} session(s)")

        # PS-03: None for missing persona
        assert store.get_sessions_for_persona(None) is None
        print("✅ PS-03: get_sessions_for_persona returns None for invalid persona_id")

        # PS-05: teaching sessions for persona
        teaching = store.get_teaching_sessions_for_persona("persona-alpha")
        assert teaching is not None, "get_teaching_sessions_for_persona should not return None for valid persona"
        assert len(teaching) >= 1, "persona-alpha should have at least one teaching session"
        assert all(t["persona_id"] == "persona-alpha" for t in teaching)
        # Check teaching session fields
        for t in teaching:
            assert "status" in t, "Teaching session should have status"
            assert "topic" in t, "Teaching session should have topic"
            assert "outcomes" in t, "Teaching session should have outcomes"
        print(f"✅ PS-05: get_teaching_sessions_for_persona returns {len(teaching)} session(s)")

        # PS-05: None for missing persona
        assert store.get_teaching_sessions_for_persona(None) is None
        print("✅ PS-05: get_teaching_sessions_for_persona returns None for invalid persona_id")

        # Backend-shaped allowed actions
        actions = store.get_persona_allowed_actions("persona-alpha")
        assert actions is not None, "get_persona_allowed_actions should not return None for valid persona"
        assert isinstance(actions, dict), "allowed_actions should be a dict"
        # persona-alpha has lifecycle_state="active" in seed data
        assert "canEdit" in actions, "active persona should have canEdit"
        assert "canRetire" in actions, "active persona should have canRetire"
        print(f"✅ backend_shaped_persona_actions: get_persona_allowed_actions returns {actions}")

        # None for missing persona
        assert store.get_persona_allowed_actions("nonexistent") is None
        assert store.get_persona_allowed_actions(None) is None
        print("✅ backend_shaped_persona_actions: returns None for invalid persona_id")

        # Persistence: reload and verify
        store2 = ReadSurfaceStore(store_path, allow_local_snapshot_fallback=True)
        assert store2.get_persona("persona-alpha") is not None
        assert store2.get_bindings_for_persona("persona-alpha") is not None
        assert store2.get_sessions_for_persona("persona-alpha") is not None
        assert store2.get_teaching_sessions_for_persona("persona-alpha") is not None
        print("✅ Persistence: store reloads persona data correctly")

    print("\n" + "=" * 50)
    print("Persona-management read_store tests: ALL PASSED")


if __name__ == "__main__":
    test_store()
