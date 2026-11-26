#!/usr/bin/env python3
"""Post-deploy acceptance stage for the dev pair (RC1 step 5).

The stage runs AFTER a deploy has been switched and verified.  It never rolls
anything back and never fails the run: every outcome, including an internal
error, becomes the ``acceptance`` verdict (pending / passed / failed) and the
process exits 0.  ``deploymentState`` of the hosted manifest is not touched.

Inputs
  * declared checks   ``acceptance/checks.json``  (id, owner, mutates, argv)
  * feature probes    executable files directly inside ``acceptance/probes/``
    (contract: ``acceptance/README.md``)
  * one built-in read-only check, ``deployed-pair-identity``
  * after the probes, the existing execute-plans management page crawl
    (``scripts/accept-management-hosted-production.mjs`` at the deployed
    frontend commit) as ``check:management-crawl``, read-only, as the viewer

Growth bound: the verdict is ONE small JSON document that the caller replaces
(latest result per release).  Per-check output is truncated and the number of
checks is capped, so nothing here can grow without bound.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "pantheon.dev-acceptance-verdict.v1"
SELF_OWNER = "DEV-DEPLOY-ACCEPTANCE-STAGE-20261117"
SKIP_EXIT = 77
DETAIL_LIMIT = 2000
MAX_CHECKS = 200
DEFAULT_TIMEOUT = 300
OWNER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,127}$")
OWNER_HEADER_RE = re.compile(r"^\s*(?:#|//)\s*acceptance-owner:\s*(\S+)\s*$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
HEADER_LINES = 20

# Post-switch management page crawl (ACCEPTANCE-STAGE-POST-SWITCH-CRAWL-20261126).
CRAWL_ID = "check:management-crawl"
CRAWL_OWNER = "ACCEPTANCE-STAGE-POST-SWITCH-CRAWL-20261126"
CRAWL_SCRIPT = "scripts/accept-management-hosted-production.mjs"
CRAWL_SCHEMA = "pantheon.dev-acceptance-crawl.v1"
# The pre-switch crawl step (execute-plans Pantheon FE-BFF Integration Gate, "Management hosted
# production acceptance") took 328-421 s in the six gate runs of 2026-11-24 that minted a credential
# (45364275324 421 s, 45401183199 361 s, 45428583657 373 s, 45453849299 328 s, 45472712064 334 s,
# 45500113085 374 s). Limit: the longest plus about 70 % margin.
CRAWL_DEADLINE_SECONDS = 720
CRAWL_ROUTE_TIMEOUT_MS = "45000"   # same per-route limits as the pre-switch gate step
CRAWL_SETTLE_TIMEOUT_MS = "20000"
MAX_CRAWL_ROUTES = 300
MAX_FAILED_ROUTES = 50
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
FAILING_MOCK_FINDINGS = {"seed_fallback_armed", "mock_write_success"}
# Failed-read detail per failed route (ACCEPTANCE-CRAWL-CARRY-FAILED-READS-20261126). execute-plans
# #1108 records routes[].failedReads (counted first, capped 12/route and 300/run upstream); the release
# artifact keeps an allowlisted copy capped at 12 per failed route and 100 per run, so a bad release
# adds at most ~100 small entries. Ok routes carry nothing extra.
FAILED_READ_CAP_PER_ROUTE = 12
FAILED_READ_CAP_PER_RUN = 100
FAILED_READ_REASONS = {"http_status", "network_error", "pending_response", "pending_body",
                       "unexpected_status", "invalid_body"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


REDACTED = "[redacted]"
SECRET_NAME_SUFFIXES = ("_SECRET", "_TOKEN", "_PASSWORD")


def redact(text: str, env: dict) -> str:
    """Replace every non-empty secret-like environment value (and the viewer secret) with [redacted]."""
    names = {"ACCEPTANCE_VIEWER_CLIENT_SECRET"}
    names.update(name for name in env if name.endswith(SECRET_NAME_SUFFIXES))
    values = sorted({env[name] for name in names if env.get(name)}, key=len, reverse=True)
    for value in values:
        text = (text or "").replace(value, REDACTED)
    return text or ""


def _clip(text: str) -> str:
    text = (text or "").strip()
    return text if len(text) <= DETAIL_LIMIT else text[-DETAIL_LIMIT:]


def read_owner(path: Path) -> str | None:
    """Owning task or chain id declared in the first lines of a probe file."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for _ in range(HEADER_LINES):
                match = OWNER_HEADER_RE.match(handle.readline())
                if match and OWNER_RE.match(match.group(1)):
                    return match.group(1)
    except OSError:
        pass
    return None


