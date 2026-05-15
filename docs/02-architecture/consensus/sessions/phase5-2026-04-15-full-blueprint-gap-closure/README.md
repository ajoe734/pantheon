# Discussion Planning Mode — Phase 5: Full Blueprint Gap Closure

Record note: this session directory is planning history and execution-shaping record material, not canonical blueprint truth.

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase5-2026-04-15-full-blueprint-gap-closure`
- Phase: `phase5`
- Objective: converge the full Pantheon delivery gap across serviceization, workbench packet coverage, Lovable execution, OSS realization, and CI/CD + GCP infrastructure before materializing the next execution waves.
- Facilitator: `Claude`
- Starter draft owner: `Codex`

## Scope

This session is intentionally broader than `phase4-2026-04-15-service-layer-completion`.

The active phase4 session remains the focused service-layer planning record. This phase5 session is the umbrella planning layer that decides how the full blueprint should be cut into execution waves without pretending that the remaining OSS, infrastructure, and front-end work is "someone else's problem."

## Brief Files

### Canonical State
- `ai-status.json`
- `current-work.md`
- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`

### Prior Planning Inputs
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/consensus-packet.md`
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`

### Architecture / Integration / Delivery Bus
- `TARGET_ARCHITECTURE.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `docs/delivery-coordination-bus.md`
- `Pantheon_GCP_GitHub_Docker_正式部署與環境設計_v2.md`
- `docs/remote-dev-gcp-vm.md`

### Session-Specific Inventory
- `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/full-blueprint-gap-inventory.md`

## Gap Summary

This session treats the remaining blueprint gap as four linked buckets:

1. `service stack honesty`
   phase2-phase4 domain objects and operator semantics exist, but the runtime/governance/evidence surfaces are still not deployable and compose-backed end to end.
2. `surface and Lovable closure`
   APP-002 packets exist, but most of the front-end wave is still waiting for Lovable/front-end implementation, and large parts of the non-APP-002 workbench backlog remain unpacketized.
3. `OSS realization`
   OpenClaw and the deferred framework stack are governed but not truly integrated.
4. `delivery infrastructure`
   the repo contains target-state GCP / CI-CD design, but not a closed, executable pipeline and environment baseline.

## Expected Outputs

| Output | Owner | Path |
|---|---|---|
| `starter-draft.md` | Codex | this dir |
| `full-blueprint-gap-inventory.md` | Codex | this dir |
| `review-round-01.md` | Gemini, Claude, Codex | this dir |
| `consensus-packet.md` | Claude | this dir |
| `execution-materialization.md` | Codex | this dir |

## Baton Loop

1. Every healthy lane reads the brief and writes an independent readout using `LLM_READOUT_TEMPLATE.md`
2. `Codex` seeds `starter-draft.md` and the first execution slice DAG
3. Cited cross-review happens in `review-round-01.md`
4. `Claude` drafts `consensus-packet.md`
5. Human gate decides which waves are approved for execution
6. `Codex` materializes the approved slices via `scripts/planning_state.py materialize`

## Review Discipline

- `Qwen` and `Copilot` have repeatedly stalled in this repo. This session starts with explicit fallback coverage:
  - `Claude` absorbs Qwen-style schema / boundary review
  - `Codex` absorbs Copilot-style acceptance / external dependency review
- The agent-of-record files remain in this directory for auditability, but the active reviewer order for this session is `Gemini -> Claude -> Codex`.

## Rules

- Only `Codex` edits `starter-draft.md` directly
- Reviewers do not rewrite the shared draft; disagreements go into review rounds
- `planning-session.json` is the machine-readable source of truth
- Execution tasks stay in `ai-status.json`; planning docs remain planning artifacts until human-gated materialization
- This session may reference previous phase3 / phase4 planning artifacts, but it must not overwrite their historical scope
- If a category is called out here as a blueprint blocker, it must either become an execution slice, a waived/follow-on item with explicit reasoning, or a human-gated open question
