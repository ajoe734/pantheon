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
- the coordination board currently reports `Runtime verified: 32` out of `46` tracked frontend-delivery
  features; that coverage number is useful operationally, but it is not a higher execution-proof
  level by itself
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
3. raise runtime-verification coverage beyond the current `32/46` so the operator and delivery
   surfaces have broader replayable evidence before any EP5 proof claim
4. address the telemetry event-trace read-model gap (local dev 404 on port 38083) if EP5 requires
   queryable event-trace projections beyond counter-level ingest proof

## 7. EP5-002 Closeout Checklist

`EP5-002` is not complete until a human-approved canary/live packet exists and all evidence below
is archived in one replayable bundle.

### Preconditions

- `EP4` packet remains the latest approved governed-paper baseline
- production or canary credentials are injected through the approved operator path
- rollback drill tooling is available and tested against the target environment
- the operator has reviewed the current `current-work.md`, `ai-status.json`, and the active
  deployment target tuple

### Required Operator Run Steps

1. record the target runtime tuple: code commit, env file revision, credentials revision, and
   broker/venue target
2. execute one canary or live deployment through the governed deployment path, not a direct OSS
   bypass
3. verify the runtime reaches the intended stage and emits telemetry, lineage, and governance events
4. execute one rollback drill against the same runtime tuple
5. confirm the rollback restores the expected runtime state and leaves an auditable lineage trail
6. capture explicit operator signoff, including whether the proof was canary or live

### Required Evidence Bundle

Archive all of the following under a new `docs/deployment/evidence/ep5-*` packet:

- deployment plan / runtime binding identifiers
- runtime stage transition proof
- telemetry and lineage excerpt proving the execution path was real
- rollback drill transcript or command log
- post-rollback state snapshot
- operator acceptance note with timestamp and approver identity
- any exception, partial failure, or follow-up required after the run

### Close Condition

`EP5-002` closes only when the packet above is committed, reviewable, and explicitly linked from
this file or its successor evidence index. A successful run without archived evidence is still not
an `EP5` claim.

## 8. Runtime Verification 32 → 46 Checklist

The coordination count is operational proof coverage, not a higher execution-proof level. To raise
coverage from `32` to full tracked coverage, each remaining feature must have one runtime-visible
verification artifact attached to its tracked packet family.

### Accepted Runtime Proof Types

- `needs-runtime` request resolved with `runtime_verified_at` or equivalent proof field
- `frontend-feedback` / `backend-delivery` payload with a concrete runtime verification reference
- a review packet that explicitly verifies runtime behavior against a Git-visible request pair

### Per-Feature Checklist

For each feature still missing runtime verification:

1. identify the canonical feature id from `ai-status.json` / `current-work.md`
2. locate the request pair or closeout response in `.coordination/`
3. run the smallest truthful runtime proof for that feature family
4. attach the proof reference back into the tracked payload or follow-up review
5. rerun `python3 scripts/ai_status.py sync`
6. confirm the feature increments the `Runtime verified` count without regressing stage truth

### Do Not Do

- do not mark runtime proof complete from unit or contract tests alone
- do not backfill proof counts from memory if no payload or review packet records the verification
- do not count the same runtime artifact against unrelated features unless the packet explicitly says
  the proof is shared
