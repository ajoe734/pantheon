from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import permission_broker
import provider_permissions
from provider_permissions import ROOT, _verified_claude_hooks


class ProviderPermissionsTest(unittest.TestCase):
    def test_verified_claude_hooks_use_absolute_broker_path(self) -> None:
        expected = str(Path(ROOT) / ".orchestrator" / "permission_broker.py")
        hooks = _verified_claude_hooks()
        for entries in hooks.values():
            command = entries[0]["hooks"][0]["command"]
            self.assertIn(expected, command)
            self.assertTrue(command.startswith("python3 /"))

    def test_toolsearch_is_auto_allowed(self) -> None:
        evaluation = permission_broker.evaluate_tool_request("ToolSearch", {}, {})

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_read")

    def test_task_output_is_auto_allowed(self) -> None:
        # Regression for OPS-APPROVAL-BROKER-RISK-CLASS-001: TaskOutput
        # (polling a background sub-task's output) previously fell through
        # to risk_class=unknown and sat pending indefinitely, suspending
        # claude worker slots for hours.
        evaluation = permission_broker.evaluate_tool_request(
            "TaskOutput", {"task_id": "bg-1", "block": True, "timeout": 30000}, {}
        )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "harness_orchestration_read")

    def test_harness_orchestration_read_tools_are_auto_allowed(self) -> None:
        for tool_name, tool_input in (
            ("TaskGet", {"taskId": "1"}),
            ("TaskList", {}),
            ("Monitor", {"description": "watch", "timeout_ms": 1000, "persistent": False}),
            ("CronList", {}),
        ):
            with self.subTest(tool_name=tool_name):
                evaluation = permission_broker.evaluate_tool_request(tool_name, tool_input, {})

                self.assertEqual(evaluation["decision"], "allow")
                self.assertEqual(evaluation["risk_class"], "harness_orchestration_read")

    def test_mutating_orchestration_tools_still_require_review(self) -> None:
        # Harness orchestration tools with real side effects (creating,
        # updating, or stopping a task) are intentionally NOT auto-allowed;
        # only read-only polling tools are.
        for tool_name in ("TaskCreate", "TaskUpdate", "TaskStop"):
            with self.subTest(tool_name=tool_name):
                evaluation = permission_broker.evaluate_tool_request(tool_name, {}, {})

                self.assertEqual(evaluation["decision"], "defer")
                self.assertEqual(evaluation["risk_class"], "unknown")

    def test_read_only_agent_explore_request_is_auto_allowed(self) -> None:
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Verify KW-04/KW-05/CW-02 routes live",
                "prompt": "Explore the repo, grep route declarations, and report file paths plus line numbers.",
                "subagent_type": "Explore",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_read")

    def test_read_only_agent_explore_request_allows_execute_plans_repo_path(self) -> None:
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Explore execute-plans repo BFF structure",
                "prompt": (
                    "Explore the repository at /home/lupin/code/execute-plans and give me the "
                    "directory tree under src/lib/bff/, existing BFF files, package.json, and "
                    "TypeScript config files. List findings only."
                ),
                "subagent_type": "Explore",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_read")

    def test_read_only_agent_explore_request_allows_safe_git_inspection(self) -> None:
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Deep check task board and push status",
                "prompt": (
                    "Audit the task board. Run `git status` and `git log --oneline -20`, "
                    "then report the current branch state."
                ),
                "subagent_type": "Explore",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_read")

    def test_read_only_agent_code_review_subagent_type_is_auto_allowed(self) -> None:
        # Regression for OPS-APPROVAL-BROKER-RISK-CLASS-001: a spawned
        # code-review subagent request (subagent_type="code-review") was
        # denied/deferred as risk_class=unknown because the exact-match
        # SAFE_AGENT_SUBAGENT_TYPES set only contained "review", not the
        # hyphenated "code-review" variant actually used to spawn review
        # subagents.
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Independent code review of the current diff",
                "prompt": "Review the pending diff for correctness bugs and report findings.",
                "subagent_type": "code-review",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_read")

    def test_incident_general_purpose_read_only_agent_request_is_auto_allowed(self) -> None:
        # Direct shape of apr-20260717T190756Z-4b4e5586: the actual
        # incident used subagent_type="general-purpose" and read-only review
        # wording with negated unsafe phrases ("Do not fix", "do not edit").
        # Those negations must not make the request look mutating.
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Review activity reader hardening code",
                "prompt": (
                    "You are doing an independent correctness/security review of a merged "
                    "change in the pantheon repo, already checked out at the relevant "
                    "commit on top of a merge of origin/dev. Files to actually read in "
                    "full and reason about: .orchestrator/common.py, .orchestrator/test_common.py, "
                    "and scripts/activity_audit_logical_inventory.py. Your job: find REAL "
                    "correctness/security bugs or gaps between what's claimed and what's "
                    "actually implemented - not style nits. Report back with confirmed "
                    "findings and things you checked. Do not fix anything - this is "
                    "read-only review, do not edit files."
                ),
                "subagent_type": "general-purpose",
                "run_in_background": False,
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_read")

    def test_mutating_agent_request_still_requires_review(self) -> None:
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Implement missing routes",
                "prompt": "Explore the repo and edit the BFF to add the missing endpoints, then update tests.",
                "subagent_type": "Explore",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "defer")
        self.assertEqual(evaluation["risk_class"], "unknown")

    def test_mutating_agent_request_with_negated_edit_still_requires_review(self) -> None:
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Review and repair tests",
                "prompt": (
                    "Do not edit files during the first pass. Then update the regression "
                    "tests, commit the fix, and report the result."
                ),
                "subagent_type": "general-purpose",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "defer")
        self.assertEqual(evaluation["risk_class"], "unknown")

    def test_edit_allows_configured_execute_plans_workspace_root(self) -> None:
        with mock.patch("permission_broker.ROOT", Path("/home/lupin/code/pantheon")):
            evaluation = permission_broker.evaluate_tool_request(
                "Edit",
                {"file_path": "/home/lupin/code/execute-plans/src/lib/bff/client.ts"},
                {
                    "permission_broker": {
                        "allowed_workspace_roots": ["../execute-plans"],
                    }
                },
            )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "repo_write")

    def test_edit_outside_configured_workspace_roots_is_denied(self) -> None:
        with mock.patch("permission_broker.ROOT", Path("/home/lupin/code/pantheon")):
            evaluation = permission_broker.evaluate_tool_request(
                "Edit",
                {"file_path": "/tmp/outside.ts"},
                {
                    "permission_broker": {
                        "allowed_workspace_roots": ["../execute-plans"],
                    }
                },
            )

        self.assertEqual(evaluation["decision"], "deny")
        self.assertEqual(evaluation["risk_class"], "out_of_workspace")

    def test_agent_execute_command_request_still_requires_review(self) -> None:
        evaluation = permission_broker.evaluate_tool_request(
            "Agent",
            {
                "description": "Explore repo and execute command checks",
                "prompt": "Run shell probes and execute commands to inspect package scripts.",
                "subagent_type": "Explore",
            },
            {},
        )

        self.assertEqual(evaluation["decision"], "defer")
        self.assertEqual(evaluation["risk_class"], "unknown")

    def test_workspace_mkdir_is_auto_allowed(self) -> None:
        command = f"mkdir -p {ROOT / 'tmp' / 'worker-artifacts'}"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_module_unittest_is_auto_allowed(self) -> None:
        command = "python3 -m unittest services.execution.test_artifact_loader 2>&1"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_module_pytest_is_auto_allowed(self) -> None:
        command = (
            "python3 -m pytest services/control-plane/governance/test_capital_pool.py "
            "services/control-plane/governance/test_persona_capital_binding.py -v 2>&1 | head -80"
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_apt_get_python3_pytest_install_is_auto_allowed(self) -> None:
        command = "apt-get install -y python3-pytest 2>&1 | tail -5"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_python_module_pip_pytest_install_and_verify_is_auto_allowed(self) -> None:
        command = "python3 -m pip install pytest --user --quiet 2>&1 | tail -5 && python3 -m pytest --version"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_python_module_test_dependency_install_and_verify_is_auto_allowed(self) -> None:
        command = (
            "pip install pytest fastapi httpx pydantic --quiet 2>&1 | tail -5 && "
            "python3 -m pytest services/governance/test_governance_api.py -v 2>&1"
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_semicolon_split_test_dependency_install_and_verify_is_auto_allowed(self) -> None:
        command = (
            "python3 -m pip install -q fastapi pydantic httpx pytest 2>/dev/null; "
            "python3 -m pytest test_governance_api.py -v 2>&1 | tail -60"
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_pip3_anyio_test_dependency_install_is_auto_allowed(self) -> None:
        command = "pip3 install -q fastapi pydantic httpx pytest anyio 2>&1 | tail -5"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_pip3_pytest_install_is_auto_allowed(self) -> None:
        command = "pip3 install pytest -q 2>&1 | tail -3"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_repo_git_add_directory_is_auto_allowed(self) -> None:
        command = "git add services/governance/"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_repo_git_add_then_status_is_auto_allowed(self) -> None:
        command = (
            "git add services/control-plane/bff/read_store.py "
            "services/control-plane/bff/main.py "
            "services/control-plane/bff/test_consultation_surfaces.py && "
            "git status --short"
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_git_add_dot_still_requires_review(self) -> None:
        command = "git add ."

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_repo_git_commit_with_heredoc_message_is_auto_allowed(self) -> None:
        command = """git commit -m "$(cat <<'EOF'
BP5-SVC-014: realize consultation read surfaces CS-01 to CS-06

Adds the missing consultation BFF surfaces.
EOF
)\""""

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_repo_git_add_then_heredoc_commit_with_stderr_merge_is_auto_allowed(self) -> None:
        command = """git add docs/operations/postgres-cutoff-wave3-runbook.md && git commit -m "$(cat <<'EOF'
SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3: owner closeout finalization

Add closeout verification section to runbook.

LLM-Agent: Claude
Task-ID: SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3
Reviewer: Codex
EOF
)\" 2>&1"""

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_repo_git_push_without_force_is_auto_allowed(self) -> None:
        command = "git push origin feature/bp5-svc-014"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_git_submodule_status_is_auto_allowed(self) -> None:
        command = "git submodule status lean 2>/dev/null"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_docker_read_checks_are_auto_allowed(self) -> None:
        command = "docker ps 2>/dev/null | head -5; docker images 2>/dev/null | head -10"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_docker_exec_python_import_probe_is_auto_allowed(self) -> None:
        command = (
            "docker exec pantheon-control-plane-router-1 "
            "python3 -c \"import pytest, fastapi, pydantic, httpx; print('all ok')\" 2>&1"
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_docker_compose_config_is_auto_allowed(self) -> None:
        command = "docker compose -f docker-compose.control.yml config --quiet 2>&1"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_docker_compose_config_with_env_file_is_auto_allowed(self) -> None:
        command = (
            "docker compose --env-file env/prod-control.env.example "
            "-f docker-compose.control.yml config --quiet 2>&1"
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_docker_compose_config_with_echo_ok_is_auto_allowed(self) -> None:
        command = 'docker compose -f docker-compose.control.yml config --quiet 2>&1 && echo "OK"'

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_docker_compose_up_still_requires_review(self) -> None:
        command = "docker compose -f docker-compose.control.yml up -d"

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_docker_compose_up_with_echo_ok_still_requires_review(self) -> None:
        command = 'docker compose -f docker-compose.control.yml up -d && echo "OK"'

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_docker_compose_config_rejects_option_shaped_file_value(self) -> None:
        command = "docker compose -f --env-file config --quiet"

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_mixed_safe_and_mutating_docker_chain_still_requires_review(self) -> None:
        command = "docker ps 2>/dev/null | head -5; docker rm -f pantheon-control-plane-router-1"

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_docker_exec_python_write_probe_still_requires_review(self) -> None:
        command = (
            "docker exec pantheon-control-plane-router-1 "
            "python3 -c \"import pathlib; pathlib.Path('/tmp/x').write_text('bad')\" 2>&1"
        )

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_package_inventory_probe_is_auto_allowed(self) -> None:
        command = (
            'apt list --installed 2>/dev/null | grep -i pip; '
            'find /usr/local/bin /usr/bin -name "pip*" 2>/dev/null; '
            'find /usr/local/lib /usr/lib -name "pip" -type d 2>/dev/null | head -5'
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_other_apt_get_install_still_requires_review(self) -> None:
        command = "apt-get install -y ripgrep 2>&1 | tail -5"

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_other_pip_install_still_requires_review(self) -> None:
        command = "python3 -m pip install requests --user --quiet 2>&1 | tail -5"

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_non_whitelisted_test_dependency_install_still_requires_review(self) -> None:
        command = "pip install pytest requests --quiet 2>&1 | tail -5 && python3 -m pytest --version"

        self.assertEqual(permission_broker.classify_command(command), "defer")

    def test_npm_test_is_auto_allowed(self) -> None:
        command = "npm test -- --runInBand"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_cargo_test_is_auto_allowed(self) -> None:
        command = "cargo test --lib -- --nocapture"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_go_test_is_auto_allowed(self) -> None:
        command = "go test ./... -run TestApprovalBroker"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_named_smoke_test_is_auto_allowed(self) -> None:
        command = "python3 services/execution/smoke_test_artifact_loader.py 2>&1"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_status_sync_with_quoted_env_value_is_auto_allowed(self) -> None:
        command = (
            'AI_NAME=Claude REVIEW_NOTES_ZH="審查通過：全部測試通過。" '
            'python3 scripts/ai_status.py approve EX-001 "Review approved by Claude."'
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_status_sync_with_absolute_workspace_path_is_auto_allowed(self) -> None:
        command = (
            f'AI_NAME=Claude python3 {ROOT / "scripts" / "ai_status.py"} '
            'progress EV-002 "Resubmitting for review." 2>&1'
        )

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_status_sync_help_via_cd_is_auto_allowed(self) -> None:
        command = f"cd {ROOT} && python3 scripts/ai_status.py --help 2>&1 | head -40"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_status_sync_shell_wrapper_via_cd_is_auto_allowed(self) -> None:
        command = f"cd {ROOT} && bash scripts/ai-status.sh sync"

        self.assertEqual(permission_broker.classify_command(command), "allow")

    def test_auto_worker_stale_status_runtime_is_denied(self) -> None:
        command = (
            "cd /home/lupin/code/pantheon && AI_NAME=Claude timeout 30 "
            "python3 scripts/ai_status.py note OPS-1 'review result' 2>&1"
        )
        env = {
            "ORCH_RUN_ID": "claude-2-run",
            "ORCH_TASK_ID": "OPS-1",
            "PANTHEON_COMMAND_ROOT": "/home/lupin/pantheon-ci-deploy/dev-root",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            evaluation = permission_broker.evaluate_tool_request(
                "Bash", {"command": command}, {}
            )

        self.assertEqual(evaluation["decision"], "deny")
        self.assertEqual(
            evaluation["risk_class"], "stale_status_command_runtime"
        )
        self.assertIn("PANTHEON_COMMAND_ROOT", evaluation["reason"])

    def test_auto_worker_pinned_status_runtime_is_allowed(self) -> None:
        command = (
            "AI_NAME=Claude timeout 30 python3 "
            "$PANTHEON_COMMAND_ROOT/scripts/ai_status.py note OPS-1 "
            "'review result' 2>&1"
        )
        env = {
            "ORCH_RUN_ID": "claude-2-run",
            "ORCH_TASK_ID": "OPS-1",
            "PANTHEON_COMMAND_ROOT": "/home/lupin/pantheon-ci-deploy/dev-root",
        }
        with mock.patch.dict(os.environ, env, clear=True):
            evaluation = permission_broker.evaluate_tool_request(
                "Bash", {"command": command}, {}
            )

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_bash")

    def test_non_worker_status_command_remains_allowed(self) -> None:
        command = "python3 scripts/ai_status.py note OPS-1 'local operator note'"
        with mock.patch.dict(os.environ, {}, clear=True):
            evaluation = permission_broker.evaluate_tool_request(
                "Bash", {"command": command}, {}
            )

        self.assertEqual(evaluation["decision"], "allow")

    def test_permission_broker_uses_provider_specific_rule_default_mode(self) -> None:
        config = {
            "providers": {
                "claude2": {
                    "delivery_mode": "claude_cli",
                    "approval": {"rule_default_mode": "auto"},
                }
            }
        }

        with mock.patch.dict(os.environ, {"ORCH_PROVIDER": "claude2"}, clear=False):
            evaluation = permission_broker.evaluate_tool_request("Read", {}, config)

        self.assertEqual(evaluation["policy_default_mode"], "auto")

    def test_provider_capabilities_include_custom_claude_cli_provider(self) -> None:
        config = {
            "paths": {
                "status_file": ".orchestrator/ai-status.json",
                "activity_log": "ai-activity-log.jsonl",
                "current_work": "current-work.md",
                "dashboard": "dashboard-bundle.json",
                "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json",
            },
            "agents": {},
            "providers": {
                "claude": {
                    "delivery_mode": "claude_cli",
                    "runtime": {"cli": "claude"},
                },
                "claude2": {
                    "delivery_mode": "claude_cli",
                    "runtime": {"cli": "claude", "home": "~/.claude2"},
                },
                "gemini": {},
                "codex": {},
                "copilot": {},
            },
        }

        def fake_find_extension(prefix: str) -> tuple[Path | None, str | None]:
            if prefix == "anthropic.claude-code":
                return Path("/tmp/anthropic.claude-code-2.1.118"), "2.1.118"
            return None, None

        def fake_claude_auth_ready(binary: str | None, *, env: dict[str, str] | None = None, refresh_if_needed: bool = True) -> bool:
            home = str((env or {}).get("HOME") or "")
            return bool(binary) and home.endswith(".claude2")

        with (
            mock.patch.object(provider_permissions, "_code_cli_info", return_value={}),
            mock.patch.object(provider_permissions, "_workspace_settings", return_value={}),
            mock.patch.object(provider_permissions, "_find_extension", side_effect=fake_find_extension),
            mock.patch.object(provider_permissions, "_claude_local_settings", return_value={"permissions": {"defaultMode": "acceptEdits"}}),
            mock.patch.object(provider_permissions, "_gemini_settings", return_value={}),
            mock.patch.object(provider_permissions, "_custom_agents_info", return_value={}),
            mock.patch.object(provider_permissions, "_relevant_extensions", return_value=[]),
            mock.patch.object(
                provider_permissions,
                "desired_workspace_settings",
                return_value={
                    "claudeCode.initialPermissionMode": "acceptEdits",
                    "claudeCode.allowDangerouslySkipPermissions": False,
                    "geminicodeassist.agentYoloMode": False,
                    "github.copilot.chat.backgroundAgent.enabled": False,
                    "github.copilot.chat.cloudAgent.enabled": False,
                    "github.copilot.chat.claudeAgent.enabled": False,
                },
            ),
            mock.patch.object(
                provider_permissions,
                "desired_claude_local_settings",
                return_value={"permissions": {"defaultMode": "acceptEdits"}},
            ),
            mock.patch.object(
                provider_permissions,
                "desired_gemini_settings",
                return_value={
                    "general": {"defaultApprovalMode": "auto_edit"},
                    "security": {
                        "enablePermanentToolApproval": True,
                        "autoAddToPolicyByDefault": True,
                        "disableYoloMode": False,
                    },
                },
            ),
            mock.patch.object(
                provider_permissions,
                "command_exists",
                side_effect=lambda cmd: "/usr/bin/claude" if cmd == "claude" else None,
            ),
            mock.patch.object(provider_permissions, "claude_auth_ready", side_effect=fake_claude_auth_ready),
        ):
            report = provider_permissions.provider_capabilities(config)

        self.assertIn("claude2", report["providers"])
        self.assertNotIn("qwen", report["providers"])
        self.assertTrue(report["providers"]["claude2"]["auth_ready"])
        self.assertTrue(report["providers"]["claude2"]["supports_auto_approve"])
        self.assertEqual(report["providers"]["claude2"]["paths"]["home"], os.path.expanduser("~/.claude2"))

    def test_provider_capabilities_include_custom_gemini_provider(self) -> None:
        config = {
            "paths": {
                "status_file": ".orchestrator/ai-status.json",
                "activity_log": "ai-activity-log.jsonl",
                "current_work": "current-work.md",
                "dashboard": "dashboard-bundle.json",
                "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json",
            },
            "agents": {},
            "providers": {
                "gemini": {
                    "delivery_mode": "gemini",
                    "gemini": {"cli": "gemini"},
                },
                "gemini2": {
                    "delivery_mode": "gemini",
                    "gemini": {
                        "cli": "gemini",
                        "config_home": "~/.gemini2",
                        "model": "gemini-2.5-flash-lite",
                        "env": {"GOOGLE_CLOUD_PROJECT": "gemini2-project"},
                    },
                },
                "claude": {},
                "codex": {},
                "copilot": {},
            },
        }

        def fake_find_extension(prefix: str) -> tuple[Path | None, str | None]:
            if prefix == "google.geminicodeassist":
                return Path("/tmp/google.geminicodeassist-2.79.0"), "2.79.0"
            return None, None

        with (
            mock.patch.object(provider_permissions, "_code_cli_info", return_value={}),
            mock.patch.object(provider_permissions, "_workspace_settings", return_value={"geminicodeassist.agentYoloMode": False}),
            mock.patch.object(provider_permissions, "_find_extension", side_effect=fake_find_extension),
            mock.patch.object(provider_permissions, "_claude_local_settings", return_value={"permissions": {}}),
            mock.patch.object(
                provider_permissions,
                "_gemini_settings",
                return_value={
                    "general": {"defaultApprovalMode": "auto_edit"},
                    "security": {
                        "enablePermanentToolApproval": True,
                        "autoAddToPolicyByDefault": True,
                        "disableYoloMode": False,
                        "auth": {"selectedType": "oauth-personal"},
                    },
                },
            ),
            mock.patch.object(provider_permissions, "_gemini_auth_ready", return_value=True),
            mock.patch.object(provider_permissions, "_gemini_selected_auth_type", return_value="oauth-personal"),
            mock.patch.object(provider_permissions, "_custom_agents_info", return_value={}),
            mock.patch.object(provider_permissions, "_relevant_extensions", return_value=[]),
            mock.patch.object(
                provider_permissions,
                "desired_workspace_settings",
                return_value={
                    "claudeCode.initialPermissionMode": "acceptEdits",
                    "claudeCode.allowDangerouslySkipPermissions": False,
                    "geminicodeassist.agentYoloMode": False,
                    "github.copilot.chat.backgroundAgent.enabled": False,
                    "github.copilot.chat.cloudAgent.enabled": False,
                    "github.copilot.chat.claudeAgent.enabled": False,
                },
            ),
            mock.patch.object(provider_permissions, "desired_claude_local_settings", return_value={"permissions": {"defaultMode": "acceptEdits"}}),
            mock.patch.object(
                provider_permissions,
                "desired_gemini_settings",
                return_value={
                    "general": {"defaultApprovalMode": "auto_edit"},
                    "security": {
                        "enablePermanentToolApproval": True,
                        "autoAddToPolicyByDefault": True,
                        "disableYoloMode": False,
                        "auth": {"selectedType": "oauth-personal"},
                    },
                },
            ),
            mock.patch.object(provider_permissions, "command_exists", side_effect=lambda cmd: "/usr/bin/gemini" if cmd == "gemini" else None),
            mock.patch.object(provider_permissions, "claude_auth_ready", return_value=False),
        ):
            report = provider_permissions.provider_capabilities(config)

        self.assertIn("gemini2", report["providers"])
        self.assertNotIn("qwen", report["providers"])
        self.assertTrue(report["providers"]["gemini2"]["auth_ready"])
        self.assertTrue(report["providers"]["gemini2"]["supports_auto_approve"])
        self.assertEqual(report["providers"]["gemini2"]["paths"]["binary"], "/usr/bin/gemini")
        self.assertEqual(report["providers"]["gemini2"]["paths"]["home"], os.path.expanduser("~/.gemini2"))
        self.assertEqual(report["providers"]["gemini2"]["selected_model"], "gemini-2.5-flash-lite")
        self.assertEqual(report["providers"]["gemini2"]["settings"]["gemini.model"], "gemini-2.5-flash-lite")
        self.assertEqual(report["providers"]["gemini2"]["settings"]["env.GOOGLE_CLOUD_PROJECT"], "gemini2-project")

    def test_claude_auth_probe_refreshes_oauth_when_needed(self) -> None:
        with mock.patch.object(provider_permissions, "claude_auth_ready", return_value=True) as claude_auth_ready:
            probe = provider_permissions._claude_auth_probe({}, "claude2", "/usr/bin/claude", {"HOME": "/tmp/claude2"})

        self.assertTrue(probe["ready"])
        self.assertEqual(probe["method"], "claude_auth_status_refresh")
        claude_auth_ready.assert_called_once()
        self.assertTrue(claude_auth_ready.call_args.kwargs["refresh_if_needed"])

    def test_targeted_pre_dispatch_probe_forces_selected_provider(self) -> None:
        config = {
            "providers": {
                "claude2": {
                    "delivery_mode": "claude_cli",
                    "runtime": {"cli": "claude"},
                }
            }
        }
        expected = {"provider": "claude2", "ready": False, "status": "auth_not_ready"}
        with (
            mock.patch.object(provider_permissions, "command_exists", return_value="/usr/bin/claude"),
            mock.patch.object(provider_permissions, "_claude_auth_probe", return_value=expected) as probe,
        ):
            result = provider_permissions.probe_provider_auth(config, "claude2", force=True)

        self.assertEqual(result, expected)
        probe.assert_called_once()
        self.assertTrue(probe.call_args.kwargs["force"])

    def test_codex_auth_probe_runs_exec_with_provider_home(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "codex2"
            home.mkdir()
            (home / "auth.json").write_text(
                '{"tokens":{"access_token":"redacted","refresh_token":"redacted"}}',
                encoding="utf-8",
            )
            config = {
                "providers": {
                    "codex2": {
                        "codex": {
                            "codex_home": str(home),
                            "api_key_env": "OPENAI_API_KEY_CODEX2",
                        }
                    }
                }
            }
            completed = subprocess.CompletedProcess(["codex"], 0, "OK\n", "")
            with (
                mock.patch.dict(os.environ, {"CODEX_SESSION_ID": "parent-session"}, clear=False),
                mock.patch.object(provider_permissions, "run_command", return_value=completed) as run_command,
            ):
                probe = provider_permissions._codex_auth_probe(config, "codex2", "/usr/bin/codex")

        self.assertTrue(probe["ready"])
        self.assertEqual(probe["method"], "codex_exec_oauth")
        command = run_command.call_args.args[0]
        self.assertEqual(command[:2], ["/usr/bin/codex", "exec"])
        self.assertIn("--skip-git-repo-check", command)
        env = run_command.call_args.kwargs["env"]
        self.assertEqual(env["CODEX_HOME"], str(home))
        self.assertNotIn("CODEX_SESSION_ID", env)

    def test_codex_auth_ready_false_on_revoked_refresh_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir) / "codex2"
            home.mkdir()
            (home / "auth.json").write_text('{"tokens":{"access_token":"redacted","refresh_token":"redacted"}}', encoding="utf-8")
            config = {"providers": {"codex2": {"codex": {"codex_home": str(home)}}}}
            revoked = subprocess.CompletedProcess(
                ["codex"],
                1,
                "",
                "error refreshing token: refresh-token-revoked",
            )
            with (
                mock.patch.object(provider_permissions, "_previous_provider_auth_probe", return_value=None),
                mock.patch.object(provider_permissions, "run_command", side_effect=[revoked, revoked]),
            ):
                self.assertFalse(
                    provider_permissions.codex_auth_ready(
                        "codex2",
                        {},
                        config=config,
                        binary="/usr/bin/codex",
                    )
                )
                probe = provider_permissions._codex_auth_probe(
                    config,
                    "codex2",
                    "/usr/bin/codex",
                    force=True,
                )

        self.assertFalse(probe["ready"])
        self.assertEqual(probe["status"], "refresh_token_revoked")

    def test_codex_probe_ready_rejects_login_status_output(self) -> None:
        ready, error, status = provider_permissions._codex_probe_ready(
            0,
            "Logged in as codex@example.test",
            "",
        )

        self.assertFalse(ready)
        self.assertEqual(status, "unexpected_output")
        self.assertIsNotNone(error)

    def test_provider_capabilities_marks_codex_revoked_token_auth_down(self) -> None:
        config = {
            "paths": {
                "status_file": ".orchestrator/ai-status.json",
                "activity_log": "ai-activity-log.jsonl",
                "current_work": "current-work.md",
                "dashboard": "dashboard-bundle.json",
                "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json",
            },
            "agents": {},
            "providers": {
                "codex": {"delivery_mode": "codex", "codex": {"cli": "codex"}},
                "codex2": {"delivery_mode": "codex", "codex": {"cli": "codex"}},
                "claude": {},
                "gemini": {},
                "copilot": {},
            },
        }

        def fake_codex_probe(config: dict, provider_id: str, binary: str | None, **kwargs: object) -> dict:
            # SUP-PROVIDER-POOL-PROBE-GATE-001: the periodic capability report is
            # telemetry, so it must NOT force a fresh `codex exec` smoke. The
            # forced probe belongs to probe_provider_auth at the launch gate.
            self.assertFalse(kwargs.get("force", False))
            return {
                "provider": provider_id,
                "kind": "codex",
                "ready": provider_id != "codex2",
                "method": "codex_exec_oauth",
                "status": "ready" if provider_id != "codex2" else "refresh_token_revoked",
                "error": None if provider_id != "codex2" else "refresh-token-revoked",
                "checked_at": "2026-06-14T15:00:00Z",
                "last_auth_probe_at": "2026-06-14T15:00:00Z",
                "source": "live",
            }

        with (
            mock.patch.object(provider_permissions, "_code_cli_info", return_value={}),
            mock.patch.object(provider_permissions, "_workspace_settings", return_value={}),
            mock.patch.object(provider_permissions, "_find_extension", return_value=(None, None)),
            mock.patch.object(provider_permissions, "_claude_local_settings", return_value={"permissions": {}}),
            mock.patch.object(provider_permissions, "_gemini_settings", return_value={}),
            mock.patch.object(provider_permissions, "_gemini_auth_ready", return_value=False),
            mock.patch.object(provider_permissions, "_custom_agents_info", return_value={}),
            mock.patch.object(provider_permissions, "_relevant_extensions", return_value=[]),
            mock.patch.object(
                provider_permissions,
                "desired_workspace_settings",
                return_value={
                    "claudeCode.initialPermissionMode": "acceptEdits",
                    "claudeCode.allowDangerouslySkipPermissions": False,
                    "geminicodeassist.agentYoloMode": False,
                    "github.copilot.chat.backgroundAgent.enabled": False,
                    "github.copilot.chat.cloudAgent.enabled": False,
                    "github.copilot.chat.claudeAgent.enabled": False,
                },
            ),
            mock.patch.object(
                provider_permissions,
                "desired_claude_local_settings",
                return_value={"permissions": {"defaultMode": "acceptEdits"}},
            ),
            mock.patch.object(
                provider_permissions,
                "desired_gemini_settings",
                return_value={
                    "general": {"defaultApprovalMode": "auto_edit"},
                    "security": {
                        "enablePermanentToolApproval": True,
                        "autoAddToPolicyByDefault": True,
                        "disableYoloMode": False,
                    },
                },
            ),
            mock.patch.object(
                provider_permissions,
                "command_exists",
                side_effect=lambda cmd: "/usr/bin/codex" if cmd == "codex" else None,
            ),
            mock.patch.object(provider_permissions, "claude_auth_ready", return_value=False),
            mock.patch.object(provider_permissions, "_codex_auth_probe", side_effect=fake_codex_probe),
        ):
            report = provider_permissions.provider_capabilities(config)

        self.assertFalse(report["providers"]["codex2"]["auth_ready"])
        self.assertFalse(report["providers"]["codex2"]["local_cli_worker_supported"])
        self.assertFalse(report["providers"]["codex2"]["supports_auto_approve"])

    def test_provider_capabilities_include_custom_antigravity_provider(self) -> None:
        config = {
            "paths": {
                "status_file": ".orchestrator/ai-status.json",
                "activity_log": "ai-activity-log.jsonl",
                "current_work": "current-work.md",
                "dashboard": "dashboard-bundle.json",
                "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json",
            },
            "agents": {},
            "providers": {
                "antigravity": {
                    "delivery_mode": "antigravity",
                    "antigravity": {"cli": "agy"},
                },
                "antigravity2": {
                    "delivery_mode": "antigravity",
                    "antigravity": {
                        "cli": "agy",
                        "config_home": "~/.gemini-agy2",
                        "print_timeout": "15m",
                    },
                },
                "claude": {},
                "gemini": {},
                "codex": {},
                "copilot": {},
            },
        }

        def fake_antigravity_probe(config: dict, provider_id: str, binary: str | None) -> dict:
            return {
                "provider": provider_id,
                "kind": "antigravity",
                "ready": provider_id == "antigravity2",
                "method": "agy_prompt_oauth",
                "error": None if provider_id == "antigravity2" else "missing",
                "checked_at": "2026-06-06T12:00:00Z",
                "last_auth_probe_at": "2026-06-06T12:00:00Z",
            }

        with (
            mock.patch.object(provider_permissions, "_code_cli_info", return_value={}),
            mock.patch.object(provider_permissions, "_workspace_settings", return_value={}),
            mock.patch.object(provider_permissions, "_find_extension", return_value=(None, None)),
            mock.patch.object(provider_permissions, "_claude_local_settings", return_value={"permissions": {}}),
            mock.patch.object(provider_permissions, "_gemini_settings", return_value={}),
            mock.patch.object(provider_permissions, "_gemini_auth_ready", return_value=False),
            mock.patch.object(provider_permissions, "_custom_agents_info", return_value={}),
            mock.patch.object(provider_permissions, "_relevant_extensions", return_value=[]),
            mock.patch.object(
                provider_permissions,
                "desired_workspace_settings",
                return_value={
                    "claudeCode.initialPermissionMode": "acceptEdits",
                    "claudeCode.allowDangerouslySkipPermissions": False,
                    "geminicodeassist.agentYoloMode": False,
                    "github.copilot.chat.backgroundAgent.enabled": False,
                    "github.copilot.chat.cloudAgent.enabled": False,
                    "github.copilot.chat.claudeAgent.enabled": False,
                },
            ),
            mock.patch.object(provider_permissions, "desired_claude_local_settings", return_value={"permissions": {"defaultMode": "acceptEdits"}}),
            mock.patch.object(
                provider_permissions,
                "desired_gemini_settings",
                return_value={
                    "general": {"defaultApprovalMode": "auto_edit"},
                    "security": {
                        "enablePermanentToolApproval": True,
                        "autoAddToPolicyByDefault": True,
                        "disableYoloMode": False,
                    },
                },
            ),
            mock.patch.object(provider_permissions, "command_exists", side_effect=lambda cmd: "/usr/bin/agy" if cmd == "agy" else None),
            mock.patch.object(provider_permissions, "claude_auth_ready", return_value=False),
            mock.patch.object(provider_permissions, "_antigravity_auth_probe", side_effect=fake_antigravity_probe),
        ):
            report = provider_permissions.provider_capabilities(config)

        self.assertIn("antigravity2", report["providers"])
        self.assertTrue(report["providers"]["antigravity2"]["auth_ready"])
        self.assertTrue(report["providers"]["antigravity2"]["supports_auto_approve"])
        self.assertEqual(report["providers"]["antigravity2"]["paths"]["binary"], "/usr/bin/agy")
        self.assertEqual(report["providers"]["antigravity2"]["paths"]["home"], os.path.expanduser("~/.gemini-agy2"))
        self.assertEqual(report["providers"]["antigravity2"]["settings"]["antigravity.print_timeout"], "15m")

    def test_antigravity_probe_ready_requires_nonempty_output(self) -> None:
        # `agy --prompt` exits 0 with empty output when the OAuth token is
        # revoked, so a clean exit code alone must NOT be treated as ready.
        ready, error, status = provider_permissions._antigravity_probe_ready(0, "", "")
        self.assertFalse(ready)
        self.assertEqual(status, "empty_output")
        self.assertIsNotNone(error)

        ready, error, status = provider_permissions._antigravity_probe_ready(0, "OK", "OK")
        self.assertTrue(ready)
        self.assertIsNone(error)
        self.assertEqual(status, "ready")

        ready, _error, status = provider_permissions._antigravity_probe_ready(
            0, "", "error getting token source: You are not logged into Antigravity."
        )
        self.assertFalse(ready)
        self.assertEqual(status, "not_logged_in")

        ready, _error, status = provider_permissions._antigravity_probe_ready(1, "", "boom")
        self.assertFalse(ready)
        self.assertEqual(status, "exit_1")

        ready, _error, status = provider_permissions._antigravity_probe_ready(
            0, "", "Individual quota reached. Contact your administrator to enable overages."
        )
        self.assertFalse(ready)
        self.assertEqual(status, "quota_reached")

    def test_antigravity_auth_probe_not_ready_on_silent_exit_zero(self) -> None:
        config = {
            "providers": {"antigravity": {"antigravity": {"cli": "agy"}}},
        }
        token = Path(os.path.expanduser("~/x-token"))
        silent = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "_previous_provider_auth_probe", return_value=None),
            mock.patch.object(provider_permissions, "run_command", return_value=silent),
        ):
            record = provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")
        self.assertFalse(record["ready"])
        self.assertEqual(record["status"], "empty_output")

    def test_antigravity_auth_probe_uses_configured_available_model(self) -> None:
        config = {
            "providers": {
                "antigravity": {
                    "antigravity": {
                        "cli": "agy",
                        "model": "gemini-3.6-flash-low",
                    }
                }
            },
        }
        token = Path(os.path.expanduser("~/x-token"))
        success = subprocess.CompletedProcess(args=["agy"], returncode=0, stdout="OK\n", stderr="")
        with (
            mock.patch.object(
                provider_permissions, "_antigravity_auth_metadata",
                return_value={"oauth_token_exists": True, "gemini_api_key_present": False, "oauth_token": str(token)},
            ),
            mock.patch.object(provider_permissions, "_previous_provider_auth_probe", return_value=None),
            mock.patch.object(provider_permissions, "run_command", return_value=success) as run_command,
        ):
            record = provider_permissions._antigravity_auth_probe(config, "antigravity", "/usr/bin/agy")

        self.assertTrue(record["ready"])
        command = run_command.call_args.args[0]
        self.assertEqual(command[command.index("--model") + 1], "gemini-3.6-flash-low")

class ProviderProbeGateTest(unittest.TestCase):
    """SUP-PROVIDER-POOL-PROBE-GATE-001.

    The supervisor calls the full capability report before every loop. Forcing a
    provider CLI smoke inside it turned an intended telemetry refresh into a
    per-tick `codex exec` / `agy --prompt` storm, and made a shared-credential
    Antigravity pool look like several independent healthy lanes.
    """

    @staticmethod
    def _codex_config(codex_home: Path, capabilities: Path) -> dict:
        return {
            "paths": {"provider_capabilities": str(capabilities)},
            "provider_auth": {"probe_interval_seconds": 900},
            "providers": {
                "codex": {
                    "delivery_mode": "codex",
                    "codex": {"cli": "codex", "codex_home": str(codex_home)},
                }
            },
        }

    @staticmethod
    def _write_recent_capabilities(path: Path, provider_id: str, *, checked_at: str) -> None:
        path.write_text(
            json.dumps(
                {
                    "providers": {
                        provider_id: {
                            "auth_ready": True,
                            "auth_probe": {
                                "provider": provider_id,
                                "kind": "codex",
                                "ready": True,
                                "status": "ready",
                                "method": "codex_exec_oauth",
                                "error": None,
                                "checked_at": checked_at,
                                "last_auth_probe_at": checked_at,
                                "source": "live",
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_codex_probe_reuses_recent_result_inside_probe_interval(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                '{"tokens":{"access_token":"redacted","refresh_token":"redacted"}}',
                encoding="utf-8",
            )
            capabilities = root / "provider-capabilities.json"
            recent = (
                datetime.now(timezone.utc) - timedelta(seconds=60)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self._write_recent_capabilities(capabilities, "codex", checked_at=recent)
            config = self._codex_config(codex_home, capabilities)

            with mock.patch.object(provider_permissions, "run_command") as run_command:
                probe = provider_permissions._codex_auth_probe(config, "codex", "/usr/bin/codex")

        run_command.assert_not_called()
        self.assertTrue(probe["ready"])
        self.assertEqual(probe["source"], "cached")

    def test_codex_probe_reruns_exec_once_the_probe_interval_elapsed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                '{"tokens":{"access_token":"redacted","refresh_token":"redacted"}}',
                encoding="utf-8",
            )
            capabilities = root / "provider-capabilities.json"
            stale = (
                datetime.now(timezone.utc) - timedelta(seconds=3600)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self._write_recent_capabilities(capabilities, "codex", checked_at=stale)
            config = self._codex_config(codex_home, capabilities)

            completed = subprocess.CompletedProcess(["codex"], 0, "OK\n", "")
            with mock.patch.object(
                provider_permissions, "run_command", return_value=completed
            ) as run_command:
                probe = provider_permissions._codex_auth_probe(config, "codex", "/usr/bin/codex")

        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0][:2], ["/usr/bin/codex", "exec"])
        self.assertEqual(probe["source"], "live")

    def test_targeted_probe_provider_auth_force_still_runs_a_fresh_probe(self) -> None:
        """The launch gate must never inherit the telemetry cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            codex_home = root / "codex-home"
            codex_home.mkdir()
            (codex_home / "auth.json").write_text(
                '{"tokens":{"access_token":"redacted","refresh_token":"redacted"}}',
                encoding="utf-8",
            )
            capabilities = root / "provider-capabilities.json"
            recent = (
                datetime.now(timezone.utc) - timedelta(seconds=60)
            ).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            self._write_recent_capabilities(capabilities, "codex", checked_at=recent)
            config = self._codex_config(codex_home, capabilities)

            completed = subprocess.CompletedProcess(["codex"], 0, "OK\n", "")
            with (
                mock.patch.object(
                    provider_permissions, "command_exists", side_effect=lambda cmd: f"/usr/bin/{cmd}"
                ),
                mock.patch.object(
                    provider_permissions, "run_command", return_value=completed
                ) as run_command,
            ):
                probe = provider_permissions.probe_provider_auth(config, "codex", force=True)

        run_command.assert_called_once()
        self.assertEqual(run_command.call_args.args[0][:2], ["/usr/bin/codex", "exec"])
        self.assertEqual(probe["source"], "live")
        self.assertTrue(probe["ready"])

    def test_antigravity_aliases_sharing_a_token_share_one_probe(self) -> None:
        """Five aliases on one OAuth token are one quota account, not five lanes."""
        config = {
            "providers": {
                "antigravity": {"delivery_mode": "antigravity", "antigravity": {"cli": "agy"}},
                "antigravity1-1": {"delivery_mode": "antigravity", "antigravity": {"cli": "agy"}},
                "antigravity1-2": {"delivery_mode": "antigravity", "antigravity": {"cli": "agy"}},
                "antigravity2": {
                    "delivery_mode": "antigravity",
                    "antigravity": {"cli": "agy", "home": "/tmp/pantheon-test-agy2"},
                },
            }
        }
        probed: list[str] = []

        def fake_probe(_config: dict, provider_id: str, _binary: str | None) -> dict:
            probed.append(provider_id)
            quota_dead = provider_id != "antigravity2"
            return {
                "provider": provider_id,
                "kind": "antigravity",
                "ready": not quota_dead,
                "status": "quota_reached" if quota_dead else "ready",
                "method": "agy_prompt_oauth",
                "error": "Individual quota reached" if quota_dead else None,
                "checked_at": "2026-07-27T18:04:00Z",
                "last_auth_probe_at": "2026-07-27T18:04:00Z",
                "source": "live",
            }

        with (
            mock.patch.object(
                provider_permissions, "command_exists", side_effect=lambda cmd: "/usr/bin/agy"
            ),
            mock.patch.object(provider_permissions, "_antigravity_auth_probe", side_effect=fake_probe),
        ):
            reports = provider_permissions._antigravity_provider_reports(
                config, ["antigravity", "antigravity1-1", "antigravity1-2", "antigravity2"]
            )

        # One live probe per credential group, not one per alias.
        self.assertEqual(probed, ["antigravity", "antigravity2"])

        shared_group = reports["antigravity"]["account_group"]
        for alias in ("antigravity", "antigravity1-1", "antigravity1-2"):
            self.assertEqual(reports[alias]["account_group"], shared_group)
            self.assertFalse(reports[alias]["auth_ready"], alias)
            self.assertFalse(reports[alias]["local_cli_worker_supported"], alias)
            self.assertFalse(reports[alias]["supports_auto_approve"], alias)
        self.assertEqual(reports["antigravity1-1"]["auth_probe"]["shared_with"], "antigravity")

        # A genuinely separate credential home keeps its own capacity.
        self.assertNotEqual(reports["antigravity2"]["account_group"], shared_group)
        self.assertTrue(reports["antigravity2"]["auth_ready"])

    def test_antigravity_declared_quota_group_shares_capacity(self) -> None:
        config = {
            "providers": {
                "antigravityA": {
                    "delivery_mode": "antigravity",
                    "quota_group": "shared_pool",
                    "antigravity": {"cli": "agy", "home": "/tmp/pantheon-test-agy-a"},
                },
                "antigravityB": {
                    "delivery_mode": "antigravity",
                    "quota_group": "shared_pool",
                    "antigravity": {"cli": "agy", "home": "/tmp/pantheon-test-agy-b"},
                },
            }
        }
        probed: list[str] = []

        def fake_probe(_config: dict, provider_id: str, _binary: str | None) -> dict:
            probed.append(provider_id)
            return {
                "provider": provider_id,
                "kind": "antigravity",
                "ready": False,
                "status": "quota_reached",
                "method": "agy_prompt_oauth",
                "error": "Individual quota reached",
                "checked_at": "2026-07-27T18:04:00Z",
                "last_auth_probe_at": "2026-07-27T18:04:00Z",
                "source": "live",
            }

        with (
            mock.patch.object(
                provider_permissions, "command_exists", side_effect=lambda cmd: "/usr/bin/agy"
            ),
            mock.patch.object(provider_permissions, "_antigravity_auth_probe", side_effect=fake_probe),
        ):
            reports = provider_permissions._antigravity_provider_reports(
                config, ["antigravityA", "antigravityB"]
            )

        # Different homes, but one declared quota group: one probe, one capacity.
        self.assertEqual(probed, ["antigravityA"])
        self.assertEqual(
            reports["antigravityA"]["account_group"], reports["antigravityB"]["account_group"]
        )
        self.assertFalse(reports["antigravityB"]["supports_auto_approve"])


class PermissionBrokerCommandTest(unittest.TestCase):
    def test_force_push_is_denied(self) -> None:
        command = "git push --force origin HEAD"

        self.assertEqual(permission_broker.classify_command(command), "deny")

    def test_finalize_commit_sequence_is_auto_allowed(self) -> None:
        command = (
            "git add ai-status.json ai-activity-log.jsonl current-work.md && "
            "git commit -m \"BG-006 finalize\""
        )
        config = {"agents": {"claude": {"display_name": "Claude"}}}
        runtime_state = {
            "workers": {
                "run-123": {
                    "task_id": "BG-006",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            }
        }
        status_state = {
            "tasks": [
                {
                    "id": "BG-006",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review_approved",
                }
            ]
        }

        with (
            mock.patch.dict(
                permission_broker.os.environ,
                {"ORCH_RUN_ID": "run-123", "ORCH_TASK_ID": "BG-006", "ORCH_AGENT_ID": "claude"},
                clear=False,
            ),
            mock.patch.object(permission_broker, "load_runtime_state", return_value=runtime_state),
            mock.patch.object(permission_broker, "load_status", return_value=status_state),
        ):
            evaluation = permission_broker.evaluate_tool_request("Bash", {"command": command}, config)

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "repo_finalize_git")
        self.assertIn("BG-006", evaluation["reason"])

    def test_finalize_heredoc_commit_sequence_with_stderr_merge_is_auto_allowed(self) -> None:
        command = """git add docs/operations/postgres-cutoff-wave3-runbook.md && git commit -m "$(cat <<'EOF'
SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3: owner closeout finalization

LLM-Agent: Claude
Task-ID: SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3
Reviewer: Codex
EOF
)\" 2>&1"""
        config = {"agents": {"claude": {"display_name": "Claude"}}}
        runtime_state = {
            "workers": {
                "run-123": {
                    "task_id": "SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3",
                    "request_snapshot": {"reason": "owned_finalize_dispatch"},
                }
            }
        }
        status_state = {
            "tasks": [
                {
                    "id": "SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3",
                    "owner": "Claude",
                    "reviewer": "Codex",
                    "status": "review_approved",
                }
            ]
        }

        with (
            mock.patch.dict(
                permission_broker.os.environ,
                {
                    "ORCH_RUN_ID": "run-123",
                    "ORCH_TASK_ID": "SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3",
                    "ORCH_AGENT_ID": "claude",
                },
                clear=False,
            ),
            mock.patch.object(permission_broker, "load_runtime_state", return_value=runtime_state),
            mock.patch.object(permission_broker, "load_status", return_value=status_state),
        ):
            evaluation = permission_broker.evaluate_tool_request("Bash", {"command": command}, config)

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "repo_finalize_git")
        self.assertIn("SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3", evaluation["reason"])

    def test_non_finalize_commit_follows_safe_bash_classification(self) -> None:
        command = "git add ai-status.json && git commit -m \"BG-006 finalize\""

        with (
            mock.patch.dict(
                permission_broker.os.environ,
                {"ORCH_RUN_ID": "run-123", "ORCH_TASK_ID": "BG-006", "ORCH_AGENT_ID": "claude"},
                clear=False,
            ),
            mock.patch.object(permission_broker, "load_runtime_state", return_value={}),
            mock.patch.object(permission_broker, "load_status", return_value={"tasks": []}),
        ):
            evaluation = permission_broker.evaluate_tool_request("Bash", {"command": command}, {})

        self.assertEqual(evaluation["decision"], "allow")
        self.assertEqual(evaluation["risk_class"], "safe_bash")


if __name__ == "__main__":
    unittest.main()
