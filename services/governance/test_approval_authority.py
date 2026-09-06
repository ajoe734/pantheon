"""Focused common-reader contract tests and explicitly injected unit snapshots.

The snapshot reader is only a transport double. Production DTO parsing and
validity predicates still execute; real HTTP/Postgres proofs live alongside.
"""
from services.governance.approval_authority import ApprovalEvidence, ApprovalInvalid


def approval_snapshot(**values):
    body = dict(decision_id='approval-unit', tenant_id='tenant-unit', target_type='registry_entry',
                target_id='artifact-unit', target_version='1.0.0', decision_state='decided',
                decision='approved', actor_id='unit-reviewer', actor_role='governance_reviewer',
                risk_level='low', decided_at='2026-01-01T00:00:00Z', expires_at='2099-01-01T00:00:00Z',
                recorded_at='2026-01-01T00:00:00Z', authority_status='authoritative',
                controller_record_ref='governance-controller://approval-unit', version=3,
                event_id='unit-decision-event', conditions=[])
    body.update(values)
    return body


class SnapshotApprovalReader:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    def get(self, decision_id):
        body = self.snapshot() if callable(self.snapshot) else self.snapshot
        try:
            evidence = ApprovalEvidence.model_validate(body)
        except ValueError as exc:
            raise ApprovalInvalid('Malformed Governance decision') from exc
        if evidence.decision_id != decision_id:
            raise ApprovalInvalid('Governance exact decision ID mismatch')
        return evidence

    def verify(self, decision_id, *, expected, now=None):
        return self.get(decision_id).require_valid(expected=expected, now=now)


def registry_approval_fixture(entry, *, approver='unit-reviewer', decision_id='approval-unit'):
    return SnapshotApprovalReader(approval_snapshot(
        decision_id=decision_id, tenant_id=entry.owner_tenant, actor_id=approver,
        target_id=entry.registry_id, target_version=entry.version, candidate_digest=entry.checksum))


def advance_registry_http(client, url, *, json, **kwargs):
    """Explicit approval transport injection for Registry state-machine unit cases."""
    if json.get('target_state') != 'approved':
        return client.post(url, json=json, **kwargs)
    from services.registry.service import get_registry_service
    from unittest.mock import patch
    import uuid
    identity = url.rstrip('/').split('/')[-2]
    entry = get_registry_service().get(identity).entry
    body = dict(json)
    reader = registry_approval_fixture(entry, approver=body.pop('approver', 'unit-reviewer'),
                                      decision_id=body.setdefault('approval_decision_id', 'approval-'+identity))
    body.setdefault('command_key', uuid.uuid4().hex)
    body.setdefault('expected_version', entry.version)
    body.setdefault('expected_updated_at', entry.updated_at)
    with patch('services.governance.approval_authority.configured_approval_reader', return_value=reader):
        return client.post(url, json=body, **kwargs)


def advance_registry_unit(service, identity, state, **kwargs):
    from services.registry.models import ArtifactState
    import uuid
    if state == ArtifactState.APPROVED:
        entry = service.get(identity).entry
        service.approval_reader = registry_approval_fixture(
            entry, approver=kwargs.pop('approver', 'unit-reviewer'),
            decision_id=kwargs.setdefault('approval_decision_id', 'approval-'+identity))
        kwargs.setdefault('command_key', uuid.uuid4().hex)
        kwargs.setdefault('actor', {'id': 'unit-operator', 'tenant': entry.owner_tenant})
        kwargs.setdefault('expected_artifact_state', entry.artifact_state)
        kwargs.setdefault('expected_version', entry.version)
        kwargs.setdefault('expected_updated_at', entry.updated_at)
    return service.advance_artifact_state(identity, state, **kwargs)


def registry_unit_headers():
    from services.runtime_auth_inbound import encode_jwt_hs256
    import time
    return {'Authorization': 'Bearer '+encode_jwt_hs256(dict(
        sub='test-operator', tenant='tenant-unit', roles=['operator'],
        iss='registry-unit', aud='registry-unit', exp=time.time()+900), secret='synthetic-unit-key')}


def configure_registry_unit_auth(monkeypatch):
    for key, value in dict(PANTHEON_REGISTRY_AUTH_MODE='strict',
                           PANTHEON_REGISTRY_JWT_SECRET='synthetic-unit-key',
                           PANTHEON_REGISTRY_JWT_ISSUER='registry-unit',
                           PANTHEON_REGISTRY_JWT_AUDIENCE='registry-unit').items():
        monkeypatch.setenv(key, value)


import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import pytest
from services.governance.approval_authority import ApprovalReader, ApprovalUnavailable


@pytest.mark.parametrize('change', [
    {'decision_state': 'proposed'}, {'decision': 'rejected'}, {'decision': 'approved_with_conditions'},
    {'revoked_at': '2026-01-02T00:00:00Z'}, {'superseded_by': 'other'}, {'conditions': ['check']},
    {'expires_at': None}, {'expires_at': 'bad'}, {'expires_at': '2026-01-01T00:00:00'},
    {'expires_at': '2000-01-01T00:00:00Z'}, {'actor_id': None}, {'actor_role': 'automated_gate', 'risk_level': 'high'},
    {'controller_record_ref': None}, {'authority_status': None}, {'recorded_at': None},
])
def test_common_validity_rejects_invalid_authority(change):
    with pytest.raises(ApprovalInvalid):
        ApprovalEvidence.model_validate(approval_snapshot(**change)).require_valid()


@pytest.mark.parametrize('field', ['tenant_id', 'target_type', 'target_id', 'target_version', 'candidate_digest', 'proof_digest'])
def test_common_validity_binds_each_expected_field(field):
    with pytest.raises(ApprovalInvalid, match=field):
        ApprovalEvidence.model_validate(approval_snapshot()).require_valid(expected={field: 'different'})


@pytest.mark.parametrize('status,content_type,body', [
    (401, 'application/json', b'{}'), (403, 'application/json', b'{}'), (404, 'application/json', b'{}'),
    (503, 'application/json', b'{}'), (200, 'text/html', b'<html>login</html>'),
    (200, 'application/json', b'{'), (200, 'application/json', b'[]'),
    (200, 'application/json', b'null'), (200, 'application/json', b'{}'),
    (200, 'application/json', json.dumps(approval_snapshot(decision_id='wrong')).encode()),
    (302, 'application/json', b'{}'),
])
def test_exact_http_reader_fails_closed(status, content_type, body):
    observed = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(self.path)
            assert self.headers['Authorization'] == 'Bearer isolated-transport-token'
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            if status == 302:
                self.send_header('Location', '/credential-leak-target')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        reader = ApprovalReader(base_url=f'http://127.0.0.1:{server.server_port}', service_token='isolated-transport-token')
        with pytest.raises(ApprovalInvalid):
            reader.get('approval-unit')
        assert observed == ['/api/governance/approvals/approval-unit']
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
        assert not thread.is_alive()


def test_reader_connection_failure_is_unavailable():
    import socket
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    with pytest.raises(ApprovalUnavailable):
        ApprovalReader(base_url=f'http://127.0.0.1:{port}', service_token='isolated', timeout_seconds=.2).get('approval-unit')


@pytest.mark.parametrize('url,timeout', [('file:///tmp/authority', 5), ('http://[broken', 5), ('http://user:password@owner', 5), ('http://owner?decision=wrong', 5), ('http://owner', float('inf')), ('http://owner', 0)])
def test_invalid_reader_configuration_is_typed_unavailable(url, timeout):
    with pytest.raises(ApprovalUnavailable):
        ApprovalReader(base_url=url, service_token='isolated', timeout_seconds=timeout)
