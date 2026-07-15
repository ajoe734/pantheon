from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping


SCRIPT = Path(__file__).with_name("verify_management_ai_repair.py")
SPEC = importlib.util.spec_from_file_location("verify_management_ai_repair", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verifier_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verifier_module
SPEC.loader.exec_module(verifier_module)


ArtifactRecorder = verifier_module.ArtifactRecorder
AuthSession = verifier_module.AuthSession
HttpResult = verifier_module.HttpResult
ManagementAiRepairVerifier = verifier_module.ManagementAiRepairVerifier
VerificationError = verifier_module.VerificationError
VerifierConfig = verifier_module.VerifierConfig
VerifierHooks = verifier_module.VerifierHooks
bridge_packet_digest = verifier_module.bridge_packet_digest
bridge_task_spec = verifier_module.bridge_task_spec
ascii_json_hash = verifier_module._ascii_json_hash


STRICT_VERSION = {
    "commit": "bff-strict-sha",
    "source_commit_sha": "bff-strict-sha",
    "image_digest": "sha256:image",
    "environment": "dev",
    "config_posture": {
        "auth_stub": False,
        "auth_mode": "strict",
        "dev_login_enabled": True,
        "mfa_required": True,
        "assistant_kernel_enabled": True,
    },
}


PERMISSIVE_VERSION = {
    "commit": "a10f752b3ea4420f271535e255f2d4e7d3d498b2",
    "source_commit_sha": "a10f752b3ea4420f271535e255f2d4e7d3d498b2",
    "image_digest": "unknown",
    "environment": "dev",
    "config_posture": {
        "auth_stub": True,
        "auth_mode": "permissive",
        "dev_login_enabled": False,
        "mfa_required": False,
        "assistant_kernel_enabled": True,
    },
}


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.calls: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> HttpResult:
        call = {
            "method": method,
            "url": url,
            "body": dict(body or {}),
            "headers": dict(headers or {}),
            "timeout": timeout,
        }
        self.calls.append(call)
        return self.handler(call)


class FakeCommandRunner:
    def __init__(self, stdout: str = "{}\n") -> None:
        self.stdout = stdout
        self.calls: list[tuple[list[str], Path | None, float]] = []

    def run(self, command, *, cwd=None, timeout=120.0):
        self.calls.append((list(command), cwd, timeout))
        return subprocess.CompletedProcess(command, 0, stdout=self.stdout, stderr="")


def preflight_config(output_dir: Path) -> VerifierConfig:
    return VerifierConfig(
        mode="preflight",
        bff_base_url="https://bff.example.test",
        frontend_deployment_url="https://fe.example.test/deployment.json",
        output_dir=output_dir,
        run_id="run-preflight-001",
        expected_bff_sha="bff-strict-sha",
        expected_frontend_sha="fe-strict-sha",
    )


def run_config(output_dir: Path) -> VerifierConfig:
    return VerifierConfig(
        mode="run",
        bff_base_url="https://bff.example.test",
        frontend_deployment_url="",
        output_dir=output_dir,
        run_id="run-repair-001",
        expected_bff_sha="bff-strict-sha",
        task_id="LOOP-PROD-MAI-001-PROBE",
        declared_scope=("tmp/loop-prod-mai/sentinel.txt",),
        expected_branch="task/LOOP-PROD-MAI-001-PROBE",
        shared_checkout_path="/shared/pantheon",
        allow_mutations=True,
        poll_attempts=0,
        bff_restart_command=("restart-bff",),
        adapter_restart_command=("restart-adapter",),
        supervisor_stop_command=("stop-supervisor",),
        supervisor_restart_command=("start-supervisor",),
    )


def auth_session() -> AuthSession:
    return AuthSession(
        token="ephemeral-token",
        operator_id="operator",
        roles=("operator",),
        capabilities=("assistant.kernel.debug", "assistant.kernel.repair"),
        tenant_id="tenant-dev",
        allowed_tenants=("tenant-dev",),
        mfa_verified=True,
    )


def bridge_packet() -> dict[str, Any]:
    return {
        "version": "pantheon.assistant.dev-task.v1",
        "packetId": "packet-loop-prod-mai-001",
        "intent": "generate_sa_sd_and_dispatch",
        "emittedAt": "2026-07-15T00:00:00Z",
        "actor": {
            "id": "operator",
            "roles": ["operator"],
            "capabilities": ["assistant.kernel.repair"],
        },
        "mode": "kernel_repair",
        "sourceConversationId": "repair-session-1",
        "sourceTurnIds": ["turn-user-1", "turn-assistant-1"],
        "documents": [
            {"path": "docs/sa.md", "kind": "SYSTEM_ANALYSIS", "sourceRefs": []},
            {"path": "docs/sd.md", "kind": "SYSTEM_DESIGN", "sourceRefs": []},
        ],
        "tasks": [
            {
                "id": "BRIDGE-PROBE-001",
                "title": "Bridge probe",
                "owner": "Codex",
                "reviewer": "Claude",
                "phase": "Hosted verification",
                "dependsOn": [],
                "artifacts": ["docs/sa.md", "docs/sd.md"],
                "acceptance": ["admission is durable"],
                "summary": "Bridge provenance probe",
            }
        ],
        "constraints": {
            "allowedRepos": ["pantheon"],
            "requiresBranchPrMerge": True,
            "noDirectShellFromWeb": True,
        },
        "auditConversationHref": "/bff/management/ai/conversations/repair-session-1",
        "signature": {
            "keyId": "assistant-bridge-dev",
            "algorithm": "HMAC-SHA256",
            "value": "signed-packet-value",
        },
    }


def operator_me(*, mfa_verified: bool = True, roles=None, capabilities=None):
    roles = roles or ["operator"]
    capabilities = capabilities or ["assistant.kernel.debug", "assistant.kernel.repair"]
    return {
        "data": {
            "operator_id": "loop-prod-mai-operator",
            "roles": roles,
            "capabilities": capabilities,
            "tenant_id": "tenant-dev",
            "allowed_tenants": ["tenant-dev"],
            "user": {
                "operator_id": "loop-prod-mai-operator",
                "roles": roles,
                "capabilities": capabilities,
                "mfa_verified": mfa_verified,
            },
            "session": {
                "authenticated": True,
                "mfa_verified": mfa_verified,
            },
            "environment": {"auth_mode": "strict", "strict_auth": True},
        }
    }


class VerifierPreflightTests(unittest.TestCase):
    def test_strict_preflight_authenticates_and_records_only_redacted_secrets(self) -> None:
        client_secret = "super-secret-client-value"
        access_token = "header.payload.signature-secret"

        def handler(call):
            url = call["url"]
            authorization = call["headers"].get("Authorization", "")
            if url.endswith("/bff/version"):
                return HttpResult(200, STRICT_VERSION)
            if url.endswith("/bff/assistant/mode") and (
                not authorization or "loop-prod-mai-fixed" in authorization
            ):
                return HttpResult(401, {"error": {"code": "AUTH_REQUIRED"}})
            if url.endswith("/bff/auth/dev-login"):
                self.assertEqual(call["body"]["client_secret"], client_secret)
                self.assertIn("Idempotency-Key", call["headers"])
                return HttpResult(200, {"access_token": access_token, "token_type": "bearer"})
            if url.endswith("/bff/me"):
                self.assertEqual(authorization, f"Bearer {access_token}")
                return HttpResult(200, operator_me())
            if url.endswith("/bff/assistant/mode"):
                return HttpResult(
                    200,
                    {
                        "data": {
                            "kernel_enabled": True,
                            "control_mode": {"configured": True, "active": False},
                        }
                    },
                )
            if "/bff/assistant/providers?auth_probe=true" in url:
                return HttpResult(
                    200,
                    {
                        "data": [
                            {"provider": "openclaw", "ready": True},
                            {
                                "provider": "codex_cli",
                                "ready": True,
                                "auth_status": "ready",
                            },
                        ]
                    },
                )
            if url.endswith("/bff/assistant/orchestrator/status"):
                return HttpResult(
                    200,
                    {
                        "data": {
                            "providerReadiness": {
                                "provider": "codex_cli",
                                "runtime": "openclaw_gateway_cli_mount",
                                "source": "openclaw_gateway_adapter",
                                "ready": True,
                                "status": "ready",
                                "repairWorkspace": {"ready": True},
                            }
                        }
                    },
                )
            if url == "https://fe.example.test/deployment.json":
                return HttpResult(
                    200,
                    {
                        "commit": "fe-strict-sha",
                        "bffCommit": "bff-strict-sha",
                        "buildMode": {
                            "VITE_BFF_MODE": "live",
                            "VITE_BFF_FALLBACK": "strict",
                            "VITE_BFF_REAL_WRITES": "false",
                            "VITE_BFF_ALLOW_DEV_STUB_WRITES": "false",
                            "VITE_BFF_EMBEDDED_BEARER_TOKEN": "false",
                            "VITE_BFF_BASE_URL": "https://bff.example.test",
                        },
                    },
                )
            self.fail(f"unexpected request: {call}")

        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "evidence"
            transport = FakeTransport(handler)
            verifier = ManagementAiRepairVerifier(
                preflight_config(output_dir),
                environ={
                    "DEV_BFF_OIDC_CLIENT_ID": "operator-client",
                    "DEV_BFF_OIDC_CLIENT_SECRET": client_secret,
                },
                transport=transport,
                hooks=VerifierHooks(sleep=lambda _seconds: None),
            )

            result = verifier.execute()

            self.assertEqual(result["status"], "pass")
            self.assertEqual(verifier._auth.operator_id, "loop-prod-mai-operator")
            self.assertTrue((output_dir / "evidence.json").is_file())
            ArtifactRecorder.verify_checksum(
                output_dir / "evidence.json",
                output_dir / "evidence.sha256",
            )
            captured = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output_dir.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(client_secret, captured)
            self.assertNotIn(access_token, captured)
            self.assertNotIn("loop-prod-mai-fixed:operator", captured)
            self.assertIn("<redacted>", captured)

    def test_current_permissive_posture_blocks_before_credentials_or_mutation(self) -> None:
        def handler(call):
            self.assertEqual(call["method"], "GET")
            self.assertTrue(call["url"].endswith("/bff/version"))
            return HttpResult(200, PERMISSIVE_VERSION)

        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp) / "blocked"
            transport = FakeTransport(handler)
            config = preflight_config(output_dir)
            config.expected_bff_sha = ""
            verifier = ManagementAiRepairVerifier(
                config,
                environ={},
                transport=transport,
                hooks=VerifierHooks(sleep=lambda _seconds: None),
            )

            with self.assertRaises(VerificationError) as raised:
                verifier.execute()

            self.assertEqual(raised.exception.code, "STRICT_AUTH_POSTURE_BLOCKED")
            self.assertEqual(len(transport.calls), 1)
            self.assertEqual(transport.calls[0]["method"], "GET")
            blocker_files = list(output_dir.glob("*-blocker.json"))
            self.assertEqual(len(blocker_files), 1)
            blocker = json.loads(blocker_files[0].read_text(encoding="utf-8"))
            self.assertEqual(blocker["status"], "blocked")
            self.assertEqual(blocker["phase"], "preflight-strict-posture")
            self.assertEqual(blocker["error"]["code"], "STRICT_AUTH_POSTURE_BLOCKED")
            ArtifactRecorder.verify_checksum(
                output_dir / "evidence.json",
                output_dir / "evidence.sha256",
            )


