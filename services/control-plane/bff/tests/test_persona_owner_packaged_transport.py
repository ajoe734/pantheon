"""Exercise provisioning transport through the production package import."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import sys
import threading


def test_packaged_transport_handles_owner_readback_and_compensation() -> None:
    calls: list[tuple[str, str, object]] = []

    class Owner(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def respond(self, status: int, value: object) -> None:
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            calls.append(("GET", self.path, None))
            if self.path == "/missing":
                self.respond(404, {"error": "not_found"})
            elif self.path == "/unavailable":
                self.respond(503, {"error": "unavailable"})
            else:
                self.respond(200, {"id": "binding", "status": "suspended"})

        def write_receipt(self) -> None:
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.command, self.path, value))
            self.respond(200, {"id": "binding", **value})

        do_POST = write_receipt
        do_PATCH = write_receipt

    server = ThreadingHTTPServer(("127.0.0.1", 0), Owner)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        # A fresh process avoids legacy test path aliases and main.py's test
        # facade masking missing imports in the canonical Persona service.
        result = subprocess.run(
            [sys.executable, "-c", """
import os, sys
from urllib.error import HTTPError
from services.control_plane.bff.personas.service import (
    _PersonaOwnerHttpTransport, build_pm12_allocation_policy_input,
)
policy = build_pm12_allocation_policy_input({'overall_score': 80, 'tier': 'tier-2'})
assert policy['rank_score'] == 80
assert policy['allocation_tier'] == 'a'
os.environ['PANTHEON_CAPITAL_API_URL'] = sys.argv[1]
transport = _PersonaOwnerHttpTransport()
assert transport.get('capital', '/missing') is None
try:
    transport.get('capital', '/unavailable')
except HTTPError as exc:
    assert exc.code == 503
else:
    raise AssertionError('owner failure was swallowed')
assert transport.post('capital', '/binding', {'status': 'active'})['status'] == 'active'
assert transport.patch('capital', '/binding', {'status': 'suspended'})['status'] == 'suspended'
assert transport.get('capital', '/binding')['status'] == 'suspended'
""", f"http://127.0.0.1:{server.server_port}"],
            cwd=Path(__file__).resolve().parents[4],
            capture_output=True,
            text=True,
            timeout=40,
        )
        assert result.returncode == 0, result.stderr
        assert calls == [
            ("GET", "/missing", None),
            ("GET", "/unavailable", None),
            ("POST", "/binding", {"status": "active"}),
            ("PATCH", "/binding", {"status": "suspended"}),
            ("GET", "/binding", None),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_packaged_route_safe_early_name_error_replay_fresh_process() -> None:
    """Reproduce actual governed bootstrap terminal-failure replay with durable store
    and packaged BFF routing in a fresh process.

    Proves that a historical NameError failed record (with empty references and null
    compensation) safely replays through forward coordination to schedule_registered,
    and fresh process durable readback confirms persistence.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys, tempfile
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ['PANTHEON_ENV'] = 'dev'
os.environ['PANTHEON_BFF_AUTH_MODE'] = 'permissive'
os.environ['PANTHEON_BFF_AUTH_STUB'] = 'true'

from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.service import (
    _persona_create_canonical_payload,
    _stable_json_hash,
    _normalize_persona_create_name,
    _persona_create_identity,
)
from services.control_plane.bff.ports import create_persona_registry_write_owner, create_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    MemoryProvisioningBackend,
    ProvisioningRecord,
)
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _schedule_receipt,
)

class FakeRankingWriteOwner:
    def put_ranking_snapshot(self, snapshot):
        return {'status': 'created', 'snapshot_id': 's-1', 'snapshot': snapshot}
    def get_ranking_snapshot(self, sid):
        return None
    def list_ranking_snapshots(self):
        return []

backend = MemoryProvisioningBackend()
store = MemoryPersonaProvisioningStore(backend=backend)

payload = {
    'name': 'Pantheon Dev Paper Baseline 3',
    'archetype': 'momentum',
    'risk': 'low',
    'mandate': 'Paper-only lifecycle verification in dev',
    'market': 'US',
    'strategy_family': 'dev_paper_baseline',
}
canonical_payload = _persona_create_canonical_payload(
    payload,
    name=payload['name'],
    tenant_id='tenant-dev',
    requested_by='op-2',
)
request_hash = _stable_json_hash({
    'route': 'POST /bff/personas',
    'tenant_id': 'tenant-dev',
    'payload': canonical_payload,
})
normalized_name = _normalize_persona_create_name(payload['name'])
persona_id = _persona_create_identity('tenant-dev', normalized_name)

