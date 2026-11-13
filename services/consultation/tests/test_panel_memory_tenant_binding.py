import json
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from services.consultation.tests.test_persona_panel_integration import _serve
from services.consultation.tests.test_persona_own_agent_routing import (
    _Fake, TENANT, TOKEN, ACTOR,
)
from services.memory import main as memory_main
from services.memory.test_main import JWT_SECRET, _make_jwt, _persona_auth_payload
from consultation_provider import (
    create_app, CONSULTATION_CONTRIBUTION_PATH, REQUIRED_OUTPUT,
)


def _turn(tmp_path, monkeypatch, *, source_tenant, scope, sentinel, reader_override=None):
    for key, value in {
        'PANTHEON_MEMORY_AUTH_MODE': 'strict',
        'PANTHEON_MEMORY_JWT_SECRET': JWT_SECRET,
        'PANTHEON_MEMORY_STORE': str(tmp_path / 'institutional.json'),
        'PANTHEON_PERSONA_MEMORY_STORE': str(tmp_path / 'persona.json'),
        'PANTHEON_MEMORY_DATA_DIR': str(tmp_path / 'memory'),
        'PANTHEON_MEMORY_STORE_BACKEND': 'json',
        'PANTHEON_PERSONA_MEMORY_STORE_BACKEND': 'json',
        'PANTHEON_MEMORY_ACTOR_ID': 'memory-operator-1',
        'PANTHEON_MEMORY_ACTOR_ROLES': 'operator',
        'PANTHEON_MEMORY_AUTHZ_MODE': 'local',
    }.items():
        monkeypatch.setenv(key, value)
    token = _make_jwt(tenant_id=source_tenant, roles=['operator'])
    monkeypatch.setenv('PANTHEON_MEMORY_AUTH_TOKEN', token)
    persona_id = 'persona-risk-officer'
    seed = TestClient(memory_main.app).post(
        '/api/memory/persona-entries',
        headers={'Authorization': 'Bearer ' + token},
        json=_persona_auth_payload(
            persona_id=persona_id, tenant_id=source_tenant,
            relevance_scope=scope, content={'summary': sentinel}),
    )
    assert seed.status_code == 201, seed.text
    for key, value in (reader_override or {}).items():
        monkeypatch.setenv(key, value)
    fake = _Fake(answer_as=persona_id)
    with _serve(memory_main.app) as memory_url:
        monkeypatch.setenv('PANTHEON_MEMORY_API_URL', memory_url)
        app = create_app(provider=fake, provider_token=TOKEN,
                         expected_service_actor=ACTOR, replay_path=tmp_path / 'replay.sqlite3')
        resp = TestClient(app).post(
            CONSULTATION_CONTRIBUTION_PATH,
            headers={'Authorization': 'Bearer ' + TOKEN,
                     'X-Pantheon-Service-Actor': ACTOR,
                     'X-Pantheon-Tenant-Id': TENANT,
                     'Idempotency-Key': 'memory-turn'},
            json={'tenant_id': TENANT, 'request_id': 'req-own',
                  'required_output': REQUIRED_OUTPUT,
                  'request': {'tenant_id': TENANT, 'request_id': 'req-own',
                              'participant': {'persona_id': persona_id,
                                              'role': 'risk_reviewer',
                                              'agent': 'persona_own_agent'},
                              'case_file': {'lesson_query': {
                                  'query': 'momentum 2330 equity trial admission'}}}})
    return resp, fake


SENTINEL = 'COMMITTEE-SENTINEL momentum 2330 equity trial admission'


def test_default_reader_never_injects_another_tenants_memory(tmp_path, monkeypatch):
    resp, fake = _turn(tmp_path, monkeypatch, source_tenant='tenant-other-than-panel',
                       scope='persona_and_committee', sentinel=SENTINEL)
    assert not any(SENTINEL in p for p in fake.prompts)


def test_default_reader_injects_same_tenant_committee_memory(tmp_path, monkeypatch):
    resp, fake = _turn(tmp_path, monkeypatch, source_tenant=TENANT,
                       scope='persona_and_committee', sentinel=SENTINEL)
    assert resp.status_code == 200, resp.text
    assert any(SENTINEL in p for p in fake.prompts)


def test_default_reader_excludes_same_tenant_private_memory(tmp_path, monkeypatch):
    resp, fake = _turn(tmp_path, monkeypatch, source_tenant=TENANT,
                       scope='persona_private', sentinel=SENTINEL)
    assert not any(SENTINEL in p for p in fake.prompts)


@pytest.mark.parametrize("override", [
    {"PANTHEON_MEMORY_AUTH_TOKEN": ""},
    {"PANTHEON_MEMORY_AUTH_TOKEN": _make_jwt(tenant_id=TENANT, roles=["researcher"])},
    {"PANTHEON_MEMORY_ACTOR_ID": "unverified-actor"},
], ids=["missing-credential", "role-exceeds-token", "actor-differs-from-token"])
def test_environment_context_cannot_grant_memory_authority(tmp_path, monkeypatch, override):
    resp, fake = _turn(tmp_path, monkeypatch, source_tenant=TENANT,
                       scope="persona_and_committee", sentinel=SENTINEL,
                       reader_override=override)
    assert resp.status_code == 200, resp.text
    assert resp.json()["contribution"]["event_content"]["committee_memory_status"] == "unavailable"
    assert not any(SENTINEL in p for p in fake.prompts)


def test_compose_supplies_committee_memory_reader_to_actual_provider():
    import yaml
    compose = yaml.safe_load(Path('docker-compose.yml').read_text())
    env = compose['services']['openclaw-gateway-adapter']['environment']
    if isinstance(env, list):
        env = dict(item.split('=', 1) for item in env if '=' in item)
    assert env.get('PANTHEON_MEMORY_API_URL') or env.get('PANTHEON_MEMORY_SERVICE_URL'), (
        'the actual panel provider has no memory address; its default reader always returns None')
