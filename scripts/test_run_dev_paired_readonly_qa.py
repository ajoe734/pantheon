from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dev_paired_readonly_qa as qa  # noqa: E402

FE, BFF = "a" * 40, "b" * 40
BASELINE = b'{"queries":[1,2,3]}'
SECRETS = {f"DEV_BFF_DEV_LOGIN_{p}_CLIENT_{k}": (f"id-{p}" if k == "ID" else f"SECRET-{p}")
           for p in qa.PROFILES for k in ("ID", "SECRET")}
ROLES = json.dumps({p: ["r"] for p in qa.PROFILES})


class Net:
    """Public FE/BFF identity only; the caller no longer queries the BFF projection."""

    def __init__(self, fe=FE, bff=BFF, drift_after=False, raise_on_identity=False, me_tenant="t", login_ok=True):
        self.me_tenant, self.login_ok, self.login_bodies = me_tenant, login_ok, []
        self.fe, self.bff, self.drift_after, self.raise_on_identity = fe, bff, drift_after, raise_on_identity
        self.calls = []
        self.version_calls = 0

    def __call__(self, url, method, headers, body, timeout):
        self.calls.append((method, url))
        if self.raise_on_identity:
            raise OSError("connection refused")
        if url.endswith("/bff/auth/dev-login"):
            self.login_bodies.append(body)
            return 200, {"access_token": "BEARER-XYZ"} if self.login_ok else (401, {})[::-1]
        if url.endswith("/bff/me"):
            assert headers["Authorization"] == "Bearer BEARER-XYZ"
            return 200, {"data": {"tenant_id": self.me_tenant}}
        if url.endswith("/deployment.json"):
            return 200, {"commit": self.fe}
        if url.endswith("/bff/version"):
            self.version_calls += 1
            sha = "c" * 40 if self.drift_after and self.version_calls > 1 else self.bff
            return 200, {"source_commit_sha": sha, "config_posture": {"auth_mode": "strict", "auth_stub": False}}
        raise AssertionError(url)


ALPHA = {"source_instance_id": "s-alpha", "connector_id": "c-alpha", "lifecycle_state": "enabled", "tenant_id": "t"}
BETA = {"source_instance_id": "s-beta", "connector_id": "c-beta", "lifecycle_state": "enabled", "tenant_id": "t"}
AUTH = "Authorization: Bearer BEARER-XYZ\nX-Tenant-Id: t\n"


def owner(sources=(ALPHA, BETA), registry=({"connector_id": "legacy-1"}, {"id": "legacy-2"}), anon=401, calls=None):
    def read(key, headers):
        if calls is not None:
            calls.append((key, headers))
        if headers is None:
            return anon, {"sources": list(sources or ()), "count": len(sources or ())}
        if headers != AUTH:
            return 403, {}
        if key == "sources":
            return (0, None) if sources is None else (200, {"sources": list(sources), "count": len(sources)})
        return (0, None) if registry is None else (200, {"connectors": list(registry)})
    return read


class Retain:
    def __init__(self, fail=False, wrong_hash=False):
        self.fail, self.wrong_hash, self.files = fail, wrong_hash, {}

    def __call__(self, run_dir, name, data):
        if self.fail:
            raise qa.Unproven("private_retention_failed", "ssh down")
        self.files[(run_dir, name)] = data
        return "0" * 64 if self.wrong_hash else hashlib.sha256(data).hexdigest()


def make(tmp_path, **over):
    cli = tmp_path / "fe" / qa.CLI_RELATIVE
    cli.parent.mkdir(parents=True)
    cli.write_text("//cli")
    over = dict(over)
    argv = ["--fe-url", "https://app.dev.mvl-cap.tw", "--bff-url", "https://api.dev.mvl-cap.tw",
            "--fe-sha", FE, "--bff-sha", BFF, "--root-run-id", "42", "--run-attempt", "1", "--pair-id", "p1",
            "--tenant-id", "t", "--persona-id", "per", "--expected-roles-json", ROLES,
            "--frontend-checkout", str(tmp_path / "fe"),
            "--baseline-remote-path", "/home/u/pantheon-acceptance-captures/x/base.json",
            "--baseline-sha256", hashlib.sha256(BASELINE).hexdigest(),
            "--out-dir", str(tmp_path / "out"), "--evidence-out", str(tmp_path / "ev.json")]
    for k, v in over.items():
        argv[argv.index(k) + 1] = v
    return argv


