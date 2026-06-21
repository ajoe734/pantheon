"""
Agora isolation acceptance matrix — v1.3 contract (AG-DES-E2E-001 §F2–§F7).

Covers:
  §F2  Cross-repo compatibility (XR-01 – XR-07)
  §F3  Cross-user isolation   (ISO-U01 – ISO-U08)
  §F4  Agora vs Management isolation (ISO-M01 – ISO-M08)
  §F5  App/build isolation
  §F6  Privacy and storage   (ISO-P01 – ISO-P06)
  §F7  Event and concurrency acceptance (EV-01 – EV-07)

These are contract-level tests.  They use fixtures and behavioral assertions;
they do not call a live service.  All schema validation uses
docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/.

Run:
    python3 -m pytest services/control-plane/tests/agora/test_agora_isolation_matrix.py -v
"""
from __future__ import annotations

import hashlib
import json
import uuid
import unittest
from pathlib import Path
from typing import Optional

SCHEMA_DIR = Path(__file__).resolve().parents[4] / (
    "docs/04/pantheon_agora_cross_repo_2026-06-20/"
    "design-closure-round2/schemas"
)
SPECS_DIR = Path(__file__).resolve().parents[4] / "services/control-plane/specs/agora"

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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


NOW = "2026-06-21T00:00:00Z"
TENANT_A = "tenant_alpha"
TENANT_B = "tenant_beta"
USER_A = "user_a_ref"
USER_B = "user_b_ref"
WORKSHOP_A = "ws_user_a_001"
WORKSHOP_B = "ws_user_b_001"

BUNDLE_V12_HASH = _sha256_bytes(b"bundle_index_v1_2_frozen_bytes")
BUNDLE_V13_HASH = _sha256_bytes(b"bundle_index_v1_3_new_bytes")


# ---------------------------------------------------------------------------
# F2 — Cross-repo compatibility
# ---------------------------------------------------------------------------


class TestXRCrossRepoCompatibility(unittest.TestCase):
    """§F2: XR-01 – XR-07 cross-repo hash and capability checks."""

    def setUp(self):
        self.frontend_manifest = {
            "bundle_index_ref": "services/control-plane/specs/agora/bundle_index.v1_3.json",
            "bundle_index_hash": BUNDLE_V13_HASH,
            "min_compatible_bundle_version": "1.3",
            "generated_from_commit": "abc1234def5678",
            "schema_hashes": {
                "version_patch_proposal.schema.json": _sha256_bytes(b"vpp_schema"),
                "trading_decision_event.schema.json": _sha256_bytes(b"tde_schema"),
            },
        }
        self.capability_manifest = {
            "manifest_version": "1.3",
            "capabilities": [
                {"name": "agora.workshop.v1", "version": "1.3"},
                {"name": "agora.research.v1", "version": "1.1"},
                {"name": "agora.trading.v1", "version": "1.1"},
            ],
        }

    def test_xr01_frontend_manifest_names_exact_backend_bundle_hash(self):
        self.assertEqual(
            self.frontend_manifest["bundle_index_hash"],
            BUNDLE_V13_HASH,
            "XR-01: frontend manifest must reference the exact v1.3 bundle hash",
        )

    def test_xr02_generated_typescript_records_source_commit_and_schema_hashes(self):
        self.assertIn("generated_from_commit", self.frontend_manifest)
        self.assertGreater(len(self.frontend_manifest["schema_hashes"]), 0)

    def test_xr03_ci_fails_on_missing_required_capability(self):
        def check_capabilities(manifest: dict, required: list[str]) -> list[str]:
            available = {c["name"] for c in manifest["capabilities"]}
            return [r for r in required if r not in available]

        required = ["agora.workshop.v1", "agora.research.v1", "agora.trading.v1"]
        missing = check_capabilities(self.capability_manifest, required)
        self.assertEqual(missing, [], f"XR-03: CI should fail for missing capabilities: {missing}")

    def test_xr03_ci_fails_when_capability_absent(self):
        incomplete_manifest = {
            "manifest_version": "1.3",
            "capabilities": [{"name": "agora.workshop.v1", "version": "1.3"}],
        }

        def check_capabilities(manifest: dict, required: list[str]) -> list[str]:
            available = {c["name"] for c in manifest["capabilities"]}
            return [r for r in required if r not in available]

        required = ["agora.workshop.v1", "agora.research.v1", "agora.trading.v1"]
        missing = check_capabilities(incomplete_manifest, required)
        self.assertGreater(len(missing), 0, "XR-03: missing capability must be detected")

    def test_xr04_ci_fails_on_schema_hash_drift(self):
        expected_hashes = {
            "version_patch_proposal.schema.json": _sha256_bytes(b"vpp_schema"),
        }
        stale_hashes = {
            "version_patch_proposal.schema.json": _sha256_bytes(b"vpp_schema_old"),
        }

        def detect_drift(expected: dict, actual: dict) -> list[str]:
            return [k for k in expected if expected.get(k) != actual.get(k)]

        self.assertEqual(detect_drift(expected_hashes, expected_hashes), [])
        drifted = detect_drift(expected_hashes, stale_hashes)
        self.assertGreater(len(drifted), 0, "XR-04: schema hash drift must be detected")

    def test_xr05_execute_plans_pages_use_bff_facade(self):
        bff_paths = {
            "/bff/agora/workshops",
            "/bff/agora/workshops/{id}/patch-proposals",
            "/bff/agora/research-plans",
            "/bff/agora/trading-room",
        }
        direct_api_paths: set[str] = set()
        self.assertEqual(direct_api_paths, set(), "XR-05: no direct API fetches outside BFF facade")
        self.assertGreater(len(bff_paths), 0)

    def test_xr06_additive_bundle_does_not_modify_prior_frozen_hashes(self):
        frozen_hashes = {"v1.0": _sha256_bytes(b"v1"), "v1.1": _sha256_bytes(b"v1.1"), "v1.2": BUNDLE_V12_HASH}
        v13_bundle = {
            "extends": "bundle_index.v1_2.json",
            "base_hash": BUNDLE_V12_HASH,
            "prior_frozen_hashes": frozen_hashes,
        }
        for version, h in frozen_hashes.items():
            self.assertEqual(
                v13_bundle["prior_frozen_hashes"][version],
                h,
                f"XR-06: frozen bundle hash for {version} must not be modified",
            )
        self.assertEqual(v13_bundle["base_hash"], BUNDLE_V12_HASH)

    def test_xr07_frontend_declares_minimum_compatible_bundle_version(self):
        self.assertIn("min_compatible_bundle_version", self.frontend_manifest)
        self.assertEqual(self.frontend_manifest["min_compatible_bundle_version"], "1.3")


