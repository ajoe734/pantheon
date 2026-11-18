import sys,types
f=types.ModuleType('fastapi')
class HTTPException(Exception): pass
f.HTTPException=HTTPException
sys.modules['fastapi']=f
import sys, time, json, threading, tempfile, datetime as dt
from pathlib import Path
from services.source_ingestion import requirement_state as m, snapshot_universe as su
from services.source_ingestion.tests.bench_market_snapshot_incremental_read import _line
d = Path(tempfile.mkdtemp()); j = d/"latest_market_snapshots.jsonl"
with j.open("w") as f:
    for i in range(10700): f.write(_line(f"{10000+i}.TWSE",0)+"\n")
today=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(hours=8)).date().isoformat()
members={str(10000+i):{"symbol":f"{10000+i}.TWSE","last_requested_date":today,"origin":"request"} for i in range(300)}
st=d/"latest_market_snapshots.jsonl.universe.json"
st.write_text(json.dumps({"schema_version":su.STATE_SCHEMA_VERSION,"updated_at":"2026-11-18T00:00:00Z","members":members,"transitions":[],"transitions_total":0}))
store=m.LatestMarketSnapshotStore(j,max_closes=60)
u=su.SnapshotUniverse(store,state_path=st,storage_root=d/"s")
u._seed_from_run_files=lambda *a,**k: None  # synthetic copy has no run files; avoids heavy imports
done=threading.Event(); res={}
def sw():
    t=time.perf_counter(); u.sweep(force=True); res["sweep_s"]=time.perf_counter()-t; done.set()
th=threading.Thread(target=sw); th.start(); time.sleep(0.05)
worst=0; n=0
while not done.is_set():
    t=time.perf_counter(); u.snapshot_for_request("10299.TWSE" if n%2 else "10100.TWSE"); worst=max(worst,time.perf_counter()-t); n+=1; time.sleep(0.01)
th.join()
print(json.dumps({"sweep_s":round(res["sweep_s"],3),"lookup_during_sweep_max_s":round(worst,6),"lookups":n}))
