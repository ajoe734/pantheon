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
import json, os, sys
from pathlib import Path
state = Path({str(state)!r})
rows = json.loads(state.read_text())
args = sys.argv[1:]
if args[0] == "ps":
    for r in rows: print(r["id"])
elif args[0] == "inspect":
    r = next(r for r in rows if r["id"] == args[-1])
    if "org.opencontainers.image.revision" in args[2]:
        print(os.environ["PANTHEON_DEPLOY_SHA"])
    else:
        print("|".join([r["service"], r.get("oneoff", "False"), r["restart"], r["state"], r["image"], r["id"]]))
elif args[0] == "image" and args[1] == "inspect":
    sys.exit(0 if args[2] in os.environ.get("RETAINED_IMAGES", "").split() else 1)
elif args[0] == "tag":
    tags = state.with_name("tags.json")
    data = json.loads(tags.read_text()) if tags.exists() else {{}}
    data[args[2]] = args[1]
    tags.write_text(json.dumps(data))
elif args[0] == "compose" and "config" in args:
    if os.environ.get("CONFIG_STDERR"):
        print(os.environ["CONFIG_STDERR"], file=sys.stderr)
    if os.environ.get("CONFIG_EXIT"):
        sys.exit(int(os.environ["CONFIG_EXIT"]))
    names = [r["service"] for r in rows]
    names += os.environ.get("EXPECTED_STEADY_FOR_TEST", "").split()
    print(json.dumps({{"services": {{n: {{"restart": "unless-stopped", **(
        {{}} if os.environ.get("CONFIG_NO_IMAGE") else {{"image": "pantheon-" + n + ":t"}})}} for n in names}}}}))
elif args[0] == "compose" and "ps" in args:
    for r in rows:
        if r["service"] == args[-1]: print(r["id"])
elif args[0] == "compose" and "up" in args:
    if os.environ.get("FAIL_UP"):
        sys.exit(1)
    tags = state.with_name("tags.json")
    data = json.loads(tags.read_text()) if tags.exists() else {{}}
    for r in rows:
        if r["service"] == args[-1]:
            r["image"] = data.get("pantheon-" + r["service"] + ":t", os.environ.get("CANDIDATE_IMAGE"))
            r["state"] = "running"
    state.write_text(json.dumps(rows))
elif args[0] == "start":
    for r in rows:
        if r["id"] == args[1] and not r.get("fail_start"):
            r["state"] = "running"
    state.write_text(json.dumps(rows))