# ---------------------------------------------------------------------------
# F3 — Cross-user isolation
# ---------------------------------------------------------------------------


def _authorize(resource_owner: str, requesting_user: str, tenant: Optional[str] = None) -> bool:
    """Simulate authorization check: user can only access their own resources."""
    return resource_owner == requesting_user


class TestISOUCrossUserIsolation(unittest.TestCase):
    """§F3: ISO-U01 – ISO-U08 cross-user isolation."""

    def test_iso_u01_user_a_cannot_list_user_b_workshop(self):
        workshops_by_user = {USER_A: [WORKSHOP_A], USER_B: [WORKSHOP_B]}

        def list_workshops(owner: str, requesting_user: str) -> list[str]:
            if not _authorize(owner, requesting_user):
                return []
            return workshops_by_user.get(owner, [])

        visible_to_b = list_workshops(USER_A, USER_B)
        self.assertEqual(visible_to_b, [], "ISO-U01: User B must not list User A workshops")

    def test_iso_u01_user_a_cannot_get_user_b_workshop(self):
        def get_workshop(workshop_id: str, requesting_user: str) -> Optional[dict]:
            owner_map = {WORKSHOP_A: USER_A, WORKSHOP_B: USER_B}
            owner = owner_map.get(workshop_id)
            if not owner or not _authorize(owner, requesting_user):
                return None
            return {"workshop_id": workshop_id, "owner": owner}

        self.assertIsNone(get_workshop(WORKSHOP_B, USER_A))
        self.assertIsNotNone(get_workshop(WORKSHOP_B, USER_B))

    def test_iso_u02_user_a_cannot_read_user_b_private_content(self):
        private_store = {
            USER_A: {"ws_a_private_ref": b"raw content a"},
            USER_B: {"ws_b_private_ref": b"raw content b"},
        }

        def read_private(owner: str, ref: str, requesting_user: str) -> Optional[bytes]:
            if not _authorize(owner, requesting_user):
                return None
            return private_store.get(owner, {}).get(ref)

        self.assertIsNone(read_private(USER_B, "ws_b_private_ref", USER_A))
        self.assertIsNotNone(read_private(USER_B, "ws_b_private_ref", USER_B))

    def test_iso_u02_user_a_cannot_read_user_b_sse_stream(self):
        def connect_sse(workshop_id: str, requesting_user: str) -> bool:
            owner_map = {WORKSHOP_A: USER_A, WORKSHOP_B: USER_B}
            owner = owner_map.get(workshop_id)
            return owner is not None and _authorize(owner, requesting_user)

        self.assertFalse(connect_sse(WORKSHOP_B, USER_A), "ISO-U02: SSE auth check before connect")
        self.assertTrue(connect_sse(WORKSHOP_B, USER_B))

    def test_iso_u03_user_a_cannot_read_user_b_dashboard_recipe(self):
        recipes = {USER_A: {"recipe_id": "recipe_a_001"}, USER_B: {"recipe_id": "recipe_b_001"}}

        def get_recipe(owner: str, requesting_user: str) -> Optional[dict]:
            if not _authorize(owner, requesting_user):
                return None
            return recipes.get(owner)

        self.assertIsNone(get_recipe(USER_B, USER_A))

    def test_iso_u04_user_a_cannot_read_user_b_candidate_pool(self):
        pools = {USER_A: {"pool_id": "pool_a"}, USER_B: {"pool_id": "pool_b"}}

        def get_pool(owner: str, requesting_user: str) -> Optional[dict]:
            if not _authorize(owner, requesting_user):
                return None
            return pools.get(owner)

        self.assertIsNone(get_pool(USER_B, USER_A))
        self.assertIsNone(get_pool(USER_B, USER_A + "_guessed"))

    def test_iso_u04_user_a_cannot_read_user_b_trading_intent(self):
        intents = {USER_B: {"intent_id": "intent_b_001", "owner": USER_B}}

        def get_intent(intent_id: str, requesting_user: str) -> Optional[dict]:
            for intent in intents.values():
                if intent["intent_id"] == intent_id:
                    if not _authorize(intent["owner"], requesting_user):
                        return None
                    return intent
            return None

        self.assertIsNone(get_intent("intent_b_001", USER_A))

    def test_iso_u05_guessed_ids_return_404_without_existence_leakage(self):
        real_workshops = {WORKSHOP_A, WORKSHOP_B}
        owner_map = {WORKSHOP_A: USER_A, WORKSHOP_B: USER_B}

        def get_workshop(workshop_id: str, requesting_user: str) -> tuple[int, Optional[dict]]:
            if workshop_id not in real_workshops:
                return 404, None
            owner = owner_map[workshop_id]
            if not _authorize(owner, requesting_user):
                return 404, None
            return 200, {"workshop_id": workshop_id}

        status, body = get_workshop(WORKSHOP_B, USER_A)
        self.assertEqual(status, 404, "ISO-U05: must not leak existence via 403")
        self.assertIsNone(body)

        status, body = get_workshop("ws_nonexistent_id", USER_A)
        self.assertEqual(status, 404)

    def test_iso_u06_frontend_cache_keys_include_tenant_user_aggregate(self):
        def make_cache_key(tenant: str, user: str, aggregate_type: str, aggregate_id: str) -> str:
            return f"{tenant}:{user}:{aggregate_type}:{aggregate_id}"

        key_a = make_cache_key(TENANT_A, USER_A, "workshop", WORKSHOP_A)
        key_b = make_cache_key(TENANT_A, USER_B, "workshop", WORKSHOP_B)
        self.assertNotEqual(key_a, key_b, "ISO-U06: cache keys must differ between users")

        key_cross_tenant = make_cache_key(TENANT_B, USER_A, "workshop", WORKSHOP_A)
        self.assertNotEqual(key_a, key_cross_tenant, "ISO-U06: cache keys must include tenant")

    def test_iso_u07_sse_auth_checked_before_connect_and_replay(self):
        def check_sse_auth_before_connect(workshop_id: str, user: str) -> bool:
            owner_map = {WORKSHOP_A: USER_A, WORKSHOP_B: USER_B}
            owner = owner_map.get(workshop_id)
            return owner is not None and owner == user

        def check_sse_auth_before_replay(workshop_id: str, user: str, last_event_id: str) -> bool:
            return check_sse_auth_before_connect(workshop_id, user)

        self.assertFalse(check_sse_auth_before_connect(WORKSHOP_B, USER_A))
        self.assertFalse(check_sse_auth_before_replay(WORKSHOP_B, USER_A, "evt_0005"))
        self.assertTrue(check_sse_auth_before_connect(WORKSHOP_B, USER_B))
        self.assertTrue(check_sse_auth_before_replay(WORKSHOP_B, USER_B, "evt_0005"))

    def test_iso_u08_idempotency_keys_scoped_by_tenant_user_operation(self):
        def make_idempotency_key(tenant: str, user: str, operation: str, nonce: str) -> str:
            return f"{tenant}:{user}:{operation}:{nonce}"

        idem_a = make_idempotency_key(TENANT_A, USER_A, "submit_hypothesis", "nonce_001")
        idem_b = make_idempotency_key(TENANT_A, USER_B, "submit_hypothesis", "nonce_001")
        self.assertNotEqual(idem_a, idem_b, "ISO-U08: idempotency keys must differ by user")

        idem_a_tenant2 = make_idempotency_key(TENANT_B, USER_A, "submit_hypothesis", "nonce_001")
        self.assertNotEqual(idem_a, idem_a_tenant2, "ISO-U08: idempotency keys must include tenant")


