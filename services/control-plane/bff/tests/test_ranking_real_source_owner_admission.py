"""Independent exact-source controls: real served Source app/store, transport only replaced.
This is offline local owner verification, NOT hosted/provider/durability acceptance.
"""
import importlib,json,sys,time,urllib.request,urllib.error
from urllib.parse import urlsplit
import pytest
from fastapi.testclient import TestClient
from services.runtime_auth_inbound import encode_jwt_hs256
from services.control_plane.bff.core import owner_reads
from services.control_plane.bff.ports.research_knowledge_source import DefaultResearchKnowledgeSourcePort

@pytest.fixture
def actual_source(tmp_path,monkeypatch):
 for key in ['SOURCE_INGEST_STORE_PATH','SOURCE_INGEST_CONNECTOR_STORE_PATH','SOURCE_INGEST_EVIDENCE_STORE_PATH','SOURCE_INGEST_DLQ_PATH','SOURCE_INGEST_AUDIT_PATH']:
  monkeypatch.delenv(key,raising=False)
 monkeypatch.setenv('SOURCE_INGEST_DATA_DIR',str(tmp_path));monkeypatch.setenv('SOURCE_INGEST_MAX_RECORDS','3');monkeypatch.setenv('PANTHEON_RUNTIME_JWT_SECRET','isolated-real-source-read-secret')
 sys.modules.pop('services.source_ingestion.main',None)
 module=importlib.import_module('services.source_ingestion.main');client=TestClient(module.app)
 payload={'connector':{'connector_id':'fixture-source-connector','source_type':'paper','provider':'OpenAlex','license_scope':'open'},'trace_id':'isolated-source-fixture-trace','trigger_type':'manual','records':[{'source_id':'source-owned-fixture','connector_id':'fixture-source-connector','source_type':'paper','title':'isolated source fixture','content_ref':'https://doi.org/10.5555/isolated-fixture','trace_id':'source-fixture-record-trace','metadata':{'tenant_id':'tenant-fixture','body':'bounded local supplied facts','access_scope':['research']}}]}
 seeded=client.post('/api/source-ingest/source-records',json=payload);assert seeded.status_code==201,seeded.text
 token=encode_jwt_hs256({'sub':'isolated-source-reader','roles':['operator'],'tenant_id':'tenant-fixture','exp':int(time.time())+600},secret='isolated-real-source-read-secret')
 headers={'Authorization':'Bearer '+token};direct=client.get('/api/source-ingest/evidence/items',headers=headers);assert direct.status_code==200 and direct.json()['items']
 return client,headers,direct.json()['items']

class ActualHTTPResponse:
 def __init__(self,response):self.response=response
 def __enter__(self):return self
 def __exit__(*args):return False
 def read(self):return self.response.content

def test_default_source_read_forwards_bound_verified_caller_to_real_owner(actual_source,monkeypatch):
 client,headers,owned=actual_source;observed=[]
 def actual_transport(request,**kwargs):
  url=request if isinstance(request,str) else request.full_url
  incoming={} if isinstance(request,str) else dict(request.header_items())
  response=client.get(urlsplit(url).path,headers=incoming);observed.append({'status':response.status_code,'authorization':next((v for k,v in incoming.items() if k.lower()=='authorization'),None)})
  if response.status_code>=400:raise urllib.error.HTTPError(url,response.status_code,'actual owner rejection',None,None)
  return ActualHTTPResponse(response)
 monkeypatch.setattr(urllib.request,'urlopen',actual_transport)
 auth=owner_reads.authorization.set(headers['Authorization']);scope=owner_reads.selected_tenant.set('tenant-fixture')
 try:
  port=DefaultResearchKnowledgeSourcePort(source_ingest_service_url='http://127.0.0.1:1')
  rows=port.list_evidence_refs(tenant_id='tenant-fixture',include_tenant_agnostic=False)
  assert {r['ref_id'] for r in rows}=={r['evidence_item_id'] for r in owned},observed
  assert observed[0]['authorization']==headers['Authorization']
 finally:owner_reads.selected_tenant.reset(scope);owner_reads.authorization.reset(auth)

@pytest.mark.parametrize('payload',[None,{'wrong-envelope':[]}])
def test_malformed_reachable_source_never_reports_healthy_empty(payload):
 port=DefaultResearchKnowledgeSourcePort(source_ingest_service_url='http://127.0.0.1:1',http_get_fn=lambda *_:(True,payload))
 assert port.dataset_source('evidence_refs')=='unavailable'
