# AG-E2E-SW-001 — Acceptance Packet and Dependency Map

**Sidecar kind:** acceptance_packet
**Sidecar task:** AG-E2E-SW-001-SIDECAR-ACCEPTANCE
**Parent task:** AG-E2E-SW-001
**Parent owner:** Codex
**Parent reviewer:** Claude
**Prepared by:** Claude (sidecar owner)
**Date:** 2026-06-22
**Authority docs:**
- `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/06_winner_branch_e2e_and_isolation.md`
- `services/control-plane/tests/agora/test_winner_branch_e2e_v13.py` (AG-DES-E2E-001 contract baseline)
- `services/control-plane/tests/agora/test_agora_isolation_matrix.py` (AG-DES-E2E-001 isolation baseline)

---

## Purpose

This packet gives the parent task reviewer (Claude) a structured acceptance
checklist and gate matrix for reviewing AG-E2E-SW-001, and records the review
evidence now that PR #2261 has merged and the parent task brief records
`review_approved`. It is a support artifact only — it does not modify canonical
truth, schema definitions, or implementation code.

---

## Scope of AG-E2E-SW-001

AG-E2E-SW-001 ("Winner-branch workshop E2E acceptance") centers on one new
task-scoped test file:

```
services/control-plane/tests/agora/test_winner_branch_workshop_e2e.py
```

This file must prove the end-to-end winner-branch workshop flow: a trader
submits a governed evidence description, the servant reconstructs it and
produces a completeness map, a StrategySpec draft is created and registered,
and the full flow asserts no broker order, RuntimeBinding, or capital binding
is created at any step.

Reviewer correction, 2026-06-22: PR #2261 also added the parent task brief and
modified the two AG-DES-E2E-001 baseline test files. Treat those edits as part
of the parent implementation review scope, not as sidecar output. The
acceptance gate should verify those baseline edits preserve the §F1 and §F2–F7
contracts; it should not assume the parent PR is a single-file diff.

The task is **not** a repeat of AG-DES-E2E-001 (which produced the 11-step
§F1 contract test and the §F2–F7 isolation matrix). AG-E2E-SW-001 is the
Strategy Workshop integration test that exercises the **SW-layer implementation**
against those established contracts.

**Iron rule (inherited from AG-DES-E2E-001 and AG-XR-OPENAPI-004):**
Frozen v1 / v1.1 / v1.2 bundle indexes and OpenAPI files must not be altered.
No new schema fields, routes, or enum values may be invented.

---

## Dependency State

All three task dependencies are `done` and merged into `dev` per GitHub PR
metadata checked by Codex on 2026-06-22.