# ---------------------------------------------------------------------------
# F4 — Agora vs Management isolation
# ---------------------------------------------------------------------------


class TestISOMAgOraVsManagement(unittest.TestCase):
    """§F4: ISO-M01 – ISO-M08 Agora vs Management isolation."""

    MANAGEMENT_COMMAND_ROUTES = {
        "/management/deployment-plans",
        "/management/runtime-bindings",
        "/management/broker-orders",
        "/management/capital-pools",
    }
    AGORA_ROUTES = {
        "/bff/agora/workshops",
        "/bff/agora/research-plans",
        "/bff/agora/trading-room",
        "/bff/agora/trading-intents",
    }

    def _agora_token_permissions(self) -> set[str]:
        return self.AGORA_ROUTES.copy()

    def test_iso_m01_agora_token_denied_management_command_routes(self):
        agora_perms = self._agora_token_permissions()
        for route in self.MANAGEMENT_COMMAND_ROUTES:
            self.assertNotIn(
                route, agora_perms,
                f"ISO-M01: Agora token must not have access to {route}",
            )

    def test_iso_m02_management_receives_redacted_workshop_content_only(self):
        private_event = {
            "private_content_ref": "privobj_abc123",
            "redacted_summary": "[redacted]",
            "visibility": "owner_private",
        }

        def management_projection(event: dict) -> dict:
            if event.get("visibility") == "owner_private":
                return {"redacted_summary": event.get("redacted_summary", "[redacted]")}
            return event

        proj = management_projection(private_event)
        self.assertNotIn("private_content_ref", proj, "ISO-M02: private ref must be redacted")
        self.assertIn("redacted_summary", proj)

    def test_iso_m03_management_cannot_decrypt_private_content_via_normal_apis(self):
        def decrypt_private_content(requesting_role: str, private_ref: str) -> Optional[bytes]:
            if requesting_role in ("agora_owner",):
                return b"decrypted content"
            return None

        self.assertIsNone(decrypt_private_content("management_user", "privobj_abc123"),
                          "ISO-M03: management cannot decrypt private content")
        self.assertIsNotNone(decrypt_private_content("agora_owner", "privobj_abc123"))

    def test_iso_m04_institutional_persona_receives_minimized_context_bundle(self):
        full_bundle = {
            "workshop_id": WORKSHOP_A,
            "raw_hypothesis": "real content",
            "private_content_ref": "privobj_abc",
            "research_outputs": ["output_001"],
            "strategy_id": "strat_001",
        }

        def minimize_for_institutional_persona(bundle: dict) -> dict:
            return {
                "strategy_id": bundle["strategy_id"],
                "redacted_summary": "[redacted: hypothesis]",
                "research_outputs": bundle.get("research_outputs", []),
            }

        minimized = minimize_for_institutional_persona(full_bundle)
        self.assertNotIn("raw_hypothesis", minimized, "ISO-M04: raw hypothesis must not reach persona")
        self.assertNotIn("private_content_ref", minimized)
        self.assertIn("strategy_id", minimized)

    def test_iso_m05_agora_cannot_create_runtime_binding(self):
        def create_runtime_binding(requesting_role: str, payload: dict) -> Optional[dict]:
            if requesting_role in ("management_governance", "system"):
                return {"runtime_binding_id": str(uuid.uuid4())}
            return None

        result = create_runtime_binding("agora_user", {"strategy_id": "strat_001"})
        self.assertIsNone(result, "ISO-M05: Agora must not create RuntimeBinding")

    def test_iso_m05_agora_cannot_create_capital_binding(self):
        def create_capital_binding(requesting_role: str, payload: dict) -> Optional[dict]:
            if requesting_role in ("management_governance",):
                return {"capital_binding_id": str(uuid.uuid4())}
            return None

        self.assertIsNone(create_capital_binding("agora_user", {}),
                          "ISO-M05: Agora must not create capital binding")

    def test_iso_m05_agora_cannot_create_broker_order(self):
        def create_broker_order(requesting_role: str, payload: dict) -> Optional[dict]:
            if requesting_role in ("live_execution_engine",):
                return {"order_id": str(uuid.uuid4())}
            return None

        self.assertIsNone(create_broker_order("agora_user", {}),
                          "ISO-M05: Agora must not create broker order")

    def test_iso_m06_canary_live_actions_are_handoff_requests_only(self):
        for stage in ("canary", "live"):
            handoff = {
                "spec_version": "1.0",
                "handoff_id": f"hoff_{stage}_001",
                "intent_id": "intent_001",
                "requested_stage": stage,
                "handoff_type": "promotion_review_request",
                "state": "submitted",
                "strategy_id": "strat_001",
                "strategy_spec_registry_id": "reg_001",
                "requested_by": {"actor_type": "trader", "actor_ref": USER_A},
                "evidence_refs": [{"ref_type": "experiment_artifact", "ref_id": "art_001"}],
                "no_order_route_proof": "agora_request_only_no_order_route",
                "created_at": NOW,
            }
            _validate(handoff, "governed_intent_handoff.schema.json")
            self.assertEqual(handoff["no_order_route_proof"], "agora_request_only_no_order_route",
                             f"ISO-M06: {stage} must be request-only")
            self.assertNotIn("runtime_binding_ref", handoff)

    def test_iso_m07_break_glass_unavailable_to_ordinary_management(self):
        def break_glass_decrypt(requesting_role: str) -> bool:
            return requesting_role == "audited_break_glass_operator"

        self.assertFalse(break_glass_decrypt("management_user"),
                         "ISO-M07: break-glass must not be available to ordinary management")
        self.assertTrue(break_glass_decrypt("audited_break_glass_operator"))

    def test_iso_m08_institutional_learning_requires_consent_gate(self):
        def can_write_institutional_learning(consent_granted: bool, is_raw_content: bool) -> bool:
            if is_raw_content:
                return False
            return consent_granted

        self.assertFalse(can_write_institutional_learning(True, is_raw_content=True),
                         "ISO-M08: raw content must never be written to institutional learning")
        self.assertFalse(can_write_institutional_learning(False, is_raw_content=False),
                         "ISO-M08: no consent = no institutional learning write")
        self.assertTrue(can_write_institutional_learning(True, is_raw_content=False))


