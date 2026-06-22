# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-38

| Field | Value |
|---|---|
| Reviewer | Claude |
| Reviewed at | 2026-06-22 |
| Packet commit | `8ae62e9765d1668240e09f208af3a07acaac096f` |
| Pantheon dev base | `9b429c439f505be5c94528a0471f013a7a4c040a` |
| Decision | **Approved** |

## Scope Discipline Check

The packet declares `mutates_canonical: false` and opens with an explicit scope
constraint confirming it does not touch L1 canonical truth, OpenAPI/source-of-truth
contract semantics, BFF runtime code, route registries, governance policy, database
migrations, OpenClaw adapter code, compatibility manifest source, or execute-plans
source files. Inspection of packet commit `8ae62e97` confirms it adds exactly one file:
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-38.md`. No
violation was observed. **Accepted.**

## Factual Accuracy Assessment

### No identity/servant path changed since followup-37

The packet records the diff command and result:

```
git diff --name-status 582857407773db653060467b69e06f786ad1cb39..origin/dev -- \
  services/control-plane/bff/agora/router.py \
  services/control-plane/bff/agora/servant \
  services/control-plane/bff/agora/identity \
  services/control-plane/bff/main.py \
  docs/contracts/agora
```

Result: no output. A separate diff over the full `services/control-plane/bff/agora`
subtree also produced no output. The followup-37 BFF query ledger entries for `/me`,
`/capabilities`, and the full `/bff/agora/servant/*` suite carry forward without
modification. **Accepted.**

### Only Pantheon dev delta since followup-37 is the AG-FE-RS-001 support packet

The packet records:

```
git log --oneline 582857407773db653060467b69e06f786ad1cb39..origin/dev --decorate
```

showing only PR `#2199` / `AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4`. The named
commit `9c2fa257` touched only
`support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-4.md`. The
git log visible from this worktree corroborates: the only commit between
`582857407773` and `9b429c439f50` is that PR `#2199` merge, and it is support-only for
a different parent (`AG-FE-RS-001`). **Accepted.**

### Execute-plans PR `#66` remains the parent merge/deployment blocker

The packet records `gh pr view 66` returning `OPEN` / `UNSTABLE` with head `d1ae3149`,
updated `2026-06-22T01:31:49Z`, and `gh pr checks 66` showing `integration-gate fail`
in run `27923882836`, job `82622466995`. No improvement in merge-readiness is claimed.
The gate ownership table (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) is
preserved verbatim from followup-37. PR `#63` legacy compatibility risk is also carried
forward as unresolved. **Accepted.**

### Compatibility manifest remains fail-closed

The packet records the `agora_compat_manifest.py deployment-gate` command returning
the expected three blocking reasons: `compatibility_status must be compatible`,
`frontend.runtime_commit is a placeholder commit`, and
`blocking_reasons must be empty for deployment`. No deployment readiness claim is made.
**Accepted.**

### Trading-room, candidate-pool, research, and workshop remain outside Phase 1

Section 5 keeps all four surfaces in separate ledger rows, each marked with their
respective phase/scope boundary. The operator journey in Section 8 ends at servant
ensure and does not extend into trading-room decision queues, candidate-pool widgets,
research plan/run routes, or workshop SSE streams. The absorption checklist in Section 9
includes explicit separation checks. **Accepted.**

## Additional Observations

1. The five-file diff comparison (`AgoraApp.tsx`, `identity.ts`, `identity.test.ts`,
   `servant.ts`, `servant.test.ts`) with three commits ahead / zero behind against
   execute-plans `dev ee835e2e` is consistent with followup-37 findings and confirms no
   scope changes to the AG-FE-ID-001 frontend slice at current head `d1ae3149`.

2. The 39 BFF identity/servant/session tests, 3 candidate-pool tests, and 24
   trading-room tests all passing provides appropriate backend regression evidence
   without overclaiming frontend readiness.

3. The generated task brief status (`todo`) versus live `ai-status` (`in_progress`)
   discrepancy is correctly noted in Section 3, avoiding stale-mirror confusion.

4. The packet accurately records that `current-work.md` and the full
   `ai-activity-log.jsonl` were not read, maintaining the read discipline described
   in the task brief.

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

Approved. The packet is scope-disciplined, factually accurate for the followup-38
refresh window, and provides the correct handoff baseline for the parent task to resume
once the execute-plans aggregate gate clears.
