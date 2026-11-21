# Read-only task-scoped evidence collector; run with timeout 180s in the named hosted container.
import os,json,pathlib,hashlib
from services.capital import risk_policy
roots=[pathlib.Path(risk_policy.__file__).resolve().parents[2]/'config/risk_policies',pathlib.Path(risk_policy.__file__).parent/'risk_policies']
if os.environ.get('CAPITAL_RISK_POLICY_DIR'):roots.insert(0,pathlib.Path(os.environ['CAPITAL_RISK_POLICY_DIR']))
print(json.dumps({'policy_roots':[{'path':str(p),'exists':p.exists(),'files':[f.name for f in p.glob('*.json')]} for p in roots],'source_hashes':{p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in ['services/capital/risk_policy.py','services/capital/capital_guard.py','services/control-plane/bff/persona_provisioning_coordinator.py','services/control-plane/bff/personas/service.py'] if pathlib.Path(p).exists()}}))
