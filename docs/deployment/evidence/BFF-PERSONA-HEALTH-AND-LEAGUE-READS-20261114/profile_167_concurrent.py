"""167-persona concurrent profile: persona-health, persona-league, rankings, persona-fleet.
usage: prof.py [--cprofile ROUTE] ; env BEFORE=1 reverts the two read-path changes."""
import sys, os, json, time, copy, statistics, tempfile, concurrent.futures as cf
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), 'services/control-plane/bff'))
import importlib; b3 = importlib.import_module('services.control_plane.bff.tests.test_bff_b3_persona_fleet')
from fastapi.testclient import TestClient
import services.control_plane.bff.personas.service as svc
if os.environ.get("BEFORE"):
    orig_rows = svc._pm12_persona_league_rows
    def rows(*a, **k):
        for x in ("include_route_policy","include_health","include_allowed_actions"): k.pop(x, None)
        return orig_rows(*a, **k)
    svc._pm12_persona_league_rows = rows
    orig_ov = svc.overlay_source_health_truth if hasattr(svc, "overlay_source_health_truth") else None
    if orig_ov:
        def ov(*a, **k):
            k.pop("thin", None); return orig_ov(*a, **k)
        svc.overlay_source_health_truth = ov
N = 167
with tempfile.TemporaryDirectory() as td:
    data = dict(b3._load_default_fixture_pack_datasets())
    b3._admit_b3_fixture_personas(data)
    personas = data["personas"]
    base = [copy.deepcopy(v) for k, v in personas.items() if isinstance(v, dict)]
    i = 0
    while len(personas) < N:
        src = copy.deepcopy(base[i % len(base)]); i += 1
        pid = f"persona-prof-{i:03d}"
        src["persona_id"] = src["id"] = pid; src["name"] = f"Prof {i:03d}"
        src.setdefault("metadata", {})["tenant_id"] = src.get("tenant_id")
        # real-shaped heavy requirement list (~tens of KB each)
        src["required_data_sources"] = [{"source_key": f"s{p}", "connector_candidates": ["tw-twse-tpex-official-market"]} for p in range(8)]
        personas[pid] = src
    store = b3._PersonaFleetTestStore(data)
    app = b3._create_app(store)
    from services.control_plane.bff.runtime.router import create_runtime_router
    service = app.state.service
    app.include_router(create_runtime_router(read_surface=store, dependencies={
        "_extract_identity": lambda a=None: type("I",(),{"operator_id":"op","roles":["operator","viewer"],"claims":{"tenant_id":b3.B3_ADMITTED_FIXTURE_TENANT_ID},"mfa_verified":True,"__getitem__":lambda s,k:getattr(s,k)})(),
        "_require_read_role": lambda i: None,
        "_dataset_surface_status": lambda n, snapshot_at=None: {"status":"ok"},
        "_composed_dataset_surface_status": lambda n, i, snapshot_at=None, source="": {"status":"ok"},
        "_build_persona_health_items": service.build_persona_health_items,
        "utc_now": lambda: "2026-11-14T00:00:00Z"}))
    client = TestClient(app, raise_server_exceptions=False)
    H = b3.OPERATOR_HEADERS
    ROUTES = {"persona-health": "/bff/v5/execution/persona-health",
              "persona-league": "/bff/management/persona-league",
              "rankings": "/bff/management/persona-league/rankings",
              "persona-fleet": "/bff/management/persona-fleet"}
    if "--cprofile" in sys.argv:
        import cProfile, pstats
        r = ROUTES[sys.argv[sys.argv.index("--cprofile")+1]]
        client.get(r, headers=H)
        import threading
        profs = []
        def mk(*a):
            pr = cProfile.Profile(); profs.append(pr); pr.enable()
        threading.setprofile(lambda *a: None)
        import anyio.to_thread as tt, functools
        orig = tt.run_sync
        async def run_sync(func, *a, **k):
            def wrapped(*x):
                pr = cProfile.Profile(); pr.enable()
                try: return func(*x)
                finally: pr.disable(); profs.append(pr)
            return await orig(wrapped, *a, **k)
        tt.run_sync = run_sync
        import starlette.concurrency as sc; sc.anyio.to_thread.run_sync = run_sync
        resp = client.get(r, headers=H)
        st = pstats.Stats(profs[0]); [st.add(x) for x in profs[1:]]
        print(resp.status_code, len(resp.content), "profiled threads", len(profs)); st.strip_dirs().sort_stats("cumtime").print_stats(22); sys.exit()
    for k, r in ROUTES.items():
        resp = client.get(r, headers=H); print(k, resp.status_code, len(resp.content), "personas", len(personas))
    lat = {k: [] for k in ROUTES}
    def one(k):
        t = time.perf_counter(); resp = client.get(ROUTES[k], headers=H); lat[k].append(time.perf_counter()-t); assert resp.status_code == 200, (k, resp.status_code)
    for rnd in range(int(os.environ.get("ROUNDS", 10))):
        with cf.ThreadPoolExecutor(4) as ex: list(ex.map(one, ROUTES))
    out = {}
    for k, v in lat.items():
        v.sort(); out[k] = {"n": len(v), "p50_s": round(statistics.median(v), 3), "p95_s": round(v[max(0, int(len(v)*0.95+0.5)-1)], 3)}
    print(json.dumps(out, indent=1))
