"""A failed Root must not leave a steady service stopped or on a candidate image.

Recorded hosted state (Root 43932397748, restore 43983824207): the steady
source-ingest-scheduler was on the candidate image and stopped (exit 137, restart
policy unless-stopped) beside a source-ingest already on the baseline image,
while /bff/version matched the accepted backend.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from scripts import fetch_dev_artifact_evidence as evidence

ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "scripts" / "deploy_nonprod_vm.sh"
BASE = "sha256:" + "a" * 64
CAND = "sha256:" + "b" * 64
SRC = "sha256:" + "c" * 64


def _function(name: str) -> str:
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    start = source.index(f"{name}() {{")
    following = re.search(r"\n[A-Za-z_][A-Za-z_0-9]*\(\) \{", source[start:])
    chunk = source[start: start + following.start()] if following else source[start:]
    return chunk[: chunk.rindex("\n}\n") + 3]


def _stub_docker(tmp_path: Path, containers: list[dict]) -> dict:
    state = tmp_path / "state.json"
    state.write_text(json.dumps(containers), encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(f"""#!/usr/bin/env python3
import json, sys
from pathlib import Path
state = Path({str(state)!r})
rows = json.loads(state.read_text())
args = sys.argv[1:]
if args[0] == "ps":
    for r in rows: print(r["id"])
elif args[0] == "inspect":
    r = next(r for r in rows if r["id"] == args[-1])
    print("|".join([r["service"], r.get("oneoff", "False"), r["restart"], r["state"], r["image"], r["id"]]))
elif args[0] == "start":
    for r in rows:
        if r["id"] == args[1] and not r.get("fail_start"):
            r["state"] = "running"
    state.write_text(json.dumps(rows))
