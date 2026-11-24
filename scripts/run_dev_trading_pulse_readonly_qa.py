#!/usr/bin/env python3
"""Protected caller for the Trading Pulse page read-only browser acceptance.

Runs in the same supplementary, non-gating dev release QA position as
``run_dev_paired_readonly_qa.py`` and drives the SAME frontend CLI
(``accept-persona-strategy-hosted-readonly.mjs --mode trading-pulse``) on the
exact served FE/BFF pair with the VIEWER profile only.

Contract with the CLI: one JSON document on stdin
(``profile``, ``expected``, ``sources``, ``history``), ``--mode trading-pulse``
and ``--out-dir`` as the only arguments; exit 0 accepted, 1 measured failure,
2 invalid input/runtime.  The caller maps those codes to its own exit code and
to ``pass``/``failed``/``unproven`` without rewriting them.

``sources`` is an independent same-tenant witness built HERE from direct reads
of the registry (personas, runtime bindings) and the ledger (per-runtime
telemetry summary) with a real viewer dev-login; it is never copied from the
trading-pulse endpoints under test.  ``history`` is read from the immutable
journey event named by ``--history-journey-id/--history-event-id`` (the 2317
refusal), never from current rows.

The viewer secret is read from the process environment supplied by the
protected workflow step, kept in RAM, and written only to the CLI's stdin.  It
never enters argv, logs or evidence.  Evidence: one sanitized summary plus at
most two masked screenshots, <= 5 MiB in total, uploaded with the release
evidence; nothing is retained after a release (daily growth 0).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote, urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_dev_paired_readonly_qa as base  # noqa: E402

Unproven = base.Unproven

SCHEMA = "pantheon.dev-trading-pulse-readonly-qa-caller.v1"
CLI_RELATIVE = base.CLI_RELATIVE
MODE = "trading-pulse"
VIEWER = "VIEWER"
MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
MAX_RUNTIMES = 200
MAX_IMAGES = 2
SUMMARY_SCHEMA = "trading-pulse-browser-readonly.v1"
STAGES = ("paper", "canary", "live", "broker_sandbox")
# Canonical labels the existing Journey page renders for the wire reason code.
REASON_TEXT = {"insufficient_cash": {"zh-TW": "現金不足", "en-US": "Not enough cash"}}
EXIT_BY_OUTCOME = {"pass": 0, "failed": 1, "unproven": 2}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> "float | None":
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _is_probe(persona: Mapping[str, Any]) -> bool:
    """Same exclusion rule the BFF read model and the stage probe use."""
    root = str(Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from services.control_plane.bff.persona_paper_enrollment import is_probe_or_test_persona  # noqa: PLC0415
    return bool(is_probe_or_test_persona(persona))


Probe = Callable[[Mapping[str, Any]], bool]


class Caller:
    def __init__(
        self,
        args: argparse.Namespace,
        environ: Mapping[str, str],
        *,
        http: base.Http = base.default_http,
        cli_runner: base.CliRunner = base.default_cli_runner,
        probe: Probe = _is_probe,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.args, self.environ = args, environ
        self.http, self.cli_runner, self.probe, self.now = http, cli_runner, probe, now
        self.secrets: list[str] = []
        self.token = ""

    def _redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    # -- reads -----------------------------------------------------------
    def _get(self, bff: str, path: str) -> Any:
        status, doc = self.http(bff + path, "GET", {"Authorization": f"Bearer {self.token}",
                                                    "Accept": "application/json"}, None, 30)
        if status != 200 or not isinstance(doc, dict):
            raise Unproven("source_unavailable", f"independent read {path.split('?')[0]} returned HTTP {status}")
        meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
        bad = {"unavailable", "degraded"}
        if meta.get("status") in bad or meta.get("read_state") == "unavailable" or any(
                isinstance(s, dict) and s.get("status") in bad for s in (meta.get("surfaces") or {}).values()):
            raise Unproven("source_unavailable", f"independent read {path.split('?')[0]} is degraded")
        return doc.get("data")

    def _login(self, bff: str) -> None:
        status, login = self.http(
            f"{bff}/bff/auth/dev-login", "POST", {"Content-Type": "application/json", "Accept": "application/json"},
            json.dumps({"grant_type": "client_credentials", "client_id": self.profile["client_id"],
                        "client_secret": self.profile["client_secret"]}).encode(), 30)
        token = login.get("access_token") if isinstance(login, dict) else None
        if status != 200 or not isinstance(token, str) or not token:
            raise Unproven("viewer_auth_unavailable", "viewer dev-login for the independent witness failed")
        self.token = token
        self.secrets.append(token)
        status, me = self.http(f"{bff}/bff/me", "GET", {"Authorization": f"Bearer {token}",
                                                         "Accept": "application/json"}, None, 30)
        data = me.get("data") if isinstance(me, dict) else None
        if status != 200 or not isinstance(data, dict) or data.get("tenant_id") != self.args.tenant_id:
            raise Unproven("viewer_tenant_mismatch", "authenticated viewer is not in the tested tenant")

    def _identity(self, fe: str, bff: str) -> dict:
        status, manifest = self.http(f"{fe}/deployment.json", "GET", {"Accept": "application/json"}, None, 30)
        if status != 200 or not isinstance(manifest, dict):
            raise Unproven("identity_unreachable", "FE deployment identity unavailable")
        status, version = self.http(f"{bff}/bff/version", "GET", {"Accept": "application/json"}, None, 30)
        if status != 200 or not isinstance(version, dict):
            raise Unproven("identity_unreachable", "BFF version identity unavailable")
        live_fe = str(manifest.get("frontendSha") or manifest.get("commit") or "")
        live_bff = str(version.get("source_commit_sha") or version.get("commit") or "")
        posture = version.get("config_posture") if isinstance(version.get("config_posture"), dict) else {}
        if live_fe != self.args.fe_sha or live_bff != self.args.bff_sha:
            raise Unproven("pair_mismatch", "served FE/BFF differs from the accepted pair")
        if posture.get("auth_mode") != "strict" or posture.get("auth_stub") is True:
            raise Unproven("auth_posture", "BFF auth posture is not strict")
        return {"fe": live_fe, "bff": live_bff, "auth_mode": "strict"}

    def _roster(self, bff: str) -> "tuple[list, list]":
        people = self._get(bff, "/api/v1/personas")
        bindings = self._get(bff, "/api/v1/runtime-bindings")
        if not isinstance(people, list) or not isinstance(bindings, list):
            raise Unproven("source_shape", "registry roster is malformed")
        return people, bindings

    def build_sources(self, bff: str) -> dict:
        """Independent same-tenant registry + ledger witness (never the pulse endpoint)."""
        tenant = self.args.tenant_id
        people, bindings = self._roster(bff)
        directory = {str(p.get("persona_id") or p.get("id")): p for p in people
                     if isinstance(p, dict) and p.get("tenant_id") == tenant}
        scoped: list[tuple[dict, str, str]] = []
        for b in bindings:
            if not isinstance(b, dict):
                raise Unproven("source_shape", "runtime binding is not an object")
            if str(b.get("status") or "").strip().lower() in {"retired", "failed"} or b.get("retired_at"):
                continue
            stage = b.get("deployment_stage") or b.get("deployment_mode")
            if stage not in STAGES:
                continue
            meta = b.get("metadata") if isinstance(b.get("metadata"), dict) else {}
            claims = {v for v in (b.get("tenant_id"), meta.get("tenant_id")) if v not in (None, "")}
            if claims and tenant not in claims:
                continue
            if claims != {tenant}:
                raise Unproven("binding_tenant_unverified", "runtime binding tenant claim is missing")
            pid = str(b.get("persona_id") or "")
            if pid not in directory:
                raise Unproven("identity_missing", "runtime binding persona is not in the tenant directory")
            scoped.append((b, pid, stage))
        if len(scoped) > MAX_RUNTIMES:
            raise Unproven("runtime_count_cap_exceeded", "too many runtimes for the bounded witness")
        rows: list[dict] = []
        excluded: list[dict] = []
        ledger: dict[str, Any] = {}
        for b, pid, stage in scoped:
            person = directory[pid]
            runtime = str(b.get("runtime_id") or "")
            bid = str(b.get("runtime_binding_id") or b.get("binding_id") or b.get("id") or "")
            if self.probe(person):
                excluded.append({"persona_id": pid, "persona_name": str(person.get("name") or ""),
                                 "runtime_id": runtime})
                continue
            raw = self._get(bff, f"/api/v1/telemetry/{quote(runtime, safe='')}/summary")
            if not isinstance(raw, dict) or raw.get("tenant_id") != tenant or (
                    raw.get("binding_id") or raw.get("runtime_binding_id")) != bid:
                raise Unproven("telemetry_scope", "ledger telemetry is not scoped to the tested binding")
            ledger[runtime] = raw
            meta = b.get("metadata") if isinstance(b.get("metadata"), dict) else {}
            symbol = b.get("underlying_symbol") or meta.get("underlying_symbol") or raw.get("underlying_symbol")
            fills = raw.get("ledger_fill_count", raw.get("total_trades"))
            details = raw.get("fill_rate_details") if isinstance(raw.get("fill_rate_details"), dict) else {}
            start, end = raw.get("pnl_period_start_at"), raw.get("pnl_at")
            row = {
                "persona_id": pid, "persona_name": str(person.get("name") or ""), "runtime_id": runtime,
                "binding_id": bid, "underlying_symbol": str(symbol or ""), "stage": stage,
                "ledger_fill_count": _number(fills), "pnl": _number(raw.get("pnl")),
                "pnl_currency": raw.get("pnl_currency") if isinstance(raw.get("pnl_currency"), str) else None,
                "pnl_period": f"{start}/{end}" if start and end else None,
                "filled_quantity": _number(details.get("numerator")),
                "requested_quantity": _number(details.get("denominator")),
            }
            unit = raw.get("quantity_unit") or details.get("quantity_unit")
            if unit:
                row["quantity_unit"] = unit
            if not row["persona_name"] or not row["underlying_symbol"]:
                raise Unproven("identity_missing", "registry name or symbol is missing for an operator row")
            rows.append(row)
        if not rows:
            raise Unproven("empty_roster", "independent operator roster is empty")
        people_after, bindings_after = self._roster(bff)
        if people_after != people or bindings_after != bindings:
            raise Unproven("roster_changed_during_read", "registry roster changed while the witness was read")
        groups: dict[tuple, dict] = {}
        for r in rows:
            if r["pnl_currency"] and r["pnl_period"]:
                g = groups.setdefault((r["stage"], r["pnl_currency"], r["pnl_period"]),
                                      {"covered": set(), "eligible": set(), "value": 0.0})
                g["eligible"].add(r["persona_id"])
                if r["pnl"] is not None:
                    g["covered"].add(r["persona_id"])
                    g["value"] += r["pnl"]
        pnl_groups = [{"stage": k[0], "currency": k[1], "period": k[2], "value": g["value"],
                       "covered_persona_count": len(g["covered"]), "eligible_persona_count": len(g["eligible"])}
                      for k, g in sorted(groups.items()) if g["covered"]]
        return {
            "tenant_id": tenant, "captured_at": self.now().isoformat(),
            "provenance": [
                {"source": "registry:/api/v1/personas+/api/v1/runtime-bindings", "sha256": _digest([people, bindings])},
                {"source": "ledger:/api/v1/telemetry/{runtime}/summary", "sha256": _digest(ledger)},
            ],
            "rows": rows, "excluded": excluded, "pnl_groups": pnl_groups,
        }

    def build_history(self, bff: str, sources: dict) -> dict:
        a = self.args
        if not (a.history_journey_id and a.history_event_id):
            raise Unproven("missing_history", "immutable history event identity is required")
        env = a.history_environment
        qs = "?" + urlencode({"tenant_id": a.tenant_id, "environment": env})
        base_path = f"/bff/management/trade-journeys/{quote(a.history_journey_id, safe='')}"
        detail = self._get(bff, base_path + qs)
        timeline = self._get(bff, base_path + "/timeline" + qs)
        row = (detail or {}).get("row") or (detail or {}).get("journey") or detail
        events = (timeline or {}).get("items") or (timeline or {}).get("events") if isinstance(timeline, dict) else None
        if not isinstance(row, dict) or not isinstance(events, list):
            raise Unproven("history_unavailable", "immutable journey detail/timeline is unavailable")
        event = next((e for e in events if isinstance(e, dict)
                      and a.history_event_id in (e.get("source_event_id"), e.get("event_id"))), None)
        if event is None:
            raise Unproven("history_unavailable", "immutable history event is not in the journey timeline")
        outcome = row.get("outcome") if isinstance(row.get("outcome"), dict) else {}
        cause = event if event.get("reason_code") else outcome
        detail_src = cause.get("reason_detail") if isinstance(cause.get("reason_detail"), dict) else cause
        code = str(cause.get("reason_code") or "")
        text = REASON_TEXT.get(code)
        pid = str(row.get("persona_id") or "")
        name = next((r["persona_name"] for r in sources["rows"] if r["persona_id"] == pid), None) or str(
            row.get("persona_name") or "")
        if text is None or not (pid and name):
            raise Unproven("history_unavailable", "historical refusal reason or persona is not resolvable")
        return {
            "journey_id": a.history_journey_id, "event_id": a.history_event_id, "persona_id": pid,
            "persona_name": name, "tenant_id": a.tenant_id, "environment": env,
            "symbol": str(row.get("symbol") or row.get("underlying_symbol") or ""), "reason_code": code,
            "reason_text": text, "required": _number(detail_src.get("required")),
            "available": _number(detail_src.get("available")),
            "requested_quantity": _number(detail_src.get("requested_quantity")),
            "submitted_to_broker": cause.get("submitted_to_broker"),
            "occurred_at": str(event.get("occurred_at") or ""),
        }

    # -- evidence --------------------------------------------------------
    def _collect(self, out_dir: Path, evidence_dir: Path, evidence: dict) -> "dict | None":
        dirs = sorted((d for d in out_dir.glob("trading-pulse-*") if d.is_dir() and not d.is_symlink()),
                      key=lambda d: d.stat().st_mtime)
        if not dirs:
            return None
        d = dirs[-1]
        summary = d / "summary.json"
        if not summary.is_file() or summary.is_symlink():
            return None
        images = sorted(p for p in d.glob("*.png") if p.is_file() and not p.is_symlink())
        total = summary.stat().st_size + sum(p.stat().st_size for p in images)
        if len(images) > MAX_IMAGES or total > MAX_EVIDENCE_BYTES:
            raise Unproven("evidence_budget", "CLI evidence exceeds two screenshots or 5 MiB")
        text = self._redact(summary.read_text("utf-8", "replace"))
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "trading-pulse-summary.json").write_text(text)
        names = []
        for p in images:
            shutil.copyfile(p, evidence_dir / f"trading-pulse-{p.name}")
            names.append(f"trading-pulse-{p.name}")
        evidence["evidence_files"] = ["trading-pulse-summary.json", *names]
        evidence["evidence_bytes"] = total
        try:
            doc = json.loads(text)
        except ValueError:
            return {"parse_error": True}
        return doc if isinstance(doc, dict) else {"parse_error": True}

    # -- main ------------------------------------------------------------
    def run(self) -> dict:
        a = self.args
        evidence: dict[str, Any] = {
            "schema_version": SCHEMA, "started_at": _now(), "supplementary_not_a_release_gate": True,
            "mode": MODE, "run": {"root": a.root_run_id, "attempt": a.run_attempt, "fe": a.fe_sha, "bff": a.bff_sha},
            "outcome": "unproven",
        }
        tmp = Path(tempfile.mkdtemp(prefix="pantheon-pulse-qa-"))
        os.chmod(tmp, 0o700)
        try:
            self._run(evidence)
        except Unproven as exc:
            evidence.update(outcome="unproven", reason_code=exc.code, reason=self._redact(str(exc)))
        except Exception as exc:  # noqa: BLE001 - any runtime failure stays an honest unproven record
            evidence.update(outcome="unproven", reason_code="runtime_error",
                            reason=self._redact(f"{type(exc).__name__}: {exc}")[:500])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        evidence["finished_at"] = _now()
        return evidence

    def _run(self, evidence: dict) -> None:
        a = self.args
        for label in ("fe_sha", "bff_sha"):
            if not base.SHA40.match(getattr(a, label) or ""):
                raise Unproven("bad_pair", f"{label} is not an exact 40-hex SHA")
        if not (a.root_run_id and a.tenant_id):
            raise Unproven("missing_input", "root run and tenant identity are required")
        if a.history_environment not in ("paper", "canary", "live"):
            raise Unproven("missing_input", "history environment is invalid")
        fe = base._check_origin("DEV_FE_URL", a.fe_url, "https://app.dev.mvl-cap.tw")
        bff = base._check_origin("DEV_BFF_URL", a.bff_url, "https://api.dev.mvl-cap.tw")
        cid = self.environ.get(f"DEV_BFF_DEV_LOGIN_{VIEWER}_CLIENT_ID", "")
        secret = self.environ.get(f"DEV_BFF_DEV_LOGIN_{VIEWER}_CLIENT_SECRET", "")
        if not cid or not secret:
            raise Unproven("missing_profile", "viewer credentials are not configured")
        self.secrets.append(secret)
        self.profile = {"client_id": cid, "client_secret": secret}

        cli = Path(a.frontend_checkout) / CLI_RELATIVE
        if not cli.is_file() or cli.is_symlink():
            raise Unproven("missing_cli", "qualified frontend CLI is absent from the exact FE revision")
        if "trading-pulse" not in cli.read_text("utf-8", "replace"):
            raise Unproven("missing_cli_mode", "frontend CLI has no trading-pulse mode")
        evidence["cli"] = {"path": CLI_RELATIVE, "sha256": hashlib.sha256(cli.read_bytes()).hexdigest()}
        ready, _, ready_err = self.cli_runner(["node", "--input-type=module", "-e", base.READY_SCRIPT], b"",
                                              base.READY_TIMEOUT_SECONDS, cwd=a.frontend_checkout)
        evidence["cli_ready"] = {"exit_code": ready}
        if ready != 0:
            raise Unproven("cli_not_ready", "frontend CLI dependencies/browser are not prepared: "
                           + self._redact(ready_err)[-300:])

        evidence["identity_before"] = self._identity(fe, bff)
        self._login(bff)
        sources = self.build_sources(bff)
        history = self.build_history(bff, sources)
        evidence["witness"] = {
            "persona_count": len({r["persona_id"] for r in sources["rows"]}),
            "runtime_count": len(sources["rows"]), "excluded_count": len(sources["excluded"]),
            "pnl_group_count": len(sources["pnl_groups"]), "provenance": sources["provenance"],
            "history_witness_digest": _digest(history),
        }
        payload = {
            "profile": self.profile,
            "expected": {"fe": a.fe_sha, "bff": a.bff_sha, "tenant_id": a.tenant_id},
            "sources": sources, "history": history,
        }
        out_dir = Path(a.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(out_dir, 0o700)
        code, out, err = self.cli_runner(
            ["node", str(cli), "--mode", MODE, "--out-dir", str(out_dir)],
            json.dumps(payload, separators=(",", ":")).encode(), base.CLI_TIMEOUT_SECONDS, cwd=a.frontend_checkout)
        evidence["cli_result"] = {"exit_code": code, "stdout_tail": self._redact(out)[-2000:],
                                  "stderr_tail": self._redact(err)[-2000:]}
        summary = self._collect(out_dir, Path(a.evidence_dir), evidence) if a.evidence_dir else None
        evidence["identity_after"] = self._identity(fe, bff)  # drift raises Unproven
        if code == 2 or code not in (0, 1):
            raise Unproven("cli_invalid_input_or_runtime", f"CLI exited {code} (invalid input or runtime)")
        if code == 1:
            evidence["outcome"] = "failed"
            return
        # Exit 0 alone is not enough: the retained summary must say accepted.
        if not isinstance(summary, dict) or summary.get("schema") != SUMMARY_SCHEMA or summary.get("pass") is not True \
                or summary.get("mode") != "acceptance":
            raise Unproven("summary_missing", "CLI exited 0 without a positive acceptance summary")
        runs = {(r.get("locale"), r.get("width")) for r in summary.get("runs") or [] if isinstance(r, dict)}
        if runs != {("zh-TW", 1440), ("zh-TW", 390), ("en-US", 1440)}:
            raise Unproven("summary_incomplete", "summary lacks the three locale/viewport runs")
        evidence["outcome"] = "pass"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("fe-url", "bff-url", "fe-sha", "bff-sha", "root-run-id", "run-attempt", "tenant-id",
                 "history-journey-id", "history-event-id", "frontend-checkout", "out-dir", "evidence-dir",
                 "evidence-out"):
        p.add_argument(f"--{name}", default="")
    p.add_argument("--history-environment", default="paper")
    return p


def main(argv: "Sequence[str] | None" = None, environ: "Mapping[str, str] | None" = None, **kw: Any) -> int:
    args = build_parser().parse_args(argv)
    if not args.evidence_out or not args.out_dir:
        print("--evidence-out and --out-dir are required", file=sys.stderr)
        return 2
    caller = Caller(args, os.environ if environ is None else environ, **kw)
    evidence = caller.run()
    dest = Path(args.evidence_out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    os.chmod(dest, 0o600)
    print(json.dumps({"outcome": evidence["outcome"], "reason_code": evidence.get("reason_code")}))
    return EXIT_BY_OUTCOME[evidence["outcome"]]


if __name__ == "__main__":
    sys.exit(main())