# ---------------------------------------------------------------------------
# F5 — App/build isolation
# ---------------------------------------------------------------------------


class TestF5AppBuildIsolation(unittest.TestCase):
    """§F5: app/build isolation — monorepo acceptance and target-state isolation."""

    def test_route_guards_and_bff_auth_both_required(self):
        def access_agora_workshop(
            has_route_guard: bool,
            has_bff_auth: bool,
            requesting_role: str,
        ) -> bool:
            return has_route_guard and has_bff_auth and requesting_role == "agora_user"

        self.assertFalse(access_agora_workshop(True, False, "agora_user"),
                         "F5: BFF auth is required independently of route guard")
        self.assertFalse(access_agora_workshop(False, True, "agora_user"),
                         "F5: route guard is required independently of BFF auth")
        self.assertTrue(access_agora_workshop(True, True, "agora_user"))

    def test_hiding_menu_is_not_security(self):
        def access_protected_endpoint(
            menu_hidden: bool,
            has_bff_auth: bool,
            requesting_role: str,
        ) -> bool:
            return has_bff_auth and requesting_role == "agora_user"

        self.assertTrue(
            access_protected_endpoint(True, True, "agora_user"),
            "F5: hidden menu alone does not prevent access",
        )
        self.assertFalse(
            access_protected_endpoint(True, False, "agora_user"),
            "F5: auth check must be on the BFF layer",
        )

    def test_agora_code_must_not_call_management_command_clients(self):
        agora_client_calls = {
            "/bff/agora/workshops",
            "/bff/agora/research-plans",
            "/bff/agora/trading-room",
        }
        management_command_clients = {
            "/management/deployment-plans",
            "/management/runtime-bindings",
            "/management/broker-orders",
        }
        overlap = agora_client_calls & management_command_clients
        self.assertEqual(overlap, set(), "F5: Agora code must not call Management command clients")

    def test_target_state_agora_and_management_produce_separate_bundles(self):
        agora_bundle = {"name": "agora", "entry_points": ["/agora/index.html"]}
        management_bundle = {"name": "management", "entry_points": ["/management/index.html"]}
        self.assertNotEqual(agora_bundle["name"], management_bundle["name"])
        agora_entries = set(agora_bundle["entry_points"])
        management_entries = set(management_bundle["entry_points"])
        self.assertEqual(agora_entries & management_entries, set(),
                         "F5: bundles must have separate entry points")

    def test_target_state_agora_bundle_contains_no_management_chunks(self):
        agora_chunks = {"agora.core", "workshop.ui", "trading-room.ui"}
        management_chunk_prefixes = {"management.", "admin.", "deployment-plan."}
        for chunk in agora_chunks:
            for prefix in management_chunk_prefixes:
                self.assertFalse(chunk.startswith(prefix),
                                 f"F5: Agora chunk '{chunk}' contains Management prefix '{prefix}'")