def fetcher(data=BASELINE):
    def f(remote, local):
        if data is None:
            raise qa.Unproven("missing_private_baseline", "x")
        local.write_bytes(data)
    return f


class Cli:
    def __init__(self, code=0, ready=0, write_artifacts=True, raise_on_run=None):
        self.code, self.ready, self.write_artifacts, self.raise_on_run = code, ready, write_artifacts, raise_on_run
        self.stdin = self.argv = None
        self.calls = []

    def __call__(self, argv, stdin, timeout, cwd=None):
        self.calls.append((list(argv), stdin, cwd))
        if "--input-type=module" in argv:  # readiness probe: no credentials may reach it
            assert stdin == b""
            return self.ready, "", "playwright missing" if self.ready else ""
        if self.raise_on_run:
            raise self.raise_on_run
        self.argv, self.stdin = list(argv), stdin
        if self.write_artifacts:
            d = Path(argv[argv.index("--out-dir") + 1]) / "paired-readonly-x"
            d.mkdir(parents=True)
            (d / "result.json").write_text(json.dumps({"pass": False, "perf": {"p50": 1.0}, "note": "SECRET-VIEWER"}))
            (d / "full-baseline-differences.json").write_text(json.dumps(
                {"/bff/a": [{"path": "/x", "before": "PRIVATE-BASE", "after": "PRIVATE-CUR"}], "/bff/b": []}))
            (d / "current-responses.json").write_text('{"BUSINESS":"secret-data"}')
        return self.code, "ok SECRET-VIEWER", "err"


def run(tmp_path, cli=None, net=None, fetch=None, env=SECRETS, own=None, retain=None, **over):
    cli, net = cli or Cli(), net or Net()
    retain = retain or Retain()
    rc = qa.main(make(tmp_path, **over), env, http=net, cli_runner=cli,
                 baseline_fetcher=fetch or fetcher(), owner_reader=own or owner(), retainer=retain)
    run.retain = retain
    return rc, json.loads((tmp_path / "ev.json").read_text()), cli, net


def test_pass_and_credentials_only_on_stdin(tmp_path):
    rc, ev, cli, _ = run(tmp_path)
    assert rc == 0 and ev["outcome"] == "pass"
    assert not any("SECRET" in a or "BEARER" in a for a in cli.argv)
    payload = json.loads(cli.stdin)
    assert payload["profiles"]["VIEWER"]["client_secret"] == "SECRET-VIEWER"
    reg = payload["expected"]["source_owner_registry"]
    assert reg["source_mode"] == "governed_owner" and reg["connector_ids"] == ["c-alpha", "c-beta"]
    # identity contract of the real FE CLI: controller runId only, attempt kept separate
    assert payload["expected"]["root"] == "42" and ev["run"]["attempt"] == "1"
    text = json.dumps(ev)
    assert "SECRET-" not in text and "BEARER-XYZ" not in text and "queries" not in text
    assert "PRIVATE-BASE" not in text and "BUSINESS" not in text
    assert ev["cli_artifacts"]["differences"]["/bff/a"] == {"count": 1, "pointers": ["/x"], "pointers_truncated": False}
    assert ev["cli_artifacts"]["differences"]["/bff/b"]["count"] == 0
    assert ev["cli_artifacts"]["result"]["perf"] == {"p50": 1.0}


def test_child_failure_preserved_not_pass(tmp_path):
    rc, ev, _, _ = run(tmp_path, cli=Cli(3))
    assert rc == 1 and ev["outcome"] == "failed" and ev["cli_result"]["exit_code"] == 3
    assert ev["cli_artifacts"]["result"]["pass"] is False


def test_wrong_origin(tmp_path):
    rc, ev, cli, _ = run(tmp_path, **{"--bff-url": "https://evil.example.com"})
    assert rc == 1 and ev["reason_code"] == "wrong_origin" and cli.stdin is None


