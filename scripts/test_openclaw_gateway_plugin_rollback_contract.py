#!/usr/bin/env python3
"""Rollback-safety contract for the openclaw-gateway persona-tools plugin startup.

The plugin is a STOCK plugin baked into the image (enabledByDefault, broker config from
env). There is deliberately no $include overlay: a root $include made the gateway refuse
every config.patch ("Config write would flatten $include-owned config at <root>").

Static tier (always): compose startup never persists plugin config, strips legacy
persisted config, deletes the legacy overlay file, never points OPENCLAW_CONFIG_PATH at
an $include overlay, keeps staging/production compose files free of the plugin wiring.
Docker tier (when PANTHEON_GATEWAY_TEST_IMAGE is set, e.g. a built
integrations/openclaw/gateway image): candidate startup on a fresh volume, then the
"previous" image (same image with the plugin dir hidden by a tmpfs) on the same volume
must validate and become ready. The real gateway must also accept the consult-agent
config.patch while the plugin is loaded, and the agent must survive restart + rollback.
PANTHEON_GATEWAY_TEST_PLUGIN_DIR (optional) bind-mounts a built plugin dir over
PLUGIN_DIR for an image built before the plugin was baked in.
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
PLUGIN_DIR = "/app/dist/extensions/" + PLUGIN
PLUGIN_MOUNT = os.environ.get("PANTHEON_GATEWAY_TEST_PLUGIN_DIR", "")
LEGACY_OVERLAY = "/home/node/.openclaw/openclaw.runtime.json"


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

    def test_no_include_overlay_is_written_or_selected(self):
        script = startup_script()
        env = env_of()
        self.assertNotIn("OPENCLAW_CONFIG_PATH", script)
        self.assertNotIn("PANTHEON_PERSONA_TOOLS_OVERLAY", env)
        self.assertNotIn("$include", json.dumps(env))
        self.assertNotIn("openclaw.runtime.json >", script.replace("rm -f", ""))
        self.assertIn("rm -f " + LEGACY_OVERLAY, script, "a legacy overlay left on the volume must be deleted")
        self.assertLess(script.index("rm -f " + LEGACY_OVERLAY), script.index(GATEWAY_EXEC))

    def test_plugin_is_a_stock_enabled_by_default_plugin_configured_from_env(self):
        manifest = json.loads((ROOT / "integrations/openclaw/plugins" / PLUGIN / "openclaw.plugin.json").read_text())
        self.assertTrue(manifest["enabledByDefault"])
        self.assertNotIn("required", manifest["configSchema"])
        dockerfile = (ROOT / "integrations/openclaw/gateway/Dockerfile").read_text()
        self.assertIn("/app/dist/extensions/" + PLUGIN, dockerfile)
        self.assertNotIn("/opt/pantheon/plugins", dockerfile.replace("# ", ""))
        env = env_of()
        self.assertTrue(env["PERSONA_TOOL_BROKER_URL"].startswith("http://openclaw-gateway-adapter:8104/"))
        self.assertIn("PERSONA_TOOL_BROKER_TOKEN", env)

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
            args = [a for a in args if a != "--tmpfs"]
            args += ["--tmpfs", PLUGIN_DIR]
        elif PLUGIN_MOUNT:
            args += ["-v", f"{PLUGIN_MOUNT}:{PLUGIN_DIR}:ro"]
        for k in ("PANTHEON_OPENCLAW_BASE_PATCH", "PERSONA_TOOL_BROKER_TOKEN", "PERSONA_TOOL_BROKER_URL",
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
        self.assertNotIn("$include", self.read_config())

    def test_candidate_without_token_leaves_no_overlay_and_validates(self):
        docker("run", "--rm", "-v", f"{self.vol}:/v", "--entrypoint", "sh", IMAGE, "-c",
               "printf '{\"$include\":\"./openclaw.json\"}' > /v/openclaw.runtime.json")
        out = self.run_startup(token="", tail="test ! -e " + LEGACY_OVERLAY + " && node dist/index.js config validate").stdout
        self.assertIn("Config valid", out)

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


    # ---- real gateway config behaviour (not a fake gateway_call) ----------------------------------
    CALL = r"""
