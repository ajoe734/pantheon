import json
import os
import re
import subprocess
import time
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIGURE_SCRIPT = REPO_ROOT / "scripts" / "openclaw-configure-shared-model-pool.sh"


# Fixture values of the OPENCLAW_MODEL_CHAIN variable; the script names no model.
CHAIN = ["fx-a/model-1", "fx-b/model-2", "fx-b/model-3"]
CHAIN_ENV = {"OPENCLAW_MODEL_CHAIN": ",".join(CHAIN)}
NATIVE = {"agentRuntime": {"id": "openclaw"}}
REGISTERED = {ref: dict(NATIVE) for ref in CHAIN}
MODEL_NAME_RE = re.compile(r"\b(?:gpt-[0-9]|claude-(?:opus|sonnet|haiku)|gemini-[0-9])")


def _model_pool_batch(source: str) -> list[dict[str, object]]:
    match = re.search(r"MODEL_POOL_BATCH='(\[.*?\])'", source, re.DOTALL)
    assert match is not None
    return json.loads(match.group(1))


def test_script_names_no_model_and_static_batch_has_no_chain() -> None:
    source = CONFIGURE_SCRIPT.read_text(encoding="utf-8")
    assert not MODEL_NAME_RE.search(source)
    paths = {item["path"] for item in _model_pool_batch(source)}
    assert not any(p.startswith("agents.defaults.model") for p in paths)
    assert "gateway.http.endpoints.responses.enabled" in paths


def test_chain_is_written_as_primary_and_fallbacks_with_native_runtime(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=False, extra_env=CHAIN_ENV)
    assert result.returncode == 0, result.stderr
    store = result.final_config
    assert store["model"] == {"primary": CHAIN[0], "fallbacks": CHAIN[1:]}
    assert all(store["models"][ref]["agentRuntime"] == {"id": "openclaw"} for ref in CHAIN)
    # Gateway restarts after the chain write (fallback change needs a restart).
    set_idx = max(i for i, a in enumerate(calls) if "set" in a and any("agents.defaults.model.primary" in x for x in a))
    assert any("restart" in a for a in calls[set_idx:])


def test_unset_variable_keeps_the_shipped_default_chain(tmp_path) -> None:
    sys.path.insert(0, str(REPO_ROOT))
    from services.persona.runtime_profile import default_chain

    default = default_chain()
    result, _ = _run_model_pool_script(
        tmp_path, token_present=False, config={"models": {}, "providers": {}},
        extra_env={"OPENCLAW_MODEL_CHAIN": ""},
    )
    assert result.returncode == 0, result.stderr
    assert result.final_config["model"] == {"primary": default[0], "fallbacks": default[1:]}


def test_chain_naming_a_cli_runtime_model_is_refused_and_previous_chain_kept(tmp_path) -> None:
    prev = {"primary": CHAIN[0], "fallbacks": [CHAIN[1]]}
    config = {"model": prev, "models": {**REGISTERED, "fx-c/cli": {"agentRuntime": {"id": "fx-cli"}}}}
    result, calls = _run_model_pool_script(
        tmp_path, token_present=False, config=config,
        extra_env={"OPENCLAW_MODEL_CHAIN": "fx-a/model-1,fx-c/cli"},
    )
    assert result.returncode == 3, result.stderr
    assert "chain_model_cli_runtime" in result.stderr
    assert result.final_config["model"] == prev
    assert not any("set" in a and any("agents.defaults.model.primary" in x for x in a) for a in calls)
    assert any("restart" in a for a in calls)  # rest of the configure step still completes


def test_cli_backend_provider_prefix_is_refused(tmp_path) -> None:
    config = {"model": {"primary": CHAIN[0], "fallbacks": []}, "models": REGISTERED,
              "cliBackends": {"fx-cli": {}}}
    result, _ = _run_model_pool_script(
        tmp_path, token_present=False, config=config,
        extra_env={"OPENCLAW_MODEL_CHAIN": "fx-cli/some-model"},
    )
    assert result.returncode == 3
    assert "chain_model_cli_runtime" in result.stderr