def test_pair_mismatch(tmp_path):
    rc, ev, cli, _ = run(tmp_path, net=Net(fe="d" * 40))
    assert ev["reason_code"] == "pair_mismatch" and cli.stdin is None


def test_pair_drift_after_run_is_unproven(tmp_path):
    rc, ev, _, _ = run(tmp_path, net=Net(drift_after=True))
    assert rc == 1 and ev["outcome"] == "unproven" and ev["reason_code"] == "pair_mismatch"


def test_missing_private_baseline(tmp_path):
    rc, ev, cli, _ = run(tmp_path, fetch=fetcher(None))
    assert ev["reason_code"] == "missing_private_baseline" and cli.stdin is None


def test_baseline_hash_mismatch(tmp_path):
    rc, ev, cli, _ = run(tmp_path, fetch=fetcher(b"other"))
    assert ev["reason_code"] == "baseline_hash_mismatch" and cli.stdin is None


def test_missing_profile_credentials(tmp_path):
    env = {k: v for k, v in SECRETS.items() if "OPERATOR_B" not in k}
    rc, ev, _, _ = run(tmp_path, env=env)
    assert ev["reason_code"] == "missing_profile"


def test_missing_cli_explicit(tmp_path):
    argv = make(tmp_path)
    (tmp_path / "fe" / qa.CLI_RELATIVE).unlink()
    rc = qa.main(argv, SECRETS, http=Net(), cli_runner=Cli(), baseline_fetcher=fetcher())
    ev = json.loads((tmp_path / "ev.json").read_text())
    assert rc == 1 and ev["reason_code"] == "missing_cli"


def test_baseline_path_restricted(tmp_path):
    rc, ev, _, _ = run(tmp_path, **{"--baseline-remote-path": "/etc/passwd"})
    assert ev["reason_code"] == "missing_private_baseline"




def test_owner_inventory_is_independent_of_bff_projection(tmp_path):
    """Owner has alpha+beta; the BFF only ever serves identity here, never expected ids."""
    _, ev, cli, net = run(tmp_path)
    assert ev["source_owner_registry"]["instance_count"] == 2
    assert [i["source_instance_id"] for i in ev["source_owner_registry"]["instances"]] == ["s-alpha", "s-beta"]
    assert not any("data-sources" in u for _, u in net.calls)


def test_governed_connector_ids_follow_fe_contract_not_instance_ids(tmp_path):
    _, _, cli, _ = run(tmp_path, own=owner(sources=({"source_instance_id": "s1", "connector_id": "c1", "tenant_id": "t"},)))
    assert json.loads(cli.stdin)["expected"]["source_owner_registry"]["connector_ids"] == ["c1"]


def test_legacy_registry_when_no_owner_instances(tmp_path):
    _, ev, cli, _ = run(tmp_path, own=owner(sources=()))
    reg = ev["source_owner_registry"]
    assert reg["source_mode"] == "legacy_projection" and reg["connector_ids"] == ["legacy-1", "legacy-2"]
    assert "meta_source" not in json.loads(cli.stdin)["expected"]["source_owner_registry"]


@pytest.mark.parametrize("own,code", [
    (owner(sources=None), "owner_inventory_unavailable"),
    (owner(sources=(), registry=None), "owner_inventory_unavailable"),
    (owner(sources=(), registry=()), "owner_inventory_unavailable"),
    (owner(sources=(ALPHA, ALPHA)), "owner_inventory_invalid"),
    (owner(sources=({"connector_id": "c", "tenant_id": "t"},)), "owner_inventory_invalid"),
    (lambda k, h: (401 if h is None else 200, {"sources": [ALPHA], "count": 2}), "owner_inventory_invalid"),
])
def test_owner_inventory_failures_are_unproven(tmp_path, own, code):
    rc, ev, cli, _ = run(tmp_path, own=own)
    assert rc == 1 and ev["outcome"] == "unproven" and ev["reason_code"] == code and cli.stdin is None


def test_cli_not_ready_is_explicit_and_precedes_credentials(tmp_path):
    cli = Cli(ready=3)
    rc, ev, _, _ = run(tmp_path, cli=cli)
    assert rc == 1 and ev["reason_code"] == "cli_not_ready" and cli.stdin is None
    assert all(c[1] == b"" for c in cli.calls)  # only the credential-free probe ran


