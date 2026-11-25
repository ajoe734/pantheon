from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.execution.lean_runtime.runtime_identity import RuntimeIdentity
from services.runtime_auth_inbound import AuthError, validate_request_auth


class TestRuntimeIdentity(unittest.TestCase):
    def test_from_env_collects_isolation_and_binding_refs(self):
        env = {
            "PANTHEON_RUNTIME_ROLE": "pantheon-lean-paper-runtime",
            "PANTHEON_RUNTIME_MODE": "paper",
            "PANTHEON_PAPER_RUNTIME_ID": "paper-runtime-001",
            "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
            "PANTHEON_TELEMETRY_URL": "http://telemetry:8083",
            "PANTHEON_WORKSPACE_REF": "workspace-paper",
            "PANTHEON_AUTH_PROFILE_REF": "auth-profile-paper",
            "PANTHEON_PERSONA_ID": "persona-paper-ops",
            "PANTHEON_SESSION_ID": "session-paper-runtime",
            "PANTHEON_TRACE_ID": "trace-paper-runtime",
            "PANTHEON_REQUEST_ID": "request-paper-runtime",
            "PANTHEON_RUNTIME_BINDING_ID": "binding-001",
            "PANTHEON_CAPITAL_POOL_ID": "pool-001",
            "PANTHEON_ARTIFACT_ID": "artifact-001",
            "PANTHEON_ARTIFACT_VERSION": "1.2.3",
            "PANTHEON_DEPLOYMENT_STAGE": "paper",
            "PANTHEON_DEPLOYMENT_PLAN_ID": "plan-001",
            "PANTHEON_PERSONA_CAPITAL_BINDING_ID": "pcb-001",
            "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-token-inline",
        }

        identity = RuntimeIdentity.from_env(env)

        self.assertEqual(identity.runtime_id, "paper-runtime-001")
        self.assertTrue(identity.isolation_ready)
        self.assertTrue(identity.binding_context_complete)
        self.assertEqual(identity.authority_refs()["workspace_ref"], "workspace-paper")
        self.assertEqual(identity.telemetry_binding_context()["authority_refs"]["auth_profile_ref"], "auth-profile-paper")
        self.assertEqual(identity.runtime_manager_auth.source, "env")

    def test_token_file_precedence_is_exposed_in_health_payload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            token_path = Path(temp_dir) / "runtime-manager.token"
            token_path.write_text("runtime-token-from-file\n", encoding="utf-8")
            identity = RuntimeIdentity.from_env(
                {
                    "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-token-inline",
                    "PANTHEON_RUNTIME_MANAGER_TOKEN_FILE": str(token_path),
                }
            )

        self.assertEqual(identity.runtime_manager_auth.token, "runtime-token-from-file")
        self.assertEqual(identity.runtime_manager_auth.source, "token_file")
        self.assertFalse(identity.runtime_manager_auth.used_default)
        self.assertEqual(
            identity.to_health_payload()["runtime_manager_auth"]["source"],
            "token_file",
        )

    def test_dynamic_jwt_minting_and_health_redaction_across_clock_advances(self):
        secret = "runtime-identity-dynamic-jwt-secret-key-2026!"
        strict_env = {
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_SECRET": secret,
            "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-dev-issuer",
            "PANTHEON_RUNTIME_JWT_AUDIENCE": "pantheon-runtime-manager",
        }
        clock_now = [1_700_200_000.0]
        identity = RuntimeIdentity.from_env(
            {
                "PANTHEON_RUNTIME_ROLE": "pantheon-lean-paper-runtime",
                "PANTHEON_RUNTIME_MODE": "paper",
                "PANTHEON_PAPER_RUNTIME_ID": "paper-runtime-001",
                "PANTHEON_RUNTIME_MANAGER_URL": "http://runtime-manager:8081",
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_RUNTIME_JWT_SECRET": secret,
                "PANTHEON_RUNTIME_JWT_ISSUER": "pantheon-dev-issuer",
                "PANTHEON_RUNTIME_JWT_AUDIENCE": "pantheon-runtime-manager",
                "PANTHEON_RUNTIME_MANAGER_ROLES": "operator",
                "PANTHEON_RUNTIME_MANAGER_JWT_TTL_SECONDS": "30",
            }
        )
        self.assertEqual(identity.runtime_manager_auth.source, "jwt_mint")

        with mock.patch("time.time", side_effect=lambda: clock_now[0]):
            first_headers = identity.runtime_manager_headers()
            ctx0 = validate_request_auth(
                authorization=first_headers["Authorization"],
                required_roles=("operator",),
                env=strict_env,
                allow_shared_secret=True,
            )
            self.assertEqual(ctx0.actor_id, "paper-runtime-001")

            clock_now[0] = 1_700_200_045.0
            with self.assertRaises(AuthError) as expired:
                validate_request_auth(
                    authorization=first_headers["Authorization"],
                    required_roles=("operator",),
                    env=strict_env,
                    allow_shared_secret=True,
                )
            self.assertEqual(expired.exception.code, "AUTH_JWT_EXPIRED")

            second_headers = identity.runtime_manager_headers()
            ctx1 = validate_request_auth(
                authorization=second_headers["Authorization"],
                required_roles=("operator",),
                env=strict_env,
                allow_shared_secret=True,
            )
            self.assertEqual(ctx1.actor_id, "paper-runtime-001")

            health_dump = json.dumps(identity.to_health_payload())
            self.assertNotIn(secret, health_dump)
            self.assertNotIn(str(identity.runtime_manager_auth.token), health_dump)


if __name__ == "__main__":
    unittest.main()
