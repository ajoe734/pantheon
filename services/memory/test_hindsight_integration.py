"""HTTP-route integration against a real Hindsight + memory-index-postgres pair.

Two opt-in modes.  (1) ``PANTHEON_HINDSIGHT_IT_URL`` / ``PANTHEON_HINDSIGHT_IT_KEY`` target an existing
compose ``hindsight`` (LLM layers off).  (2) ``PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE=1`` starts real
hindsight + pgroonga/pgvector containers wired to the real OpenClaw adapter bridge with the gateway double
(``services/openclaw-gateway-adapter/test_memory_llm_bridge.py``) and exercises the LLM-layer routes.

Mode (1): opt in with ``PANTHEON_HINDSIGHT_IT_URL`` / ``PANTHEON_HINDSIGHT_IT_KEY`` pointing at
the compose ``hindsight`` service (see docker-compose.yml).  Every test uses a unique
tenant so its banks are isolated and deleted afterwards; no product data is touched.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from services.memory import hindsight_index as hs
from services.memory import main
from services.memory.institutional_memory_store import InstitutionalMemoryStore
from services.memory.persona_memory_store import PersonaMemoryStore
from services.memory.test_hindsight_index import institution, persona
from services.memory.test_main import JWT_SECRET, _make_jwt

URL = os.getenv("PANTHEON_HINDSIGHT_IT_URL", "")
KEY = os.getenv("PANTHEON_HINDSIGHT_IT_KEY", "")


@pytest.fixture
def env(monkeypatch):
    if not (URL and KEY):
        pytest.skip("set PANTHEON_HINDSIGHT_IT_URL and PANTHEON_HINDSIGHT_IT_KEY")
    tenant = "it-" + uuid.uuid4().hex[:10]
    stores = (InstitutionalMemoryStore(), PersonaMemoryStore())
    monkeypatch.setattr(main, "_store", lambda: stores[0])
    monkeypatch.setattr(main, "_persona_store", lambda: stores[1])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv(hs.URL_ENV, URL)
    monkeypatch.setenv(hs.KEY_ENV, KEY)
    monkeypatch.delenv(hs.LLM_LAYERS_ENV, raising=False)
    hs.set_hindsight_client(None)
    hs.DIAGNOSTICS.reset()
    client = hs.HindsightClient(URL, KEY)
    yield TestClient(main.app), stores, tenant, client
    hs.flush_mirrors()
    for bank in (hs.facts_bank(tenant), hs.persona_bank(tenant, "persona-alpha"), hs.persona_bank(tenant, "persona-beta")):
        client.delete_bank(bank)


def auth(tenant, roles=("operator",)):
    return {"Authorization": "Bearer " + _make_jwt(tenant_id=tenant, roles=list(roles))}


def retrieve(client, tenant, roles=("operator",), **params):
    base = {"actor_id": "memory-operator-1", "actor_roles": list(roles), "session_id": "it-session",
            "scope": "institutional", "query": ""}
    base.update(params)
    return client.get("/api/memory/retrieve", params=base, headers=auth(tenant, roles))


def ids(response):
    assert response.status_code == 200, response.text
    return [h["entry"].get("entry_id") or h["entry"].get("memory_id") for h in response.json()["hits"]]


def test_traditional_chinese_recall_through_http_route_with_anchor_filtering(env):
    api, (inst, _), tenant, _client = env
    tw = inst.create(institution(tenant_id=tenant, content={
        "headline": "台灣股票風控檢討", "body": "市場波動上升，流動性不足時應降低台灣股票曝險",
        "tags": ["underlying:TW"]}))
    other = inst.create(institution(tenant_id=tenant, content={
        "headline": "外匯部位檢討", "body": "美元兌日圓部位於波動上升時應縮減",
        "tags": ["underlying:JPY"]}))
    assert hs.flush_mirrors()
    assert hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    response = retrieve(api, tenant, query="台灣股票風險曝險")
    assert tw.entry_id in ids(response)
    body = response.json()
    assert body["retrieval_mode"] == "hindsight" and body["fallback_reason"] is None
    assert body["llm_layers"] == "disabled"
    filtered = retrieve(api, tenant, query="部位波動", subject_anchors=["underlying:JPY"])
    assert ids(filtered) == [other.entry_id]


def test_supersede_and_archive_invalidate_in_the_index(env):
    api, (inst, _), tenant, client = env
    old = inst.create(institution(tenant_id=tenant))
    new = inst.create(institution(tenant_id=tenant, source_event_id="pm-new"))
    assert hs.flush_mirrors()
    bank = hs.facts_bank(tenant)
    assert client.memory_units(bank, old.entry_id)
    inst.supersede(old.entry_id, new.entry_id)
    assert hs.flush_mirrors()
    assert client.memory_units(bank, old.entry_id) == []
    assert client.memory_units(bank, new.entry_id)
    recalled = hs.recall_canonical_ids(client, bank, query="流動性風控", groups=[], limit=10)
    assert [cid for cid, _ in recalled] == [new.entry_id]
    inst.archive(new.entry_id, reason="obsolete")
    assert hs.flush_mirrors()
    assert hs.recall_canonical_ids(client, bank, query="流動性風控", groups=[], limit=10) == []


def test_persona_never_sees_other_persona_and_committee_only_gets_committee_scope(env):
    api, (_, per), tenant, _client = env
    private = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_private",
                                 content={"summary": "我的私人解讀：流動性不足時降低曝險", "tags": []}))
    shared = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_and_committee",
                                content={"summary": "可供委員會的解讀：流動性不足時降低曝險", "tags": []}))
    beta = per.create(persona(tenant_id=tenant, persona_id="persona-beta", relevance_scope="persona_and_committee",
                              content={"summary": "貝塔的解讀：流動性不足時降低曝險", "tags": []}))
    assert hs.flush_mirrors()
    own = retrieve(api, tenant, scope="persona", persona_id="persona-alpha", query="流動性不足降低曝險")
    assert set(ids(own)) == {private.memory_id, shared.memory_id}
    assert own.json()["retrieval_mode"] == "hindsight"
    theirs = retrieve(api, tenant, scope="persona", persona_id="persona-beta", query="流動性不足降低曝險")
    assert ids(theirs) == [beta.memory_id]
    committee = retrieve(api, tenant, roles=("consultation_session",), scope="persona", persona_id="persona-alpha",
                         query="流動性不足降低曝險", session_persona_id="persona-alpha")
    assert committee.status_code == 200, committee.text
    assert ids(committee) == [shared.memory_id]
    # Index-level proof: beta's bank never holds alpha's documents.
    bank = hs.persona_bank(tenant, "persona-beta")
    assert not _client.memory_units(bank, private.memory_id) and not _client.memory_units(bank, shared.memory_id)


def test_rebuild_restores_a_dropped_bank_idempotently(env):
    api, (inst, per), tenant, client = env
    live = inst.create(institution(tenant_id=tenant))
    gone = inst.create(institution(tenant_id=tenant, source_event_id="pm-gone"))
    inst.archive(gone.entry_id, reason="obsolete")
    assert hs.flush_mirrors()
    bank = hs.facts_bank(tenant)
    client.delete_bank(bank)
    assert client.memory_units(bank, live.entry_id) == []
    first = hs.rebuild(inst, per)
    second = hs.rebuild(inst, per)
    assert first["failures"] == [] and first["retained"] == second["retained"] == 1
    assert client.memory_units(bank, live.entry_id) and not client.memory_units(bank, gone.entry_id)
    assert ids(retrieve(api, tenant, query="流動性風控")) == [live.entry_id]


def test_unreachable_index_falls_back_explicitly_without_blocking_canonical_state(env, monkeypatch):
    api, (inst, _), tenant, _client = env
    monkeypatch.setenv(hs.URL_ENV, "http://127.0.0.1:1")
    monkeypatch.setattr(hs.time, "sleep", lambda *_: None)
    entry = inst.create(institution(tenant_id=tenant))
    assert inst.get(entry.entry_id) is entry
    assert hs.flush_mirrors()
    assert hs.DIAGNOSTICS.snapshot()["failures"][0]["reason"] == "hindsight_unreachable"
    body = retrieve(api, tenant, query="流動性風控").json()
    assert body["retrieval_mode"] == "keyword_fallback" and body["fallback_reason"] == "hindsight_unreachable"


# --- LLM layers: real hindsight + pg containers, real adapter bridge, gateway double ----------------
PROBE = os.getenv("PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE") == "1"
ADAPTER_DIR = os.path.join(os.path.dirname(__file__), "..", "openclaw-gateway-adapter")


@pytest.fixture(scope="module")
def layered_stack():
    """Compose-equivalent hindsight (chunks, consolidation off, worker on, bridge providers) behind the bridge."""
    if not PROBE:
        pytest.skip("set PANTHEON_RUN_HINDSIGHT_CONTAINER_PROBE=1 (starts real hindsight + postgres containers)")
    import json
    import secrets
    import socket
    import subprocess
    import sys
    import threading
    import time

    sys.path.insert(0, os.path.abspath(ADAPTER_DIR))
    import uvicorn
    import main as adapter
    from assistant_openclaw_provider import AssistantOpenClawProvider
    from memory_llm_bridge import BridgeConfig, MemoryLlmBridge
    from test_memory_llm_bridge import GatewayDouble, deny_all_policy_snapshot

    hs_image = os.getenv("HINDSIGHT_IMAGE", "pantheon-case-file-hindsight:local")
    pg_image = os.getenv("PG_IMAGE", "pantheon-hindsight-eval-pg:pgroonga-pgvector")
    tag = "memllm-it-" + secrets.token_hex(4)
    net, pg, hsc = f"{tag}-net", f"{tag}-pg", f"{tag}-hs"
    api_key, bridge_token = secrets.token_hex(8), secrets.token_hex(16)
    state = {"mode": "ok", "log": []}

    def sh(*args, check=True):
        return subprocess.run(args, text=True, capture_output=True, check=check)

    def instance(schema, hints):
        """A minimal value satisfying a JSON schema (the gateway double's stand-in for a model answer)."""
        if "anyOf" in schema:
            return instance(next((x for x in schema["anyOf"] if x.get("type") != "null"), schema["anyOf"][0]), hints)
        if "enum" in schema:
            return schema["enum"][0]
        kind = schema.get("type")
        if kind == "object":
            return {k: (hints[k] if k in hints else instance(v, hints)) for k, v in (schema.get("properties") or {}).items()
                    if k in (schema.get("required") or []) or k in hints}
        if kind == "array":
            return []
        return {"string": "x", "integer": 1, "number": 1, "boolean": True, "null": None}.get(kind)

    def reply(agent_id, request):
        text = json.dumps(request)
        state["log"].append(text)
        if state["mode"] == "refuse":
            raise RuntimeError("scripted refusal")
        parts = [m.get("content", "") for m in request.get("input", []) if isinstance(m.get("content"), str)]
        system = " ".join(parts)
        if "Extract SIGNIFICANT facts" in system:
            return json.dumps({"facts": [{"when": "N/A", "where": "N/A", "who": "N/A", "why": "N/A", "fact_kind": "conversation",
                                          "fact_type": "world", "entities": []}]})
        marker = "satisfies this JSON schema:\n"
        if marker in system and "reflection agent" in system:
            schema = json.loads(next(p for p in parts if marker in p).split(marker, 1)[1])
            state.setdefault("schemas", []).append(sorted((schema.get("properties") or {})))
            return json.dumps(instance(schema, {"query": "動能策略 流動性", "reason": "search", "answer": "動能策略在流動性不足時應降低台灣股票曝險。"}))
        return json.dumps({"creates": [], "updates": [], "deletes": []})

    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 0))
        port = sock.getsockname()[1]
    mp = pytest.MonkeyPatch()
    with GatewayDouble(reply) as double:
        provider = AssistantOpenClawProvider(gateway_url=double.url, token=double.token)
        mp.setattr(provider, "_gateway_call", lambda method, *a, **k: deny_all_policy_snapshot(
            "memory-index", toolsearch_args=adapter._CLAUDE_CLI_TOOLSEARCH_ARGS,
            toolsearch_resume_args=adapter._CLAUDE_CLI_TOOLSEARCH_RESUME_ARGS))
        mp.setattr(adapter, "_OPENCLAW_AGENT_PROVIDER", provider)
        mp.setattr(adapter, "_MEMORY_LLM_BRIDGE", MemoryLlmBridge(BridgeConfig(
            enabled=True, token=bridge_token, daily_budget=1000, max_concurrency=2)))
        server = uvicorn.Server(uvicorn.Config(adapter.app, host="0.0.0.0", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        assert server.started
        try:
            sh("docker", "network", "create", net)
            sh("docker", "run", "-d", "--name", pg, "--network", net, "-e", "POSTGRES_USER=u",
               "-e", "POSTGRES_PASSWORD=p", "-e", "POSTGRES_DB=mem", pg_image)
            for _ in range(60):
                if sh("docker", "exec", pg, "pg_isready", "-U", "u", "-d", "mem", check=False).returncode == 0:
                    break
                time.sleep(1)
            time.sleep(2)
            base = f"http://host.docker.internal:{port}/internal/memory-llm/v1"
            env = {
                "HINDSIGHT_API_DATABASE_URL": f"postgresql://u:p@{pg}:5432/mem",
                "HINDSIGHT_API_TEXT_SEARCH_EXTENSION": "pgroonga", "HINDSIGHT_API_VECTOR_EXTENSION": "pgvector",
                "HINDSIGHT_API_RUN_MIGRATIONS_ON_STARTUP": "true", "HINDSIGHT_API_MCP_ENABLED": "false",
                "HINDSIGHT_API_TENANT_EXTENSION": "hindsight_api.extensions.builtin.tenant:ApiKeyTenantExtension",
                "HINDSIGHT_API_TENANT_API_KEY": api_key,
                # compose defaults with the bridge configured: chunks + no auto-consolidation + worker on
                "HINDSIGHT_API_RETAIN_EXTRACTION_MODE": "chunks", "HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION": "false",
                "HINDSIGHT_API_WORKER_ENABLED": "true", "OMP_NUM_THREADS": "2",
                "HINDSIGHT_API_LLM_PROVIDER": "openai", "HINDSIGHT_API_LLM_MODEL": "memory-index",
                "HINDSIGHT_API_LLM_API_KEY": bridge_token, "HINDSIGHT_API_LLM_BASE_URL": base,
            }
            for op in ("RETAIN", "CONSOLIDATION", "REFLECT", "MENTAL_MODEL_REFRESH"):
                env.update({f"HINDSIGHT_API_{op}_LLM_PROVIDER": "openai", f"HINDSIGHT_API_{op}_LLM_MODEL": "memory-index",
                            f"HINDSIGHT_API_{op}_LLM_API_KEY": bridge_token, f"HINDSIGHT_API_{op}_LLM_BASE_URL": base})
            args = ["docker", "run", "-d", "--name", hsc, "--network", net, "-p", "127.0.0.1::8888",
                    "--add-host", "host.docker.internal:host-gateway"]
            for k, v in env.items():
                args += ["-e", f"{k}={v}"]
            sh(*args, hs_image)
            host_port = sh("docker", "port", hsc, "8888/tcp").stdout.strip().splitlines()[0].rsplit(":", 1)[1]
            url = f"http://127.0.0.1:{host_port}"
            ready = False
            for _ in range(180):
                if sh("docker", "exec", hsc, "python", "-m", "hindsight_api.http_probe",
                      "http://127.0.0.1:8888/health", "5", check=False).returncode == 0:
                    ready = True
                    break
                time.sleep(2)
            assert ready, sh("docker", "logs", "--tail", "40", hsc, check=False).stderr
            yield {"url": url, "key": api_key, "double": double, "state": state, "container": hsc}
        finally:
            for c in (hsc, pg):
                sh("docker", "rm", "-f", c, check=False)
            sh("docker", "network", "rm", net, check=False)
            server.should_exit = True
            thread.join(timeout=5)
            mp.undo()


@pytest.fixture
def layered(layered_stack, monkeypatch):
    tenant = "it-" + uuid.uuid4().hex[:10]
    stores = (InstitutionalMemoryStore(), PersonaMemoryStore())
    monkeypatch.setattr(main, "_store", lambda: stores[0])
    monkeypatch.setattr(main, "_persona_store", lambda: stores[1])
    monkeypatch.setenv("PANTHEON_MEMORY_AUTH_MODE", "strict")
    monkeypatch.setenv("PANTHEON_MEMORY_JWT_SECRET", JWT_SECRET)
    monkeypatch.setenv("PANTHEON_MEMORY_AUTHZ_MODE", "local")
    monkeypatch.setenv(hs.URL_ENV, layered_stack["url"])
    monkeypatch.setenv(hs.KEY_ENV, layered_stack["key"])
    hs.set_hindsight_client(None)
    hs.DIAGNOSTICS.reset()
    layered_stack["state"]["mode"] = "ok"
    client = hs.HindsightClient(layered_stack["url"], layered_stack["key"])
    yield TestClient(main.app), stores, tenant, client, layered_stack
    hs.flush_mirrors()
    for persona_id in ("persona-alpha", "persona-beta"):
        client.delete_bank(hs.persona_bank(tenant, persona_id))
    client.delete_bank(hs.facts_bank(tenant))


def _bank_config(client, bank):
    return (client._call("GET", client._bank(bank) + "/config") or {}).get("config") or {}


def _svc(tenant):
    return {"Authorization": "Bearer " + _make_jwt(tenant_id=tenant, roles=["persona-svc"])}


def test_layers_unset_keeps_chunks_and_reports_disabled(layered, monkeypatch):
    api, (inst, _), tenant, client, stack = layered
    monkeypatch.delenv(hs.LLM_LAYERS_ENV, raising=False)
    entry = inst.create(institution(tenant_id=tenant))
    assert hs.flush_mirrors() and hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    assert _bank_config(client, hs.facts_bank(tenant)).get("retain_extraction_mode") == "chunks"
    body = retrieve(api, tenant, query="流動性風控").json()
    assert body["llm_layers"] == "disabled" and entry.entry_id in [h["entry"]["entry_id"] for h in body["hits"]]
    declared = api.put("/api/memory/persona-standing-questions", headers=_svc(tenant),
                       json={"persona_id": "persona-alpha", "questions": []})
    assert declared.status_code == 503 and declared.json()["detail"]["reason"] == "llm_layers_disabled"
    assert stack["double"].requests == []  # nothing reached the bridge


def test_layers_enabled_use_verbatim_and_consolidation_and_a_refusal_never_fails_canonical_work(layered, monkeypatch):
    api, (inst, per), tenant, client, stack = layered
    monkeypatch.setenv(hs.LLM_LAYERS_ENV, "true")
    monkeypatch.setattr(hs, "_LAYERED_BANKS", set())
    monkeypatch.setattr(hs.time, "sleep", lambda *_: None)
    entry = inst.create(institution(tenant_id=tenant))
    assert hs.flush_mirrors() and hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    config = _bank_config(client, hs.facts_bank(tenant))
    assert config["retain_extraction_mode"] == "verbatim" and config["enable_auto_consolidation"] is True
    body = retrieve(api, tenant, query="流動性風控").json()
    assert body["llm_layers"] == "enabled" and body["retrieval_mode"] == "hindsight"
    assert entry.entry_id in [h["entry"]["entry_id"] for h in body["hits"]]
    # The bridge refuses everything: canonical writes and recall still succeed, background work is merely deferred.
    stack["state"]["mode"] = "refuse"
    second = inst.create(institution(tenant_id=tenant, source_event_id="pm-refused"))
    assert hs.flush_mirrors()
    assert inst.get(second.entry_id) is second
    assert retrieve(api, tenant, query="流動性風控").status_code == 200


def _wait(predicate, seconds=150):
    import time
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(3)
    return predicate()


def _read_questions(api, tenant, persona_id, token_tenant=None):
    return api.get("/api/memory/persona-standing-questions", params={
        "persona_id": persona_id, "actor_id": "memory-operator-1", "actor_roles": ["operator"], "session_id": "it"},
        headers=auth(token_tenant or tenant))


def test_standing_questions_declare_refresh_after_consolidation_and_read_with_canonical_citations(layered, monkeypatch):
    api, (_, per), tenant, client, stack = layered
    monkeypatch.setenv(hs.LLM_LAYERS_ENV, "true")
    monkeypatch.setenv("PANTHEON_MEMORY_MENTAL_MODEL_MIN_REFRESH_SECONDS", "0")
    monkeypatch.setattr(hs, "_LAYERED_BANKS", set())
    mine = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_private",
                              content={"summary": "動能策略在流動性不足時應降低台灣股票曝險", "tags": ["family:momentum"]}))
    theirs = per.create(persona(tenant_id=tenant, persona_id="persona-beta", relevance_scope="persona_private",
                                content={"summary": "BETA-ONLY 動能策略的私人解讀", "tags": ["family:momentum"]}))
    assert hs.flush_mirrors() and hs.DIAGNOSTICS.snapshot()["counts"]["failed"] == 0
    questions = [{"key": "family:momentum", "anchors": ["family:momentum"], "text": "動能策略有哪些教訓？"},
                 {"key": "risk_lessons", "memory_types": ["risk_observation"], "text": "有哪些風控教訓？"}]
    for persona_id in ("persona-alpha", "persona-beta"):
        declared = api.put("/api/memory/persona-standing-questions", headers=_svc(tenant),
                           json={"persona_id": persona_id, "questions": questions})
        assert declared.status_code == 200, declared.text
        assert sorted(declared.json()["created"]) == ["family:momentum", "risk_lessons"]
    again = api.put("/api/memory/persona-standing-questions", headers=_svc(tenant),
                    json={"persona_id": "persona-alpha", "questions": questions}).json()
    assert again["created"] == [] and again["updated"] == [] and len(again["unchanged"]) == 2
    bank = hs.persona_bank(tenant, "persona-alpha")

    def answered():
        body = _read_questions(api, tenant, "persona-alpha").json()
        by_key = {a["key"]: a for a in body["answers"]}
        return by_key if by_key.get("family:momentum", {}).get("status") == "answered" else None

    by_key = _wait(answered)
    assert by_key, _read_questions(api, tenant, "persona-alpha").text
    family = by_key["family:momentum"]
    assert family["citations"] == [mine.memory_id] and family["text"].strip() and family["refreshed_at"]
    assert by_key["risk_lessons"]["status"] == "unavailable" and by_key["risk_lessons"]["reason"] == "no_sources_in_scope"
    # Refresh follows consolidation: a new retained memory is consolidated and the model refreshes again.
    first_refresh = family["refreshed_at"]
    second = per.create(persona(tenant_id=tenant, persona_id="persona-alpha", relevance_scope="persona_private",
                                source_event_id="pm-second", content={"summary": "動能策略的第二則私人解讀", "tags": ["family:momentum"]}))
    assert hs.flush_mirrors()

    def refreshed_again():
        body = _read_questions(api, tenant, "persona-alpha").json()
        item = {a["key"]: a for a in body["answers"]}["family:momentum"]
        return item if item["status"] == "answered" and item["refreshed_at"] > first_refresh else None

    item = _wait(refreshed_again)
    assert item and set(item["citations"]) <= {mine.memory_id, second.memory_id} and second.memory_id in item["citations"]
    operations = client._call("GET", client._bank(bank) + "/operations")["operations"]
    assert any(op["operation_type"] == "consolidation" for op in operations)
    # Isolation: beta's answers are beta's own; alpha's never leak across personas or tenants.
    beta = _read_questions(api, tenant, "persona-beta")
    assert beta.status_code == 200 and "BETA-ONLY" not in _read_questions(api, tenant, "persona-alpha").text
    assert mine.memory_id not in beta.text and "動能策略在流動性不足" not in beta.text
    other_tenant = _read_questions(api, "it-other-" + uuid.uuid4().hex[:6], "persona-alpha")
    assert other_tenant.status_code == 200 and other_tenant.json()["answers"] == []
    assert theirs.memory_id not in _read_questions(api, tenant, "persona-alpha").text
    # Answers exist only in Hindsight: the canonical persona store is unchanged and never holds answer text.
    stored = per.list(active_only=False)
    assert {e.memory_id for e in stored} == {mine.memory_id, theirs.memory_id, second.memory_id}
    assert all(family["text"] not in json.dumps(e.to_dict(), ensure_ascii=False) for e in stored)
    # Retire: dropping a key removes the model; the persona keeps only the declared set.
    retired = api.put("/api/memory/persona-standing-questions", headers=_svc(tenant),
                      json={"persona_id": "persona-alpha", "questions": [questions[1]]}).json()
    assert retired["retired"] == ["family:momentum"]
    assert [m["name"] for m in client.list_mental_models(bank)] == ["risk_lessons"]
    # Default minimum refresh interval is one day when configuration is absent.
    monkeypatch.delenv("PANTHEON_MEMORY_MENTAL_MODEL_MIN_REFRESH_SECONDS")
    api.put("/api/memory/persona-standing-questions", headers=_svc(tenant),
            json={"persona_id": "persona-beta", "questions": [{**questions[0], "text": "動能策略的第二個問題？"}]})
    assert [m["trigger"]["min_refresh_interval_seconds"] for m in client.list_mental_models(
        hs.persona_bank(tenant, "persona-beta"))] == [86400]