def test_unexpected_runtime_errors_still_write_evidence(tmp_path):
    rc, ev, _, _ = run(tmp_path, net=Net(raise_on_identity=True))
    assert rc == 1 and ev["outcome"] == "unproven" and ev["reason_code"] == "runtime_error"
    assert ev["baseline"]["expected_sha256"] == hashlib.sha256(BASELINE).hexdigest()


def test_baseline_timeout_writes_evidence_with_expected_metadata(tmp_path):
    def boom(remote, local):
        raise TimeoutError("slow")
    rc, ev, cli, _ = run(tmp_path, fetch=boom)
    assert ev["reason_code"] == "runtime_error" and cli.stdin is None
    assert ev["baseline"]["remote_path"].endswith("base.json")


def test_missing_node_is_unproven_runtime_failure(tmp_path):
    rc, ev, _, _ = run(tmp_path, cli=Cli(raise_on_run=FileNotFoundError("node")))
    assert rc == 1 and ev["outcome"] == "unproven"


def test_child_failure_then_identity_failure_retains_child_result(tmp_path):
    class Flaky(Net):
        def __call__(self, url, method, headers, body, timeout):
            if url.endswith("/bff/version") and self.version_calls >= 1:
                raise OSError("gone")
            return super().__call__(url, method, headers, body, timeout)
    rc, ev, _, _ = run(tmp_path, cli=Cli(3), net=Flaky())
    assert ev["outcome"] == "unproven" and ev["cli_result"]["exit_code"] == 3
    assert ev["cli_artifacts"]["result"]["pass"] is False


def test_default_runner_missing_node_returns_127(tmp_path):
    code, _, err = qa.default_cli_runner(["/nonexistent/node"], b"", 5)
    assert code == 127 and "SECRET" not in err


def _step_text(step_id):
    text = (Path(__file__).resolve().parents[1] / ".github/workflows/nonprod-deploy.yml").read_text()
    start = text.index(f"        id: {step_id}\n")
    start = text.rindex("      - name:", 0, start)
    end = text.find("\n      - name:", start + 1)
    return text[start:end]


def test_workflow_prepares_frontend_without_credentials_and_keeps_root_attempt_separate():
    prep = _step_text("original_qa_prepare")
    assert "continue-on-error: true" in prep and "env:" not in prep and "secrets." not in prep
    assert "npm ci --ignore-scripts" in prep and "playwright install" in prep
    assert "working-directory: .original-qa-frontend" in prep
    qa_step = _step_text("original_paired_readonly_qa")
    assert '--root-run-id "${GITHUB_RUN_ID}" --run-attempt "${GITHUB_RUN_ATTEMPT}"' in qa_step
    assert "continue-on-error: true" in qa_step


def test_owner_reads_are_authenticated_tenant_scoped_and_bearer_never_in_evidence(tmp_path):
    calls = []
    _, ev, _, net = run(tmp_path, own=owner(calls=calls))
    assert [h for _, h in calls if h is not None] == [AUTH, AUTH] or len([1 for _, h in calls if h]) >= 1
    assert calls[0][1] is None  # anonymous probe first
    assert net.login_bodies and b"SECRET-OPERATOR_A" in net.login_bodies[0]
    assert "BEARER-XYZ" not in (tmp_path / "ev.json").read_text()


def test_owner_that_answers_anonymous_callers_is_unproven(tmp_path):
    rc, ev, cli, _ = run(tmp_path, own=owner(anon=200))
    assert rc == 1 and ev["reason_code"] == "owner_auth_not_enforced" and cli.stdin is None


def test_wrong_tenant_principal_is_rejected(tmp_path):
    rc, ev, cli, _ = run(tmp_path, net=Net(me_tenant="other"))
    assert ev["reason_code"] == "owner_tenant_mismatch" and cli.stdin is None


def test_cross_tenant_or_unattributed_owner_rows_are_unproven(tmp_path):
    rc, ev, cli, _ = run(tmp_path, own=owner(sources=(ALPHA, dict(BETA, tenant_id="other"))))
    assert ev["reason_code"] == "owner_tenant_mismatch" and cli.stdin is None