def discover_probes(directory: Path) -> list[dict]:
    """Executable regular files directly inside ``directory`` (no recursion, no dotfiles)."""
    if not directory.is_dir():
        return []
    found = []
    for path in sorted(directory.iterdir(), key=lambda item: item.name):
        if path.name.startswith(".") or path.name.endswith((".md", ".json")):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        found.append({"id": f"probe:{path.name}", "kind": "probe", "path": path,
                      "owner": read_owner(path), "executable": os.access(path, os.X_OK)})
    return found


def load_checks(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    checks = []
    for item in document.get("checks", []):
        if not (isinstance(item.get("id"), str) and isinstance(item.get("argv"), list) and item["argv"]
                and all(isinstance(part, str) for part in item["argv"])
                and isinstance(item.get("owner"), str) and OWNER_RE.match(item["owner"])):
            raise ValueError(f"invalid acceptance check declaration: {item!r}")
        checks.append({"id": f"check:{item['id']}", "kind": "check", "owner": item["owner"],
                       "mutates": bool(item.get("mutates", False)), "argv": item["argv"],
                       "timeout": int(item.get("timeout", DEFAULT_TIMEOUT))})
    return checks


def plan(checks: list[dict], probes: list[dict]) -> dict:
    return {"checks": len(checks), "probes": len(probes), "redacts_output": True,
            "mutating": any(check["mutates"] for check in checks)}


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def identity_check(*, fe_url: str, bff_url: str, backend_sha: str, frontend_sha: str, fetch=_fetch_json) -> dict:
    """Read-only: the hosted pair still serves exactly the pair that was deployed."""
    manifest = fetch(f"{fe_url.rstrip('/')}/deployment.json")
    version = fetch(f"{bff_url.rstrip('/')}/bff/version")
    problems = []
    if manifest.get("deploymentState") not in {"accepted", "functional-accepted"}:
        problems.append(f"deploymentState={manifest.get('deploymentState')!r}")
    if manifest.get("bffCommit") != backend_sha:
        problems.append("hosted manifest backend SHA differs from the deployed pair")
    if manifest.get("frontendSha") != frontend_sha:
        problems.append("hosted manifest frontend SHA differs from the deployed pair")
    if version.get("source_commit_sha") != backend_sha:
        problems.append("/bff/version source_commit_sha differs from the deployed backend")
    return {"status": "failed" if problems else "passed", "detail": "; ".join(problems)}


def _run(argv: list[str], env: dict, timeout: int) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except OSError as exc:
        return 126, f"could not execute: {exc}"
    return done.returncode, _clip(redact((done.stdout or "") + (done.stderr or ""), env))


def _result(item: dict, status: str, *, exit_code: int | None = None, detail: str = "", seconds: float = 0.0) -> dict:
    return {"id": item["id"], "kind": item["kind"], "owner": item.get("owner") or "UNDECLARED",
            "status": status, "exit_code": exit_code, "detail": _clip(detail), "seconds": round(seconds, 2)}


def run_stage(*, checks: list[dict], probes: list[dict], identity: dict, base_env: dict,
              lease_held: bool, runner=_run, identity_fn=identity_check, clock=time.monotonic) -> dict:
    """Returns the ``checks`` list. Never raises."""
    results = []
    probe_env = {**base_env,
                 "ACCEPTANCE_BACKEND_SHA": identity["backend_sha"],
                 "ACCEPTANCE_FRONTEND_SHA": identity["frontend_sha"],
                 "ACCEPTANCE_FE_URL": identity["fe_url"], "ACCEPTANCE_BFF_URL": identity["bff_url"]}
    started = clock()
    try:
        outcome = identity_fn(fe_url=identity["fe_url"], bff_url=identity["bff_url"],
                              backend_sha=identity["backend_sha"], frontend_sha=identity["frontend_sha"])
    except Exception as exc:  # advisory stage: an unreachable host is a failed check, not a crash
        outcome = {"status": "failed", "detail": f"identity read failed: {exc}"}
    results.append(_result({"id": "check:deployed-pair-identity", "kind": "check", "owner": SELF_OWNER},
                           outcome["status"], detail=outcome["detail"], seconds=clock() - started))
    for check in checks:
        if len(results) >= MAX_CHECKS:
            break
        if check["mutates"] and not lease_held:
            results.append(_result(check, "skipped", detail="mutating check needs the qualification lease, which is not held"))
            results[-1]["lease_required"] = True
            continue
        started = clock()
        code, output = runner(check["argv"], probe_env, check["timeout"])
        output = _clip(redact(output, probe_env))
        results.append(_result(check, "passed" if code == 0 else "failed", exit_code=code, detail=output,
                               seconds=clock() - started))
    for probe in probes:
        if len(results) >= MAX_CHECKS:
            break
        if not probe["owner"]:
            results.append(_result(probe, "failed", detail="probe lacks an '# acceptance-owner: <id>' header"))
            continue
        if not probe["executable"]:
            results.append(_result(probe, "failed", detail="probe is not executable"))
            continue
        started = clock()
        code, output = runner([str(probe["path"])], probe_env, DEFAULT_TIMEOUT)
        output = _clip(redact(output, probe_env))
        status = "passed" if code == 0 else "skipped" if code == SKIP_EXIT else "failed"
        results.append(_result(probe, status, exit_code=code, detail=output, seconds=clock() - started))
    return results


# --- post-switch management page crawl -------------------------------------------------------------

def _crawl_result(status: str, *, detail: str = "", seconds: float = 0.0, reason: str = "",
                  failed_routes: list | None = None, exit_code: int | None = None) -> dict:
    result = _result({"id": CRAWL_ID, "kind": "check", "owner": CRAWL_OWNER}, status, exit_code=exit_code,
                     detail=detail or reason, seconds=seconds)
    result["required"] = True  # skipped keeps the verdict pending; it is never a pass
    if reason:
        result["reason"] = reason
    if failed_routes is not None:
        result["failedRoutes"] = failed_routes[:MAX_FAILED_ROUTES]
    return result


def mint_viewer_token(bff_url: str, client_id: str, client_secret: str) -> str:
    """Exchange the existing read-only viewer identity at /bff/auth/dev-login; the token stays in memory."""
    body = json.dumps({"grant_type": "client_credentials", "client_id": client_id,
                       "client_secret": client_secret}).encode()
    request = urllib.request.Request(f"{bff_url.rstrip('/')}/bff/auth/dev-login", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - pinned dev BFF URL
            token = str(json.loads(response.read().decode("utf-8")).get("access_token") or "").strip()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"http_{exc.code}") from None
    except Exception as exc:  # never echo the body or the secret
        raise RuntimeError(f"request_failed:{type(exc).__name__}") from None
    if token.count(".") != 2:
        raise RuntimeError("no_access_token")
    return token


def run_crawl_process(argv: list[str], *, env: dict, cwd: Path, deadline: int) -> tuple[int | None, str]:
    """Run the crawl in its own process group; on the deadline kill the group (node and Chromium).

    Returns (exit code, output); exit code None means the deadline expired.
    """
    try:
        process = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, start_new_session=True)
    except OSError as exc:
        return 126, f"could not execute: {exc}"
    try:
        output, _ = process.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        output, _ = process.communicate()
        return None, output or ""
    return process.returncode, output or ""