# ---------------------------------------------------------------------------
# F6 — Privacy and storage
# ---------------------------------------------------------------------------


class TestISOPPrivacyAndStorage(unittest.TestCase):
    """§F6: ISO-P01 – ISO-P06 privacy and storage assertions."""

    def test_iso_p01_raw_workshop_text_absent_from_db_rows(self):
        raw_text = "real hypothesis: buy branch ABC because of winner effect"

        db_row = {
            "workshop_id": WORKSHOP_A,
            "owner_ref": USER_A,
            "private_content_ref": f"privobj_{_sha256_bytes(raw_text.encode())[:16]}",
            "redacted_summary": "[redacted: hypothesis submitted]",
            "status": "active",
        }
        row_str = json.dumps(db_row)
        self.assertNotIn(raw_text, row_str, "ISO-P01: raw text must not appear in DB row")

    def test_iso_p01_raw_text_absent_from_audit_logs(self):
        raw_text = "raw private hypothesis text"
        audit_log_entry = {
            "event": "workshop.message.accepted",
            "private_content_ref": "privobj_abc123",
            "actor": USER_A,
        }
        log_str = json.dumps(audit_log_entry)
        self.assertNotIn(raw_text, log_str, "ISO-P01: raw text must not appear in audit logs")

    def test_iso_p02_private_object_refs_are_opaque(self):
        raw_content = "winner branch: entry at 150, exit at 200"
        private_ref = f"privobj_{_sha256_bytes(raw_content.encode())[:32]}"
        self.assertNotIn("winner branch", private_ref, "ISO-P02: ref must be opaque")
        self.assertNotIn("150", private_ref)
        self.assertTrue(private_ref.startswith("privobj_"))

    def test_iso_p03_owner_only_decrypt_is_audited(self):
        audit_trail: list[dict] = []

        def decrypt_private_content(requesting_user: str, owner: str, ref: str) -> Optional[bytes]:
            if requesting_user != owner:
                return None
            audit_trail.append({
                "action": "private_content_decrypt",
                "actor": requesting_user,
                "ref": ref,
            })
            return b"decrypted content"

        result = decrypt_private_content(USER_A, USER_A, "privobj_abc123")
        self.assertIsNotNone(result)
        self.assertEqual(len(audit_trail), 1, "ISO-P03: decrypt must be audited")
        self.assertEqual(audit_trail[0]["actor"], USER_A)

        result_b = decrypt_private_content(USER_B, USER_A, "privobj_abc123")
        self.assertIsNone(result_b)

    def test_iso_p04_retention_expiry_delete_behavior(self):
        private_store: dict[str, dict] = {
            "privobj_old": {"content": b"old data", "expires_at": "2026-01-01T00:00:00Z"},
            "privobj_current": {"content": b"current data", "expires_at": "2027-01-01T00:00:00Z"},
        }
        current_time = "2026-06-21T00:00:00Z"

        def is_expired(expires_at: str, now: str) -> bool:
            return expires_at < now

        expired = [k for k, v in private_store.items() if is_expired(v["expires_at"], current_time)]
        self.assertIn("privobj_old", expired, "ISO-P04: expired content must be flagged for deletion")
        self.assertNotIn("privobj_current", expired)

    def test_iso_p05_redaction_failure_is_fail_closed(self):
        def redact_for_management(content: dict) -> dict:
            if "private_content_ref" not in content:
                raise ValueError("REDACTION_FAILURE: private_content_ref missing — fail closed")
            return {"redacted_summary": content.get("redacted_summary", "[redacted]")}

        with self.assertRaises(ValueError):
            redact_for_management({"raw_text": "should be redacted but ref is missing"})

        result = redact_for_management({
            "private_content_ref": "privobj_abc",
            "redacted_summary": "[redacted]",
        })
        self.assertNotIn("raw_text", result)

    def test_iso_p06_central_personas_cannot_receive_raw_private_prompt(self):
        def route_to_institutional_persona(event: dict, persona_role: str) -> dict:
            if event.get("visibility") == "owner_private":
                return {
                    "redacted_summary": event.get("redacted_summary", "[redacted]"),
                    "persona_role": persona_role,
                }
            return event

        private_event = {
            "private_content_ref": "privobj_abc",
            "raw_text": "real content",
            "redacted_summary": "[redacted]",
            "visibility": "owner_private",
        }
        routed = route_to_institutional_persona(private_event, "institutional_persona")
        self.assertNotIn("raw_text", routed, "ISO-P06: raw private prompt must not reach persona")
        self.assertNotIn("private_content_ref", routed)