""", encoding="utf-8")
    stub.chmod(0o755)
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PANTHEON_STEADY_RESTORE_WAIT_SECONDS": "1", "RETAINED_IMAGES": os.environ.get("RETAINED_IMAGES", f"{BASE} {SRC}"),
            "EXPECTED_STEADY_FOR_TEST": os.environ.get("EXPECTED_STEADY_FOR_TEST", "source-ingest source-ingest-scheduler")}


def _ctr(service, state, image, cid, restart="unless-stopped", **extra):
    return {"id": cid, "service": service, "state": state, "image": image, "restart": restart, **extra}


def _run(tmp_path: Path, before: list[dict], after: list[dict]):
    """Capture a baseline from ``before`` then run the compensation check on ``after``."""
    baseline = tmp_path / "baseline.txt"
    env = _stub_docker(tmp_path, before)
    prelude = _steady_prelude()
    (tmp_path / "state.json").write_text(json.dumps(before))
    subprocess.run(["bash", "-c", prelude + f'capture_steady_service_baseline {baseline}'], env=env, check=True)
    (tmp_path / "state.json").write_text(json.dumps(after))
    res = subprocess.run(["bash", "-c", prelude + f'verify_steady_services_restored {baseline}'],
                         env=env, capture_output=True, text=True, timeout=60)
    return baseline, res, json.loads((tmp_path / "state.json").read_text())


def _steady_prelude():
    fmt = re.search(r"^STEADY_SERVICE_FORMAT=.*$", DEPLOY_SCRIPT.read_text(encoding="utf-8"), re.M).group(0)
    return ('set -euo pipefail\ninfo() { echo "[info] $*"; }\n' + fmt + "\n"
               + 'expected_steady_services() { printf "%s\\n" ${EXPECTED_STEADY_FOR_TEST:-}; }\n'
               + _function("steady_service_rows") + _function("capture_steady_service_baseline")
               + _function("steady_service_problems") + _function("recreate_steady_service_on_baseline_image")
               + _function("verify_steady_services_restored"))


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


def test_stopped_scheduler_on_candidate_image_is_reported_incomplete(tmp_path, monkeypatch):
    # The recorded hosted state: candidate image, exit 137, baseline source-ingest.
    # With the baseline image no longer retained it cannot be restored and is named.
    monkeypatch.setenv("RETAINED_IMAGES", SRC)
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


# ---- completeness: expected set is derived, not observed ------------------

def test_2026_11_16_stopped_scheduler_at_capture_is_drift_and_restore_starts_it(tmp_path):
    # The scheduler was already stopped when the baseline was taken, so a
    # running-only baseline never contained it and compensation left it stopped.
    before = [_ctr("source-ingest", "running", SRC, "c1"), _ctr("source-ingest-scheduler", "exited", BASE, "c2")]
    baseline, res, final = _run(tmp_path, before, before)
    assert baseline.read_text().splitlines() == [f"source-ingest|{SRC}", "source-ingest-scheduler|"]
    assert res.returncode == 0, res.stdout + res.stderr
    assert {r["service"]: r["state"] for r in final}["source-ingest-scheduler"] == "running"


def test_missing_expected_service_is_reported_even_if_never_running(tmp_path):
    before = [_ctr("source-ingest", "running", SRC, "c1")]
    _b, res, _ = _run(tmp_path, before, before)
    assert res.returncode != 0
    assert "source-ingest-scheduler" in res.stdout + res.stderr


def test_one_shot_and_excluded_services_are_not_expected():
    body = _function("expected_steady_services")
    assert "unless-stopped" in body and "always" in body
    assert "config --format json" in body and "COMPOSE_PROFILES" in body


def test_capture_fails_closed_without_expected_set():
    body = _function("capture_steady_service_baseline")
    assert "return 1" in body
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert 'capture_steady_service_baseline "${DEV_STEADY_SERVICE_BASELINE_FILE}" || true' not in source


def test_root_post_up_check_covers_whole_expected_set():
    source = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    assert "verify_steady_stack_complete \\\n      || rollback_dev_bff_on_failure \"exact_component_deployment\"" in source
    body = _function("verify_steady_stack_complete")
    assert "expected_steady_services" in body and "REQUIRED_LOOP_WORKERS" in body


def test_restore_workflow_passes_steady_state_file():
    wf = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    job = wf[wf.index("  restore-accepted-baseline:"):]
    assert job.count("--steady-state-file") >= 3
    assert "--mode verify-steady-state" in job


def test_restart_loop_after_restore_is_unhealthy_and_named():
    """Restore path: the post-restore second sample reports the loop (exit 76)."""
    def r(n):
        return {"service": "openclaw-gateway-adapter", "state": "running", "restart_count": n, "oom_killed": False}
    loops = evidence.steady_service_restart_loop([r(54)], [r(55)])
    assert loops and "openclaw-gateway-adapter" in loops
    body = (ROOT / ".github/workflows/nonprod-deploy.yml").read_text(encoding="utf-8")
    restore = body[body.index("--mode verify-steady-state"):]
    assert "--mode verify-restart-loop" in restore[:2500]


# ---- 2026-11-17 18:41Z: issuer rebuilt under the same tag ------------------

ISSUER_BEFORE = BEFORE + [_ctr("dev-paper-principal-issuer", "running", BASE, "c5")]
ISSUER_AFTER = [_ctr("source-ingest", "running", SRC, "c1"), _ctr("source-ingest-scheduler", "running", BASE, "c2"),
                _ctr("dev-paper-principal-issuer", "running", CAND, "c5")]


def _run_issuer(tmp_path, monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("EXPECTED_STEADY_FOR_TEST", "source-ingest source-ingest-scheduler dev-paper-principal-issuer")
    return _run(tmp_path, ISSUER_BEFORE, ISSUER_AFTER)


def test_issuer_on_candidate_image_is_recreated_on_baseline_image(tmp_path, monkeypatch):
    _b, res, final = _run_issuer(tmp_path, monkeypatch)
    assert res.returncode == 0, res.stdout + res.stderr
    assert {r["service"]: r["image"] for r in final}["dev-paper-principal-issuer"] == BASE
    assert "steady_service_compensation_incomplete" not in res.stdout + res.stderr


def test_unrestorable_issuer_is_named_with_the_reason(tmp_path, monkeypatch):
    _b, res, _ = _run_issuer(tmp_path, monkeypatch, RETAINED_IMAGES=SRC)
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "steady_service_compensation_incomplete: dev-paper-principal-issuer" in out
    assert "image-mismatch" in out and "is not retained" in out


def test_failed_recreate_is_named_with_the_reason(tmp_path, monkeypatch):
    _b, res, _ = _run_issuer(tmp_path, monkeypatch, FAIL_UP="1")
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "steady_service_compensation_incomplete: dev-paper-principal-issuer" in out
    assert "recreate on the baseline image failed" in out


# Root 46095941360: the brief supplies these observed identity prefixes, not
# full digests/IDs. Keep those prefixes verbatim rather than inventing bytes.
NOV28_BASE_IMAGE = "sha256:03797094bdc4"
NOV28_ISSUER = _ctr("dev-paper-principal-issuer", "running", NOV28_BASE_IMAGE, "27129bc604b6")


def _nov28_pre_rollout(tmp_path):
    """Execute the real root pre-rollout order, including actual issuer start.

    Infrastructure helpers are inert; Docker retains the actual six-field row
    shape. The candidate digest is synthetic because the brief doesn't name it.
    """
    before = [*BEFORE, NOV28_ISSUER]
    env = _stub_docker(tmp_path, before)
    env.update(EXPECTED_STEADY_FOR_TEST="source-ingest source-ingest-scheduler dev-paper-principal-issuer",
               RETAINED_IMAGES=f"{BASE} {SRC} {NOV28_BASE_IMAGE}", CANDIDATE_IMAGE=CAND,
               PANTHEON_DEPLOY_SHA="5f634668b", DEV_PAPER_PRINCIPALS_SUPPORTED="true",
               PANTHEON_DEV_COMPOSE_PROFILES="root,dev-paper-principals",
               PANTHEON_DEV_PPL_ALLOC_009_DEV_PROOF_ENABLED="false",
               PANTHEON_DEV_PAPER_EXPRESSION_LAYER="false",
               PANTHEON_DEV_LIFECYCLE_PROJECTOR_HEALTH_MAX_AGE_SECONDS="120")
    source = DEPLOY_SCRIPT.read_text()
    root = source.index('\n  root)\n', source.index('case "${PANTHEON_DEPLOY_COMPONENT}" in'))
    start = source.index("    validate_source_refresh_profile\n", root)
    end = source.index('    DEV_PRE_DEPLOY_BFF_SHA=', start)
    segment = source[start:end]
    # Ordering regression must include bootstrap: it can recreate postgres too.
    assert segment.index("capture_steady_service_baseline") < segment.index("bootstrap_dev_lifecycle_projection")
    inert = ("validate_source_refresh_profile validate_required_loop_workers ensure_dev_management_ai_bucket "
             "ensure_dev_management_ai_postgres_role prune_dev_management_ai_telemetry_for_disk "
             "retire_dormant_and_one_off_profile_containers prune_dev_release_artifacts_for_build "
             "prune_dev_docker_storage_for_build check_dev_deploy_disk_preflight seal_dev_candidate_images "
             "bootstrap_dev_lifecycle_projection resolve_bounded_source_refresh_active_symbols "
             "dump_dev_root_failure_diagnostics")
    prelude = _steady_prelude() + _function("expected_steady_services") + _function("start_dev_paper_principal_issuer")
    prelude += '\nrollback_dev_bff_on_failure() { echo "unexpected rollback: $*" >&2; exit 99; }\n'
    prelude += "\n".join(f"{name}() {{ :; }}" for name in inert.split()) + "\n"
    # Use the production mktemp call, in this test's private temporary directory.
    env["TMPDIR"] = str(tmp_path)
    result = subprocess.run(["bash", "-c", prelude + segment + '\nprintf "BASELINE=%s\\n" "$DEV_STEADY_SERVICE_BASELINE_FILE"'],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    baseline = Path(next(line.removeprefix("BASELINE=") for line in result.stdout.splitlines()
                         if line.startswith("BASELINE=")))
    assert f"dev-paper-principal-issuer|{NOV28_BASE_IMAGE}" in baseline.read_text().splitlines()
    current = json.loads((tmp_path / "state.json").read_text())
    assert next(r for r in current if r["service"] == NOV28_ISSUER["service"])["image"] == CAND
    return baseline, env


@pytest.mark.parametrize("sealed_restore_restores_issuer", [True, False])
def test_nov28_root_sequence_restores_pre_deploy_issuer(tmp_path, sealed_restore_restores_issuer):
    baseline, env = _nov28_pre_rollout(tmp_path)
    if sealed_restore_restores_issuer:
        # Sealed restore did restore the issuer correctly on 11/28; baseline
        # verification must accept it, without attempting to reinstall candidate.
        state = tmp_path / "state.json"
        rows = json.loads(state.read_text())
        for row in rows:
            if row["service"] == NOV28_ISSUER["service"]:
                row["image"] = NOV28_BASE_IMAGE
        state.write_text(json.dumps(rows))
    result = subprocess.run(["bash", "-c", _steady_prelude() + f'verify_steady_services_restored {baseline}'],
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "steady_service_compensation_incomplete" not in result.stdout + result.stderr
    rows = json.loads((tmp_path / "state.json").read_text())
    assert next(r for r in rows if r["service"] == NOV28_ISSUER["service"])["image"] == NOV28_BASE_IMAGE
    tags = tmp_path / "tags.json"
    if sealed_restore_restores_issuer:
        assert not tags.exists(), "already restored issuer must not be re-tagged"
    else:
        assert json.loads(tags.read_text()) == {"pantheon-dev-paper-principal-issuer:t": NOV28_BASE_IMAGE}


@pytest.mark.parametrize("config_exit", [0, 17])
def test_compose_reference_failure_reports_status_and_bounded_redacted_stderr(tmp_path, monkeypatch, config_exit):
    secret = "opaque-environment-secret"
    monkeypatch.setenv("SERVICE_API_KEY", secret)
    diagnostic = (f"invalid config {secret} Bearer bearer-credential "
                  "TOKEN=inline-credential password='quoted secret' "
                  "https://user:pass@example.invalid/ eyJabcdef.abcdefghi.signature\n" + "x" * 500)
    _b, res, _ = _run_issuer(tmp_path, monkeypatch, CONFIG_EXIT=str(config_exit),
                           CONFIG_NO_IMAGE="1", CONFIG_STDERR=diagnostic)
    out = res.stdout + res.stderr
    assert res.returncode != 0
    assert "steady_service_compensation_incomplete: dev-paper-principal-issuer" in out
    assert f"compose config exit status={config_exit}" in out
    excerpt = out.split("; stderr=", 1)[1].split(")", 1)[0]
    assert len(excerpt) == 300
    for value in (secret, "bearer-credential", "inline-credential", "quoted secret", "user:pass", "eyJabcdef"):
        assert value not in out
    assert "[REDACTED]" in excerpt and "[REDACTED_JWT]" in excerpt


def test_issuer_without_pre_deploy_container_keeps_empty_image_baseline(tmp_path, monkeypatch):
    monkeypatch.setenv("EXPECTED_STEADY_FOR_TEST", "source-ingest source-ingest-scheduler dev-paper-principal-issuer")
    baseline, result, final = _run(tmp_path, BEFORE, ISSUER_AFTER)
    assert "dev-paper-principal-issuer|" in baseline.read_text().splitlines()
    assert result.returncode == 0, result.stdout + result.stderr
    assert next(r for r in final if r["service"] == "dev-paper-principal-issuer")["image"] == CAND
    assert not (tmp_path / "tags.json").exists()