def _route_problems(route: dict) -> list[str]:
    problems = []
    status = route.get("status")
    if status != "ok":
        problems.append(str(status))
    counts = route.get("consoleErrorCounts") or {}
    if counts.get("render_crash"):
        problems.append("render_crash_console")
    if counts.get("cors"):
        problems.append("cors_console")
    if (route.get("alias") or {}).get("directRenderFail"):
        problems.append("alias_direct_render")
    if route.get("detailHonestyViolations"):
        problems.append("detail_honesty")
    if any(isinstance(m, dict) and m.get("id") in FAILING_MOCK_FINDINGS for m in route.get("mockFindings") or []):
        problems.append("mock_finding")
    return problems


def _route_error(route: dict) -> str:
    """The render error that names the failure: first render-crash console line, else nav error or data reasons."""
    for entry in route.get("consoleErrors") or []:
        if isinstance(entry, dict) and entry.get("kind") == "render_crash":
            return str(entry.get("text") or "")[:240]
    if route.get("navError"):
        return str(route["navError"])[:240]
    reasons = (route.get("dataCheck") or {}).get("reasons") or []
    return ", ".join(str(r) for r in reasons)[:240]


def _read_path(value) -> str:
    """Pathname only: scheme, host, query (keys and values) and fragment are dropped."""
    text = str(value or "")
    path = urllib.parse.urlsplit(text).path if "://" in text else text.split("?", 1)[0].split("#", 1)[0]
    return path[:200]