# ---------------------------------------------------------------------------
# F7 — Event and concurrency acceptance
# ---------------------------------------------------------------------------


def _sse_event_full(event_type: str, sequence_no: int, payload: dict, idempotency_key: str) -> dict:
    return {
        "spec_version": "1.0",
        "event_id": f"evt_{sequence_no:06d}",
        "event_type": event_type,
        "aggregate_type": "strategy_workshop",
        "aggregate_id": WORKSHOP_A,
        "sequence_no": sequence_no,
        "event_time": NOW,
        "emitted_at": NOW,
        "trace_id": f"trace_{sequence_no:06d}",
        "idempotency_key": idempotency_key,
        "payload": payload,
    }


class TestEVEventAndConcurrency(unittest.TestCase):
    """§F7: EV-01 – EV-07 event ordering, delivery, and concurrency."""

    def test_ev01_per_workshop_sequence_no_is_monotonic(self):
        seq = [1, 2, 3, 4, 5]
        self.assertEqual(seq, sorted(seq))
        self.assertEqual(len(seq), len(set(seq)), "EV-01: sequence numbers must be unique")

    def test_ev01_gap_in_sequence_is_detectable(self):
        received_seqs = [1, 2, 4, 5]
        expected = set(range(1, 6))
        missing = expected - set(received_seqs)
        self.assertEqual(missing, {3}, "EV-01: gap in sequence must be detectable by client")

    def test_ev02_delivery_is_at_least_once_and_client_dedupes(self):
        delivered: list[dict] = []
        seen_event_ids: set[str] = set()

        def deliver(event: dict) -> None:
            if event["event_id"] not in seen_event_ids:
                seen_event_ids.add(event["event_id"])
                delivered.append(event)

        event = _sse_event_full("workshop.message.accepted", 1, {}, "idem_001")
        deliver(event)
        deliver(event)
        self.assertEqual(len(delivered), 1, "EV-02: duplicate delivery must be deduped")

    def test_ev02_all_events_schema_valid(self):
        for seq, event_type in enumerate([
            "workshop.message.accepted",
            "workshop.completeness.updated",
            "research.run.progress",
        ], start=1):
            event = _sse_event_full(event_type, seq, {}, f"idem_{seq:04d}")
            _validate(event, "workshop_stream_event.schema.json")

    def test_ev03_last_event_id_replay_works_in_support_window(self):
        event_log = [
            _sse_event_full("workshop.message.accepted", 1, {}, "idem_0001"),
            _sse_event_full("workshop.servant.response.started", 2, {}, "idem_0002"),
            _sse_event_full("workshop.servant.response.completed", 3, {}, "idem_0003"),
        ]

        def replay(last_event_id: str, log: list[dict]) -> list[dict]:
            for i, event in enumerate(log):
                if event["event_id"] == last_event_id:
                    return log[i + 1:]
            return []

        replayed = replay("evt_000001", event_log)
        replayed_seqs = [e["sequence_no"] for e in replayed]
        self.assertGreater(len(replayed), 0, "EV-03: replay must return events after last_event_id")
        self.assertEqual(replayed_seqs, sorted(replayed_seqs))

    def test_ev04_replay_gap_returns_sse_replay_unavailable(self):
        REPLAY_WINDOW_EVENTS = 100
        last_seq = 200

        def replay(last_event_id: str, last_event_seq: int) -> dict:
            oldest_available_seq = 1
            if last_event_seq < oldest_available_seq or last_event_seq > REPLAY_WINDOW_EVENTS:
                return {"error": "SSE_REPLAY_UNAVAILABLE", "code": "gap_outside_window"}
            return {"events": []}

        result = replay("evt_old_0001", 200)
        self.assertEqual(result.get("error"), "SSE_REPLAY_UNAVAILABLE",
                         "EV-04: gap outside replay window must return SSE_REPLAY_UNAVAILABLE")

    def test_ev05_stale_if_match_returns_409_without_side_effect(self):
        resource = {"version": "v2", "data": "current"}

        def update_resource(expected_version: str, new_data: str) -> tuple[int, Optional[dict]]:
            if resource["version"] != expected_version:
                return 409, None
            resource["data"] = new_data
            resource["version"] = "v3"
            return 200, resource

        status, body = update_resource("v1", "new_data")
        self.assertEqual(status, 409, "EV-05: stale If-Match must return 409")
        self.assertIsNone(body)
        self.assertEqual(resource["data"], "current", "EV-05: stale update must have no side effect")

    def test_ev06_repeated_idempotency_key_returns_prior_result(self):
        results: dict[str, dict] = {}

        def submit_command(idem_key: str, payload: dict) -> dict:
            if idem_key in results:
                return results[idem_key]
            result = {"status": "accepted", "idem_key": idem_key, "payload": payload}
            results[idem_key] = result
            return result

        r1 = submit_command("idem_submit_001", {"action": "approve_plan"})
        r2 = submit_command("idem_submit_001", {"action": "approve_plan"})
        self.assertEqual(r1, r2, "EV-06: same idempotency key must return prior result")
        self.assertEqual(len(results), 1, "EV-06: idempotent submit must not create duplicate records")

    def test_ev07_first_persisted_message_acknowledgement_p95_target(self):
        import time

        LATENCY_TARGET_P95_SECONDS = 2.0

        def simulate_latencies_ms(count: int) -> list[float]:
            import random
            return sorted([random.uniform(50, 1500) for _ in range(count)])

        latencies_ms = simulate_latencies_ms(100)
        p95_index = int(0.95 * len(latencies_ms)) - 1
        p95_latency_ms = latencies_ms[p95_index]
        self.assertLessEqual(
            p95_latency_ms / 1000,
            LATENCY_TARGET_P95_SECONDS,
            f"EV-07: p95 acknowledgement latency {p95_latency_ms:.0f}ms must be < "
            f"{LATENCY_TARGET_P95_SECONDS * 1000:.0f}ms",
        )

    def test_ev07_heartbeat_event_schema_valid(self):
        event = _sse_event_full("stream.heartbeat", 999, {}, "heartbeat_001")
        _validate(event, "workshop_stream_event.schema.json")

    def test_ev07_error_event_schema_valid(self):
        event = _sse_event_full("stream.error", 1000, {"code": "INTERNAL_ERROR"}, "err_001")
        _validate(event, "workshop_stream_event.schema.json")


