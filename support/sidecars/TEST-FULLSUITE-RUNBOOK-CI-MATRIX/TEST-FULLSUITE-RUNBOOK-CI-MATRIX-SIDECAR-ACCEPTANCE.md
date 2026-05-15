# TEST-FULLSUITE-RUNBOOK-CI-MATRIX Acceptance Packet

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `TEST-FULLSUITE-RUNBOOK-CI-MATRIX-SIDECAR-ACCEPTANCE`
**Helper parent:** `TEST-FULLSUITE-RUNBOOK-CI-MATRIX`
**Parent owner:** `Codex`
**Parent reviewer:** `Claude`
**Prepared by:** `Codex2`
**Reviewer:** `Codex`
**Date:** `2026-04-30`
**Status:** `ready for review`

> Scope constraint: support artifact only. This packet summarizes acceptance,
> dependency, and reviewer handoff checks for the parent task without changing
> canonical truth, L1 policy, runtime code, registry behavior, or governance
> implementation.

## Purpose

This packet gives the parent owner a narrow acceptance read for the canonical
full-suite runbook and CI matrix. It does not approve or finalize the parent.
It records which evidence surfaces should be considered, which commands are
default-safe, and which follow-up questions remain for the parent owner or
reviewer.

## Current Parent Read

The parent task is `in_progress` in `ai-status.json` with three acceptance
criteria:

| Parent acceptance criterion | Current read | Evidence surface |
|---|---|---|
| Full-suite runbook lists root pytest, per-service smoke, compile, compose config, full compose smoke, and gated production-posture commands | `met with review notes` | `docs/testing/full-suite-runbook.md` lists root collection/execution, focused smoke commands, script compile, compose config, full compose smoke, compose activation-ready smokes, and production activation gate reporting. |
| Required `PYTHONPATH` and env flags are explicit | `met` | `docs/testing/full-suite-runbook.md` sets `export PYTHONPATH="${PYTHONPATH:-.}"` and scopes activation flags to the named rows only. |
| No command activates Qlib, TRL, RL, W&B, paper, canary, or live execution by default | `met with reviewer confirmation needed` | The runbook marks activation-ready rows as local/offline or profile-disabled; `docker-compose.yml` keeps activation-ready and dormant smoke services behind non-default profiles and false production/live envs. |
| Runbook is linked from current work or CI documentation | `met for CI docs surface` | `docs/testing/pytest-harness.md` links to `docs/testing/full-suite-runbook.md`; CI mapping is included in the runbook. |

## Acceptance Checklist For Parent Review

| ID | Check | Reviewer action |
|---|---|---|
| AC-1 | Confirm the runbook command order is complete: Stage-0 config, orchestrator regression, root collect, root execution, script compile, dormant OSS, activation-ready OSS, OpenClaw E2E, compose config, full compose smoke, compose activation-ready smokes, and production gate report. | Compare `docs/testing/full-suite-runbook.md` `Matrix` section against parent acceptance. |
| AC-2 | Confirm direct smoke entrypoints stay outside default pytest collection. | Check `docs/testing/pytest-harness.md` and the runbook focused follow-up commands. |
| AC-3 | Confirm the environment model is explicit and scoped. | Verify `PYTHONPATH` is documented once and activation flags are listed as row-scoped, not global shell exports. |
| AC-4 | Confirm default-safe behavior for research and execution gates. | Ensure default rows do not enable Qlib/TRL/RL/W&B online sync, production adapters, paper, canary, or live execution. |
| AC-5 | Confirm compose profile commands remain opt-in. | Check `docker-compose.yml` profiles `activation-ready-smoke`, `openclaw-activation-ready-e2e`, and `dormant-smoke` are not default services. |
| AC-6 | Confirm CI mapping is descriptive and does not imply that every runbook row already has first-class CI automation. | Review the `CI Mapping` table and decide whether parent closeout should record remaining CI gaps as follow-up rather than acceptance failure. |
| AC-7 | Confirm closeout evidence does not overclaim full-suite green. | The runbook records focused checks and notes full compose smoke as the canonical end-to-end row; if root execution or full compose has runtime failures, they should be tracked as runtime/domain tasks, not hidden. |

## Dependency Map

All parent dependencies are already `done` in the task brief and current
`ai-status.json`.

