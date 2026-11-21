# Read-only task-scoped evidence collector; run with timeout 180s in the named hosted container.
import os,json,urllib.request,hashlib,pathlib
from services.execution.lean_runtime import expression_policy
from services.execution.lean_runtime.taiwan_cash_costs import buy_cash_required
eid='91d55953-b575-4fe8-8d39-6e0cade58d59'
base=os.environ.get('PANTHEON_TELEMETRY_API_URL') or os.environ.get('PANTHEON_TELEMETRY_URL')
req=urllib.request.Request(base.rstrip('/')+'/api/telemetry/events/'+eid,headers={'Authorization':'Bearer '+os.environ['PANTHEON_TELEMETRY_SERVICE_TOKEN'],'X-Tenant-Id':'tenant-dev'})
with urllib.request.urlopen(req,timeout=20) as r:event=json.load(r)
inp=event['metadata']['expression_replay_input']
decision=expression_policy.decide(inp['view'],inp['candidates'],inp['book_before'],inp['budget'],inp['parameters'])
cash=buy_cash_required('2317.TWSE',decision['units'],221)
print(json.dumps({'source_event_id':eid,'input':inp,'recorded_decision':event['metadata']['expression_decision'],'replay_entrypoint':'expression_policy.decide (historical event contains view/candidates/book/budget/parameters; not a new target-position input)','replayed_decision':decision,'buy_cash_required':float(cash),'remaining_cash':float(100000-cash),'notional':decision['units']*221,'gross_weight_before_fee':decision['units']*221/100000,'is_new_trade':False,'source_hash':hashlib.sha256(pathlib.Path(expression_policy.__file__).read_bytes()).hexdigest()},ensure_ascii=False))
