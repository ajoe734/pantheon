# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-37

| Field | Value |
|---|---|
| Reviewer | Claude |
| Reviewed at | 2026-06-22 |
| Packet commit | `12f27730b369b732a445c608f43f393c5d39b0bd` |
| Pantheon dev base | `fd335b5c43623d5821180faf25e4337052cb786f` |
| Decision | **Approved** |

## Scope Discipline Check

The packet declares `mutates_canonical: false` and opens with an explicit scope
constraint confirming it does not touch L1 canonical truth, OpenAPI/source-of-truth
contract semantics, BFF runtime code, route registries, governance policy, database
migrations, OpenClaw adapter code, compatibility manifest source, or execute-plans
source files. No violation was observed; only
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-37.md` was
committed in the task-scoped packet commit.

## Factual Accuracy Assessment

### No identity/servant path changed since followup-36

The packet records the diff command and result:

```
git diff --name-status 447d215e6a9d..origin/dev -- \
  services/control-plane/bff/agora/router.py \
  services/control-plane/bff/agora/servant \
  services/control-plane/bff/agora/identity \
  services/control-plane/bff/main.py \
  docs/contracts/agora
```

Result: no output. The packet also records a separate diff over the full
`services/control-plane/bff/agora` subtree with no output. The followup-36 BFF query
ledger entries for `/me`, `/capabilities`, and the full `/bff/agora/servant/*` suite
carry forward without modification. **Accepted.**

### Only Pantheon dev delta since followup-36 is the AG-FE-RS-001 support packet

The packet records:

```
git log --oneline 447d215e6a9d..origin/dev --decorate
```

showing only PR `#2196` / `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3`. The named
commit `1901039d` touched only
`support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md`. The
git log visible from this worktree corroborates: the only commit between `447d215e` and
`fd335b5c` is that PR `#2196` merge. **Accepted.**

### Execute-plans PR `#66` remains the parent merge/deployment blocker

The packet records `gh pr view 66` returning `OPEN` / `UNSTABLE` with head `d1ae3149`,
and `gh pr checks 66` showing `integration-gate fail` in run `27923882836`, job
`82622466995`. The gate ownership table (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6:
Codex2) is preserved verbatim from followup-36 with no buried aggregate failures.
PR `#63` legacy compatibility risk is also carried forward as unresolved. **Accepted.**

### Trading-room, candidate-pool, research, and workshop remain outside Phase 1

Section 5 keeps all four surfaces in separate ledger rows each marked with their
respective phase/scope boundary. The operator journey in Section 8 ends at servant
ensure and does not extend into trading-room decision queues, candidate-pool widgets,
research plan/run routes, or workshop SSE streams. The absorption checklist in Section 9
includes explicit separation checks for candidate-pool, trading-room, and
research/workshop surfaces. **Accepted.**

## Additional Observations

1. The five-file diff comparison (`AgoraApp.tsx`, `identity.ts`, `identity.test.ts`,
   `servant.ts`, `servant.test.ts`) with three commits ahead / zero behind against
   execute-plans `dev ee835e2e` is accurate and confirms no `types.ts` reintroduction
   at current head `d1ae3149`.

2. The compatibility manifest gate is correctly flagged fail-closed with three expected
   blocking reasons (`compatibility_status`, placeholder `runtime_commit`,
   `blocking_reasons` non-empty). No deployment readiness claim was made.

3. The 39 BFF identity/servant/session tests, 3 candidate-pool tests, and 24
   trading-room tests all passing provides appropriate backend regression evidence
   without overclaiming frontend readiness.

4. The packet correctly records that the generated task brief remains in `todo` state
   while active `ai-status` is authoritative for `in_progress`, avoiding confusion
   between stale mirrors and live state.

## Carry-Forward Rules For Parent

The parent (`AG-FE-ID-001`, owner Claude) should not absorb this sidecar into
completion until:

- execute-plans PR `#66` merges into execute-plans `dev`
- the compatibility manifest records a non-placeholder frontend runtime commit and
  `compatibility_status: compatible`
- the aggregate gate reruns cleanly or a formal exception is recorded by the gate
  owners (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2)

Gate ownership assignments remain active and must not be silently resolved inside
AG-FE-ID-001 closeout.

## Decision

Approved. The packet is scope-disciplined, factually accurate for the followup-37
refresh window, and provides the correct handoff baseline for the parent task to resume
once the execute-plans aggregate gate clears.
