# Read-only task-scoped evidence collector; run with timeout 180s in the named hosted container.
import json,os,urllib.request,dataclasses
from datetime import datetime,timezone
from services.execution.lean_runtime.expression_inputs import capital_pool_reader_from_env
from services.capital.risk_policy import resolve_bound_pool_risk_policy
def get(path):
 req=urllib.request.Request(os.environ['PANTHEON_RUNTIME_MANAGER_URL'].rstrip('/')+path,headers={'Authorization':'Bearer '+os.environ['PANTHEON_RUNTIME_MANAGER_TOKEN'],'X-Tenant-Id':'tenant-dev','X-Pantheon-Service':'paper-signal-producer'})
 with urllib.request.urlopen(req,timeout=20) as r:return json.load(r)
reader=capital_pool_reader_from_env()
out={'observed_at':datetime.now(timezone.utc).isoformat(),'pools':{},'bindings':get('/api/runtime-fleet/desired-state?stage=paper')}
for suffix in ['06d4775a9b6a8f63b311','039ad01f8bcd2b4aa466','a81b4fd15944698341aa']:
 pid='pool-persona-paper-'+suffix
 pool=reader(pid)
 policy=resolve_bound_pool_risk_policy(capital_pool_id=pid,capital_pool_reader=lambda _:pool)
 out['pools'][pid]={'owner_record':pool,'resolved':dataclasses.asdict(policy)}
print(json.dumps(out,ensure_ascii=False,default=str))