class RecorderAndIdempotencyTests(unittest.TestCase):
    def test_http_artifact_redacts_headers_body_response_and_known_secret_values(self) -> None:
        secret = "client-secret-that-must-not-leak"
        bearer = "actual-bearer-that-must-not-leak"

        def handler(_call):
            return HttpResult(
                200,
                {
                    "access_token": bearer,
                    "nested": {"passphrase": secret},
                    "answer": f"provider accidentally echoed {secret}",
                },
            )

        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            recorder = ArtifactRecorder(output_dir, secrets=[secret, bearer])
            recorder.http(
                FakeTransport(handler),
                "redaction",
                "POST",
                "https://bff.example.test/redaction",
                body={"client_secret": secret, "safe": "kept"},
                headers={"Authorization": f"Bearer {bearer}"},
                expected={200},
            )
            recorder.finalize()

            captured = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output_dir.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(secret, captured)
            self.assertNotIn(bearer, captured)
            self.assertIn("<redacted>", captured)
            self.assertIn("kept", captured)

    def test_idempotency_keys_are_stable_per_run_and_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = preflight_config(Path(temp))
            first = ManagementAiRepairVerifier(config, environ={})
            second = ManagementAiRepairVerifier(config, environ={})

            key = first._idempotency_key("repair-provider-ask")

            self.assertEqual(key, first._idempotency_key("repair-provider-ask"))
            self.assertEqual(key, second._idempotency_key("repair-provider-ask"))
            self.assertNotEqual(key, first._idempotency_key("dev-docs-generate"))
            first._auth = AuthSession(
                token="ephemeral-token",
                operator_id="operator",
                roles=("operator",),
                capabilities=("assistant.kernel.debug", "assistant.kernel.repair"),
                tenant_id="tenant-dev",
                allowed_tenants=("tenant-dev",),
                mfa_verified=True,
            )
            headers = first._auth_headers(idempotency_phase="repair-provider-ask")
            self.assertEqual(headers["Idempotency-Key"], key)

    def test_checksum_is_written_then_immediately_verifiable_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            recorder = ArtifactRecorder(output_dir)
            recorder.record("proof", {"status": "pass"})
            index, checksum = recorder.finalize()

            digest = ArtifactRecorder.verify_checksum(index, checksum)
            self.assertEqual(len(digest), 64)
            index.write_text(index.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaises(VerificationError) as raised:
                ArtifactRecorder.verify_checksum(index, checksum)
            self.assertEqual(raised.exception.code, "ARTIFACT_CHECKSUM_MISMATCH")

    def test_checksum_covers_every_child_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            recorder = ArtifactRecorder(output_dir)
            child = recorder.record("child-proof", {"value": "original"})
            index, checksum = recorder.finalize()

            child.write_text('{"value":"tampered"}\n', encoding="utf-8")
            with self.assertRaises(VerificationError) as raised:
                ArtifactRecorder.verify_checksum(index, checksum)

            self.assertEqual(raised.exception.code, "ARTIFACT_CHECKSUM_MISMATCH")

    def test_repair_receipt_is_hashed_and_redacted_while_bridge_receipt_remains_visible(self) -> None:
        repair_receipt = "signed-repair-capability.payload"
        bridge_marker = "processed-bridge-correlation-visible"
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            verifier = ManagementAiRepairVerifier(preflight_config(output_dir), environ={})

            digest = verifier._register_repair_receipt(
                {"receipt": repair_receipt}, label="unit"
            )
            verifier.recorder.record(
                "bridge-proof",
                {
                    "bridge_receipt": {
                        "packetId": "packet-1",
                        "status": "processed",
                        "correlation": bridge_marker,
                    },
                    "repair": {"receipt": repair_receipt},
                },
            )
            verifier.recorder.finalize()

            captured = "\n".join(
                path.read_text(encoding="utf-8")
                for path in output_dir.rglob("*")
                if path.is_file()
            )
            self.assertNotIn(repair_receipt, captured)
            self.assertIn(digest, captured)
            self.assertIn(bridge_marker, captured)

    def test_finalized_artifact_directory_is_append_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            recorder = ArtifactRecorder(output_dir)
            recorder.record("proof", {"status": "pass"})
            recorder.finalize()

            with self.assertRaises(VerificationError) as raised:
                ArtifactRecorder(output_dir)

            self.assertEqual(raised.exception.code, "ARTIFACT_RUN_ALREADY_FINALIZED")

    def test_post_bridge_shared_check_allows_expected_artifacts_but_not_head_or_sentinel(self) -> None:
        before = {"head": "sha-1", "status": [], "candidate_exists": False}
        ManagementAiRepairVerifier._assert_shared_head_and_candidate_unchanged(
            before,
            {"head": "sha-1", "status": ["?? ai-task-archive/tasks/x.json"], "candidate_exists": False},
            phase="post-deactivation",
        )
        with self.assertRaises(VerificationError):
            ManagementAiRepairVerifier._assert_shared_head_and_candidate_unchanged(
                before,
                {"head": "sha-2", "status": [], "candidate_exists": False},
                phase="post-deactivation",
            )
        with self.assertRaises(VerificationError):
            ManagementAiRepairVerifier._assert_shared_head_and_candidate_unchanged(
                before,
                {"head": "sha-1", "status": [], "candidate_exists": True},
                phase="post-deactivation",
            )


class StrictBoundaryTests(unittest.TestCase):
    def test_exact_deployment_sha_is_required_at_configuration_time(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(VerificationError) as raised:
                VerifierConfig(
                    mode="preflight",
                    bff_base_url="https://bff.example.test",
                    frontend_deployment_url="",
                    output_dir=Path(temp),
                    run_id="missing-sha",
                )

            self.assertEqual(raised.exception.code, "EXPECTED_BFF_SHA_REQUIRED")

    def test_strict_posture_requires_mfa_and_known_exact_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(preflight_config(Path(temp)), environ={})
            posture = json.loads(json.dumps(STRICT_VERSION))
            posture["config_posture"]["mfa_required"] = False

            with self.assertRaises(VerificationError) as raised:
                verifier._assert_strict_posture(posture)

            self.assertEqual(raised.exception.code, "STRICT_AUTH_POSTURE_BLOCKED")
            self.assertIn("mfa_required must be true", raised.exception.details["violations"])

    def test_provider_readiness_requires_openclaw_and_repair_capable_codex_delegate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(preflight_config(Path(temp)), environ={})

            with self.assertRaises(VerificationError) as raised:
                verifier._assert_provider_ready(
                    {"data": [{"provider": "codex_cli", "ready": True}]},
                    {
                        "data": {
                            "providerReadiness": {
                                "provider": "openclaw",
                                "ready": True,
                            }
                        }
                    },
                )

            self.assertEqual(raised.exception.code, "PROVIDER_NOT_READY")


class RuntimeBoundaryTests(unittest.TestCase):
    def test_control_activation_binds_exact_management_session(self) -> None:
        requested_session = "repair-session-exact"

        def handler(call):
            self.assertEqual(call["body"]["managementSessionId"], requested_session)
            return HttpResult(
                200,
                {
                    "data": {
                        "active": True,
                        "mode": "kernel_repair",
                        "managementSessionId": requested_session,
                    }
                },
            )

        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(
                run_config(Path(temp)),
                environ={"PANTHEON_ASSISTANT_CONTROL_PASSPHRASE": "passphrase"},
                transport=FakeTransport(handler),
            )
            verifier._auth = auth_session()

            verifier._activate(
                "kernel_repair",
                phase_key="activate-unit",
                management_session_id=requested_session,
            )

            self.assertTrue(verifier._activation_active)

    def test_lifecycle_hook_rejects_noop_and_accepts_governed_identity_change(self) -> None:
        good_report = {
            "version": "pantheon.management-ai.lifecycle-hook.v1",
            "service": "bff",
            "action": "restart",
            "authoritative": True,
            "before_instance_id": "bff-old",
            "after_instance_id": "bff-new",
            "stopped": True,
            "started": True,
            "ready": True,
        }
        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(
                preflight_config(Path(temp) / "good"),
                environ={},
                command_runner=FakeCommandRunner(json.dumps(good_report)),
            )
            report = verifier._run_lifecycle_hook(
                "restart-bff", ("restart",), {}, service="bff"
            )
            self.assertEqual(report["after_instance_id"], "bff-new")

            bad = dict(good_report, after_instance_id="bff-old")
            bad_verifier = ManagementAiRepairVerifier(
                preflight_config(Path(temp) / "bad"),
                environ={},
                command_runner=FakeCommandRunner(json.dumps(bad)),
            )
            with self.assertRaises(VerificationError) as raised:
                bad_verifier._run_lifecycle_hook(
                    "restart-bff", ("true",), {}, service="bff"
                )
            self.assertEqual(raised.exception.code, "LIFECYCLE_HOOK_EVIDENCE_INVALID")

    def test_adapter_reprepare_uses_fresh_bff_idempotency_key(self) -> None:
        receipts = iter(("initial.signature", "fresh.signature"))

        def handler(_call):
            return HttpResult(
                201,
                {
                    "data": {
                        "repair": {
                            "task_id": "LOOP-PROD-MAI-001-PROBE",
                            "repo_key": "pantheon",
                            "task_worktree": "/repair/worktree",
                            "declared_scope": ["tmp/loop-prod-mai/sentinel.txt"],
                            "expected_branch": "task/LOOP-PROD-MAI-001-PROBE",
                            "remote": "origin",
                            "merge_target": "dev",
                            "receipt": next(receipts),
                        },
                        "workflow": {"clean": True},
                    }
                },
            )

        lifecycle = {
            "version": "pantheon.management-ai.lifecycle-hook.v1",
            "service": "openclaw-adapter",
            "action": "restart",
            "authoritative": True,
            "before_instance_id": "adapter-old",
            "after_instance_id": "adapter-new",
            "stopped": True,
            "started": True,
            "ready": True,
        }
        with tempfile.TemporaryDirectory() as temp:
            transport = FakeTransport(handler)
            verifier = ManagementAiRepairVerifier(
                run_config(Path(temp)),
                environ={},
                transport=transport,
                command_runner=FakeCommandRunner(json.dumps(lifecycle)),
            )
            verifier._auth = auth_session()

            repair = verifier._repair_positive()

            keys = [call["headers"]["Idempotency-Key"] for call in transport.calls]
            self.assertEqual(len(keys), 2)
            self.assertNotEqual(keys[0], keys[1])
            self.assertEqual(repair["receipt"], "fresh.signature")

    def test_processing_repair_response_cannot_cross_bff_restart_boundary(self) -> None:
        def handler(_call):
            return HttpResult(
                202,
                {
                    "data": {
                        "status": "processing",
                        "session_id": "repair-session-1",
                        "message_id": "message-1",
                        "provider_status": {"status": "processing"},
                    }
                },
            )

        def sentinel(context):
            return {
                "authoritative": True,
                "source": "unit",
                "exists": False,
                "content": None,
                "dirty_paths": [],
                "branch": "task/LOOP-PROD-MAI-001-PROBE",
                "head": "worktree-sha",
                "repo_root": "/repair/worktree",
                "candidate": context["sentinel"],
            }

        with tempfile.TemporaryDirectory() as temp:
            runner = FakeCommandRunner()
            verifier = ManagementAiRepairVerifier(
                run_config(Path(temp)),
                environ={},
                transport=FakeTransport(handler),
                command_runner=runner,
                hooks=VerifierHooks(sentinel_readback=sentinel),
            )
            verifier._auth = auth_session()

            with self.assertRaises(VerificationError) as raised:
                verifier._repair_provider_and_restart(
                    {"task_worktree": "/repair/worktree"},
                    session_id="repair-session-1",
                )

            self.assertEqual(raised.exception.code, "BFF_RESTART_ADMISSION_NOT_DURABLE")
            self.assertEqual(runner.calls, [])

    def test_post_deactivation_denied_path_remains_absent_and_dirty_set_is_exact(self) -> None:
        sentinel_rel = "tmp/loop-prod-mai/sentinel.txt"
        content = "management-ai-repair-verifier\ntask_id=x\nrun_id=y\n"
        observed_sessions: list[str] = []

        def sentinel(context):
            denied = context["sentinel"].endswith(".post-deactivate-denied")
            return {
                "authoritative": True,
                "source": "unit",
                "exists": not denied,
                "content": None if denied else content,
                "dirty_paths": [sentinel_rel],
                "branch": "task/LOOP-PROD-MAI-001-PROBE",
                "head": "worktree-sha",
                "repo_root": "/repair/worktree",
                "candidate": context["sentinel"],
            }

        def handler(call):
            if call["url"].endswith("/control-mode/deactivate"):
                return HttpResult(200, {"data": {"active": False}})
            if call["url"].endswith("/bff/assistant/mode"):
                return HttpResult(200, {"data": {"control_mode": {"active": False}}})
            if call["url"].endswith("/bff/management/nl/ask"):
                observed_sessions.append(call["body"]["sessionId"])
            return HttpResult(403, {"error": {"code": "CONTROL_MODE_REQUIRED"}})

        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(
                run_config(Path(temp)),
                environ={},
                transport=FakeTransport(handler),
                hooks=VerifierHooks(sentinel_readback=sentinel),
            )
            verifier._auth = auth_session()
            verifier._activation_active = True

            verifier._post_deactivation_negative(
                repair={"task_worktree": "/repair/worktree", "receipt": "repair.token"},
                sentinel_rel=sentinel_rel,
                sentinel_content=content,
                session_id="repair-session-exact",
            )

            self.assertEqual(observed_sessions, ["repair-session-exact"])
            self.assertFalse(verifier._activation_active)

    def test_conversation_readback_rejects_more_than_exact_user_assistant_pair(self) -> None:
        def handler(call):
            if call["method"] == "GET":
                return HttpResult(
                    200,
                    {
                        "data": {
                            "session_id": "repair-session-1",
                            "turns": [
                                {"role": "user", "message_id": "message-1"},
                                {"role": "assistant", "message_id": "message-1"},
                                {"role": "assistant", "message_id": "message-1"},
                            ]
                        }
                    },
                )
            if str(call["body"].get("question") or "").startswith("conflicting replay"):
                return HttpResult(409, {"error": {"code": "IDEMPOTENCY_CONFLICT"}})
            return HttpResult(
                202,
                {"data": {"session_id": "repair-session-1", "message_id": "message-1"}},
            )

        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(
                run_config(Path(temp)), environ={}, transport=FakeTransport(handler)
            )
            verifier._auth = auth_session()
            completed = HttpResult(
                202,
                {
                    "data": {
                        "status": "completed",
                        "session_id": "repair-session-1",
                        "message_id": "message-1",
                    }
                },
            )

            with self.assertRaises(VerificationError) as raised:
                verifier._duplicate_and_conversation_proof(
                    completed,
                    repair={"task_worktree": "/repair/worktree", "receipt": "repair.token"},
                    sentinel_rel="tmp/loop-prod-mai/sentinel.txt",
                )

            self.assertEqual(raised.exception.code, "CONVERSATION_DUPLICATE_TURNS")

    def test_execute_finally_restores_supervisor_and_deactivates_without_masking_error(self) -> None:
        class FailingVerifier(ManagementAiRepairVerifier):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.cleanup_calls: list[str] = []

            def run(self):
                self._activation_active = True
                self._supervisor_stop_attempted = True
                self._supervisor_stop_report = {"before_instance_id": "supervisor-old"}
                raise VerificationError("PRIMARY_FAILURE", "primary verifier failure")

            def _run_lifecycle_hook(self, label, command, context, **kwargs):
                self.cleanup_calls.append(label)
                return {
                    "before_instance_id": "supervisor-old",
                    "after_instance_id": "supervisor-new",
                }

            def _deactivate(self, phase_key):
                self.cleanup_calls.append(phase_key)
                self._activation_active = False

        with tempfile.TemporaryDirectory() as temp:
            output_dir = Path(temp)
            verifier = FailingVerifier(run_config(output_dir), environ={})

            with self.assertRaises(VerificationError) as raised:
                verifier.execute()

            self.assertEqual(raised.exception.code, "PRIMARY_FAILURE")
            self.assertEqual(
                verifier.cleanup_calls,
                ["cleanup-start-supervisor", "cleanup-deactivate"],
            )
            ArtifactRecorder.verify_checksum(
                output_dir / "evidence.json", output_dir / "evidence.sha256"
            )


class BridgeAdmissionTests(unittest.TestCase):
    def _terminal_snapshot(self):
        packet = bridge_packet()
        digest = bridge_packet_digest(packet)
        task = packet["tasks"][0]
        spec = bridge_task_spec(task)
        admission = {
            "schema": "pantheon.assistant-dev-bridge-admission.v1",
            "record_kind": "assistant_dev_bridge_admission",
            "durable": True,
            "packet_version": packet["version"],
            "packet_id": packet["packetId"],
            "packet_digest": digest,
            "admitted_at": "2026-07-15T00:00:01Z",
            "admission_record_path": (
                "ai-task-archive/tasks/assistant-dev-bridge-admissions/"
                f"{packet['packetId']}--{digest[:16]}.json"
            ),
            "actor": packet["actor"],
            "mode": packet["mode"],
            "intent": packet["intent"],
            "emitted_at": packet["emittedAt"],
            "constraints": packet["constraints"],
            "conversation_id": packet["sourceConversationId"],
            "source_turn_ids": packet["sourceTurnIds"],
            "documents": packet["documents"],
            "audit_conversation_href": packet["auditConversationHref"],
            "tasks": [
                {
                    "task_id": task["id"],
                    "task_spec_hash": ascii_json_hash(spec),
                    "task_spec": spec,
                }
            ],
            "dispatch_records": [
                {
                    "taskId": task["id"],
                    "owner": task["owner"],
                    "reviewer": task["reviewer"],
                    "status": "dispatched",
                    "error": None,
                }
            ],
        }
        admission["record_payload_sha256"] = ascii_json_hash(admission)
        bridge_provenance = {
            "packet_id": packet["packetId"],
            "packet_digest": digest,
            "task_spec_hash": ascii_json_hash(spec),
            "task_spec": spec,
            "conversation_id": packet["sourceConversationId"],
            "source_turn_ids": packet["sourceTurnIds"],
            "documents": packet["documents"],
            "audit_conversation_href": packet["auditConversationHref"],
            "emitted_at": packet["emittedAt"],
            "intent": packet["intent"],
            "mode": packet["mode"],
            "actor": packet["actor"],
        }
        task_record = {
            "id": task["id"],
            "title": task["title"],
            "owner": task["owner"],
            "reviewer": task["reviewer"],
            "phase": task["phase"],
            "depends_on": spec["depends_on"],
            "artifacts": spec["artifacts"],
            "acceptance": spec["acceptance"],
            "summary_zh": spec["summary"],
            "status": "todo",
            "dev_bridge": bridge_provenance,
        }
        result = {
            "packetId": packet["packetId"],
            "dryRun": False,
            "errors": [],
            "auditRefs": {
                "packetDigest": digest,
                "taskIds": [task["id"]],
            },
            "taskRecords": admission["dispatch_records"],
            "admissionRecord": admission,
        }
        snapshot = {
            "packet_id": packet["packetId"],
            "authoritative": True,
            "source": "unit",
            "pending_exists": False,
            "processing_exists": False,
            "processed_exists": True,
            "receipt_exists": True,
            "pending": None,
            "processing": None,
            "processed": {"taskPacket": packet},
            "bridge_receipt": {
                "packetId": packet["packetId"],
                "status": "processed",
                "result": result,
            },
            "admission_record": admission,
            "active_task_records": {task["id"]: task_record},
        }
        return packet, digest, snapshot

    def test_terminal_bridge_requires_exact_admission_and_active_task_provenance(self) -> None:
        packet, digest, snapshot = self._terminal_snapshot()
        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(preflight_config(Path(temp)), environ={})

            verifier._validate_bridge_readback_schema(
                snapshot,
                packet_id=packet["packetId"],
                task_ids=[packet["tasks"][0]["id"]],
            )
            verifier._assert_bridge_terminal(
                snapshot,
                packet=packet,
                packet_digest=digest,
                tasks=packet["tasks"],
                polled_receipt={"packetId": packet["packetId"], "status": "processed"},
            )

            tampered = json.loads(json.dumps(snapshot))
            tampered_record = tampered["admission_record"]
            tampered_record["tasks"][0]["task_spec"]["title"] = "tampered"
            tampered["bridge_receipt"]["result"]["admissionRecord"] = tampered_record
            with self.assertRaises(VerificationError) as raised:
                verifier._assert_bridge_terminal(
                    tampered,
                    packet=packet,
                    packet_digest=digest,
                    tasks=packet["tasks"],
                    polled_receipt={
                        "packetId": packet["packetId"],
                        "status": "processed",
                    },
                )
            self.assertEqual(raised.exception.code, "BRIDGE_ADMISSION_RECORD_INVALID")

    def test_empty_bridge_readback_cannot_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            verifier = ManagementAiRepairVerifier(preflight_config(Path(temp)), environ={})
            with self.assertRaises(VerificationError) as raised:
                verifier._validate_bridge_readback_schema(
                    {}, packet_id="packet-1", task_ids=["task-1"]
                )
            self.assertEqual(raised.exception.code, "BRIDGE_READBACK_INVALID")

    def test_supervisor_recovery_sequence_proves_pending_before_start_and_terminal_after(self) -> None:
        packet, digest, terminal = self._terminal_snapshot()
        task_id = packet["tasks"][0]["id"]
        pending = {
            "packet_id": packet["packetId"],
            "authoritative": True,
            "source": "unit",
            "pending_exists": True,
            "processing_exists": False,
            "processed_exists": False,
            "receipt_exists": False,
            "pending": {"taskPacket": packet},
            "processing": None,
            "processed": None,
            "bridge_receipt": None,
            "admission_record": None,
            "active_task_records": {task_id: None},
        }

        class SequenceVerifier(ManagementAiRepairVerifier):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.events: list[str] = []
                self.readback_count = 0

            def _run_lifecycle_hook(
                self, label, command, context, *, service, action="restart", **kwargs
            ):
                self.events.append(action)
                if action == "stop":
                    return {
                        "before_instance_id": "supervisor-old",
                        "started": False,
                    }
                return {
                    "before_instance_id": "supervisor-old",
                    "after_instance_id": "supervisor-new",
                    "started": True,
                }

            def _http(self, label, method, path_or_url, **kwargs):
                if label == "dev-docs-generate":
                    self.events.append("queue")
                    return HttpResult(
                        201,
                        {
                            "data": {"packetId": "dev-doc-packet-1"},
                            "meta": {
                                "taskPacket": packet,
                                "taskPacketQueued": True,
                                "taskPacketQueueReceipt": {
                                    "packetId": packet["packetId"],
                                    "status": "queued",
                                    "queued": True,
                                },
                            },
                        },
                    )
                if label == "dev-docs-archive-readback":
                    self.events.append("archive")
                    return HttpResult(200, {"data": {"packetId": "dev-doc-packet-1"}})
                if label == "dev-bridge-exact-packet-replay":
                    self.events.append("duplicate")
                    return HttpResult(
                        201,
                        {
                            "meta": {
                                "taskPacketQueued": False,
                                "taskPacketQueueReceipt": {
                                    "packetId": packet["packetId"],
                                    "status": "duplicate",
                                    "queued": False,
                                },
                            }
                        },
                    )
                raise AssertionError(f"unexpected HTTP label {label}")

            def _bridge_readback(self, *, packet_id, packet_digest, task_ids):
                self.readback_count += 1
                if self.readback_count == 1:
                    self.events.append("pending-readback")
                    return pending
                self.events.append("terminal-readback")
                return terminal

            def _poll_bridge_receipt(self, packet_id):
                self.events.append("bounded-poll")
                return {"packetId": packet_id, "status": "processed"}

        with tempfile.TemporaryDirectory() as temp:
            verifier = SequenceVerifier(run_config(Path(temp)), environ={})
            verifier._auth = auth_session()

            bridge_id, task_ids = verifier._dev_docs_and_bridge(
                session_id=packet["sourceConversationId"],
                sentinel_rel="tmp/loop-prod-mai/sentinel.txt",
            )

            self.assertEqual(bridge_id, packet["packetId"])
            self.assertEqual(task_ids, [task_id])
            self.assertEqual(
                verifier.events,
                [
                    "stop",
                    "queue",
                    "archive",
                    "pending-readback",
                    "duplicate",
                    "start",
                    "bounded-poll",
                    "terminal-readback",
                ],
            )
            self.assertEqual(digest, bridge_packet_digest(packet))


class PollAndSequencingTests(unittest.TestCase):
    def test_async_provider_poll_hook_reaches_completed_without_transport_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            calls = []

            def poll_hook(context):
                calls.append(context)
                return {
                    "data": {
                        "status": "completed",
                        "message_id": "message-1",
                        "provider_status": {
                            "status": "completed",
                            "mode": "kernel_repair",
                            "sandbox": "workspace-write",
                            "workspace_class": "task_worktree",
                        },
                    }
                }

            verifier = ManagementAiRepairVerifier(
                preflight_config(Path(temp)),
                environ={},
                transport=FakeTransport(lambda call: self.fail(f"unexpected HTTP: {call}")),
                hooks=VerifierHooks(provider_poll=poll_hook, sleep=lambda _seconds: None),
            )
            verifier._auth = AuthSession(
                token="token",
                operator_id="operator",
                roles=("operator",),
                capabilities=("assistant.kernel.debug", "assistant.kernel.repair"),
                tenant_id="tenant-dev",
                allowed_tenants=("tenant-dev",),
                mfa_verified=True,
            )
            initial = HttpResult(
                202,
                {
                    "data": {
                        "status": "processing",
                        "message_id": "message-1",
                        "provider_status": {"status": "processing"},
                    }
                },
            )

            completed = verifier._poll_provider(
                label="async-provider",
                request_body={"question": "safe"},
                idempotency_phase="async-provider",
                initial=initial,
            )

            self.assertEqual(verifier._provider_state(completed.payload), "completed")
            self.assertEqual(len(calls), 1)
            self.assertEqual(
                calls[0]["idempotency_key"],
                verifier._idempotency_key("async-provider"),
            )

    def test_run_phase_sequence_keeps_preflight_before_every_mutating_phase(self) -> None:
        class SequencingVerifier(ManagementAiRepairVerifier):
            def preflight(self):
                self._enter("preflight-strict-posture")
                self._auth = AuthSession(
                    token="token",
                    operator_id="operator",
                    roles=("operator",),
                    capabilities=("assistant.kernel.debug", "assistant.kernel.repair"),
                    tenant_id="tenant-dev",
                    allowed_tenants=("tenant-dev",),
                    mfa_verified=True,
                )
                return self._auth

            def _shared_snapshot(self, label, *, candidate=""):
                return {"head": "sha", "status": [], "candidate_exists": False}

            def _validate_run_requirements(self):
                self._enter("run-requirements")

            def _security_negative_matrix(self):
                self._enter("security-negative-matrix")

            def _debug_phase(self):
                self._enter("kernel-debug")

            def _activate(self, mode, *, phase_key, management_session_id, auth=None):
                return {"active": True, "mode": mode}

            def _repair_negative_matrix(self):
                self._enter("repair-negative-matrix")

            def _repair_positive(self):
                self._enter("repair-prepare-positive")
                return {
                    "task_id": self.config.task_id,
                    "task_worktree": "/repair/task",
                    "declared_scope": list(self.config.declared_scope),
                    "expected_branch": self.config.expected_branch,
                    "remote": "origin",
                    "merge_target": "dev",
                    "repo_key": "pantheon",
                }

            def _repair_provider_and_restart(self, repair, *, session_id):
                self._enter("repair-provider-sentinel")
                return (
                    HttpResult(
                        202,
                        {
                            "data": {
                                "status": "completed",
                                "session_id": "session-1",
                                "message_id": "message-1",
                                "provider_status": {"status": "completed"},
                            }
                        },
                    ),
                    self.config.declared_scope[0],
                    "content",
                    self._auth,
                )

            def _duplicate_and_conversation_proof(self, completed, *, repair, sentinel_rel):
                self._enter("duplicate-and-conversation-readback")

            def _dev_docs_and_bridge(self, *, session_id, sentinel_rel):
                self._enter("dev-docs-generate-and-queue")
                return "packet-1", ["task-1"]

            def _post_deactivation_negative(
                self, *, repair, sentinel_rel, sentinel_content, session_id
            ):
                self._enter("deactivate-and-post-write-negative")

        with tempfile.TemporaryDirectory() as temp:
            config = VerifierConfig(
                mode="run",
                bff_base_url="https://bff.example.test",
                frontend_deployment_url="",
                output_dir=Path(temp),
                run_id="sequence-run",
                expected_bff_sha="bff-strict-sha",
                task_id="LOOP-PROD-MAI-001-PROBE",
                declared_scope=("tmp/loop-prod-mai/sentinel.txt",),
                expected_branch="task/LOOP-PROD-MAI-001-PROBE",
                shared_checkout_path="/shared",
                allow_mutations=True,
            )
            verifier = SequencingVerifier(
                config,
                environ={},
                transport=FakeTransport(lambda call: self.fail(f"unexpected HTTP: {call}")),
                command_runner=FakeCommandRunner(),
                hooks=VerifierHooks(sleep=lambda _seconds: None),
            )

            verifier.run()

            self.assertEqual(
                verifier.phase_history,
                [
                    "preflight-strict-posture",
                    "run-requirements",
                    "shared-checkout-baseline",
                    "security-negative-matrix",
                    "kernel-debug",
                    "activate-kernel-repair",
                    "repair-negative-matrix",
                    "repair-prepare-positive",
                    "repair-provider-sentinel",
                    "duplicate-and-conversation-readback",
                    "reactivate-repair-after-bff-restart",
                    "dev-docs-generate-and-queue",
                    "deactivate-and-post-write-negative",
                ],
            )
            first_mutation = verifier.phase_history.index("security-negative-matrix")
            self.assertLess(
                verifier.phase_history.index("preflight-strict-posture"),
                first_mutation,
            )


if __name__ == "__main__":
    unittest.main()
