"""Current exact-source counterexamples, isolated/offline; not hosted evidence."""
from datetime import datetime
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort
from services.control_plane.bff.ports.persona_capital_runtime import _parse_rfc3339, _is_unconfigured


def test_valid_rfc3339_remains_a_datetime():
    parsed = _parse_rfc3339('2026-10-04T17:50:19Z')
    assert isinstance(parsed, datetime), 'valid source timestamp parser was broken by inserting _is_unconfigured above its try block'


def test_configured_source_owner_down_is_not_healthy_local_empty_repository():
    calls = []
    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError('isolated Source owner unavailable')
    port = DefaultResearchKnowledgeSourcePort(source_ingest_service_url='http://127.0.0.1:1', http_get_fn=unavailable)
    port.list_evidence_refs(tenant_id='tenant-a', include_tenant_agnostic=False)
    source = port.dataset_source('evidence_refs')
    assert source == 'unavailable', f'actual configured Source owner was not consulted; reported {source} from fresh local {type(port._evidence_repo).__name__}'


def test_absent_source_owner_is_not_healthy_new_process_memory(monkeypatch):
    for key in ('PANTHEON_SOURCE_INGEST_API_URL', 'PANTHEON_SOURCE_INGEST_URL', 'SOURCE_INGEST_URL'):
        monkeypatch.delenv(key, raising=False)
    port = DefaultResearchKnowledgeSourcePort()
    assert port.dataset_source('evidence_refs') == 'missing', 'an unconfigured Source owner was replaced by a new empty in-process repository'


def test_configured_unauthorized_owner_is_not_reclassified_unconfigured():
    assert not _is_unconfigured(RuntimeError('401 Unauthorized from configured Persona owner')), 'configured owner authentication failure is not missing owner configuration'