# Seed historical failure: current_step=capital_pool_failed, error has NameError,
# references is empty, compensation is None, attempt_count is 4
record, _ = store.reserve(
    tenant_id='tenant-dev',
    idempotency_key='dev-paper-bootstrap-20260720-operator-a-v3',
    request_hash=request_hash,
    normalized_name=normalized_name,
    persona_id=persona_id,
    request_payload=canonical_payload,
)
failed = store.acquire('tenant-dev', 'dev-paper-bootstrap-20260720-operator-a-v3', lease_owner='prior-worker', lease_seconds=60)
failed.state = 'failed'
failed.current_step = 'capital_pool_failed'
failed.error = {
    'failed_at': '2026-09-04T11:52:48Z',
    'error_type': 'NameError',
    'failed_step': 'capital_pool',
    'terminal_reason': "name 'urllib_error' is not defined",
    'compensation_error': "name 'urllib_error' is not defined",
}
failed.references = {}
failed.compensation = None
failed.attempt_count = 4
failed = store.checkpoint(failed, lease_owner='prior-worker', lease_seconds=60)
store.release(failed, lease_owner='prior-worker', lease_seconds=60)

# Wire second store instance into personas service to prove shared durable backing
transport = FakeOwnerTransport()
store2 = MemoryPersonaProvisioningStore(backend=backend)
personas_service._PERSONA_PROVISIONING_STORE = store2
personas_service._PersonaOwnerHttpTransport = lambda: transport
personas_service._register_persona_cron_required = _schedule_receipt