def _failed_read(read: dict) -> dict:
    """Allowlisted copy of one execute-plans failedReads entry; every other field is dropped."""
    status = read.get("status")
    entry = {"method": str(read.get("method") or "GET").upper()[:10], "path": _read_path(read.get("path")),
             "status": status if isinstance(status, int) and not isinstance(status, bool) else None,
             "counted": read.get("counted") is True}
    reason = read.get("reason") if read.get("reason") in FAILED_READ_REASONS else None
    if entry["status"] is None:  # error name only: first token, no query, never the free-text message
        error = re.sub(r"\s+", " ", str(read.get("error") or "")).strip()
        entry["error"] = (error.split(" ", 1)[0].split("?", 1)[0] or reason or "request_failed")[:80]
    if reason:
        entry["reason"] = reason
    elapsed = read.get("elapsedMs")
    if isinstance(elapsed, (int, float)) and not isinstance(elapsed, bool):
        entry["elapsedMs"] = max(0, int(elapsed))
    return entry


def _failed_reads(route: dict, budget: int) -> tuple[list[dict], int]:
    """(kept reads, omitted count) for one failed route; counted reads first, capped per route and run."""
    reads = [r for r in route.get("failedReads") or [] if isinstance(r, dict)] \
        if isinstance(route.get("failedReads"), list) else []
    reads = [r for r in reads if r.get("counted") is True] + [r for r in reads if r.get("counted") is not True]
    keep = min(len(reads), FAILED_READ_CAP_PER_ROUTE, max(0, budget))
    upstream = route.get("failedReadsOmitted")
    upstream = upstream if isinstance(upstream, int) and not isinstance(upstream, bool) and upstream > 0 else 0
    return [_failed_read(r) for r in reads[:keep]], len(reads) - keep + upstream


def _first_counted_read(route: dict) -> str:
    read = next((r for r in route.get("failedReads") or [] if r.get("counted")), None)
    if not read:
        return ""
    outcome = read["status"] if read.get("status") is not None else read.get("error", "")
    elapsed = f" {read['elapsedMs']}ms" if "elapsedMs" in read else ""
    return f"; first failed read {read['method']} {read['path']} {outcome}{elapsed}"


def summarize_crawl(manifest: dict) -> dict:
    """Routes and statuses only: no token, no response body, no request URL query.

    Failed routes also carry a bounded, allowlisted copy of the crawl's failedReads
    (method, path, status or error name, counted, elapsedMs) and failedReadsOmitted.
    """
    routes, failed, non_get = [], [], set()
    read_budget = FAILED_READ_CAP_PER_RUN
    for route in (manifest.get("routes") or [])[:MAX_CRAWL_ROUTES]:
        if not isinstance(route, dict):
            continue
        problems = _route_problems(route)
        entry = {"path": str(route.get("path") or ""), "kind": str(route.get("kind") or ""),
                 "status": str(route.get("status") or ""),
                 "dataPass": bool((route.get("dataCheck") or {}).get("pass"))}
        if problems:
            entry["problems"] = problems
            failed_route = {"path": entry["path"], "status": entry["status"], "problems": problems,
                            "error": _route_error(route)}
            if "failedReads" in route or "failedReadsOmitted" in route:  # absent in pre-#1108 manifests
                reads, omitted = _failed_reads(route, read_budget)
                read_budget -= len(reads)
                failed_route["failedReads"] = reads
                failed_route["failedReadsOmitted"] = omitted
            failed.append(failed_route)
        routes.append(entry)
        for request in route.get("bffRequests") or []:
            method = str((request or {}).get("method") or "GET").upper()
            if method not in SAFE_METHODS:
                url = str((request or {}).get("url") or "")
                non_get.add(f"{method} {urllib.parse.urlsplit(url).path if '://' in url else url.split('?', 1)[0]}")
    result = manifest.get("result") or {}
    return {"pass": result.get("pass") is True, "overall": str(result.get("overall") or ""),
            "failures": [str(f)[:200] for f in (result.get("failures") or [])][:20],
            "routeCount": len(routes), "routes": routes, "failedRoutes": failed,
            "nonGetRequests": sorted(non_get)[:50]}