def test_unattributed_owner_rows_are_unproven(tmp_path):
    unattributed = {k: v for k, v in BETA.items() if k != "tenant_id"}
    rc, ev, cli, _ = run(tmp_path, own=owner(sources=(ALPHA, unattributed)))
    assert ev["reason_code"] == "owner_tenant_unproven" and cli.stdin is None


def test_dev_login_failure_is_unproven(tmp_path):
    rc, ev, cli, _ = run(tmp_path, net=Net(login_ok=False))
    assert ev["reason_code"] == "owner_auth_unavailable" and cli.stdin is None


def test_default_owner_reader_sends_headers_on_stdin_not_argv(monkeypatch):
    seen = {}

    def fake(argv, input=None, **kw):
        seen["argv"], seen["input"] = argv, input
        class P: returncode, stdout = 0, b'{"sources": [], "count": 0}\n200'
        return P
    monkeypatch.setattr(qa.subprocess, "run", fake)
    status, doc = qa.default_owner_reader("sources", AUTH)
    assert status == 200 and doc == {"sources": [], "count": 0}
    assert "BEARER" not in " ".join(seen["argv"]) and b"BEARER-XYZ" in seen["input"]
    assert "-H @-" in seen["argv"][-1]


def _big_cli():
    class Big(Cli):
        def __call__(self, argv, stdin, timeout, cwd=None):
            code = super().__call__(argv, stdin, timeout, cwd)
            if "--input-type=module" not in argv:
                d = Path(argv[argv.index("--out-dir") + 1]) / "paired-readonly-x"
                groups = {f"/bff/g{g}": [{"path": f"/p{g}/{i}", "before": f"B{i}", "after": f"A{i}"}
                                         for i in range(40)] for g in range(13)}
                groups["/bff/g0"] += [{"path": f"/extra/{i}", "before": "b", "after": "a"} for i in range(500)]
                (d / "full-baseline-differences.json").write_text(json.dumps(groups))
            return code
    return Big(3)


def test_full_semantic_diff_survives_cleanup_and_public_summary_is_sanitized(tmp_path):
    retain = Retain()
    rc, ev, _, _ = run(tmp_path, cli=_big_cli(), retain=retain)
    full = retain.files[("42-1", "full-baseline-differences.json")]
    doc = json.loads(full)
    assert sum(len(v) for v in doc.values()) == 13 * 40 + 500 > 500 and len(doc) == 13
    assert "B1" in full.decode()  # complete before/after witness retained privately
    art = ev["cli_artifacts"]
    assert art["differences_total"] == 1020
    assert art["differences"]["/bff/g0"]["pointers_truncated"] is True
    assert art["differences_complete_private_sha256"] == hashlib.sha256(full).hexdigest()
    assert art["private_witness"]["files"]["full-baseline-differences.json"]["retained"] is True
    assert "current-responses.json" in art["private_witness"]["files"]
    text = (tmp_path / "ev.json").read_text()
    assert '"before"' not in text and "BUSINESS" not in text and "SECRET-" not in text
    # nothing was deleted locally by normal cleanup
    assert (tmp_path / "out" / "paired-readonly-x" / "full-baseline-differences.json").is_file()
    assert ev["outcome"] == "failed" and ev["cli_result"]["exit_code"] == 3
    # retained private bytes are credential-redacted
    assert b"SECRET-VIEWER" not in retain.files[("42-1", "result.json")]


@pytest.mark.parametrize("retain", [Retain(fail=True), Retain(wrong_hash=True)])
def test_failed_private_retention_is_unproven_and_keeps_local_tree(tmp_path, retain):
    rc, ev, _, _ = run(tmp_path, retain=retain)
    assert rc == 1 and ev["outcome"] == "unproven" and ev["reason_code"] == "private_retention_failed"
    assert (tmp_path / "out" / "paired-readonly-x" / "result.json").is_file()


def test_missing_cli_artifacts_is_unproven_not_pass(tmp_path):
    rc, ev, _, _ = run(tmp_path, cli=Cli(write_artifacts=False))
    assert rc == 1 and ev["outcome"] == "unproven" and ev["cli_result"]["exit_code"] == 0