def test_chain_naming_an_unregistered_model_is_refused_and_previous_chain_kept(tmp_path) -> None:
    prev = {"primary": CHAIN[0], "fallbacks": CHAIN[1:]}
    result, _ = _run_model_pool_script(
        tmp_path, token_present=False, config={"model": prev, "models": REGISTERED},
        extra_env={"OPENCLAW_MODEL_CHAIN": "fx-a/model-1,fx-z/not-registered"},
    )
    assert result.returncode == 3
    assert "chain_model_not_registered" in result.stderr
    assert result.final_config["model"] == prev


def test_provider_catalog_counts_as_registered(tmp_path) -> None:
    config = {"model": {"primary": CHAIN[0], "fallbacks": []}, "models": REGISTERED,
              "providers": {"fx-p": {"models": [{"id": "m9"}]}}}
    result, _ = _run_model_pool_script(
        tmp_path, token_present=False, config=config,
        extra_env={"OPENCLAW_MODEL_CHAIN": "fx-a/model-1,fx-p/m9"},
    )
    assert result.returncode == 0, result.stderr
    assert result.final_config["model"] == {"primary": CHAIN[0], "fallbacks": ["fx-p/m9"]}


def test_claude_token_and_workspace_bindings_are_narrow_and_reference_only() -> None:
    source = CONFIGURE_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"CLAUDE_TOKEN_BATCH='(\[.*?\])'", source, re.DOTALL)
    assert match is not None
    assert json.loads(match.group(1)) == [
        {"path": 'agents.defaults.cliBackends["claude-cli"].command', "value": "claude"},
        {
            "path": 'agents.defaults.cliBackends["claude-cli"].env.CLAUDE_CODE_OAUTH_TOKEN',
            "value": "${CLAUDE_CODE_OAUTH_TOKEN}",
        },
    ]
    assert "${ANTHROPIC_WORKSPACE_ID}" not in source
    assert 'path: "models.providers.anthropic.headers.anthropic-workspace-id"' in source
    assert "delete env.ANTHROPIC_WORKSPACE_ID;" in source
    assert "OPENCLAW_LIVE_CLI_BACKEND_PRESERVE_ENV" not in source
    assert '"clearEnv"' not in source
    assert "auth-profiles.json" not in source


def test_toolsearch_override_matches_adapter_admission_and_keeps_defaults() -> None:
    sys.path.insert(0, str(REPO_ROOT / "services" / "openclaw-gateway-adapter"))
    import main as adapter

    source = CONFIGURE_SCRIPT.read_text(encoding="utf-8")
    batch = json.loads(re.search(r"CLAUDE_TOOLSEARCH_BATCH='(\[.*?\])'", source, re.DOTALL).group(1))
    values = {item["path"]: item["value"] for item in batch}
    prefix = 'agents.defaults.cliBackends["claude-cli"].'
    assert values[prefix + "args"] == adapter._CLAUDE_CLI_TOOLSEARCH_ARGS
    assert values[prefix + "resumeArgs"] == adapter._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS
    # OpenClaw 2026.7.1 registered defaults must all survive in both lists.
    defaults = ["-p", "--output-format", "stream-json", "--include-partial-messages", "--verbose",
                "--setting-sources", "user", "--allowedTools", "mcp__openclaw__*", "--disallowedTools",
                "ScheduleWakeup,CronCreate,Bash(run_in_background:true),Monitor"]
    assert values[prefix + "args"][:len(defaults)] == defaults
    assert values[prefix + "args"][len(defaults):] == ["--tools", "ToolSearch"]