def _find_manifest(out_dir: Path) -> dict | None:
    candidates = sorted(out_dir.glob("management-hosted-acceptance-*.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None
    try:
        document = json.loads(candidates[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def crawl_check(*, frontend_dir: Path | None, prepared: str, identity: dict, identity_passed: bool,
                base_env: dict, work_dir: Path, deadline: int = CRAWL_DEADLINE_SECONDS,
                minter=mint_viewer_token, runner=run_crawl_process, clock=time.monotonic) -> tuple[dict, dict]:
    """Run the existing management crawl against the served pair. Returns (verdict check, crawl artifact).

    Never raises for an expected condition: every way the crawl cannot run is ``skipped`` with a reason.
    """
    artifact = {"schemaVersion": CRAWL_SCHEMA, "generatedAt": _now(), "deadlineSeconds": deadline,
                "frontendSha": identity["frontend_sha"], "backendSha": identity["backend_sha"],
                "feUrl": identity["fe_url"], "bffUrl": identity["bff_url"], "script": CRAWL_SCRIPT,
                "identity": "viewer", "readOnly": True}

    def skipped(reason: str, seconds: float = 0.0, detail: str = "") -> tuple[dict, dict]:
        artifact.update({"status": "skipped", "reason": reason, "seconds": round(seconds, 2)})
        return _crawl_result("skipped", reason=reason, detail=detail or f"skipped: {reason}", seconds=seconds), artifact

    if not identity_passed:
        return skipped("stack_not_verified")  # starts only once the served pair is the deployed pair
    if prepared != "success":
        return skipped("frontend_not_prepared", detail=f"skipped: frontend_not_prepared ({prepared or 'not run'})")
    if frontend_dir is None or not (frontend_dir / CRAWL_SCRIPT).is_file():
        return skipped("crawl_script_missing")
    client_id = base_env.get("ACCEPTANCE_VIEWER_CLIENT_ID", "").strip()
    client_secret = base_env.get("ACCEPTANCE_VIEWER_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        return skipped("viewer_credential_missing")
    try:
        token = minter(identity["bff_url"], client_id, client_secret)
    except Exception as exc:
        return skipped("viewer_login_failed", detail=f"skipped: viewer_login_failed ({redact(str(exc), base_env)})")
    out_dir = work_dir / "crawl-output"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = {"PATH": base_env.get("PATH", "/usr/local/bin:/usr/bin:/bin"), "HOME": base_env.get("HOME", "/tmp"),
           "LANG": "C.UTF-8", "CI": "1",
           "PANTHEON_FE_BASE_URL": identity["fe_url"], "PANTHEON_BFF_BASE_URL": identity["bff_url"],
           "PANTHEON_AUDIT_OUT_DIR": str(out_dir), "PANTHEON_LOAD_BASELINE_OUT_DIR": str(out_dir),
           "PANTHEON_ACCEPT_ROUTE_TIMEOUT_MS": CRAWL_ROUTE_TIMEOUT_MS,
           "PANTHEON_ACCEPT_SETTLE_TIMEOUT_MS": CRAWL_SETTLE_TIMEOUT_MS,
           "PANTHEON_ACCEPT_CLICK_WRITE_CTAS": "0", "BFF_AUTH_TOKEN": token}
    if base_env.get("PLAYWRIGHT_BROWSERS_PATH"):
        env["PLAYWRIGHT_BROWSERS_PATH"] = base_env["PLAYWRIGHT_BROWSERS_PATH"]
    redact_env = {**base_env, "CRAWL_BEARER_TOKEN": token}
    started = clock()
    code, output = runner(["node", CRAWL_SCRIPT, "--out-dir", str(out_dir)], env=env, cwd=frontend_dir,
                          deadline=deadline)
    seconds = clock() - started
    tail = _clip(redact(output, redact_env))
    if code is None:
        return skipped("crawl_deadline", seconds, detail=f"skipped: crawl_deadline (limit {deadline}s)")
    manifest = _find_manifest(out_dir)
    if manifest is None:
        artifact["outputTail"] = tail[-500:]
        return skipped("crawl_no_report", seconds, detail=f"skipped: crawl_no_report (exit {code}); {tail[-400:]}")
    summary = json.loads(redact(json.dumps(summarize_crawl(manifest)), redact_env))
    artifact.update({"seconds": round(seconds, 2), "exitCode": code, "crawlOverall": summary["overall"],
                     "crawlFailures": summary["failures"], "routeCount": summary["routeCount"],
                     "routes": summary["routes"], "failedRoutes": summary["failedRoutes"],
                     "nonGetRequests": summary["nonGetRequests"]})
    note = f"; non-GET BFF requests seen: {', '.join(summary['nonGetRequests'])}" if summary["nonGetRequests"] else ""
    if summary["pass"]:
        artifact["status"] = "passed"
        return _crawl_result("passed", exit_code=code, seconds=seconds, failed_routes=[],
                             detail=f"{summary['routeCount']} routes crawled; overall {summary['overall']}{note}"), artifact
    artifact["status"] = "failed"
    named = "; ".join(f"{r['path']} ({r['status']}: {r['error']}{_first_counted_read(r)})"
                      for r in summary["failedRoutes"][:10])
    detail = (f"failed routes: {named}" if named else "no failing route") + \
        f"; crawl failures: {', '.join(summary['failures']) or 'none'}{note}"
    return _crawl_result("failed", exit_code=code, seconds=seconds, detail=detail,
                         failed_routes=summary["failedRoutes"]), artifact


def verdict_status(results: list[dict]) -> str:
    if any(item["status"] == "failed" for item in results):
        return "failed"
    if any(item.get("lease_required") for item in results):
        return "pending"  # a required mutating check did not run: not a pass
    if any(item.get("required") and item["status"] == "skipped" for item in results):
        return "pending"  # a required check (the management crawl) could not run: not a pass
    return "passed"


def build_verdict(*, status: str, results: list[dict], identity: dict, run: dict) -> dict:
    failed = [{"id": item["id"], "kind": item["kind"], "owner": item["owner"], "detail": item["detail"]}
              for item in results if item["status"] == "failed"]
    return {
        "schemaVersion": SCHEMA,
        "acceptance": status,
        "generatedAt": _now(),
        "deployment": {"backendSha": identity["backend_sha"], "frontendSha": identity["frontend_sha"]},
        "evidenceRun": run,
        "failed": failed,
        "checks": results,
    }


def _identity_from(args) -> dict:
    for label, value in (("backend", args.backend_sha), ("frontend", args.frontend_sha)):
        if not SHA_RE.match(value):
            raise SystemExit(f"--{label}-sha must be an exact 40-hex commit")
    return {"backend_sha": args.backend_sha, "frontend_sha": args.frontend_sha,
            "fe_url": args.fe_url, "bff_url": args.bff_url}


def _run_info() -> dict:
    repo, run_id = os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_RUN_ID", "")
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    return {"runId": run_id, "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "url": f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""}


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)  # replace, never append: one verdict per release


def _output(name: str, value: str) -> None:
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def summary_markdown(verdict: dict, deployed: str) -> str:
    lines = ["## Dev release result", f"- deployed: `{deployed}`", f"- acceptance: `{verdict['acceptance']}`"]
    for item in verdict["failed"]:
        lines.append(f"  - FAILED `{item['id']}` (owner `{item['owner']}`)")
    skipped = [item["id"] for item in verdict["checks"] if item["status"] == "skipped"]
    if skipped:
        lines.append(f"- skipped: {', '.join(f'`{name}`' for name in skipped)}")
    crawl = next((item for item in verdict["checks"] if item["id"] == CRAWL_ID), None)
    if crawl:
        routes = ", ".join(f"`{r['path']}`" for r in crawl.get("failedRoutes") or [])
        lines.append(f"- management crawl: `{crawl['status']}`"
                     + (f" ({crawl['reason']})" if crawl.get("reason") else "")
                     + (f" failed routes: {routes}" if routes else ""))
    lines.append("- a release is `accepted` only when acceptance is `passed`; acceptance never rolls back a deploy")
    return "\n".join(lines) + "\n"


def _run_crawl(args, identity: dict, results: list[dict]) -> dict:
    """The crawl after the probes; any internal error is a skipped crawl (pending), never a pass or a crash."""
    import tempfile
    identity_passed = any(item["id"] == "check:deployed-pair-identity" and item["status"] == "passed"
                          for item in results)
    try:
        with tempfile.TemporaryDirectory(prefix="acceptance-crawl-") as work:
            check, artifact = crawl_check(frontend_dir=args.crawl_frontend_dir, prepared=args.crawl_prepared,
                                          identity=identity, identity_passed=identity_passed,
                                          base_env=dict(os.environ), work_dir=Path(work),
                                          deadline=args.crawl_deadline_seconds)
    except Exception as exc:
        check = _crawl_result("skipped", reason="crawl_internal_error",
                              detail=f"skipped: crawl_internal_error ({redact(str(exc), dict(os.environ))})")
        artifact = {"schemaVersion": CRAWL_SCHEMA, "status": "skipped", "reason": "crawl_internal_error"}
    try:
        _write(args.crawl_out, artifact)
    except OSError:
        pass
    return check


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "pending", "run"))
    parser.add_argument("--checks", type=Path, default=Path("acceptance/checks.json"))
    parser.add_argument("--probes-dir", type=Path, default=Path("acceptance/probes"))
    parser.add_argument("--backend-sha", default="")
    parser.add_argument("--frontend-sha", default="")
    parser.add_argument("--fe-url", default="")
    parser.add_argument("--bff-url", default="")
    parser.add_argument("--verdict-out", type=Path, default=Path("acceptance-verdict.json"))
    parser.add_argument("--lease-held", action="store_true")
    parser.add_argument("--deployed", default="success")
    parser.add_argument("--crawl-frontend-dir", type=Path, default=None,
                        help="deployed execute-plans checkout; when given, the management crawl runs after the probes")
    parser.add_argument("--crawl-prepared", default="", help="outcome of the credential-free dependency install")
    parser.add_argument("--crawl-deadline-seconds", type=int, default=CRAWL_DEADLINE_SECONDS)
    parser.add_argument("--crawl-out", type=Path, default=Path("acceptance-crawl.json"))
    args = parser.parse_args(argv)
    try:
        checks, probes = load_checks(args.checks), discover_probes(args.probes_dir)
    except Exception as exc:
        checks, probes, load_error = [], [], str(exc)
    else:
        load_error = ""
    if args.command == "plan":
        info = plan(checks, probes)
        print(json.dumps(info))
        _output("mutating", "true" if info["mutating"] else "false")
        _output("redacts_output", "true" if info["redacts_output"] else "false")
        return 0
    identity = _identity_from(args)
    if args.command == "pending":
        _write(args.verdict_out, build_verdict(status="pending", results=[], identity=identity, run=_run_info()))
        return 0
    try:
        if load_error:
            raise ValueError(load_error)
        results = run_stage(checks=checks, probes=probes, identity=identity,
                            base_env=dict(os.environ), lease_held=args.lease_held)
    except Exception as exc:
        results = [_result({"id": "check:stage-internal", "kind": "check", "owner": SELF_OWNER}, "failed",
                           detail=f"acceptance stage error: {exc}")]
    if args.crawl_frontend_dir is not None:
        results.append(_run_crawl(args, identity, results))
    verdict = build_verdict(status=verdict_status(results), results=results, identity=identity, run=_run_info())
    _write(args.verdict_out, verdict)
    _output("acceptance", verdict["acceptance"])
    markdown = summary_markdown(verdict, args.deployed)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as handle:
            handle.write(markdown)
    print(markdown)
    return 0  # advisory: the verdict, not the exit code, carries the result


if __name__ == "__main__":
    sys.exit(main())
