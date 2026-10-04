"""Run one bounded task-evidence batch; never enables provider access."""
import collections
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OUT = Path(os.environ.get("TW_OFFLINE_OUT", "/tmp/persona-tw-offline-20261004"))
OUT.mkdir(parents=True, exist_ok=True)
PREFIX = "services/source_ingestion/"
EXCLUDED = {
    PREFIX + "tests/test_provider_management_coverage.py::test_provider_owned_adapters_execute_bounded_fetches_when_payload_omitted": "Unmocked real provider fetch; prior incident; do not rerun",
    PREFIX + "tests/test_taiwan_official_connectors.py::test_taiwan_official_live_read_only_smoke_for_one_twse_and_tpex_symbol": "Opt-in provider network smoke",
    PREFIX + "test_postgres_store.py::test_real_postgres_reload_uses_dependency_topology_after_stable_object_upsert": "Opt-in real PostgreSQL; no task-local database provisioned",
    PREFIX + "test_postgres_store.py::test_real_postgres_legacy_and_tenanted_record_coexistence": "Opt-in real PostgreSQL; no task-local database provisioned",
}
files = sorted(str(p.relative_to(ROOT)) for p in (ROOT / PREFIX / "tests").glob("test_*.py"))
BATCHES = {"root": sorted(str(p.relative_to(ROOT)) for p in (ROOT / PREFIX).glob("test_*.py"))}
BATCHES.update({f"source-{i // 9 + 1}": files[i:i + 9] for i in range(0, len(files), 9)})
BATCHES["persona"] = ["services/control-plane/bff/tests/test_read_store_final_deletion.py"]
batch = sys.argv[1]
env = dict(os.environ, PYTHONPATH=str(HERE), REGISTRY_STORE_BACKEND="memory",
           PANTHEON_TW_OFFICIAL_LIVE_SMOKE="0", TW_INVENTORY=str(OUT / batch))
for key in list(env):
    if key.lower().endswith("_proxy") or key in {"SOURCE_INGEST_TEST_POSTGRES_DSN", "TEST_DATABASE_URL"}:
        env.pop(key)
command = [str(ROOT / ".venv-pantheon/bin/python3"), "-m", "pytest", "-p", "inventory_plugin",
           "-q", *BATCHES[batch], *[f"--deselect={node}" for node in EXCLUDED],
           "--disable-warnings", "--tb=short"]
plan = {"batch": batch, "files": BATCHES[batch], "excluded": EXCLUDED, "command": command,
        "code_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()}
(OUT / f"{batch}.plan.json").write_text(json.dumps(plan, indent=2) + "\n")
with (OUT / f"{batch}.log").open("w") as log:
    try:
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=600)
        code = result.returncode
    except subprocess.TimeoutExpired:
        code = 124
result_path = OUT / f"{batch}.results.json"
data = json.loads(result_path.read_text()) if result_path.exists() else {"results": []}
summary = {"batch": batch, "exit": code,
           "counts": dict(collections.Counter(row["outcome"] for row in data["results"])),
           "nonpasses": [row for row in data["results"] if row["outcome"] != "passed"],
           "log_sha256": hashlib.sha256((OUT / f"{batch}.log").read_bytes()).hexdigest()}
(OUT / f"{batch}.summary.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps(summary), flush=True)
raise SystemExit(code)
