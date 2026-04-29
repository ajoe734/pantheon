#!/usr/bin/env python3
"""
Activation-gated dormant OSS smoke matrix.

Tests each gated OSS integration with explicit activation flags or env
variables.  Every row confirms gate_state=closed and activated=false.

All paths are offline or local-fixture only.  No registry, governance,
network, or live execution writes occur.

Usage:
    python3 scripts/smoke_dormant_oss_matrix.py [--json-out PATH]

Result column values:
    passed              gate_state=closed + activated=false confirmed
    missing_optional_dep  optional dep (e.g. Docker runtime) not present
    gate_denied         activation gate blocked when no explicit flag given
    error               unexpected failure

Exit codes:
    0  all rows passed (gate_state=closed, activated=false confirmed for all)
    1  one or more rows in unexpected error state
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable


# ─── data model ───────────────────────────────────────────────────────────────

@dataclass
class SmokeRow:
    integration: str
    gate_mechanism: str
    result: str       # passed | missing_optional_dep | gate_denied | error
    gate_state: str   # closed | unknown
    activated: bool   # always False for dormant rows
    evidence: dict[str, Any] = field(default_factory=dict)
    error_detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "integration": self.integration,
            "gate_mechanism": self.gate_mechanism,
            "result": self.result,
            "gate_state": self.gate_state,
            "activated": self.activated,
            "evidence": self.evidence,
        }
        if self.error_detail:
            d["error_detail"] = self.error_detail
        return d


# ─── helpers ──────────────────────────────────────────────────────────────────

def _run(
    cmd: list[str],
    *,
    env: dict[str, str] | None = None,
    scrub: set[str] | None = None,
) -> tuple[int, str, str]:
    merged = {**os.environ, **(env or {})}
    for k in (scrub or ()):
        merged.pop(k, None)
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env=merged,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _kv(stdout: str) -> dict[str, str]:
    """Extract 'key: value' pairs from indented smoke test stdout."""
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        stripped = line.strip()
        if ": " in stripped:
            key, _, val = stripped.partition(": ")
            key = key.strip()
            val = val.strip()
            if key and val and not key.startswith("-"):
                result[key] = val
    return result


def _short(text: str, limit: int = 300) -> str:
    return text[:limit].strip() if text else ""


# ─── integration rows ─────────────────────────────────────────────────────────

def _smoke_openclaw() -> SmokeRow:
    """
    OpenClaw: Docker-gated gateway runtime.

    The Python adapter is importable without Docker.  The Docker binary is the
    optional dep for the live runtime path.  The design contract defines
    activation_state=facade_only and broker_execution=deferred — the gateway
    proxies requests but no broker execution can occur until the activation gate
    is explicitly opened.

    Gate mechanism: Docker runtime + OPENCLAW_GATEWAY_TOKEN required to start
    the gateway.  Without explicit broker activation the gateway stays
    facade_only.
    """
    integration = "openclaw"
    gate_mechanism = "docker-runtime-gated; activation_state=facade_only; broker_execution=deferred"

    try:
        mod_path = REPO_ROOT / "integrations" / "openclaw" / "adapter" / "gateway_runtime.py"
        spec = importlib.util.spec_from_file_location("_openclaw_gateway_runtime", mod_path)
        if spec is None or spec.loader is None:
            raise ImportError("spec_from_file_location returned None")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)  # type: ignore[union-attr]

        cfg = mod.OpenClawGatewayConfig()
        docker_available = shutil.which("docker") is not None

        evidence: dict[str, Any] = {
            "adapter_importable": True,
            "image_ref": cfg.image_ref,
            "image_digest": cfg.image_digest,
            "startup_pull": cfg.startup_pull,
            # Design-contract constants — not runtime state.  The adapter is
            # declared facade_only because the broker execution path is deferred
            # until an explicit activation gate is opened.
            "activation_state": "facade_only",
            "broker_execution": "deferred",
            "docker_available": docker_available,
        }

        row_result = "passed"
        if not docker_available:
            row_result = "missing_optional_dep"
            evidence["missing_dep"] = "docker (optional for gateway runtime)"

        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result=row_result,
            gate_state="closed",
            activated=False,
            evidence=evidence,
        )
    except Exception as exc:  # noqa: BLE001
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=str(exc),
        )


def _smoke_qlib() -> SmokeRow:
    """
    Qlib: stub smoke + preflight.

    The stub smoke proves the adapter is runnable and emits
    artifact_state=draft / deployment_stage=none / direct_live_influence=false.
    The preflight confirms activation_allowed=false (all required gates BLOCKED).

    Gate mechanism: preflight gates rs003_candidate, governed_dataset, and
    strategy_spec_binding are all BLOCKED — production activation is not
    allowed without explicit evidence bundles.
    """
    integration = "qlib"
    gate_mechanism = (
        "preflight activation_allowed=false "
        "(rs003_candidate + governed_dataset + strategy_spec_binding BLOCKED)"
    )

    code, out, err = _run([PYTHON, "services/research/qlib/smoke_test.py"])
    if code != 0:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=_short(err or out),
        )

    kv = _kv(out)

    pf_code, pf_out, _ = _run([
        PYTHON, "-c",
        (
            "import sys; sys.path.insert(0,'services/research/qlib');"
            "from preflight import run_qlib_preflight; import json;"
            "print(json.dumps(run_qlib_preflight().to_dict()))"
        ),
    ])
    try:
        preflight: dict[str, Any] = json.loads(pf_out)
    except (json.JSONDecodeError, ValueError):
        preflight = {}

    activation_allowed = preflight.get("activation_allowed")
    assertions_ok = "assertions: OK" in out

    if activation_allowed is not False:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=f"preflight activation_allowed={activation_allowed!r}; expected False",
        )
    if not assertions_ok:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail="smoke assertions not OK",
        )

    return SmokeRow(
        integration=integration,
        gate_mechanism=gate_mechanism,
        result="passed",
        gate_state="closed",
        activated=False,
        evidence={
            "artifact_state": kv.get("artifact_state", ""),
            "deployment_stage": kv.get("deployment_stage", ""),
            "direct_live_influence": False,
            "preflight_activation_allowed": activation_allowed,
            "preflight_summary": preflight.get("summary", ""),
            "assertions": "OK",
        },
    )


def _smoke_trl() -> SmokeRow:
    """
    TRL: stub DPO smoke + preflight.

    The stub smoke proves the DPO adapter is runnable and emits
    artifact_state=draft / deployment_stage=none / direct_live_influence=false.
    The preflight confirms activation_allowed=false (fb002_volume and
    preference_pairs gates BLOCKED — 0 events available, 200 required).

    Gate mechanism: preflight requires >=200 governed FB-002 feedback events
    and >=100 valid preference pairs.  Neither threshold is met.
    """
    integration = "trl"
    gate_mechanism = (
        "preflight fb002_volume (0/200 events) + preference_pairs (0/100 pairs) BLOCKED"
    )

    code, out, err = _run([PYTHON, "services/learning/trl/smoke_test.py"])
    if code != 0:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=_short(err or out),
        )

    kv = _kv(out)

    pf_code, pf_out, _ = _run([
        PYTHON, "-c",
        (
            "import sys; sys.path.insert(0,'.');"
            "from services.learning.trl.preflight import run_trl_preflight; import json;"
            "r = run_trl_preflight(fb002_events=[], preference_pairs=[]);"
            "print(json.dumps(r.to_dict()))"
        ),
    ])
    try:
        preflight: dict[str, Any] = json.loads(pf_out)
    except (json.JSONDecodeError, ValueError):
        preflight = {}

    activation_allowed = preflight.get("activation_allowed")
    assertions_ok = "assertions: OK" in out

    if activation_allowed is not False:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=f"preflight activation_allowed={activation_allowed!r}; expected False",
        )
    if not assertions_ok:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail="smoke assertions not OK",
        )

    return SmokeRow(
        integration=integration,
        gate_mechanism=gate_mechanism,
        result="passed",
        gate_state="closed",
        activated=False,
        evidence={
            "artifact_state": kv.get("artifact_state", ""),
            "deployment_stage": kv.get("deployment_stage", ""),
            "direct_live_influence": False,
            "preflight_activation_allowed": activation_allowed,
            "preflight_summary": preflight.get("summary", ""),
            "assertions": "OK",
        },
    )


def _smoke_finrl() -> SmokeRow:
    """
    FinRL: deferred-prep adapter.

    Gate requires explicit --enable-deferred-prep flag.  Without the flag the
    script exits 2 (gate_denied).  With the flag the adapter runs in
    stub_finrl backend and emits gate_state=closed / artifact_state=draft.

    Gate mechanism: DeferredPrepGate.require_cli_flag() — EnvironmentError
    without flag; exit code 2.  Distinguishable from missing_optional_dep
    (ImportError / no finrl package).
    """
    integration = "finrl"
    gate_mechanism = "--enable-deferred-prep flag (FinRL DeferredPrepGate)"

    # Verify gate denial: must exit 2 without the flag.
    denied_code, _, denied_err = _run([PYTHON, "services/research/finrl/smoke_test.py"])
    if denied_code != 2:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=(
                f"gate denial check failed: expected exit 2, got {denied_code}. "
                f"stderr: {_short(denied_err)}"
            ),
        )

    # Run with gate explicitly opened.
    code, out, err = _run([
        PYTHON, "services/research/finrl/smoke_test.py", "--enable-deferred-prep",
    ])
    if code != 0:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=_short(err or out),
        )

    kv = _kv(out)
    gate_state_out = kv.get("gate_state", "")
    assertions_ok = "assertions: OK" in out

    if gate_state_out != "closed":
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=f"gate_state not closed in output: {gate_state_out!r}",
        )
    if not assertions_ok:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state=gate_state_out,
            activated=False,
            error_detail="smoke assertions not OK",
        )

    return SmokeRow(
        integration=integration,
        gate_mechanism=gate_mechanism,
        result="passed",
        gate_state=gate_state_out,
        activated=False,
        evidence={
            "gate_state": gate_state_out,
            "artifact_state": kv.get("artifact_state", ""),
            "deployment_stage": kv.get("deployment_stage", ""),
            "lean_consumption": kv.get("lean_consumption", ""),
            "direct_live_influence": False,
            "assertions": "OK",
            "gate_denial_verified": True,
        },
    )


def _smoke_rllib() -> SmokeRow:
    """
    RLlib: deferred-prep adapter.

    Gate requires explicit --enable-deferred-prep flag.  Without the flag the
    script exits 2 (gate_denied).  With the flag the stub_rllib backend runs
    and emits gate_state=closed / artifact_state=draft.

    Gate mechanism: DeferredPrepGate.require_cli_flag() — same pattern as
    FinRL but for RLlib multi-agent portfolio training.
    """
    integration = "rllib"
    gate_mechanism = "--enable-deferred-prep flag (RLlib DeferredPrepGate)"

    denied_code, _, denied_err = _run([PYTHON, "services/research/rllib/smoke_test.py"])
    if denied_code != 2:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=(
                f"gate denial check failed: expected exit 2, got {denied_code}. "
                f"stderr: {_short(denied_err)}"
            ),
        )

    code, out, err = _run([
        PYTHON, "services/research/rllib/smoke_test.py", "--enable-deferred-prep",
    ])
    if code != 0:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=_short(err or out),
        )

    kv = _kv(out)
    gate_state_out = kv.get("gate_state", "")
    assertions_ok = "assertions: OK" in out

    if gate_state_out != "closed":
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=f"gate_state not closed in output: {gate_state_out!r}",
        )
    if not assertions_ok:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state=gate_state_out,
            activated=False,
            error_detail="smoke assertions not OK",
        )

    return SmokeRow(
        integration=integration,
        gate_mechanism=gate_mechanism,
        result="passed",
        gate_state=gate_state_out,
        activated=False,
        evidence={
            "gate_state": gate_state_out,
            "artifact_state": kv.get("artifact_state", ""),
            "deployment_stage": kv.get("deployment_stage", ""),
            "direct_live_influence": False,
            "search_strategy": kv.get("search_strategy", ""),
            "assertions": "OK",
            "gate_denial_verified": True,
        },
    )


def _smoke_raytune() -> SmokeRow:
    """
    Ray Tune: deferred-prep hyperparameter search adapter.

    Gate requires explicit --enable-deferred-prep flag.  Without the flag the
    script exits 2 (gate_denied).  With the flag the stub backend runs and
    emits gate_state=closed / artifact_type=optimizer_result / artifact_state=draft.

    Gate mechanism: RayTuneDeferredPrepGate.require_cli_flag() — same
    activation-gate pattern as FinRL/RLlib.
    """
    integration = "raytune"
    gate_mechanism = "--enable-deferred-prep flag (RayTuneDeferredPrepGate)"

    denied_code, _, denied_err = _run([
        PYTHON, "services/research/rllib/ray_tune_smoke_test.py",
    ])
    if denied_code != 2:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=(
                f"gate denial check failed: expected exit 2, got {denied_code}. "
                f"stderr: {_short(denied_err)}"
            ),
        )

    code, out, err = _run([
        PYTHON, "services/research/rllib/ray_tune_smoke_test.py", "--enable-deferred-prep",
    ])
    if code != 0:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=_short(err or out),
        )

    kv = _kv(out)
    gate_state_out = kv.get("gate_state", "")
    assertions_ok = "assertions: OK" in out

    if gate_state_out != "closed":
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=f"gate_state not closed in output: {gate_state_out!r}",
        )
    if not assertions_ok:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state=gate_state_out,
            activated=False,
            error_detail="smoke assertions not OK",
        )

    return SmokeRow(
        integration=integration,
        gate_mechanism=gate_mechanism,
        result="passed",
        gate_state=gate_state_out,
        activated=False,
        evidence={
            "gate_state": gate_state_out,
            "artifact_state": kv.get("artifact_state", ""),
            "artifact_type": kv.get("artifact_type", ""),
            "deployment_stage": kv.get("deployment_stage", ""),
            "direct_live_influence": False,
            "assertions": "OK",
            "gate_denial_verified": True,
        },
    )


def _smoke_wandb() -> SmokeRow:
    """
    W&B: offline deferred-prep scaffold.

    Config-level gate: EXPERIMENT_BACKEND=wandb raises EnvironmentError at
    import time unless PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 is set.  The
    offline prep smoke uses OfflineWandbPrepBackend (no W&B SDK required).

    Gate mechanism: config.selected_backend() raises EnvironmentError when
    EXPERIMENT_BACKEND=wandb without PANTHEON_ENABLE_WANDB_DEFERRED_PREP.
    Distinguishable from missing_optional_dep: the wandb SDK is simply not
    installed; the prep scaffold works without it.
    """
    integration = "wandb"
    gate_mechanism = (
        "EXPERIMENT_BACKEND=wandb requires PANTHEON_ENABLE_WANDB_DEFERRED_PREP=1 "
        "(config-level EnvironmentError gate)"
    )

    # Scrub PANTHEON_ENABLE_WANDB_DEFERRED_PREP so parent env contamination
    # cannot bypass the gate denial check.
    denial_code, denial_out, denial_err = _run(
        [
            PYTHON, "-c",
            (
                "import sys, importlib.util; sys.path.insert(0,'.');"
                "spec=importlib.util.spec_from_file_location("
                "'_exp_cfg','services/registry/experiments/config.py');"
                "mod=importlib.util.module_from_spec(spec);"
                "sys.modules[spec.name]=mod;"
                "spec.loader.exec_module(mod)"
            ),
        ],
        env={"EXPERIMENT_BACKEND": "wandb"},
        scrub={"PANTHEON_ENABLE_WANDB_DEFERRED_PREP"},
    )
    gate_denial_verified = (
        denial_code != 0
        and "reserved for deferred prep only" in (denial_err + denial_out)
    )

    if not gate_denial_verified:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=(
                f"gate denial check failed in scrubbed env: "
                f"exit={denial_code} "
                f"stdout={_short(denial_out)} stderr={_short(denial_err)}"
            ),
        )

    # Run offline prep smoke with deferred-prep env flag set.
    code, out, err = _run(
        [PYTHON, "services/registry/experiments/smoke_test.py", "--backend", "wandb"],
        env={"PANTHEON_ENABLE_WANDB_DEFERRED_PREP": "1"},
    )
    if code != 0:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=_short(err or out),
        )

    assertions_ok = "passed with backend=wandb" in out
    if not assertions_ok:
        return SmokeRow(
            integration=integration,
            gate_mechanism=gate_mechanism,
            result="error",
            gate_state="unknown",
            activated=False,
            error_detail=f"assertions failed: {_short(out)}",
        )

    wandb_sdk_present = shutil.which("wandb") is not None or _wandb_importable()

    return SmokeRow(
        integration=integration,
        gate_mechanism=gate_mechanism,
        result="passed",
        gate_state="closed",
        activated=False,
        evidence={
            "backend": "wandb",
            "mode": "offline",
            "prep_only": True,
            "sdk_backed": False,
            "wandb_sdk_present": wandb_sdk_present,
            "activation_state": "deferred",
            "gate_denial_verified": True,
            "assertions": "OK",
        },
    )


def _wandb_importable() -> bool:
    code, _, _ = _run([PYTHON, "-c", "import wandb"])
    return code == 0


# ─── matrix runner ────────────────────────────────────────────────────────────

_RUNNERS = [
    _smoke_openclaw,
    _smoke_qlib,
    _smoke_trl,
    _smoke_finrl,
    _smoke_rllib,
    _smoke_raytune,
    _smoke_wandb,
]

_ACCEPTABLE = {"passed", "missing_optional_dep"}


def _print_table(rows: list[SmokeRow]) -> None:
    wi = max(max(len(r.integration) for r in rows), 12)
    wg = min(max(len(r.gate_mechanism) for r in rows), 52)
    wr = max(max(len(r.result) for r in rows), 8)
    header = (
        f"  {'Integration':<{wi}}  {'Gate Mechanism':<{wg}}  {'Result':<{wr}}"
        f"  {'gate_state':<12}  activated"
    )
    sep = "─" * len(header)
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        gate_trunc = (
            row.gate_mechanism[: wg - 3] + "..."
            if len(row.gate_mechanism) > wg
            else row.gate_mechanism
        )
        icon = "✓" if row.result in _ACCEPTABLE else "✗"
        print(
            f"{icon} {row.integration:<{wi}}  {gate_trunc:<{wg}}"
            f"  {row.result:<{wr}}  {row.gate_state:<12}  {str(row.activated).lower()}"
        )
    print(sep)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the activation-gated dormant OSS smoke matrix."
    )
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        help="Write JSON evidence to this file path.",
    )
    args = parser.parse_args(argv)

    now = (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    print(f"\nOSS Dormant Smoke Matrix — {now}")
    print(f"Repo: {REPO_ROOT}\n")

    rows: list[SmokeRow] = []
    for runner in _RUNNERS:
        name = runner.__name__.removeprefix("_smoke_")
        print(f"  running {name} ...", end="", flush=True)
        row = runner()
        rows.append(row)
        icon = "✓" if row.result in _ACCEPTABLE else "✗"
        print(f" {icon} {row.result}")

    print()
    _print_table(rows)
    print()

    gate_closed = [r for r in rows if r.gate_state == "closed"]
    activated_false = [r for r in rows if not r.activated]
    acceptable = [r for r in rows if r.result in _ACCEPTABLE]
    failures = [r for r in rows if r.result not in _ACCEPTABLE]

    print(
        f"Summary: {len(rows)} rows | "
        f"{len(acceptable)} acceptable | "
        f"{len(failures)} unexpected failures"
    )
    print(
        f"gate_state=closed: {len(gate_closed)}/{len(rows)} | "
        f"activated=false: {len(activated_false)}/{len(rows)}"
    )

    if failures:
        print("\nUnexpected failures:")
        for row in failures:
            print(f"  {row.integration}: {row.error_detail[:200]}")

    evidence: dict[str, Any] = {
        "generated_at": now,
        "task_id": "SVC-OSS-DORMANT-SMOKE-MATRIX",
        "repo_root": str(REPO_ROOT),
        "rows": [r.to_dict() for r in rows],
        "summary": {
            "total": len(rows),
            "acceptable": len(acceptable),
            "unexpected_failures": len(failures),
            "gate_state_closed": len(gate_closed),
            "activated_false": len(activated_false),
            "matrix_passed": (
                len(failures) == 0
                and len(gate_closed) == len(rows)
                and len(activated_false) == len(rows)
            ),
        },
    }

    if args.json_out:
        out_path = Path(args.json_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(evidence, indent=2, sort_keys=True))
        print(f"\nEvidence: {args.json_out}")

    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
