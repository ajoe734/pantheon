"""H1/H2 contracts and opt-in probes of the real deployed image entry point."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = yaml.safe_load((ROOT / 'docker-compose.yml').read_text())
SERVICES = COMPOSE['services']
DOCKERFILE = (ROOT / 'deploy/hindsight/Dockerfile').read_text()


def test_internal_index_and_only_memory_holds_client_key():
    pg, api = (SERVICES[k] for k in ('memory-index-postgres', 'hindsight'))
    for service in (pg, api):
        assert not service.get('ports')
        assert service['profiles'] == ['core', 'root']
        assert service['build']['args']['GIT_SHA'] == '${GIT_SHA:-unknown}'
    assert pg['volumes'] == ['memory-index-postgres-data:/var/lib/postgresql/data']
    assert pg['networks'] == ['memory-index-storage']
    assert api['networks'] == ['memory-index', 'memory-index-storage']
    for network, members in [('memory-index', {'memory', 'hindsight'}),
                             ('memory-index-storage', {'memory-index-postgres', 'hindsight'})]:
        assert COMPOSE['networks'][network]['internal'] is True
        actual = {name for name, svc in SERVICES.items() if network in svc.get('networks', [])}
        assert actual == members
    holders = {name for name, svc in SERVICES.items()
               if 'HINDSIGHT_API_KEY' in json.dumps(svc.get('environment', {}))}
    assert holders == {'memory', 'hindsight'}
    env = api['environment']
    assert env['HINDSIGHT_API_MCP_ENABLED'] == 'false'
    assert env['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'] == 'pgroonga'
    assert env['HINDSIGHT_API_VECTOR_EXTENSION'] == 'pgvector'
    assert env['HINDSIGHT_API_RUN_MIGRATIONS_ON_STARTUP'] == 'true'
    assert env['HINDSIGHT_API_TENANT_EXTENSION'].endswith(':ApiKeyTenantExtension')
    assert SERVICES['memory']['environment']['PANTHEON_MEMORY_HINDSIGHT_API_KEY'] == env['HINDSIGHT_API_TENANT_API_KEY']
    assert api['depends_on']['memory-index-postgres']['condition'] == 'service_healthy'
    assert 'hindsight_api.http_probe' in api['healthcheck']['test']


def test_version_digest_and_extension_pins():
    pg = (ROOT / 'deploy/memory-index-postgres/Dockerfile').read_text()
    assert re.search(r'FROM groonga/pgroonga:4\.0\.8-debian-17@sha256:[0-9a-f]{64}', pg)
    assert re.search(r'postgresql-17-pgvector=\d+\.\d+\.\d+-[^\s]+', pg)
    assert re.search(r'FROM ghcr.io/vectorize-io/hindsight-api:1\.2\.1-slim@sha256:[0-9a-f]{64}', DOCKERFILE)
    init = (ROOT / 'deploy/memory-index-postgres/init.sql').read_text()
    assert 'CREATE EXTENSION IF NOT EXISTS vector;' in init
    assert 'CREATE EXTENSION IF NOT EXISTS pgroonga;' in init
    assert 'onnxruntime==' in DOCKERFILE
    assert 'LABEL org.opencontainers.image.revision=$GIT_SHA' in DOCKERFILE
    assert 'LABEL org.opencontainers.image.revision=$GIT_SHA' in pg
    assert 'torch' not in re.search(r'RUN uv pip install.*?(?=COPY)', DOCKERFILE, re.S)[0]


def test_offline_manifest_and_none_provider_defaults():
    embedding = json.loads((ROOT / 'services/search/model-manifest.json').read_text())
    manifest = json.loads((ROOT / 'deploy/hindsight/model-manifest.json').read_text())
    assert manifest['embedding_manifest'] == 'services/search/model-manifest.json'
    assert 'COPY services/search/model-manifest.json /opt/hindsight/embedding-manifest.json' in DOCKERFILE
    assert embedding['dimension'] == 1024
    assert 'HINDSIGHT_API_EMBEDDINGS_ONNX_QUERY_PREFIX="query: "' in DOCKERFILE
    assert 'HINDSIGHT_API_EMBEDDINGS_ONNX_PASSAGE_PREFIX="passage: "' in DOCKERFILE
    assert 'HF_HUB_OFFLINE=1' in DOCKERFILE and 'TRANSFORMERS_OFFLINE=1' in DOCKERFILE
    for row in (embedding, manifest['reranker']):
        assert re.fullmatch('[0-9a-f]{40}', row['revision'])
        assert all(re.fullmatch('[0-9a-f]{64}', f['sha256']) for f in row['files'].values())
        assert 'model.onnx' in row['files'] and 'tokenizer.json' in row['files']
    env = SERVICES['hindsight']['environment']
    assert env['HINDSIGHT_API_LLM_PROVIDER'] == 'none'
    assert env['HINDSIGHT_API_RETAIN_EXTRACTION_MODE'] == 'chunks'
    assert env['HINDSIGHT_API_ENABLE_AUTO_CONSOLIDATION'] == 'false'
    assert env['HINDSIGHT_API_WORKER_ENABLED'] == 'false'
    for layer in ('RETAIN', 'CONSOLIDATION', 'REFLECT', 'MENTAL_MODEL_REFRESH'):
        assert env[f'HINDSIGHT_API_{layer}_LLM_PROVIDER'].endswith(':-none}')
        assert env[f'HINDSIGHT_API_{layer}_LLM_API_KEY'].endswith(':-}')


def test_startup_verifies_all_models_before_exec_and_rejects_corruption(tmp_path, monkeypatch):
    script = DOCKERFILE.split("COPY <<'PY' /opt/hindsight/models.py\n", 1)[1].split('\nPY\n', 1)[0]
    namespace = {'__name__': 'model_verifier'}
    exec(compile(script, 'models.py', 'exec'), namespace)
    namespace['ROOT'] = tmp_path
    sample = {'source_repository': 'unused', 'revision': 'unused', 'files': {
        name: {'sha256': hashlib.sha256(name.encode()).hexdigest()} for name in ('model.onnx', 'tokenizer.json')}}
    (tmp_path / 'embedding-manifest.json').write_text(json.dumps(sample))
    (tmp_path / 'model-manifest.json').write_text(json.dumps({'reranker': sample}))
    for kind in ('embeddings', 'reranker'):
        (tmp_path / kind).mkdir()
        for name in sample['files']:
            (tmp_path / kind / name).write_text(name)
    # Any attempt to use a runtime download fails this test.
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **kw: pytest.fail('runtime download'))
    namespace['verify']()
    (tmp_path / 'reranker/model.onnx').write_text('corrupt')
    with pytest.raises(RuntimeError, match='model_checksum_mismatch:reranker/model.onnx'):
        namespace['verify']()
    (tmp_path / 'reranker/model.onnx').unlink()
    with pytest.raises(FileNotFoundError):
        namespace['verify']()
    assert script.index('verify(package=') < script.index('os.execv(')


# This body runs inside the real Hindsight image. Test data is confined to a
# unique smoke bank in an isolated Compose project; it is never a product seed.
SMOKE = r'''
import json, os, urllib.request, urllib.error, uuid
bank = 'pantheon-smoke-' + uuid.uuid4().hex
base = 'http://127.0.0.1:8888'
headers = {'Authorization': 'Bearer ' + os.environ['HINDSIGHT_API_TENANT_API_KEY'], 'Content-Type':'application/json'}
def call(path, body=None, auth=True, method=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base+path, data=data, headers=headers if auth else {'Content-Type':'application/json'}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            payload = response.read()
            return response.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()
try:
    denied, _ = call('/v1/default/banks/'+bank+'/memories/recall', {'query':'risk'}, auth=False)
    assert denied in (401,403), denied
    status, retained = call('/v1/default/banks/'+bank+'/memories', {
        'items':[{'content':'風險委員會決定降低台灣股票曝險，因為市場波動上升。', 'document_id':'smoke-canonical-1',
                  'tags':['scope:private','underlying:TW'], 'entities':[{'text':'underlying:TW'}]}],
        'async':False})
    assert status == 200, (status,retained)
    status, recalled = call('/v1/default/banks/'+bank+'/memories/recall', {
        'query':'台灣股票風險曝險', 'budget':'low', 'max_tokens':1024, 'tags':['underlying:TW'], 'tags_match':'all_strict'})
    assert status == 200 and recalled.get('results'), (status,recalled)
    assert any(r.get('document_id') == 'smoke-canonical-1' for r in recalled['results']), recalled
    assert call('/mcp/')[0] == 404
    reflect_status, reason = call('/v1/default/banks/'+bank+'/reflect', {'query':'risk'})
    assert reflect_status == 400, (reflect_status,reason)
    print(json.dumps({'retain_status':200, 'recall_status':200, 'recall_count':len(recalled['results']),
        'document_ids':[r.get('document_id') for r in recalled['results']], 'unauthenticated_status':denied,
        'mcp_status':404, 'reflect_status':reflect_status, 'reflect_reason':reason, 'llm_provider':'none'}))
finally:
    status, result = call('/v1/default/banks/'+bank, method='DELETE')
    assert status in (200,204), (status,result)
'''


def test_real_container_retain_recall():
    # To run remotely, the caller supplies an existing pinned-key SSH command
    # as JSON argv, plus the task's isolated compose file. No shell expansion.
    raw = os.getenv('PANTHEON_HINDSIGHT_SMOKE_COMMAND')
    if not raw:
        pytest.skip('Set PANTHEON_HINDSIGHT_SMOKE_COMMAND JSON argv for an isolated real Compose project')
    result = subprocess.run(json.loads(raw), input=SMOKE, text=True, capture_output=True, timeout=360)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert evidence['recall_count'] > 0
    output = os.getenv('PANTHEON_HINDSIGHT_SMOKE_OUTPUT')
    if output:
        Path(output).write_text(json.dumps(evidence, indent=2)+'\n')