""", encoding="utf-8")
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PANTHEON_STEADY_RESTORE_WAIT_SECONDS": "1"}


def _ctr(service, state, image, cid, restart="unless-stopped", **extra):
    return {"id": cid, "service": service, "state": state, "image": image, "restart": restart, **extra}


def _run(tmp_path: Path, before: list[dict], after: list[dict]):
    """Capture a baseline from ``before`` then run the compensation check on ``after``."""
    baseline = tmp_path / "baseline.txt"
    env = _stub_docker(tmp_path, before)
    fmt = re.search(r"^STEADY_SERVICE_FORMAT=.*$", DEPLOY_SCRIPT.read_text(encoding="utf-8"), re.M).group(0)
    prelude = ('set -uo pipefail\ninfo() { echo "[info] $*"; }\n' + fmt + "\n"
               + _function("steady_service_rows") + _function("capture_steady_service_baseline")
               + _function("steady_service_problems") + _function("verify_steady_services_restored"))
    (tmp_path / "state.json").write_text(json.dumps(before))
    subprocess.run(["bash", "-c", prelude + f'capture_steady_service_baseline {baseline}'], env=env, check=True)
    (tmp_path / "state.json").write_text(json.dumps(after))
    res = subprocess.run(["bash", "-c", prelude + f'verify_steady_services_restored {baseline}'],
                         env=env, capture_output=True, text=True, timeout=60)
    return baseline, res, json.loads((tmp_path / "state.json").read_text())


BEFORE = [_ctr("source-ingest", "running", SRC, "c1"), _ctr("source-ingest-scheduler", "running", BASE, "c2"),
          _ctr("migrate", "exited", SRC, "c3", restart="no"), _ctr("oneoff", "running", SRC, "c4", oneoff="True")]


def test_baseline_records_only_running_steady_services(tmp_path):
    baseline, _res, _ = _run(tmp_path, BEFORE, BEFORE)
    assert baseline.read_text().splitlines() == [f"source-ingest|{SRC}", f"source-ingest-scheduler|{BASE}"]


def test_stopped_scheduler_on_baseline_image_is_started(tmp_path):
    after = [_ctr("source-ingest", "running", SRC, "c1"), _ctr("source-ingest-scheduler", "exited", BASE, "c2")]
    _b, res, final = _run(tmp_path, BEFORE, after)
    assert res.returncode == 0, res.stdout + res.stderr
    assert {r["service"]: r["state"] for r in final}["source-ingest-scheduler"] == "running"


def test_stopped_scheduler_on_candidate_image_is_reported_incomplete(tmp_path):
    # The recorded hosted state: candidate image, exit 137, baseline source-ingest.
    after = [_ctr("source-ingest", "running", SRC, "c1"), _ctr("source-ingest-scheduler", "exited", CAND, "c2")]
    _b, res, _ = _run(tmp_path, BEFORE, after)
    assert res.returncode != 0
    assert "steady_service_compensation_incomplete: source-ingest-scheduler" in res.stdout + res.stderr


def test_missing_and_unstartable_steady_services_are_named(tmp_path):
    after = [_ctr("source-ingest-scheduler", "exited", BASE, "c2", fail_start=True)]
    _b, res, _ = _run(tmp_path, BEFORE, after)
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "source-ingest" in out and "source-ingest-scheduler" in out


def test_all_steady_services_running_on_baseline_passes(tmp_path):
    _b, res, _ = _run(tmp_path, BEFORE, BEFORE)
    assert res.returncode == 0


def test_rollback_reports_incomplete_when_steady_services_not_restored():
    body = _function("rollback_dev_bff_on_failure")
    assert 'verify_steady_services_restored "${DEV_STEADY_SERVICE_BASELINE_FILE:-}"' in body
    assert "INCOMPLETE" in body
    assert body.index("verify_steady_services_restored") < body.index("restore verified")


# ---- drift check ----------------------------------------------------------

def _manifest(stack: bool = True) -> dict:
    services = {"source-ingest": {"image_id": SRC, "state": "running"},
                "source-ingest-scheduler": {"image_id": BASE, "state": "running"},
                "migrate": {"image_id": SRC, "state": "completed"}}
    return {"stack_baseline": {"services": services}} if stack else {
        "image_bundle": {"services": {"operator-bff": {"image_id": BASE}}}}


def _row(service, state, image):
    return {"service": service, "state": state, "image_id": image}


def test_drift_flags_stopped_scheduler_on_candidate_image_beside_baseline_source_ingest():
    drift = evidence.steady_service_drift(_manifest(), [
        _row("source-ingest", "running", SRC), _row("source-ingest-scheduler", "exited", CAND)])
    assert drift == {"source-ingest-scheduler": "stopped-on-non-baseline-image"}


def test_drift_flags_stopped_running_wrong_image_and_missing():
    rows = [_row("source-ingest", "exited", SRC), _row("source-ingest-scheduler", "running", CAND)]
    assert evidence.steady_service_drift(_manifest(), rows) == {
        "source-ingest": "stopped", "source-ingest-scheduler": "non-baseline-image"}
    assert evidence.steady_service_drift(_manifest(), [_row("source-ingest", "running", SRC)]) == {
        "source-ingest-scheduler": "container-missing"}


def test_no_drift_when_steady_services_match_baseline():
    rows = [_row("source-ingest", "running", SRC), _row("source-ingest-scheduler", "running", BASE),
            _row("migrate", "exited", SRC)]
    assert evidence.steady_service_drift(_manifest(), rows) == {}
    assert evidence.steady_service_drift(_manifest(stack=False), [_row("operator-bff", "exited", BASE)]) == {
        "operator-bff": "stopped"}


def _preconditions(steady):
    sha_a, sha_b = "1" * 40, "2" * 40
    identity = {k: sha_a for k in ("previous_backend_sha", "candidate_backend_sha", "controller_sha")}
    identity.update(previous_frontend_sha=sha_b, candidate_frontend_sha=sha_b, candidate_id="d" * 64,
                    run_id="1", attempt="1")
    manifest = {**_manifest(), "identity": identity,
                "frontend": {"backend_sha": sha_a, "frontend_sha": sha_b}}
    deployment = {"deploymentState": "accepted", "bffCommitEvidence": True, "bffCommit": sha_a,
                  "bffSourceCommitSha": sha_a, "commit": sha_b, "frontendSha": sha_b,
                  "bff": {"baseUrl": "https://api.dev.mvl-cap.tw", "sourceCommitSha": sha_a},
                  "frontend": {"commitSha": sha_b}, "schemaVersion": 1, "app": "execute-plans",
                  "repository": "ajoe734/execute-plans", "sourceBranch": "dev"}
    return evidence.verify_restore_accepted_baseline_preconditions(
        baseline_manifest=manifest, deployment_manifest=deployment, bff_version={"source_commit_sha": sha_a},
        is_ancestor=lambda _sha: True, steady_services=steady)


def test_restore_preconditions_proceed_on_steady_drift_even_when_bff_matches():
    result = _preconditions([_row("source-ingest", "running", SRC),
                             _row("source-ingest-scheduler", "exited", CAND)])
    assert result["steady_service_drift"] == {"source-ingest-scheduler": "stopped-on-non-baseline-image"}


def test_restore_preconditions_still_refuse_when_nothing_drifted():
    with pytest.raises(evidence.CaptureError, match="no drift to restore"):
        _preconditions([_row("source-ingest", "running", SRC), _row("source-ingest-scheduler", "running", BASE)])
    with pytest.raises(evidence.CaptureError, match="no drift to restore"):
        _preconditions(None)
