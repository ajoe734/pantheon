"""Unit tests for dynamic runtime-manager bearer auth (PAPER-RUNTIME-MANAGER-DYNAMIC-BEARER-20261125)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services.runtime_auth import RuntimeManagerAuthConfig, resolve_runtime_manager_auth
from services.runtime_auth_inbound import (
    AuthError,
    encode_jwt_hs256,
    validate_request_auth,
)


class TestRuntimeManagerDynamicAuth(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "runtime-manager-dynamic-bearer-test-secret-key-2026"
        self.issuer = "https://auth.pantheon-test.internal"
        self.audience = "pantheon-runtime-manager"
        self.strict_inbound_env = {
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_SECRET": self.secret,
            "PANTHEON_RUNTIME_JWT_ISSUER": self.issuer,
            "PANTHEON_RUNTIME_JWT_AUDIENCE": self.audience,
        }

    def test_hs256_jwt_minting_passes_strict_inbound_validation_across_clock_advances_past_ttl(
        self,
    ) -> None:
        clock_now = [1_700_000_000.0]

        auth = resolve_runtime_manager_auth(
            env={
                "PANTHEON_RUNTIME_MANAGER_TOKEN": "runtime-control-internal",
                "PANTHEON_RUNTIME_JWT_SECRET": self.secret,
                "PANTHEON_RUNTIME_JWT_ISSUER": self.issuer,
                "PANTHEON_RUNTIME_JWT_AUDIENCE": self.audience,
                "PANTHEON_RUNTIME_MANAGER_ACTOR_ID": "paper-fleet-reconciler",
                "PANTHEON_RUNTIME_MANAGER_ROLES": "operator,reviewer",
                "PANTHEON_TENANT_ID": "tenant-dev",
                "PANTHEON_RUNTIME_MANAGER_JWT_TTL_SECONDS": "30",
            },
        )
        self.assertEqual(auth.source, "jwt_mint")
        self.assertTrue(auth.configured)
        self.assertFalse(auth.used_default)

        with mock.patch("time.time", side_effect=lambda: clock_now[0]):
            first_headers = auth.headers()
            ctx_0 = validate_request_auth(
                authorization=first_headers["Authorization"],
                required_roles=("operator",),
                env=self.strict_inbound_env,
                allow_shared_secret=True,
            )
            self.assertEqual(ctx_0.actor_id, "paper-fleet-reconciler")
            self.assertIn("operator", ctx_0.roles)
            self.assertIn("reviewer", ctx_0.roles)
            self.assertEqual(ctx_0.claims["tenant_id"], "tenant-dev")
            self.assertEqual(ctx_0.claims["allowed_tenants"], ["tenant-dev"])
            self.assertEqual(ctx_0.claims["exp"], 1_700_000_030)

            # Advance simulated clock past initial 30s TTL.
            clock_now[0] = 1_700_000_045.0
            with self.assertRaises(AuthError) as expired_ctx:
                validate_request_auth(
                    authorization=first_headers["Authorization"],
                    required_roles=("operator",),
                    env=self.strict_inbound_env,
                    allow_shared_secret=True,
                )
            self.assertEqual(expired_ctx.exception.code, "AUTH_JWT_EXPIRED")
            self.assertEqual(expired_ctx.exception.status_code, 401)

            # Subsequent call on the same auth config mints a fresh token valid at the new clock.
            second_headers = auth.headers()
            self.assertNotEqual(
                second_headers["Authorization"], first_headers["Authorization"]
            )
            ctx_1 = validate_request_auth(
                authorization=second_headers["Authorization"],
                required_roles=("operator",),
                env=self.strict_inbound_env,
                allow_shared_secret=True,
            )
            self.assertEqual(ctx_1.actor_id, "paper-fleet-reconciler")
            self.assertEqual(ctx_1.claims["exp"], 1_700_000_075)

            # Advance clock a second time well past second TTL.
            clock_now[0] = 1_700_000_200.0
            third_headers = auth.headers()
            ctx_2 = validate_request_auth(
                authorization=third_headers["Authorization"],
                required_roles=("operator",),
                env=self.strict_inbound_env,
                allow_shared_secret=True,
            )
            self.assertEqual(ctx_2.actor_id, "paper-fleet-reconciler")
            self.assertEqual(ctx_2.claims["exp"], 1_700_000_230)

    def test_token_file_rereads_on_mtime_and_expiry_across_clock_advances(self) -> None:
        clock_now = [1_700_100_000.0]

        def _mint_file_jwt(sub: str, issued_at: int, ttl: int = 30) -> str:
            return encode_jwt_hs256(
                {
                    "sub": sub,
                    "roles": ["operator"],
                    "tenant_id": "tenant-dev",
                    "allowed_tenants": ["tenant-dev"],
                    "iss": self.issuer,
                    "aud": self.audience,
                    "iat": issued_at,
                    "nbf": issued_at - 5,
                    "exp": issued_at + ttl,
                },
                secret=self.secret,
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            token_path = Path(tmp_dir) / "runtime-manager.token"
            tok_v1 = _mint_file_jwt("file-caller-v1", int(clock_now[0]), ttl=30)
            token_path.write_text(f"{tok_v1}\n", encoding="utf-8")

            with mock.patch("time.time", side_effect=lambda: clock_now[0]):
                auth = resolve_runtime_manager_auth(
                    env={"PANTHEON_RUNTIME_MANAGER_TOKEN_FILE": str(token_path)}
                )
                self.assertEqual(auth.source, "token_file")
                hdr_v1 = auth.headers()
                ctx_v1 = validate_request_auth(
                    authorization=hdr_v1["Authorization"],
                    required_roles=("operator",),
                    env=self.strict_inbound_env,
                    allow_shared_secret=True,
                )
                self.assertEqual(ctx_v1.actor_id, "file-caller-v1")

                # Rotate file on disk and advance clock past v1 TTL (mtime change + expiry).
                clock_now[0] = 1_700_100_040.0
                tok_v2 = _mint_file_jwt("file-caller-v2", int(clock_now[0]), ttl=30)
                token_path.write_text(f"{tok_v2}\n", encoding="utf-8")

                with self.assertRaises(AuthError) as expired_v1:
                    validate_request_auth(
                        authorization=hdr_v1["Authorization"],
                        required_roles=("operator",),
                        env=self.strict_inbound_env,
                        allow_shared_secret=True,
                    )
                self.assertEqual(expired_v1.exception.code, "AUTH_JWT_EXPIRED")

                hdr_v2 = auth.headers()
                ctx_v2 = validate_request_auth(
                    authorization=hdr_v2["Authorization"],
                    required_roles=("operator",),
                    env=self.strict_inbound_env,
                    allow_shared_secret=True,
                )
                self.assertEqual(ctx_v2.actor_id, "file-caller-v2")

                # Expiry-triggered re-read even when mtime is forced identical to cached mtime.
                st_before = token_path.stat()
                clock_now[0] = 1_700_100_085.0
                tok_v3 = _mint_file_jwt("file-caller-v3", int(clock_now[0]), ttl=30)
                token_path.write_text(f"{tok_v3}\n", encoding="utf-8")
                os.utime(
                    token_path,
                    ns=(st_before.st_atime_ns, st_before.st_mtime_ns),
                )

                hdr_v3 = auth.headers()
                ctx_v3 = validate_request_auth(
                    authorization=hdr_v3["Authorization"],
                    required_roles=("operator",),
                    env=self.strict_inbound_env,
                    allow_shared_secret=True,
                )
                self.assertEqual(ctx_v3.actor_id, "file-caller-v3")

    def test_explicit_static_token_and_permissive_fallbacks_preserved(self) -> None:
        # 1. Explicit token argument overrides env secret/file.
        arg_auth = resolve_runtime_manager_auth(
            token="local-test-actor:operator",
            env={"PANTHEON_RUNTIME_JWT_SECRET": self.secret},
        )
        self.assertEqual(arg_auth.source, "argument")
        self.assertEqual(
            arg_auth.headers(),
            {"Authorization": "Bearer local-test-actor:operator"},
        )
        ctx_arg = validate_request_auth(
            authorization=arg_auth.headers()["Authorization"],
            required_roles=("operator",),
            env={"PANTHEON_RUNTIME_AUTH_MODE": "permissive"},
        )
        self.assertEqual(ctx_arg.actor_id, "local-test-actor")

        # 2. Static env token when no JWT secret or token file is configured.
        env_auth = resolve_runtime_manager_auth(
            env={"PANTHEON_RUNTIME_MANAGER_TOKEN": "env-operator:operator"}
        )
        self.assertEqual(env_auth.source, "env")
        self.assertFalse(env_auth.used_default)
        ctx_env = validate_request_auth(
            authorization=env_auth.headers()["Authorization"],
            required_roles=("operator",),
            env={"PANTHEON_RUNTIME_AUTH_MODE": "permissive"},
        )
        self.assertEqual(ctx_env.actor_id, "env-operator")

        # 3. Default token fallback when env is empty.
        def_auth = resolve_runtime_manager_auth(env={})
        self.assertEqual(def_auth.source, "default")
        self.assertTrue(def_auth.used_default)
        self.assertEqual(
            def_auth.headers(),
            {"Authorization": "Bearer runtime-control-internal"},
        )
        ctx_def = validate_request_auth(
            authorization=def_auth.headers()["Authorization"],
            required_roles=("operator",),
            env={"PANTHEON_RUNTIME_AUTH_MODE": "permissive"},
        )
        self.assertEqual(ctx_def.actor_id, "runtime-control-internal")

    def test_status_dict_and_repr_never_expose_secret_or_bearer_token(self) -> None:
        auth = resolve_runtime_manager_auth(
            env={
                "PANTHEON_RUNTIME_JWT_SECRET": self.secret,
                "PANTHEON_RUNTIME_MANAGER_ACTOR_ID": "paper-signal-producer",
                "PANTHEON_RUNTIME_MANAGER_ROLES": "operator",
            }
        )
        minted_token = str(auth.token)
        status_json = json.dumps(auth.status_dict())
        repr_text = repr(auth)

        self.assertNotIn(self.secret, status_json)
        self.assertNotIn(minted_token, status_json)
        self.assertNotIn(self.secret, repr_text)
        self.assertNotIn(minted_token, repr_text)


if __name__ == "__main__":
    unittest.main()