cd /app
node -e '
const {execFileSync}=require("child_process");
const call=(m,p)=>JSON.parse(execFileSync("node",["dist/index.js","gateway","call",m,"--token","gw-test-token","--json"].concat(p?["--params",JSON.stringify(p)]:[]),{encoding:"utf8"}));
const snap=call("config.get");
const mode=process.argv[1];
const out={valid:snap.valid,ids:((snap.config||{}).agents||{}).list.map(a=>a.id)};
if(mode==="patch"){
  // the exact shape services/openclaw-gateway-adapter/consult_agent_provisioning.py sends
  const desired={id:"consult-t1-persona-risk-officer",workspace:"/home/node/.openclaw/workspace-consult-t1"};
  try{call("config.patch",{baseHash:snap.hash,raw:JSON.stringify({agents:{list:[desired]}}),
    replacePaths:["agents.list[].tools.allow","agents.list[].tools.deny","agents.list[].skills","agents.list[].memorySearch.sources"]});out.patch="ok";}
  catch(e){out.patch="error:"+String(e.stderr||e.message).slice(0,300);}
  out.after=call("config.get").config.agents.list.map(a=>a.id);
}
console.log("RESULT "+JSON.stringify(out));
' "$1"
"""

    def start_gateway(self, name, *, token="tok", previous=False):
        self.run_startup(token=token, previous=previous, detach_name=name,
                         tail='exec node dist/index.js gateway --allow-unconfigured --port 18789 --auth token --token "$OPENCLAW_GATEWAY_TOKEN"')
        self.addCleanup(lambda: docker("rm", "-f", name, check=False))
        for _ in range(60):
            if docker("exec", name, "curl", "-fsS", "--max-time", "3", "http://127.0.0.1:18789/readyz", check=False).returncode == 0:
                return
            if docker("inspect", "-f", "{{.State.Running}}", name, check=False).stdout.strip() != "true":
                break
            time.sleep(2)
        logs = docker("logs", name, check=False)
        raise AssertionError("gateway not ready\n" + logs.stdout + logs.stderr)

    def gateway_call(self, name, mode):
        out = docker("exec", name, "sh", "-c", self.CALL, "sh", mode).stdout
        line = [l for l in out.splitlines() if l.startswith("RESULT ")][-1]
        return json.loads(line[len("RESULT "):])

    def test_consult_agent_config_patch_succeeds_with_plugin_and_survives_restart_and_rollback(self):
        agent = "consult-t1-persona-risk-officer"
        gw = "ocrb-gw-" + uuid.uuid4().hex[:6]
        self.start_gateway(gw)
        # the base config needs a default agent before consultation agents are admitted
        docker("exec", gw, "sh", "-c", "cd /app && printf '%s' '{\"agents\":{\"list\":[{\"id\":\"main\",\"default\":true}]}}' "
                                        "| node dist/index.js config patch --stdin >/dev/null")
        result = self.gateway_call(gw, "patch")
        logs = docker("logs", gw, check=False)
        self.assertEqual(result["patch"], "ok", result)
        self.assertNotIn("would flatten", logs.stdout + logs.stderr)
        self.assertIn(agent, result["after"])
        cfg = self.read_config()
        self.assertIn(agent, cfg)
        for forbidden in (PLUGIN, "/opt/pantheon", "/app/dist/extensions", "brokerToken", "$include"):
            self.assertNotIn(forbidden, cfg)
        # restart on the same volume: the agent is still there and readable via config.get
        docker("rm", "-f", gw)
        gw2 = "ocrb-gw-" + uuid.uuid4().hex[:6]
        self.start_gateway(gw2)
        self.assertIn(agent, self.gateway_call(gw2, "read")["ids"])
        # rollback: a release without the plugin on the same volume still serves the agent
        docker("rm", "-f", gw2)
        gw3 = "ocrb-gw-" + uuid.uuid4().hex[:6]
        self.start_gateway(gw3, token="", previous=True)
        rolled = self.gateway_call(gw3, "read")
        self.assertTrue(rolled["valid"])
        self.assertIn(agent, rolled["ids"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
