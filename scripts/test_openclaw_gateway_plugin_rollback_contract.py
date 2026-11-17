#!/usr/bin/env python3
"""Rollback-safety contract for the openclaw-gateway persona-tools plugin startup.

Static tier (always): compose startup never persists plugin config, strips legacy
persisted config, keeps staging/production compose files free of the plugin wiring.
Docker tier (when PANTHEON_GATEWAY_TEST_IMAGE is set, e.g. a built
integrations/openclaw/gateway image): candidate startup on a fresh volume, then the
"previous" image (same image with /opt/pantheon/plugins hidden by a tmpfs) on the
same volume must validate and become ready.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import unittest
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
PLUGIN = "pantheon-persona-tools"
IMAGE = os.environ.get("PANTHEON_GATEWAY_TEST_IMAGE", "")
GATEWAY_EXEC = "exec node dist/index.js gateway"


def gateway_service() -> dict:
    return yaml.safe_load(COMPOSE.read_text())["services"]["openclaw-gateway"]


def startup_script() -> str:
    # compose renders "$$" -> "$"
    return gateway_service()["command"][2].replace("$$", "$")


def env_of() -> dict:
    return {k: str(v).replace("$$", "$") for k, v in gateway_service()["environment"].items()}


class StaticContract(unittest.TestCase):
    def test_startup_never_persists_plugin_config(self):
        script = startup_script()
        self.assertNotIn("plugins.load.paths", script)
        self.assertNotIn("PERSONA_TOOLS_ENABLE_BATCH", script)
        self.assertNotIn("PERSONA_TOOLS_DISABLE_BATCH", script)
        self.assertEqual(script.count("config patch"), 1, "at most one config rewrite on the shared config")
        self.assertEqual(script.count("config set"), 1, "only the opt-in isolated batch may still call config set")

    def test_overlay_is_loaded_only_with_token_and_removed_without(self):
        script = startup_script()
        self.assertIn('if [ -n "$PERSONA_TOOL_BROKER_TOKEN" ]; then', script)
        self.assertIn("export OPENCLAW_CONFIG_PATH=/home/node/.openclaw/openclaw.runtime.json; else rm -f", script)
        overlay = json.loads(env_of()["PANTHEON_PERSONA_TOOLS_OVERLAY"])
        self.assertEqual(overlay["$include"], "./openclaw.json")
        self.assertEqual(overlay["plugins"]["load"]["paths"], ["/opt/pantheon/plugins/" + PLUGIN])
        entry = overlay["plugins"]["entries"][PLUGIN]
        self.assertTrue(entry["enabled"])
        self.assertEqual(entry["config"]["brokerToken"], "${PERSONA_TOOL_BROKER_TOKEN}")

    def test_base_patch_strips_legacy_persisted_plugin_config(self):
        patch = json.loads(env_of()["PANTHEON_OPENCLAW_BASE_PATCH"])
        self.assertIsNone(patch["plugins"]["load"]["paths"])
        self.assertIsNone(patch["plugins"]["entries"][PLUGIN])
        self.assertTrue(patch["gateway"]["http"]["endpoints"]["responses"]["enabled"])

    def test_staging_and_production_compose_do_not_wire_the_plugin(self):
        for f in ROOT.glob("docker-compose*.yml"):
            if f.name == "docker-compose.yml":
                continue
            self.assertNotIn(PLUGIN, f.read_text(), f.name)


def docker(*args, check=True, timeout=300):
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)[:200]} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r


@unittest.skipUnless(IMAGE, "set PANTHEON_GATEWAY_TEST_IMAGE to run the rollback test")
class RollbackAgainstVolume(unittest.TestCase):
    def setUp(self):
        self.vol = "ocrb-" + uuid.uuid4().hex[:8]
        docker("volume", "create", self.vol)
        docker("run", "--rm", "--user", "root", "-v", f"{self.vol}:/v", "--entrypoint", "chown", IMAGE, "-R", "node:node", "/v")
        self.addCleanup(lambda: docker("volume", "rm", "-f", self.vol, check=False))

    def run_startup(self, *, token, tail, previous=False, detach_name=None):
        env = env_of()
        env["PERSONA_TOOL_BROKER_TOKEN"] = token
        env["OPENCLAW_GATEWAY_TOKEN"] = "gw-test-token"
        env["PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH"] = ""  # raw compose value is an unresolved ${...:-}
        script = startup_script()
        self.assertIn(GATEWAY_EXEC, script)
        script = script.replace(GATEWAY_EXEC + " --allow-unconfigured --port 18789 --auth token --token \"$OPENCLAW_GATEWAY_TOKEN\"", tail)
        args = ["run", "-v", f"{self.vol}:/home/node/.openclaw", "--entrypoint", "sh"]
        args += ["--rm"] if not detach_name else ["-d", "--name", detach_name]
        if previous:
            args += ["--tmpfs", "/opt/pantheon/plugins"]
        for k in ("PANTHEON_OPENCLAW_BASE_PATCH", "PANTHEON_PERSONA_TOOLS_OVERLAY", "PERSONA_TOOL_BROKER_TOKEN",
                  "OPENCLAW_GATEWAY_TOKEN", "PANTHEON_OPENCLAW_ISOLATED_MODEL_BATCH"):
            args += ["-e", f"{k}={env.get(k, '')}"]
        return docker(*args, IMAGE, "-ec", script)

    def read_config(self):
        return docker("run", "--rm", "-v", f"{self.vol}:/v", "--entrypoint", "cat", IMAGE, "/v/openclaw.json").stdout

    def seed_legacy(self):
        docker("run", "--rm", "-v", f"{self.vol}:/home/node/.openclaw", "--entrypoint", "sh", IMAGE, "-c",
               "cd /app && node dist/index.js config set --batch-json "
               "'[{\"path\":\"plugins.load.paths\",\"value\":[\"/opt/pantheon/plugins/%s\"]},"
               "{\"path\":\"plugins.entries.%s.config.brokerUrl\",\"value\":\"http://x:1/y\"},"
               "{\"path\":\"plugins.entries.%s.config.brokerToken\",\"value\":\"legacy\"},"
               "{\"path\":\"plugins.entries.%s.enabled\",\"value\":true}]' >/dev/null" % (PLUGIN, PLUGIN, PLUGIN, PLUGIN))
        self.assertIn("/opt/pantheon/plugins", self.read_config())

    def test_candidate_with_token_loads_plugin_and_leaves_rollback_safe_config(self):
        out = self.run_startup(token="tok", tail="node dist/index.js config validate && node dist/index.js plugins list").stdout
        self.assertIn("Config valid", out)
        self.assertIn("persona-tools", out.replace("\n", "").replace(" ", "").replace("│", ""))
        self.assertNotIn(PLUGIN, self.read_config())
        self.assertNotIn("/opt/pantheon", self.read_config())

    def test_candidate_without_token_does_not_load_plugin(self):
        out = self.run_startup(token="", tail="node dist/index.js plugins list").stdout
        self.assertNotIn("Persona", out)

    def test_legacy_persisted_config_is_normalised_then_rollback_becomes_ready(self):
        self.seed_legacy()
        self.run_startup(token="tok", tail="node dist/index.js config validate")
        cfg = self.read_config()
        self.assertNotIn("/opt/pantheon", cfg)
        self.assertNotIn(PLUGIN, cfg)
        name = "ocrb-gw-" + uuid.uuid4().hex[:6]
        self.run_startup(token="", previous=True, detach_name=name,
                         tail='exec node dist/index.js gateway --allow-unconfigured --port 18789 --auth token --token "$OPENCLAW_GATEWAY_TOKEN"')
        self.addCleanup(lambda: docker("rm", "-f", name, check=False))
        ready = False
        for _ in range(60):
            if docker("exec", name, "curl", "-fsS", "--max-time", "3", "http://127.0.0.1:18789/readyz", check=False).returncode == 0:
                ready = True
                break
            if docker("inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip() != "true":
                break
            time.sleep(2)
        logs = docker("logs", name, check=False)
        self.assertTrue(ready, logs.stdout + logs.stderr)
        v = docker("exec", name, "sh", "-c", "cd /app && node dist/index.js config validate")
        self.assertIn("Config valid", v.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
