# Execution Proof And Maturity Levels

Last updated: 2026-04-24
Status: canonical maturity ladder for proving Pantheon execution behavior
Tier: L2 Planning & Execution
Scope: what each proof level means, what evidence is required, and what current repo evidence does or does not prove
Conflict rule: this file defines execution-proof levels, but service contracts and deployment evidence still define the detailed technical truth

## 1. Core Rule

Lower-level proof does not imply higher-level proof.

Passing unit tests, contract tests, or a local smoke test does not mean:

- a governed paper-runtime loop is proven
- cross-host execution is proven
- canary or live execution is proven

## 2. Maturity Ladder

| Level | Name | What it proves | Required evidence | What it still does not prove |
|---|---|---|---|---|
| `EP0` | Blueprint published | canonical architecture, policy, and backlog truth exist | L1 and L2 canonical docs published and cross-linked | any runtime behavior |
| `EP1` | Contract proof | route shapes, object semantics, and command vocabulary are verified in code | unit tests, contract tests, schema tests, command validation tests | integrated runtime or deployment behavior |
| `EP2` | Local service proof | one service or one composed slice runs end-to-end in one environment | local smoke test, seeded read-store proof, endpoint acceptance against a running app | multi-service orchestration or multi-host behavior |
| `EP3` | System smoke proof | multiple services or planes operate together in a single deployable environment | single-VM or equivalent system smoke with real service boundaries | governed paper-runtime behavior against realistic external dependencies |
| `EP4` | Governed paper execution proof | the governed paper loop runs with real authority, runtime state, and recovery behavior | cross-plane acceptance plus paper-runtime execution with telemetry, governance, and rollback evidence | canary or live execution safety |
| `EP5` | Canary or live execution proof | real canary or live execution path is proven under governance and rollback controls | staged deployment evidence, operator acceptance, rollback drill, and live or canary runtime proof | nothing higher inside the current ladder |

## 3. Current Evidence Mapping

| Repo evidence | Highest level it supports today | Notes |
|---|---|---|
| `services/control-plane/bff/test_*` and similar unit or contract suites | `EP1` | proves route or object shape only |
| `services/control-plane/bff/smoke_test.py` and targeted FastAPI probes | `EP2` | proves the composed BFF slice locally |
| `docs/deployment/single-vm-smoke-results.md` | `EP3` | proves a single-environment system smoke, not final runtime authority |
| `docs/deployment/dual-vm-acceptance-results.md` | `EP3` | proves cross-plane acceptance harness behavior, but not the final governed paper loop |
| OpenClaw smoke without stable auth or runtime credentials | at most `EP2` | a failing or credential-incomplete smoke cannot be promoted to `EP3` or higher |
| `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/` | `EP4` | governed paper execution run — all eight planes pass; see `docs/deployment/ep4-evidence-packet.md` |

## 4. Promotion Rules

Use these rules when claiming progress:

1. Do not claim "system complete" from `EP1` or `EP2`.
2. Do not claim "runtime complete" from `EP3`.
3. Claim `EP4` only when Pantheon has a governed paper-runtime proof that includes authority, telemetry, and recovery semantics together.
4. Claim `EP5` only when canary or live behavior is proven under the same governance model, not merely under a direct OSS smoke path.

## 5. Current Repo Interpretation

As of the current repo state (updated 2026-04-24):

- blueprint truth is at `EP0`
- many BFF and contract slices are at `EP1`
- several local composed surfaces are at `EP2`
- documented single-VM and dual-VM harness evidence reaches `EP3`
- **the repo has a stable `EP4` governed paper execution proof** — all eight planes pass; evidence
  at `docs/deployment/evidence/ep4-governed-paper/20260419T003720Z/`, packet at
  `docs/deployment/ep4-evidence-packet.md`
- the repo does not yet have an `EP5` canary or live execution proof
- the coordination board now has repo-local proof for all `46` tracked frontend-delivery
  features after consultation + knowledge batch 1 and operator + trainer +
  residual batch 2 were consolidated in
  `docs/deployment/runtime-verification-batch-1-consultation-knowledge.md` and
  `docs/deployment/runtime-verification-batch-2-operator-trainer-residuals.md`;
  that coverage number is useful operationally, but it is not a higher
  execution-proof level by itself
- the new BFF-backed `Settings` surface improves repo truthfulness and removes a demo-backed page,
  but it does not by itself raise the repo above `EP4`

## 6. Required Follow-on Work

EP4 is stable as of 2026-04-19. The next proof-raising steps are:

1. `EP5-001` — prepare the canary-ready execution path: real broker/venue config, scaled capital
   gate, operator approval checklist, and rollback drill harness; this is a downstream prerequisite
   slice gated on stable EP4. The prepared repo-local entry bundle lives at
   `docs/deployment/ep5-canary-ready/`, with runnable tooling at
   `scripts/run_ep5_canary_readiness.py` and `env/canary-exec.env.example`
2. `EP5-002` — execute and archive the first canary/live proof packet, including rollback drill and
   operator signoff; this requires a separate human-approved gate and is not part of the current
   EP4 materialization batch
3. keep the tracked runtime-verification coverage truthful at `46/46`; if a
   future frontend cycle reopens a delivery surface or adds a new tracked
   feature, do not count it closed again without a stored proof artifact
4. keep the telemetry event-trace read-model gap explicitly dispositioned for EP5:
   the current APP-003 closeout packet at `docs/deployment/app-003-openclaw-closeout-packet.md`
   marks it as `packetized`, not closed, until a replay-clean trace-query capture is archived