| Dependency | Status | Merged artifact(s) | Acceptance consequence |
|---|---|---|---|
| `AG-BE-SW-003` | **done** (PR #2018, head `b4262bd7`, merge `bc37f3d5`) | `integrations/openclaw/skills/agora/strategy_completeness/`, `services/research/strategy_spec/completeness.py` | SW E2E must consume the real completeness/NBQ skill. 26 gold+hard-rule tests pass; five-state output confirmed. |
| `AG-FE-SW-002` | **done** (PR #2252, head `57307d15`, merge `bcac3cf4`) | `execute-plans/src/agora/components/StrategyCompletenessRail.tsx`, `ResearchPlanCard.tsx`, `ConsultResultCard.tsx` | 12 card types implemented field-for-field with v4 schema; SSE stream wired. FE completion confirms the backend E2E can trust the card/stream contract. |
| `AG-XR-OPENAPI-004` | **done** (PR #2072, head `a2be54df`, merge `dfaf1209`) | `services/control-plane/openapi/agora_v1_3.openapi.yaml`, `specs/agora/v4/capability_manifest_v1_3.json`, `specs/agora/bundle_index.v1_3.json` | 29 routes, 11 v4 schemas, SHA256 chain v1→v1.3 intact. Tests must cite these paths; no route drift or self-invented fields allowed. |

Additionally, `AG-DES-E2E-001` (the design task that produced the §F1–§F7
contract baseline) is `review_approved` and its test files are present in the
repository. PR #2261 later modified both files, so reviewer acceptance must
confirm those modifications preserve or strengthen the original baseline:

| File | Source | Role |
|---|---|---|
| `services/control-plane/tests/agora/test_winner_branch_e2e_v13.py` | AG-DES-E2E-001 | §F1 11-step contract baseline (149 tests in suite) |
| `services/control-plane/tests/agora/test_agora_isolation_matrix.py` | AG-DES-E2E-001 | §F2–F7 isolation contract baseline |

AG-E2E-SW-001 must complement these files, not replace them.

---

## Implementation Evidence (from task status)

PR: **#2261**
Commit: `7021b4ab5f087a1be3083e0f6feb36ec2d7974ff`
Merged: **2026-06-22** into `dev` with merge commit `2b44a405e25bb1a776f6a5ef9ede250c3acebaf3`

Files in PR #2261:
- Added `.orchestrator/task-briefs/ag_e2e_sw_001.md`
- Modified `services/control-plane/tests/agora/test_agora_isolation_matrix.py`
- Modified `services/control-plane/tests/agora/test_winner_branch_e2e_v13.py`
- Added `services/control-plane/tests/agora/test_winner_branch_workshop_e2e.py`

Verified (as reported in task next-field):
- Agora acceptance test suite: **149 passed**
- `strategy_spec/source seed` suite: **95 passed**
- Full `services/control-plane/tests/agora/`: **152 passed** (3 pre-existing `datetime.utcnow` warnings, non-blocking)
- `git diff --check`: clean

---

## Acceptance Checklist (for Claude as reviewer)

### Gate 1 — Scope compliance

- [ ] Review the full PR #2261 file scope listed above. The new workshop E2E file must be the task's primary implementation artifact; the two baseline-test edits must preserve or strengthen AG-DES-E2E-001 coverage, and the task brief addition must remain record-only.
- [ ] No schemas, OpenAPI files, bundle indexes, runtime/governance implementation, or frontend generated types are changed by this task.
- [ ] The test cites or validates against merged repository contract paths (`strategy_spec.schema.json`, root Agora schemas, v4 schemas, `agora_v1_3.openapi.yaml`, or the design-closure-round2 prose docs) — not only the task brief.
- [ ] Frozen v1/v1.1/v1.2 files (`bundle_index.json`, `bundle_index.v1_1.json`, `bundle_index.v1_2.json`, `agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, `agora_v1_2.openapi.yaml`) are untouched.

### Gate 2 — Winner-branch flow coverage (SW layer)

The test file must cover the SW-layer integration for each of these flow segments. Map each segment to at least one test function or parametrized case:

| Segment | Required coverage |
|---|---|
| Trader submits governed evidence description | Fixture conforms to workshop creation schema; raw initial message stored as encrypted/private ref, not plaintext |
| Servant reconstruction | Completeness map produced; five states (`confirmed`, `inferred_needs_confirmation`, `missing`, `weak`, `conflicting`, `not_applicable`) reflected correctly; NBQ emitted |
| StrategySpec draft creation | One Registry draft created; workshop-version link created; StrategySpec truth NOT copied into workshop storage; lineage IDs asserted |
| Completeness map accuracy | Map aligns with root `services/control-plane/specs/agora/strategy_completeness.schema.json` and AG-BE-SW-003 gold-case output; v4 card/stream/readiness references use the merged v4 schemas; no invented grade values |
| No-broker-order assertion | **Explicit** negative assertion that no broker order, RuntimeBinding, or capital binding is created at any step |

### Gate 3 — No self-invented fields or routes

- [ ] Every fixture field and assertion references a field defined in a merged schema (`services/control-plane/specs/strategy_spec.schema.json`, `services/control-plane/specs/agora/strategy_completeness.schema.json`, or `services/control-plane/specs/agora/v4/*.schema.json`) or the design-closure-round2 prose docs.
- [ ] Route, workshop-card, stream, readiness, versioning, research, trading-room, and governed-handoff contract checks use the merged v4 schema set listed in `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json`; no self-invented fields are allowed.
- [ ] No route paths (`/api/agora/...`) are called that do not appear in `agora_v1_3.openapi.yaml`.
- [ ] No enum values are used that are not listed in the merged schemas.

### Gate 4 — No silent fixtures

- [ ] Any stub or fixture that stands in for a real backend response must be clearly labelled (e.g., `# stub: pending <task-id>` or `pytest.mark.skip` with an explicit blocker ref).
- [ ] Tests must fail if the real implementation is absent — no vacuous assertions that always pass regardless of implementation.
- [ ] The task's own `next` field notes: "replaced vacuous no-order assertions with schema-backed fixtures" — verify this claim: no-order assertion must use a concrete fixture value, not `assert True` or an empty check.

### Gate 5 — CI alignment

- [ ] All 152 tests in `services/control-plane/tests/agora/` pass (PR #2261 commit evidence shows 152 passed). The 3 pre-existing `datetime.utcnow` warnings are non-blocking but should not have increased.
- [ ] `git diff --check` clean on the PR branch.

### Gate 6 — Non-regression

- [ ] The existing §F1 baseline (`test_winner_branch_e2e_v13.py`) and §F2–F7 isolation matrix (`test_agora_isolation_matrix.py`) continue to pass after the PR #2261 edits.
- [ ] Any PR #2261 changes to those baseline tests are compatible with the AG-DES-E2E-001 contract intent and do not weaken frozen-file, no-order, route, schema, or isolation assertions.
- [ ] The `strategy_spec/source seed` suite (95 tests) continues to pass.

---

## Review Gate Summary

When Claude receives AG-E2E-SW-001 for review in PR #2261, use these gates:

1. **Scope** — the task-scoped workshop E2E file is the primary artifact; baseline-test edits are justified and reviewed; no schema/OpenAPI/bundle drift.
2. **Flow coverage** — each SW-layer segment in Gate 2 has a named test or parametrized case.
3. **No-order assertion** — Step 11-equivalent negative assertion is explicit and schema-backed, not vacuous.
4. **No self-invented fields** — every field is traceable to the merged schema set or design-closure-round2 doc; v4 route/card/stream/readiness fields come from the v1.3 capability manifest schema list.
5. **No silent fixtures** — stubs are labelled; tests fail without the real implementation.
6. **CI green** — 152 tests pass; 3 pre-existing warnings not increased; `diff --check` clean.
7. **Non-regression** — §F1 and §F2–F7 baselines remain contract-equivalent or stronger and passing.

If any gate fails, use `reopen` with the specific failing gate(s) listed. Do not approve with unresolved self-invented fields or vacuous no-order assertions.

---

## What Depends on AG-E2E-SW-001 (Downstream Unblocks)

Per the dispatch unblock matrix:

| Downstream | Condition |
|---|---|
| Further E2E polish / gap tasks | AG-E2E-SW-001 passing confirms the SW integration layer satisfies the winner-branch contract baseline. |

Note: `AG-E2E-TR-001` (Trading Room E2E) and `AG-TEST-ID-001` (isolation matrix) are parallel unblocks from `AG-DES-E2E-001`, not blocked by this task.

---

## Files This Packet Does NOT Modify

- `services/control-plane/specs/agora/bundle_index.json` (frozen)
- `services/control-plane/specs/agora/bundle_index.v1_1.json` (frozen)
- `services/control-plane/specs/agora/bundle_index.v1_2.json` (frozen)
- `services/control-plane/openapi/agora_v1.openapi.yaml` (frozen)
- `services/control-plane/openapi/agora_v1_1.openapi.yaml` (frozen)
- `services/control-plane/openapi/agora_v1_2.openapi.yaml` (frozen)
- `services/control-plane/tests/agora/test_winner_branch_e2e_v13.py` (AG-DES-E2E-001 baseline)
- `services/control-plane/tests/agora/test_agora_isolation_matrix.py` (AG-DES-E2E-001 baseline)
- Any L1 canonical truth docs

---

## Handoff Destination

This packet should be cited in the sidecar review handoff message to Codex.
When Codex reviews this sidecar, it should confirm: dependency state is
accurate, gate matrix matches the task acceptance criteria, and the
no-self-invented-fields rule is derived from the merged repository schema set,
with v4-specific route/card/stream/readiness constraints derived from the
merged v4 schemas.

The parent task reviewer (Claude) may use this packet directly as the gate
checklist when reviewing PR #2261.