| Dependency | Status | What this parent needs from it |
|---|---|---|
| `TEST-FULLSUITE-HARNESS-ISOLATION` | `done` | Root pytest collection/import isolation and direct-smoke exclusion contract. |
| `TEST-ORCHESTRATOR-REGRESSION-CLOSEOUT` | `done` | Orchestrator/status/supervisor regression rows that can be cited in the full-suite order. |
| `SVC-HEALTH-OPENCLAW-CONTRACT-ALIGN` | `done` | Health semantics for the OpenClaw activation-ready row. |
| `SVC-SOURCE-SEARCH-TEST-CLOSURE` | `done` | Source/search production-posture smoke command and boundary. |
| `SVC-TELEMETRY-ORDER-SCHEMA-CLOSURE` | `done` | Telemetry/order schema closure that keeps root suite failures from being schema-truth ambiguity. |
| `SVC-RESEARCH-REPLICATION-SMOKE-FIX` | `done` | Research smoke entrypoint stability for full-suite inclusion. |
| `SVC-OSS-WANDB-DORMANT-MATRIX-ALIGN` | `done` | W&B dormant/offline behavior and online-sync fail-closed evidence. |
| `SVC-BFF-INCIDENT-SMOKE-FIXTURE` | `done` | BFF incident smoke fixture honesty for root/default regression scope. |
| `SVC-OPENCLAW-HONEST-STACK-SEMANTICS` | `done` | Honest-stack degraded semantics and OpenClaw optional-upstream behavior. |

## Evidence Surfaces

| Surface | Role |
|---|---|
| `docs/testing/full-suite-runbook.md` | Primary parent artifact for command order, env flags, default-safe posture, focused follow-up commands, CI mapping, and closeout evidence notes. |
| `docs/testing/pytest-harness.md` | Pytest import/collection contract and link back to the full-suite runbook. |
| `.github/pantheon-stage0-matrix.json` | Machine-readable Stage-0 target and build/verify matrix. |
| `.github/workflows/stage-0-ci.yml` | Existing CI workflow for Stage-0 matrix validation, changed-target verify, and build dry-run. |
| `.github/workflows/regression-tests.yml` | Existing Lean regression workflow mapped by the runbook as a regression layer, not necessarily a Pantheon Python full-suite replacement. |
| `.github/workflows/research-regression-tests.yml` | Existing Lean research-regression workflow; review should distinguish it from local Pantheon research smoke rows. |
| `.github/workflows/syntax-tests.yml` | Existing syntax/vectorbt smoke layer. |
| `docker-compose.yml` | Compose profile definitions for smoke, activation-ready OSS, OpenClaw E2E, and dormant smoke rows. |
| `scripts/smoke_oss_activation_ready_matrix.py` | Local/offline activation-ready OSS row. |
| `scripts/smoke_openclaw_activation_ready_e2e.py` | OpenClaw activation-ready E2E row with fake upstream/runtime-manager/broker fixtures. |
| `scripts/run_research_activation_gates.py` | Production activation gate report row; read/report only without explicit future evidence. |

## Verification Snapshot

This sidecar did not run the full suite or modify parent artifacts. Verification
was limited to support-packet evidence checks and low-risk command validation:

1. Confirmed `docs/testing/full-suite-runbook.md` and
   `docs/testing/pytest-harness.md` exist and are clean in the current worktree.
2. Confirmed `.github/pantheon-stage0-matrix.json`,
   `.github/workflows/stage-0-ci.yml`, and `docker-compose.yml` are clean in the
   current worktree.
3. Ran `python3 scripts/ci_stage0.py validate` successfully.
4. Ran `python3 -m py_compile scripts/smoke_honest_stack.py scripts/smoke_openclaw_activation_ready_e2e.py scripts/smoke_oss_activation_ready_matrix.py scripts/smoke_dormant_oss_matrix.py scripts/run_research_activation_gates.py` successfully.
5. Ran `docker compose config --quiet` successfully.

## Review Notes For Parent Owner

1. The runbook is materially present and default-safe, but parent closeout
   should be careful not to claim that every listed row is already automated in
   CI. The current CI mapping reads as layer mapping, not a full automation
   guarantee.
2. The full compose smoke remains the canonical end-to-end row. If it has not
   been run for parent closeout, the parent owner should either run it or state
   why parent acceptance is limited to runbook/matrix publication rather than
   fresh full-stack execution proof.
3. The production activation gate row is read/report only. It should not be
   described as promotion, paper, canary, or live readiness.
4. Existing Lean-oriented workflows under `.github/workflows/regression-tests.yml`
   and `.github/workflows/research-regression-tests.yml` should not be treated
   as substitutes for Pantheon Python root pytest or compose smoke unless the
   parent owner explicitly adds that automation in a separate scoped task.

## Reviewer Checklist

Before approving this sidecar, confirm:

1. The packet stays support-only and does not mutate canonical truth.
2. The acceptance checklist maps directly to the parent task acceptance criteria.
3. Dependency status is limited to the task brief/current `ai-status.json`
   `done` read.
4. Verification commands are accurately reported.
5. The handoff gives `Codex` enough information to decide whether to absorb any
   notes into the parent runbook closeout or leave them as review caveats.
