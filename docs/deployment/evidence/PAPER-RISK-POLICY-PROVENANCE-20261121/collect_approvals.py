# Read-only task-scoped evidence collector; run with timeout 180s in the named hosted container.
import os,json,urllib.request,collections
def req(path,body=None):
 r=urllib.request.Request('http://127.0.0.1:8001'+path,data=json.dumps(body).encode() if body else None,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if 'token' in globals() else {})})
 with urllib.request.urlopen(r,timeout=25) as f:return json.load(f)
token=req('/bff/auth/dev-login',{'grant_type':'client_credentials','client_id':os.environ['PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_ID'],'client_secret':os.environ['PANTHEON_BFF_DEV_LOGIN_RISK_OWNER_CLIENT_SECRET']})['access_token']
rows=req('/api/v1/approval-decisions')['data']
selected=[r for r in rows if r.get('tenant_id')=='tenant-dev' and ('policy' in str(r.get('target_type','')).lower() or any(s in str(r.get('metadata',{})).lower() for s in ['profile','risk_policy','requested_risk','gross_limit','max_leverage','max_single_name_weight','cash_reserve']))]
print(json.dumps({'total':len(rows),'target_types':dict(collections.Counter(r.get('target_type') for r in rows)),'policy_mapping_candidates':selected,'trading_approvals':[r for r in rows if r.get('decision_id') in ['apv-persona-pool-039ad01f8bcd2b4aa466-v2','apv-persona-paper-artifact-039ad01f8bcd2b4aa466']]},ensure_ascii=False))
