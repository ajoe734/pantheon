import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import dev_acceptance_stage as stage  # noqa: E402

SHA_A, SHA_B = "a" * 40, "b" * 40
IDENT = {"backend_sha": SHA_A, "frontend_sha": SHA_B, "fe_url": "https://fe", "bff_url": "https://bff"}
OK = lambda **_: {"status": "passed", "detail": ""}  # noqa: E731


def probe(directory, name, body, mode=0o755):
    path = directory / name
    path.write_text(body)
    path.chmod(mode)
    return path


def test_discover_probes_rules(tmp_path):
    probe(tmp_path, "a", "#!/bin/sh\n# acceptance-owner: TASK-1\n")
    probe(tmp_path, "noexec", "#!/bin/sh\n", mode=0o644)
    probe(tmp_path, ".hidden", "x")
    probe(tmp_path, "notes.md", "x")
    (tmp_path / "sub").mkdir()
    found = {p["id"]: p for p in stage.discover_probes(tmp_path)}
    assert set(found) == {"probe:a", "probe:noexec"}
    assert found["probe:a"]["owner"] == "TASK-1" and found["probe:a"]["executable"]
    assert not found["probe:noexec"]["executable"] and found["probe:noexec"]["owner"] is None
    assert stage.discover_probes(tmp_path / "missing") == []


def test_real_probe_exit_codes_and_verdict(tmp_path):
    for name, code in (("ok", 0), ("skip", 77), ("bad", 3)):
        probe(tmp_path, name, f"#!/bin/sh\n# acceptance-owner: OWN-{name}\necho out-{name}\nexit {code}\n")
    probe(tmp_path, "noowner", "#!/bin/sh\nexit 0\n")
    results = stage.run_stage(checks=[], probes=stage.discover_probes(tmp_path), identity=IDENT,
                              base_env=dict(os.environ), lease_held=False, identity_fn=OK)
    by_id = {r["id"]: r for r in results}
    assert by_id["probe:ok"]["status"] == "passed"
    assert by_id["probe:skip"]["status"] == "skipped"
    assert by_id["probe:bad"]["status"] == "failed" and "out-bad" in by_id["probe:bad"]["detail"]
    assert by_id["probe:noowner"]["status"] == "failed"
    verdict = stage.build_verdict(status=stage.verdict_status(results), results=results, identity=IDENT, run={})
    assert verdict["acceptance"] == "failed"
    assert {(f["id"], f["owner"]) for f in verdict["failed"]} == {("probe:bad", "OWN-bad"), ("probe:noowner", "UNDECLARED")}


def test_probe_receives_pair_environment(tmp_path):
    probe(tmp_path, "env", "#!/bin/sh\n# acceptance-owner: OWN\n[ \"$ACCEPTANCE_BACKEND_SHA\" = " + SHA_A + " ] && [ \"$ACCEPTANCE_BFF_URL\" = https://bff ]\n")
    results = stage.run_stage(checks=[], probes=stage.discover_probes(tmp_path), identity=IDENT,
                              base_env=dict(os.environ), lease_held=False, identity_fn=OK)
    assert results[-1]["status"] == "passed"


def test_mutating_check_needs_lease_and_leaves_verdict_pending():
    calls = []
    check = {"id": "check:m", "kind": "check", "owner": "OWN", "mutates": True, "argv": ["x"], "timeout": 5}
    run = lambda argv, env, timeout: calls.append(argv) or (0, "")  # noqa: E731
    res = stage.run_stage(checks=[check], probes=[], identity=IDENT, base_env={}, lease_held=False,
                          runner=run, identity_fn=OK)
    assert calls == [] and res[-1]["status"] == "skipped"
    assert stage.verdict_status(res) == "pending"
    res = stage.run_stage(checks=[check], probes=[], identity=IDENT, base_env={}, lease_held=True,
                          runner=run, identity_fn=OK)
    assert calls == [["x"]] and stage.verdict_status(res) == "passed"


def test_identity_check_detects_drift_and_unreachable():
    good = {"https://fe/deployment.json": {"deploymentState": "accepted", "bffCommit": SHA_A, "frontendSha": SHA_B},
            "https://bff/bff/version": {"source_commit_sha": SHA_A}}
    ok = stage.identity_check(fe_url="https://fe", bff_url="https://bff", backend_sha=SHA_A, frontend_sha=SHA_B,
                              fetch=good.__getitem__)
    assert ok["status"] == "passed"
    bad = {**good, "https://bff/bff/version": {"source_commit_sha": SHA_B}}
    assert stage.identity_check(fe_url="https://fe", bff_url="https://bff", backend_sha=SHA_A, frontend_sha=SHA_B,
                                fetch=bad.__getitem__)["status"] == "failed"

    def boom(**_):
        raise OSError("down")
    res = stage.run_stage(checks=[], probes=[], identity=IDENT, base_env={}, lease_held=False, identity_fn=boom)
    assert res[0]["status"] == "failed" and "down" in res[0]["detail"]


def test_load_checks_validates(tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"checks": [{"id": "x", "owner": "OWN-1", "argv": ["true"], "mutates": True}]}))
    assert stage.load_checks(path)[0]["mutates"] is True
    assert stage.plan(stage.load_checks(path), [])["mutating"] is True
    path.write_text(json.dumps({"checks": [{"id": "x", "argv": ["true"]}]}))
    with pytest.raises(ValueError):
        stage.load_checks(path)


def test_cli_never_fails_and_bounds_growth(tmp_path, monkeypatch):
    out = tmp_path / "v.json"
    bad = tmp_path / "checks.json"
    bad.write_text("{not json")
    monkeypatch.setattr(stage, "identity_check", OK)
    argv = ["run", "--checks", str(bad), "--probes-dir", str(tmp_path / "none"), "--backend-sha", SHA_A,
            "--frontend-sha", SHA_B, "--fe-url", "https://fe", "--bff-url", "https://bff", "--verdict-out", str(out)]
    assert stage.main(argv) == 0  # a broken declaration is a failed verdict, never a crash
    first = json.loads(out.read_text())
    assert first["acceptance"] == "failed" and first["failed"][0]["owner"] == stage.SELF_OWNER
    assert stage.main(argv) == 0
    assert out.read_text().count("schemaVersion") == 1  # replaced, not appended
    assert stage.main(["pending", *argv[1:]]) == 0
    assert json.loads(out.read_text())["acceptance"] == "pending"


def test_detail_is_truncated_and_checks_capped(tmp_path):
    big = stage._result({"id": "c", "kind": "check", "owner": "O"}, "failed", detail="x" * 10000)
    assert len(big["detail"]) == stage.DETAIL_LIMIT
    probes = [{"id": f"probe:{i}", "kind": "probe", "path": tmp_path / "p", "owner": "O", "executable": True}
              for i in range(300)]
    res = stage.run_stage(checks=[], probes=probes, identity=IDENT, base_env={}, lease_held=False,
                          runner=lambda *a: (0, ""), identity_fn=OK)
    assert len(res) == stage.MAX_CHECKS


def test_repo_probe_dir_is_valid():
    root = Path(__file__).resolve().parent.parent / "acceptance"
    probes = stage.discover_probes(root / "probes")
    assert probes and all(p["owner"] and p["executable"] for p in probes)
    assert stage.load_checks(root / "checks.json") == []