with tempfile.TemporaryDirectory() as td:
    write_owner = create_persona_registry_write_owner()
    read_store = create_read_surface_ports(persona_registry_store=write_owner)
    command_store = CommandStore(os.path.join(td, 'commands.jsonl'))
    service = PersonaService(
        write_owner=write_owner,
        ranking_write_owner=FakeRankingWriteOwner(),
        read_store=read_store,
        command_store=command_store,
    )
    router = create_personas_router(service=service)
    app = FastAPI(title='Persona Test App')
    app.include_router(router)
    client = TestClient(app)

    # Replay through the public packaged route
    resp = client.post(
        '/bff/management/personas/create-paper-bundle',
        json=payload,
        headers={
            'Authorization': 'Bearer op-2:operator:tenant-dev',
            'Idempotency-Key': 'dev-paper-bootstrap-20260720-operator-a-v3',
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()['data']
    meta = resp.json()['meta']
    assert data['state'] == 'provisioning'
    assert data['capitalMode'] == 'paper'
    assert meta['provisioning_state'] == 'provisioning'
    assert meta['provisioning_step'] == 'schedule_registered'
    assert meta['live_capital_side_effects'] is False

    # Fresh process durable readback from third store instance
    store3 = MemoryPersonaProvisioningStore(backend=backend)
    rec = store3.get('tenant-dev', 'dev-paper-bootstrap-20260720-operator-a-v3')
    assert rec is not None
    assert rec.state == 'provisioning'
    assert rec.current_step == 'schedule_registered'
    assert rec.attempt_count == 5
    assert rec.error is None
    assert rec.compensation is None
    assert 'capital_pool' in rec.references
    assert 'persona_capital_binding_created' in rec.references
    assert 'deployment_dispatch' in rec.references
    assert 'first_evaluation_schedule' in rec.references
""",
        ],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr


def test_packaged_route_rejects_unsafe_binding_side_effects_fresh_process() -> None:
    """Fail-closed boundary: an existing failed record with committed binding references
    must return 502 UPSTREAM_ERROR and NEVER forward retry.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os, sys, tempfile
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ['PANTHEON_ENV'] = 'dev'
os.environ['PANTHEON_BFF_AUTH_MODE'] = 'permissive'
os.environ['PANTHEON_BFF_AUTH_STUB'] = 'true'

from services.control_plane.bff.personas import PersonaService, create_personas_router
from services.control_plane.bff.personas import service as personas_service
from services.control_plane.bff.personas.service import (
    _persona_create_canonical_payload,
    _stable_json_hash,
    _normalize_persona_create_name,
    _persona_create_identity,
)
from services.control_plane.bff.ports import create_persona_registry_write_owner, create_read_surface_ports
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.persona_provisioning import (
    MemoryPersonaProvisioningStore,
    MemoryProvisioningBackend,
)
from services.control_plane.bff.test_persona_provisioning_coordinator import (
    FakeOwnerTransport,
    _schedule_receipt,
)

class FakeRankingWriteOwner:
    def put_ranking_snapshot(self, snapshot):
        return {'status': 'created', 'snapshot_id': 's-1', 'snapshot': snapshot}
    def get_ranking_snapshot(self, sid):
        return None
    def list_ranking_snapshots(self):
        return []

backend = MemoryProvisioningBackend()
store = MemoryPersonaProvisioningStore(backend=backend)

payload = {
    'name': 'Pantheon Dev Paper Baseline Unsafe',
    'archetype': 'momentum',
    'risk': 'low',
    'mandate': 'Paper-only lifecycle verification in dev',
    'market': 'US',
    'strategy_family': 'dev_paper_baseline',
}
canonical_payload = _persona_create_canonical_payload(
    payload,
    name=payload['name'],
    tenant_id='tenant-dev',
    requested_by='op-2',
)
request_hash = _stable_json_hash({
    'route': 'POST /bff/personas',
    'tenant_id': 'tenant-dev',
    'payload': canonical_payload,
})
normalized_name = _normalize_persona_create_name(payload['name'])
persona_id = _persona_create_identity('tenant-dev', normalized_name)

# Seed failure with committed binding references
record, _ = store.reserve(
    tenant_id='tenant-dev',
    idempotency_key='dev-paper-bootstrap-unsafe-v1',
    request_hash=request_hash,
    normalized_name=normalized_name,
    persona_id=persona_id,
    request_payload=canonical_payload,
)
failed = store.acquire('tenant-dev', 'dev-paper-bootstrap-unsafe-v1', lease_owner='prior-worker', lease_seconds=60)
failed.state = 'failed'
failed.current_step = 'persona_capital_binding_created_failed'
failed.error = {
    'failed_step': 'persona_capital_binding_created',
    'terminal_reason': 'binding write error',
}
failed.references = {
    'capital_pool': {'pool_id': 'pool-1', 'status': 'active'},
    'persona_capital_binding_created': {'binding_id': 'pcb-1', 'status': 'pending'},
}
failed.compensation = None
failed = store.checkpoint(failed, lease_owner='prior-worker', lease_seconds=60)
store.release(failed, lease_owner='prior-worker', lease_seconds=60)

transport = FakeOwnerTransport()
store2 = MemoryPersonaProvisioningStore(backend=backend)
personas_service._PERSONA_PROVISIONING_STORE = store2
personas_service._PersonaOwnerHttpTransport = lambda: transport
personas_service._register_persona_cron_required = _schedule_receipt

with tempfile.TemporaryDirectory() as td:
    write_owner = create_persona_registry_write_owner()
    read_store = create_read_surface_ports(persona_registry_store=write_owner)
    command_store = CommandStore(os.path.join(td, 'commands.jsonl'))
    service = PersonaService(
        write_owner=write_owner,
        ranking_write_owner=FakeRankingWriteOwner(),
        read_store=read_store,
        command_store=command_store,
    )
    router = create_personas_router(service=service)
    app = FastAPI(title='Persona Test App')
    app.include_router(router)
    client = TestClient(app)

    resp = client.post(
        '/bff/management/personas/create-paper-bundle',
        json=payload,
        headers={
            'Authorization': 'Bearer op-2:operator:tenant-dev',
            'Idempotency-Key': 'dev-paper-bootstrap-unsafe-v1',
        },
    )
    # Must fail closed with 502 UPSTREAM_ERROR
    assert resp.status_code == 502, f"Expected 502, got {resp.status_code}: {resp.text}"
    body = resp.json()
    err = body.get('error') or body.get('detail', {}).get('error', {})
    assert err['code'] == 'UPSTREAM_ERROR'
    assert err['details']['provisioningState'] in {'failed', 'compensated'}

    # Verify durable store is still terminal
    store3 = MemoryPersonaProvisioningStore(backend=backend)
    rec = store3.get('tenant-dev', 'dev-paper-bootstrap-unsafe-v1')
    assert rec.state in {'failed', 'compensated'}
    assert 'deployment_dispatch' not in rec.references
""",
        ],
        cwd=Path(__file__).resolve().parents[4],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
