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
    def __init__(self, fe=FE, bff=BFF, drift_after=False, inv_mode="service_client"):
        self.fe, self.bff, self.drift_after, self.inv_mode = fe, bff, drift_after, inv_mode
        self.calls = []
        self.version_calls = 0

    def __call__(self, url, method, headers, body, timeout):
        self.calls.append((method, url))
        if url.endswith("/deployment.json"):
            return 200, {"commit": self.fe}
        if url.endswith("/bff/version"):
            self.version_calls += 1
            sha = "c" * 40 if self.drift_after and self.version_calls > 1 else self.bff
            return 200, {"source_commit_sha": sha, "config_posture": {"auth_mode": "strict", "auth_stub": False}}
        if url.endswith("/bff/auth/dev-login"):
            return 200, {"access_token": "TOKEN-xyz"}
        if "/bff/management/data-sources" in url:
            return 200, {"meta": {"source": self.inv_mode},
                         "data": {"items": [{"source_instance_id": "s1", "connector_id": "c1"}]},
                         "page_info": {}}
        if url.endswith("/bff/logout"):
            return 200, {}
        raise AssertionError(url)


def make(tmp_path, **over):
    cli = tmp_path / "fe" / qa.CLI_RELATIVE
    cli.parent.mkdir(parents=True)
    cli.write_text("//cli")
    argv = ["--fe-url", "https://app.dev.mvl-cap.tw", "--bff-url", "https://api.dev.mvl-cap.tw",
            "--fe-sha", FE, "--bff-sha", BFF, "--root-run-id", "42", "--pair-id", "p1",
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
    def __init__(self, code=0):
        self.code, self.stdin, self.argv = code, None, None

    def __call__(self, argv, stdin, timeout):
        self.argv, self.stdin = list(argv), stdin
        return self.code, "ok SECRET-VIEWER", "err"


def run(tmp_path, cli=None, net=None, fetch=None, env=SECRETS, **over):
    cli, net = cli or Cli(), net or Net()
    rc = qa.main(make(tmp_path, **over), env, http=net, cli_runner=cli,
                 baseline_fetcher=fetch or fetcher())
    return rc, json.loads((tmp_path / "ev.json").read_text()), cli, net


def test_pass_and_credentials_only_on_stdin(tmp_path):
    rc, ev, cli, _ = run(tmp_path)
    assert rc == 0 and ev["outcome"] == "pass"
    assert not any("SECRET" in a for a in cli.argv)
    payload = json.loads(cli.stdin)
    assert payload["profiles"]["VIEWER"]["client_secret"] == "SECRET-VIEWER"
    assert payload["expected"]["source_owner_registry"]["source_mode"] == "governed_owner"
    text = json.dumps(ev)
    assert "SECRET-" not in text and "TOKEN-xyz" not in text and "queries" not in text


def test_child_failure_preserved_not_pass(tmp_path):
    rc, ev, _, _ = run(tmp_path, cli=Cli(3))
    assert rc == 1 and ev["outcome"] == "failed" and ev["cli_result"]["exit_code"] == 3


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


def test_legacy_projection_mode_reported(tmp_path):
    _, ev, _, _ = run(tmp_path, net=Net(inv_mode="legacy_projection"))
    assert ev["source_owner_registry"]["source_mode"] == "legacy_projection"
    assert ev["source_owner_registry"]["connector_ids"] == ["c1"]


def test_only_permitted_writes(tmp_path):
    _, _, _, net = run(tmp_path)
    writes = {u.split("mvl-cap.tw")[1] for m, u in net.calls if m != "GET"}
    assert writes <= {"/bff/auth/dev-login", "/bff/logout"}