def _run_model_pool_script(
    tmp_path, *, token_present: bool, workspace_present: bool = False,
    reject_binding: bool = False, agents=None, get_fail: bool = False, raw_state=None,
    extra_env=None, config=None,
):
    config_store = tmp_path / "gateway-config.json"
    config_store.write_text(json.dumps(
        {"models": dict(REGISTERED), "providers": {}, "cliBackends": {}, "model": None}
        if config is None else {"models": {}, "providers": {}, "cliBackends": {}, "model": None, **config}
    ))
    state = tmp_path / "agents-state.json"
    state.write_text(raw_state if raw_state is not None else json.dumps(agents) if agents is not None else "")
    command_log = tmp_path / "docker-commands.jsonl"
    fake_docker = tmp_path / "docker"
    fake_docker.write_text(
        f"#!{sys.executable}\n" + r'''
import json, os, sys
args = sys.argv[1:]
with open(os.environ["TEST_DOCKER_LOG"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
if "-e" in args:
    expr = args[args.index("-e") + 1]
    if "ANTHROPIC_WORKSPACE_ID" in expr:
        sys.exit(0)
    sys.exit(0 if os.environ["TEST_TOKEN_PRESENT"] == "1" else 1)
if "dist/index.js" not in args:
    sys.exit(0)
cli = args[args.index("dist/index.js") + 1:]
state = os.environ["TEST_STATE"]
if cli[:3] == ["config", "set", "agents.list"]:
    with open(state, "w") as out:
        out.write(cli[3])
elif cli[:2] == ["config", "set"]:
    batch = json.loads(cli[cli.index("--batch-json") + 1])
    if any("cliBackends" in op["path"] for op in batch):
        if os.environ["TEST_REJECT_BINDING"] == "1":
            sys.exit(33)
    store = json.load(open(os.environ["TEST_CONFIG"]))
    for op in batch:
        pth = op["path"]
        if pth == "agents.defaults.model.primary":
            store["model"] = {**(store["model"] if isinstance(store["model"], dict) else {}), "primary": op["value"]}
        elif pth == "agents.defaults.model.fallbacks":
            store["model"] = {**(store["model"] if isinstance(store["model"], dict) else {}), "fallbacks": op["value"]}
        elif pth.startswith('agents.defaults.models["'):
            ref = pth[len('agents.defaults.models["'):].split('"]')[0]
            if pth.endswith(".agentRuntime"):
                store["models"][ref]["agentRuntime"] = op["value"]
            else:
                store["models"][ref] = op["value"]
    json.dump(store, open(os.environ["TEST_CONFIG"], "w"))
elif cli[:2] == ["config", "get"]:
    path = cli[2]
    store = json.load(open(os.environ["TEST_CONFIG"]))
    if path == "agents.defaults.models":
        print(json.dumps(store["models"]))
    elif path == "models.providers":
        print(json.dumps(store["providers"]))
    elif path == "agents.defaults.cliBackends":
        print(json.dumps(store["cliBackends"]))
    elif path == "agents.defaults.model":
        if store["model"] is None:
            sys.exit(1)
        print(json.dumps(store["model"]))
    elif path in ("agents", "agents.list"):
        if os.environ["TEST_AGENTS_GET_FAIL"] == "1":
            sys.exit(44)
        raw = open(state).read()
        lst = json.loads(raw) if raw else None
        if path == "agents.list":
            print(json.dumps(lst))
        else:
            print(json.dumps({"defaults": {}, **({} if not raw else {"list": lst})}))
    elif path == "agents.defaults.model.primary":
        print(json.dumps(store["model"]["primary"]))
    elif path == "agents.defaults.model.fallbacks":
        print(json.dumps(store["model"].get("fallbacks", [])))
    else:
        print("true")
''', encoding="utf-8",
    )
    fake_docker.chmod(0o755)
    result = subprocess.run(
        ["bash", str(CONFIGURE_SCRIPT)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "TEST_DOCKER_LOG": str(command_log),
            "TEST_TOKEN_PRESENT": "1" if token_present else "0",
            "TEST_WORKSPACE_PRESENT": "1" if workspace_present else "0",
            "TEST_REJECT_BINDING": "1" if reject_binding else "0",
            "TEST_STATE": str(state),
            "TEST_CONFIG": str(config_store),
            "OPENCLAW_MODEL_CHAIN": ",".join(CHAIN),
            "TEST_AGENTS_GET_FAIL": "1" if get_fail else "0",
            "CLAUDE_CODE_OAUTH_TOKEN": "test-secret-never-in-argv-or-output",
            "ANTHROPIC_WORKSPACE_ID": "test-workspace-never-in-argv-or-output",
            **(extra_env or {}),
        },
        capture_output=True, text=True, timeout=10, check=False,
    )
    raw_log = command_log.read_text(encoding="utf-8")
    assert "test-secret-never-in-argv-or-output" not in raw_log + result.stdout + result.stderr
    assert "test-workspace-never-in-argv-or-output" not in raw_log + result.stdout + result.stderr
    calls = [json.loads(line) for line in raw_log.splitlines()]
    result.final_config = json.loads(config_store.read_text())
    final_state = state.read_text()
    try:
        result.final_agents = json.loads(final_state or "null")
    except json.JSONDecodeError:
        result.final_agents = final_state
    return result, calls


