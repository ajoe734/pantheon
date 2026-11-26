# Read-only task-scoped evidence collector; run with timeout 180s in the named hosted container.
import os,json,psycopg
from psycopg import sql
from datetime import datetime,timezone
out={'observed_at':datetime.now(timezone.utc).isoformat()}
with psycopg.connect(os.environ['PANTHEON_PERSONA_PROVISIONING_STORE_DSN']) as c:
 c.execute('SET TRANSACTION READ ONLY')
 c.execute("SET LOCAL statement_timeout='15s'")
 schema=os.environ.get('PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA','bff')
 rows=c.execute(sql.SQL('SELECT persona_id, request_payload, state, "references", created_at FROM {}.persona_provisioning WHERE tenant_id=%s AND persona_id=ANY(%s)').format(sql.Identifier(schema)),('tenant-dev',['persona-2448c7053e33ea48da47','persona-6507750312870d03a241','persona-ee7112a829d38b9135ac'])).fetchall()
 out['provisioning']=[dict(zip(['persona_id','request_payload','state','references','created_at'],r)) for r in rows]
 out['tables']=c.execute("SELECT table_schema,table_name FROM information_schema.tables WHERE table_schema NOT IN ('information_schema','pg_catalog') AND (table_name ILIKE '%pool%' OR table_name ILIKE '%binding%' OR table_name ILIKE '%polic%') ORDER BY 1,2").fetchall()
print(json.dumps(out,default=str,ensure_ascii=False))
