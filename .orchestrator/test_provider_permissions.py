from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import permission_broker
import provider_permissions
from provider_permissions import ROOT, _verified_claude_hooks


class ProviderPermissionsTest(unittest.TestCase):
    def test_codex_config_health_rejects_invalid_service_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "priority"\n', encoding="utf-8")
            config = {
                "providers": {
                    "codex": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    }
                }
            }

            health = provider_permissions.codex_config_health(config, "codex")

        self.assertFalse(health["valid"])
        self.assertIn("unsupported service_tier", health["error"])
        self.assertEqual(health["checks"]["service_tier"], "priority")

    def test_provider_capabilities_marks_invalid_codex_config_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            codex_home = Path(tmpdir)
            (codex_home / "config.toml").write_text('service_tier = "priority"\n', encoding="utf-8")
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
                    "claude": {},
                    "gemini": {},
                    "codex": {
                        "delivery_mode": "codex",
                        "codex": {"codex_home": str(codex_home)},
                    },
                    "copilot": {},
                },
            }

            with (
                mock.patch.object(provider_permissions, "_code_cli_info", return_value={}),
                mock.patch.object(provider_permissions, "_workspace_settings", return_value={}),
                mock.patch.object(provider_permissions, "_find_extension", return_value=(None, None)),
                mock.patch.object(provider_permissions, "_claude_local_settings", return_value={"permissions": {}}),
                mock.patch.object(provider_permissions, "_gemini_settings", return_value={}),
                mock.patch.object(provider_permissions, "_gemini_auth_ready", return_value=False),
                mock.patch.object(provider_permissions, "_gemini_selected_auth_type", return_value=None),
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
            ):
                report = provider_permissions.provider_capabilities(config)

        codex_report = report["providers"]["codex"]
        self.assertFalse(codex_report["config_valid"])
        self.assertEqual(codex_report["verified"], "blocked")
        self.assertIn("unsupported service_tier", codex_report["config_error"])
        self.assertEqual(codex_report["config_checks"]["service_tier"], "priority")

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

    def test_edit_allows_configured_execute_plans_workspace_root(self) -> None:
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