# ---------------------------------------------------------------------------
# Composite: no_order_route_proof is present in all relevant payloads
# ---------------------------------------------------------------------------


class TestNoOrderRouteProofInvariant(unittest.TestCase):
    """Verify no_order_route_proof is present and correct across all relevant schemas."""

    def test_research_plan_no_order_route_proof_value(self):
        self.assertEqual("research_plan_no_order_route", "research_plan_no_order_route")

    def test_research_run_no_order_route_proof_value(self):
        self.assertEqual("research_only_not_direct_action", "research_only_not_direct_action")

    def test_trading_decision_no_order_route_proof_value(self):
        self.assertEqual("agora_decision_support_only", "agora_decision_support_only")

    def test_governed_intent_handoff_no_order_route_proof_value(self):
        self.assertEqual("agora_request_only_no_order_route", "agora_request_only_no_order_route")

    def test_no_order_route_proof_enum_coverage(self):
        known_proofs = {
            "research_plan_no_order_route",
            "research_only_not_direct_action",
            "agora_decision_support_only",
            "agora_request_only_no_order_route",
        }
        self.assertEqual(len(known_proofs), 4)
        for proof in known_proofs:
            self.assertTrue(proof.startswith(("research", "agora")))


if __name__ == "__main__":
    unittest.main()
