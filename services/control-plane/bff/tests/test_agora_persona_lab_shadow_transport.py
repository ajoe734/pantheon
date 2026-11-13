"""Regression: shadow runs through the PRODUCTION default transport outlive the 3s read timeout."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from services.persona.test_harness import system, headers, initialize
from services.persona.test_harness_shadow_eval import REPLAY_AGENT, DENY_ALL_POLICY, remove, seed, turn
from services.persona.harness_shadow_eval import DenyAllReplayProvider
from services.control_plane.bff.agora.persona_lab.router import create_persona_lab_router


def test_default_transport_waits_for_long_shadow_run(system, tmp_path, monkeypatch):
    owner_client, owner, harness, _, receipts, evidence = system
    initialize(system)
    packet_path = tmp_path / 'packets.jsonl'
    seed(packet_path, 3)
    harness.packet_store_path = lambda: packet_path
    def replay(agent, prompt):
        time.sleep(0.6)
        return turn([remove(3, 0.9)] if '"version": "candidate"' in prompt else [remove(1, 0.2)])
    harness.shadow_provider = lambda pid: DenyAllReplayProvider(REPLAY_AGENT, lambda agent: DENY_ALL_POLICY, replay)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
            response = owner_client.post(self.path, json=payload, headers=headers())
            self.send_response(response.status_code)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            try:
                self.wfile.write(response.content)
            except BrokenPipeError:
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv('PANTHEON_PERSONA_API_URL', f'http://127.0.0.1:{server.server_port}')
    monkeypatch.delenv('RESEARCH_ORCHESTRATOR_TIMEOUT_SECONDS', raising=False)
    monkeypatch.delenv('PANTHEON_PERSONA_SHADOW_TIMEOUT_SECONDS', raising=False)
    app = FastAPI()
    app.include_router(create_persona_lab_router(
        extract_identity=lambda auth: {'operator_id':'u', 'tenant_id':'tenant-a'},
        require_read_role=lambda identity: None, require_write_role=lambda identity: None,
        bff_error=lambda status, code, message, reason: HTTPException(status, {'code':code, 'reason':reason}),
        utc_now=lambda:'2026-11-13T00:00:00Z'))
    client = TestClient(app)
    path = '/bff/agora/persona-lab/p-alpha'
    try:
        draft = client.post(path + '/patches', json={
            'patch_id':'transport-probe', 'session_id':'s', 'base_version':1, 'patch_type':'threshold',
            'proposed_change': {'cycle_thresholds':{'minimum_evidence_count':3,'minimum_confidence':0.8}},
            'evidence_refs':[evidence], 'expected_effect':'cite evidence', 'rollback_target':1})
        assert draft.status_code == 201, draft.text
        result = client.post(path + '/patches/transport-probe/shadow-runs')
        print('shadow default transport:', result.status_code, result.text)
        assert result.status_code == 201, result.text
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