def test_optional_claude_token_binds_before_validate_and_restart(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=True, workspace_present=True)
    assert result.returncode == 0, result.stderr
    ws_binding = next(i for i, args in enumerate(calls) if any("anthropic-workspace-id" in arg for arg in args))
    binding = next(i for i, args in enumerate(calls) if any("CLAUDE_CODE_OAUTH_TOKEN" in arg for arg in args))
    validation = next(i for i, args in enumerate(calls) if args[-2:] == ["config", "validate"])
    restart = next(i for i, args in enumerate(calls) if "restart" in args)
    assert ws_binding < binding < validation < restart


def test_missing_token_preserves_native_cli_login_path(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=False)
    assert result.returncode == 0, result.stderr
    bound = [arg for args in calls for arg in args if "cliBackends" in arg]
    assert all("CLAUDE_CODE_OAUTH_TOKEN" not in arg for arg in bound)
    assert any('"--tools","ToolSearch"' in arg for arg in bound)
    assert any("restart" in args for args in calls)


def test_invalid_token_binding_stops_before_gateway_restart(tmp_path) -> None:
    result, calls = _run_model_pool_script(tmp_path, token_present=True, reject_binding=True)
    assert result.returncode == 33
    assert not any("restart" in args for args in calls)


def _admission(agents):
    """Run the unchanged adapter admission guard against a rendered config."""
    sys.path.insert(0, str(REPO_ROOT / "services" / "openclaw-gateway-adapter"))
    import main as adapter
    from assistant_openclaw_provider import STRUCTURED_AGENT_ID

    class Provider:
        def _gateway_call(self, *_a, **_k):
            return {"valid": True, "config": {"agents": {"list": agents, "defaults": {"cliBackends": {
                "claude-cli": {"args": adapter._CLAUDE_CLI_TOOLSEARCH_ARGS,
                               "resumeArgs": adapter._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS}}}}}}

    saved = adapter._OPENCLAW_AGENT_PROVIDER
    adapter._OPENCLAW_AGENT_PROVIDER = Provider()
    try:
        adapter._assert_structured_gateway_policy(STRUCTURED_AGENT_ID, deadline=time.monotonic() + 5)
    finally:
        adapter._OPENCLAW_AGENT_PROVIDER = saved


def test_provisioned_config_inserts_and_preserves_agents(tmp_path) -> None:
    existing = [{"id": "main", "default": True}, {"id": "persona-x", "tools": {"allow": ["exec"]}}]
    result, _ = _run_model_pool_script(tmp_path, token_present=False, agents=existing)
    assert result.returncode == 0, result.stderr
    _admission(result.final_agents)
    assert result.final_agents[:2] == existing
    # Rerun on its own output: idempotent, no duplicates.
    result2, _ = _run_model_pool_script(tmp_path, token_present=False, agents=result.final_agents)
    assert result2.returncode == 0, result2.stderr
    assert result2.final_agents == result.final_agents


def test_provisioned_config_updates_target_without_dropping_properties(tmp_path) -> None:
    existing = [
        {"id": "main", "default": True},
        {"id": "structured-extraction", "name": "Extractor", "tools": {"allow": ["read"], "alsoAllow": ["lookup"]}},
    ]
    result, _ = _run_model_pool_script(tmp_path, token_present=False, agents=existing)
    assert result.returncode == 0, result.stderr
    assert result.final_agents[0] == existing[0]
    assert result.final_agents[1] == {
        "id": "structured-extraction", "name": "Extractor",
        "tools": {"allow": ["read"], "alsoAllow": ["lookup"], "deny": ["*"],
                  "exec": {"security": "deny", "ask": "always"}},
        "model": {"primary": CHAIN[0], "fallbacks": CHAIN[1:]},
    }
    _admission(result.final_agents)


