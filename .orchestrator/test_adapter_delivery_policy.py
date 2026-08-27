from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from adapters.base import DeliveryRequest
from adapters.antigravity import AntigravityAdapter
from adapters.claude_cli import ClaudeCLIAdapter
from adapters.copilot_local import CopilotLocalAdapter
from adapters.codex import CodexAdapter


class AdapterDeliveryPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        # These are adapter command-shape tests.  The authoritative binding
        # itself is covered by test_status_command_runtime_pin; avoid copying a
        # full live runtime fixture into every provider-specific case.
        self._task_state_env = mock.patch(
            "common.task_state_store_runtime_env",
            return_value={
                "PANTHEON_TASK_STATE_STORE_MODE": "authoritative",
                "PANTHEON_TASK_STATE_EVENT_LOG": "/tmp/task-state-events-v2.jsonl",
            },
        )
        self._task_state_env.start()

    def tearDown(self) -> None:
        self._task_state_env.stop()

    def test_codex_alias_sets_agent_identity_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "codex2": {
                        "id": "codex2",
                        "display_name": "Codex2",
                        "provider": "codex2",
                        "adapter": "codex",
                    }
                },
                "providers": {
                    "codex2": {
                        "codex": {
                            "cli": "codex",
                            "api_key_env": "OPENAI_API_KEY_CODEX2",
                            "codex_home": "~/.codex2",
                        }
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="codex2",
                provider="codex2",
                delivery_mode="codex",
                message="wake",
                task_id="T-REVIEW",
                reason="review_ready_dispatch",
            )
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "OPENAI_API_KEY_CODEX2": "codex2-key",
                        "CODEX_THREAD_ID": "parent-thread",
                        "CODEX_SESSION_ID": "parent-session",
                    },
                    clear=False,
                ),
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.codex.spawn_background_process", return_value=(fake_process, Path("/tmp/codex2.log"))) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["AI_NAME"], "Codex2")
        self.assertEqual(env["ORCH_AGENT_ID"], "codex2")
        self.assertEqual(env["ORCH_PROVIDER"], "codex2")
        self.assertEqual(env["ORCH_TASK_ID"], "T-REVIEW")
        self.assertEqual(env["ORCH_REASON"], "review_ready_dispatch")
        self.assertEqual(env["OPENAI_API_KEY"], "codex2-key")
        self.assertEqual(env["CODEX_HOME"], os.path.expanduser("~/.codex2"))
        self.assertNotIn("CODEX_THREAD_ID", env)
        self.assertNotIn("CODEX_SESSION_ID", env)

    def test_codex_without_api_key_env_does_not_inherit_parent_openai_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "codex2": {
                        "id": "codex2",
                        "display_name": "Codex2",
                        "provider": "codex2",
                        "adapter": "codex",
                    }
                },
                "providers": {
                    "codex2": {
                        "codex": {
                            "cli": "codex",
                            "codex_home": "~/.codex2",
                        }
                    }
                },
            }
            request = DeliveryRequest(agent_id="codex2", provider="codex2", delivery_mode="codex", message="wake")
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch.dict(os.environ, {"OPENAI_API_KEY": "parent-key", "CODEX_THREAD_ID": "parent-thread"}, clear=False),
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.codex.spawn_background_process", return_value=(fake_process, Path("/tmp/codex2.log"))) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["CODEX_HOME"], os.path.expanduser("~/.codex2"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("CODEX_THREAD_ID", env)

    def test_codex_uses_request_workspace_and_status_root_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "worktree"
            status_root = root / "status-root"
            config = {
                "paths": {"status_file": str(status_root / "ai-status.json")},
                "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"}},
                "providers": {"codex": {"codex": {"cli": "codex"}}},
            }
            request = DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                metadata={
                    "workspace_path": str(workspace),
                    "status_root": str(status_root),
                },
            )
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch("adapters.codex.command_exists", return_value="codex"),
                mock.patch("adapters.codex.spawn_background_process", return_value=(fake_process, root / "codex.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.command[result.command.index("-C") + 1], str(workspace))
        self.assertEqual(spawn.call_args.kwargs["cwd"], workspace)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["PANTHEON_WORKTREE_ROOT"], str(workspace))
        self.assertEqual(env["PANTHEON_STATUS_ROOT"], str(status_root))
        self.assertEqual(env["ORCH_WORKSPACE_PATH"], str(workspace))

    def test_codex_resolves_relative_configured_cli_before_spawning(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            workspace = root / "worktree"
            workspace.mkdir()
            cli = root / ".orchestrator" / "bin" / "codex"
            cli.parent.mkdir(parents=True)
            cli.write_text("#!/usr/bin/env bash\nexit 0\n")
            cli.chmod(0o755)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {"codex": {"id": "codex", "display_name": "Codex", "provider": "codex", "adapter": "codex"}},
                "providers": {"codex": {"codex": {"cli": ".orchestrator/bin/codex"}}},
            }
            request = DeliveryRequest(
                agent_id="codex",
                provider="codex",
                delivery_mode="codex",
                message="wake",
                metadata={"workspace_path": str(workspace)},
            )
            adapter = CodexAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)
            previous_cwd = Path.cwd()

            try:
                os.chdir(root)
                with mock.patch(
                    "adapters.codex.spawn_background_process",
                    return_value=(fake_process, root / "codex.log"),
                ):
                    result = adapter.deliver(request)
            finally:
                os.chdir(previous_cwd)

        self.assertTrue(result.ok)
        self.assertEqual(result.command[0], str(cli))
        self.assertEqual(result.command[result.command.index("-C") + 1], str(workspace))

    def test_claude_unavailable_fails_closed(self) -> None:
        config = {
            "providers": {
                "claude": {
                    "runtime": {"cli": "claude"},
                }
            }
        }
        request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
        with (
            mock.patch("adapters.claude_cli._configured_claude_cli", return_value=None),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=False),
        ):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "claude_cli")

    def test_claude_alias_uses_provider_specific_home_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            gh_config = root / ".config" / "gh"
            gh_config.mkdir(parents=True)
            config = {
                "agents": {
                    "claude2": {
                        "id": "claude2",
                        "display_name": "Claude2",
                        "provider": "claude2",
                        "adapter": "claude_cli",
                    }
                },
                "paths": {
                    "status_file": "ai-status.json",
                    "claude_mcp_config": ".orchestrator/claude-approval-broker.mcp.json",
                },
                "providers": {
                    "claude2": {
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "home": "~/.claude2",
                            "output_format": "stream-json",
                            "include_hook_events": True,
                        },
                    }
                },
            }
            request = DeliveryRequest(agent_id="claude2", provider="claude2", delivery_mode="claude_cli", message="wake")
            adapter = ClaudeCLIAdapter(
                config=config,
                provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}},
            )
            fake_process = mock.Mock(pid=1234)
            with (
                mock.patch.dict(os.environ, {"HOME": str(root)}, clear=False),
                mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
                mock.patch(
                    "adapters.claude_cli.spawn_background_process",
                    return_value=(fake_process, Path("/tmp/claude2.log")),
                ) as spawn,
            ):
                os.environ.pop("GH_CONFIG_DIR", None)
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["HOME"], str(root / ".claude2"))
        self.assertEqual(env["GH_CONFIG_DIR"], str(gh_config))
        self.assertEqual(env["ORCH_PROVIDER"], "claude2")
        self.assertIn("--permission-mode", result.command)

    def test_claude_runtime_loads_oauth_token_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            token_file = root / "claude-token"
            token_file.write_text("sk-ant-oat01-test-token\n", encoding="utf-8")
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "providers": {
                    "claude": {
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "oauth_token_file": str(token_file),
                            "output_format": "stream-json",
                            "include_hook_events": True,
                        },
                    }
                },
            }
            request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
            adapter = ClaudeCLIAdapter(config=config, provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
                mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
                mock.patch("adapters.claude_cli.spawn_background_process", return_value=(fake_process, root / "claude.log")) as spawn,
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "sk-ant-oat01-test-token")

    def test_claude_runtime_passes_model_and_effort_when_configured(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "providers": {
                "claude": {
                    "runtime": {
                        "cli": ".orchestrator/bin/claude",
                        "output_format": "stream-json",
                        "include_hook_events": True,
                        "model": "sonnet",
                        "effort": "medium",
                    },
                }
            },
        }
        request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}})
        fake_process = mock.Mock(pid=1234)

        with (
            mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
            mock.patch("adapters.claude_cli.spawn_background_process", return_value=(fake_process, Path("/tmp/claude.log"))),
        ):
            result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertIn("--model", result.command)
        self.assertEqual(result.command[result.command.index("--model") + 1], "sonnet")
        self.assertIn("--effort", result.command)
        self.assertEqual(result.command[result.command.index("--effort") + 1], "medium")

    def test_claude_runtime_auto_permission_does_not_require_retired_provider_cache(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "providers": {
                "claude": {
                    "runtime": {
                        "cli": ".orchestrator/bin/claude",
                        "output_format": "stream-json",
                        "enable_auto_mode_if_supported": True,
                        "auto_permission_mode": "auto",
                        "permission_mode": "acceptEdits",
                    },
                }
            },
        }
        request = DeliveryRequest(
            agent_id="claude",
            provider="claude",
            delivery_mode="claude_cli",
            message="wake",
        )
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
        fake_process = mock.Mock(pid=1234)

        with (
            mock.patch(
                "adapters.claude_cli._configured_claude_cli",
                return_value=".orchestrator/bin/claude",
            ),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
            mock.patch(
                "adapters.claude_cli.spawn_background_process",
                return_value=(fake_process, Path("/tmp/claude.log")),
            ),
        ):
            result = adapter.deliver(request)

        self.assertTrue(result.ok)
        permission_index = result.command.index("--permission-mode")
        self.assertEqual(result.command[permission_index + 1], "auto")

    def test_claude_runtime_adds_supervisor_issued_status_and_command_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            command_root = root / "command-runtime"
            config = {
                "paths": {"status_file": str(status_root / "ai-status.json")},
                "providers": {
                    "claude": {
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "output_format": "stream-json",
                        },
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="claude",
                provider="claude",
                delivery_mode="claude_cli",
                message="wake",
                metadata={
                    "status_root": str(status_root),
                    "status_command_runtime": {"command_root": str(command_root)},
                },
            )
            adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch(
                    "adapters.claude_cli._configured_claude_cli",
                    return_value=".orchestrator/bin/claude",
                ),
                mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
                mock.patch("adapters.claude_cli.delivery_runtime_env", return_value={}),
                mock.patch(
                    "adapters.claude_cli.spawn_background_process",
                    return_value=(fake_process, root / "claude.log"),
                ),
            ):
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        add_dirs = [
            result.command[index + 1]
            for index, value in enumerate(result.command)
            if value == "--add-dir"
        ]
        self.assertEqual(add_dirs, [str(status_root), str(command_root)])

    def test_claude_runtime_deduplicates_roots_and_ignores_task_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            status_root = root / "status-root"
            config = {
                "paths": {"status_file": str(status_root / "ai-status.json")},
                "providers": {
                    "claude": {
                        "runtime": {
                            "cli": ".orchestrator/bin/claude",
                            "output_format": "stream-json",
                        },
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="claude",
                provider="claude",
                delivery_mode="claude_cli",
                message="wake",
                context_files=["task-controlled-relative-path"],
                metadata={
                    "status_root": str(status_root),
                    "status_command_runtime": {"command_root": str(status_root)},
                },
            )
            adapter = ClaudeCLIAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)

            with (
                mock.patch(
                    "adapters.claude_cli._configured_claude_cli",
                    return_value=".orchestrator/bin/claude",
                ),
                mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
                mock.patch("adapters.claude_cli.delivery_runtime_env", return_value={}),
                mock.patch(
                    "adapters.claude_cli.spawn_background_process",
                    return_value=(fake_process, root / "claude.log"),
                ),
            ):
                result = adapter.deliver(request)

        add_dirs = [
            result.command[index + 1]
            for index, value in enumerate(result.command)
            if value == "--add-dir"
        ]
        self.assertEqual(add_dirs, [str(status_root)])

    def test_claude_runtime_omits_model_and_effort_when_unset(self) -> None:
        config = {
            "paths": {"status_file": "ai-status.json"},
            "providers": {
                "claude": {
                    "runtime": {
                        "cli": ".orchestrator/bin/claude",
                        "output_format": "stream-json",
                        "include_hook_events": True,
                    },
                }
            },
        }
        request = DeliveryRequest(agent_id="claude", provider="claude", delivery_mode="claude_cli", message="wake")
        adapter = ClaudeCLIAdapter(config=config, provider_capabilities={"providers": {"claude": {"supports_auto_approve": True}}})
        fake_process = mock.Mock(pid=1234)

        with (
            mock.patch("adapters.claude_cli._configured_claude_cli", return_value=".orchestrator/bin/claude"),
            mock.patch("adapters.claude_cli._claude_auth_ready", return_value=True),
            mock.patch("adapters.claude_cli.spawn_background_process", return_value=(fake_process, Path("/tmp/claude.log"))),
        ):
            result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertNotIn("--model", result.command)
        self.assertNotIn("--effort", result.command)

    def test_antigravity_unavailable_fails_closed(self) -> None:
        config = {
            "agents": {"antigravity": {"id": "antigravity", "display_name": "Antigravity", "provider": "antigravity"}},
            "providers": {
                "antigravity": {
                    "antigravity": {"cli": "agy"},
                }
            },
        }
        request = DeliveryRequest(agent_id="antigravity", provider="antigravity", delivery_mode="antigravity", message="wake")
        adapter = AntigravityAdapter(config=config, provider_capabilities={})
        with mock.patch("adapters.antigravity.command_exists", return_value=None):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "antigravity")

    def test_antigravity_alias_uses_provider_specific_home_and_identity_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            gh_config = root / ".config" / "gh"
            gh_config.mkdir(parents=True)
            config = {
                "paths": {"status_file": str(root / "ai-status.json")},
                "agents": {
                    "antigravity2": {
                        "id": "antigravity2",
                        "display_name": "Antigravity2",
                        "provider": "antigravity2",
                        "adapter": "antigravity",
                    }
                },
                "providers": {
                    "antigravity2": {
                        "delivery_mode": "antigravity",
                        "antigravity": {
                            "cli": "agy",
                            "config_home": str(root / "agy2-home"),
                            "include_directories": True,
                            "model": "gemini-2.5-flash-lite",
                            "print_timeout": "15m",
                            "env": {"GEMINI_API_KEY": "agy-key"},
                        },
                        "approval": {"dangerously_skip_permissions": True},
                    }
                },
            }
            request = DeliveryRequest(
                agent_id="antigravity2",
                provider="antigravity2",
                delivery_mode="antigravity",
                message="wake",
                task_id="T-AGY2",
                reason="owned_ready_dispatch",
                metadata={
                    "workspace_path": str(root / "task-worktree"),
                    "status_root": str(root / "supervisor-root"),
                },
            )
            adapter = AntigravityAdapter(config=config, provider_capabilities={})
            fake_process = mock.Mock(pid=1234)
            with (
                mock.patch.dict(os.environ, {"HOME": str(root)}, clear=False),
                mock.patch("adapters.antigravity.command_exists", return_value="agy"),
                mock.patch("adapters.antigravity._auth_ready", return_value=True),
                mock.patch("adapters.antigravity.spawn_background_process", return_value=(fake_process, root / "agy2.log")) as spawn,
            ):
                os.environ.pop("GH_CONFIG_DIR", None)
                result = adapter.deliver(request)

        self.assertTrue(result.ok)
        self.assertEqual(result.target, "Antigravity2")
        self.assertIn("--model", result.command)
        self.assertEqual(result.command[result.command.index("--model") + 1], "gemini-2.5-flash-lite")
        self.assertIn("--output-format", result.command)
        self.assertEqual(result.command[result.command.index("--output-format") + 1], "stream-json")
        self.assertIn("--print-timeout", result.command)
        self.assertEqual(result.command[result.command.index("--print-timeout") + 1], "15m")
        self.assertIn("--dangerously-skip-permissions", result.command)
        self.assertIn("--add-dir", result.command)
        self.assertEqual(result.command[result.command.index("--add-dir") + 1], str(root / "task-worktree"))
        self.assertEqual(spawn.call_args.kwargs["cwd"], root / "task-worktree")
        env = spawn.call_args.kwargs["env"]
        self.assertEqual(env["AI_NAME"], "Antigravity2")
        self.assertEqual(env["ORCH_AGENT_ID"], "antigravity2")
        self.assertEqual(env["ORCH_PROVIDER"], "antigravity2")
        self.assertEqual(env["ANTIGRAVITY_HOME"], str(root / "agy2-home"))
        self.assertEqual(env["HOME"], str(root / "agy2-home"))
        self.assertEqual(env["GH_CONFIG_DIR"], str(gh_config))
        self.assertEqual(env["GEMINI_API_KEY"], "agy-key")
        self.assertEqual(env["ORCH_TASK_ID"], "T-AGY2")
        self.assertEqual(env["ORCH_REASON"], "owned_ready_dispatch")
        self.assertEqual(env["PANTHEON_STATUS_ROOT"], str(root / "supervisor-root"))

    def _rotation_config(self, root: Path) -> dict:
        return {
            "paths": {
                "status_file": str(root / "ai-status.json"),
                "state_file": str(root / ".orchestrator" / "state.json"),
            },
            "agents": {
                "antigravity": {
                    "id": "antigravity",
                    "display_name": "Antigravity",
                    "provider": "antigravity",
                    "adapter": "antigravity",
                }
            },
            "providers": {
                "antigravity": {
                    "delivery_mode": "antigravity",
                    "antigravity": {
                        "cli": "agy",
                        "model": "gemini-3.6-flash-low",
                        "print_timeout": "15m",
                    },
                    "model_rotation": {
                        "enabled": True,
                        "primary": "gemini-3.6-flash-low",
                        "fallback": "Claude Sonnet 4.6 (Thinking)",
                        "cooldown_seconds": 900,
                    },
                    "approval": {"dangerously_skip_permissions": True},
                }
            },
        }

    def _deliver_antigravity(self, config: dict, root: Path):
        request = DeliveryRequest(
            agent_id="antigravity",
            provider="antigravity",
            delivery_mode="antigravity",
            message="wake",
            metadata={"workspace_path": str(root / "task-worktree")},
        )
        adapter = AntigravityAdapter(config=config, provider_capabilities={})
        fake_process = mock.Mock(pid=4321)
        with (
            mock.patch("adapters.antigravity.command_exists", return_value="agy"),
            mock.patch("adapters.antigravity._auth_ready", return_value=True),
            mock.patch(
                "adapters.antigravity.spawn_background_process",
                return_value=(fake_process, root / "agy.log"),
            ) as spawn,
        ):
            result = adapter.deliver(request)
        return result, spawn

    def test_antigravity_rotation_pins_available_primary_when_nothing_cooling(self) -> None:
        import model_rotation

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._rotation_config(root)
            result, spawn = self._deliver_antigravity(config, root)

        self.assertTrue(result.ok)
        self.assertIn("--model", result.command)
        self.assertEqual(result.command[result.command.index("--model") + 1], "gemini-3.6-flash-low")
        self.assertEqual(spawn.call_args.kwargs["env"]["ORCH_MODEL_ROTATION_SLOT"], model_rotation.SLOT_PRIMARY)

    def test_antigravity_rotation_switches_to_fallback_when_primary_cooling(self) -> None:
        import model_rotation

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = self._rotation_config(root)
            model_rotation.cool_slot(config, "antigravity", model_rotation.SLOT_PRIMARY)
            result, spawn = self._deliver_antigravity(config, root)

        self.assertTrue(result.ok)
        self.assertIn("--model", result.command)
        self.assertEqual(result.command[result.command.index("--model") + 1], "Claude Sonnet 4.6 (Thinking)")
        self.assertEqual(spawn.call_args.kwargs["env"]["ORCH_MODEL_ROTATION_SLOT"], model_rotation.SLOT_FALLBACK)

    def test_copilot_unavailable_fails_closed(self) -> None:
        config = {
            "providers": {
                "copilot": {
                    "local": {"cli": "copilot"},
                    "cloud": {"cli": "gh"},
                }
            }
        }
        request = DeliveryRequest(agent_id="copilot", provider="copilot", delivery_mode="copilot_local", message="wake")
        adapter = CopilotLocalAdapter(config=config, provider_capabilities={})
        with (
            mock.patch("adapters.copilot_local._configured_copilot_cli", return_value=None),
            mock.patch("adapters.copilot_local._copilot_auth_ready", return_value=False),
        ):
            result = adapter.deliver(request)
        self.assertFalse(result.ok)
        self.assertFalse(result.manual_confirmation_required)
        self.assertEqual(result.mode, "copilot_local")


if __name__ == "__main__":
    unittest.main()