def test_existing_deny_all_agents_get_exec_policy(tmp_path) -> None:
    persona = {"id": "persona-opinion-abcdef0123456789abcdef01", "name": "P",
               "tools": {"allow": [], "deny": ["*"]}}
    existing = [{"id": "main", "default": True, "tools": {"allow": ["exec"]}}, persona]
    result, _ = _run_model_pool_script(tmp_path, token_present=False, agents=existing)
    assert result.returncode == 0, result.stderr
    assert result.final_agents[0] == existing[0]
    assert result.final_agents[1] == {**persona, "tools": {
        "allow": [], "deny": ["*"], "exec": {"security": "deny", "ask": "always"}}}
    _admission(result.final_agents)


def test_memory_and_structured_agents_follow_the_chain_and_are_deny_all(tmp_path) -> None:
    result, _ = _run_model_pool_script(tmp_path, token_present=False, agents=[{"id": "main"}])
    assert result.returncode == 0, result.stderr
    agent = next(a for a in result.final_agents if a["id"] == "memory-index")
    assert agent["tools"] == {"deny": ["*"], "exec": {"security": "deny", "ask": "always"}}
    assert agent["model"] == {"primary": CHAIN[0], "fallbacks": CHAIN[1:]}
    structured = next(a for a in result.final_agents if a["id"] == "structured-extraction")
    assert structured["model"] == {"primary": CHAIN[0], "fallbacks": CHAIN[1:]}
    assert sum(a["id"] == "memory-index" for a in result.final_agents) == 1
    # Same admission assertions as structured-extraction.
    _admission(result.final_agents)
    sys.path.insert(0, str(REPO_ROOT / "services" / "openclaw-gateway-adapter"))
    import main as adapter

    class Provider:
        def _gateway_call(self, *_a, **_k):
            return {"valid": True, "config": {"agents": {"list": result.final_agents, "defaults": {"cliBackends": {
                "claude-cli": {"args": adapter._CLAUDE_CLI_TOOLSEARCH_ARGS,
                               "resumeArgs": adapter._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS}}}}}}

    saved = adapter._OPENCLAW_AGENT_PROVIDER
    adapter._OPENCLAW_AGENT_PROVIDER = Provider()
    try:
        adapter._assert_structured_gateway_policy("memory-index", deadline=time.monotonic() + 5)
    finally:
        adapter._OPENCLAW_AGENT_PROVIDER = saved
    result2, _ = _run_model_pool_script(tmp_path, token_present=False, agents=result.final_agents)
    assert result2.final_agents == result.final_agents


def test_admission_still_rejects_agent_without_deny_all() -> None:
    import pytest

    for agents in ([{"id": "structured-extraction"}], [{"id": "main", "tools": {"deny": ["*"]}}]):
        with pytest.raises(Exception, match="verified Gateway agent"):
            _admission(agents)


def test_agents_read_failure_or_bad_shape_fails_closed(tmp_path) -> None:
    cases = (
        {"get_fail": True, "agents": [{"id": "main"}]},
        {"raw_state": '{"id": "main"}'},
        {"raw_state": "false"},
        {"raw_state": "null"},
        {"raw_state": "{malformed json"},
    )
    for index, kwargs in enumerate(cases):
        case_dir = tmp_path / str(index)
        case_dir.mkdir()
        original = kwargs.get("raw_state", json.dumps(kwargs.get("agents", [])))
        result, calls = _run_model_pool_script(case_dir, token_present=False, **kwargs)
        assert result.returncode != 0
        assert not any(
            a[-3:-1] == ["set", "agents.list"]
            or "agents.list" in a[:5] and "set" in a
            for a in calls
        )
        assert not any("restart" in a for a in calls)
        assert (case_dir / "agents-state.json").read_text() == original
