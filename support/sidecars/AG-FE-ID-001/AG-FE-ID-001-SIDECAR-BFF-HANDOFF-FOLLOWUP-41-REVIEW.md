# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-41

| Field | Value |
|---|---|
| Reviewer | Claude |
| Reviewed at | 2026-06-22 |
| Packet commit | `97c66902` |
| Pantheon dev base | `80edfc2bfec1a35040d6340271754fad111fa86f` |
| Decision | **Approved** |

## Scope Discipline Check

The packet declares `Mutates canonical truth: false` and opens with an explicit scope
constraint confirming it does not touch L1 canonical truth, OpenAPI/source-of-truth
contract semantics, BFF runtime code, route registries, governance policy, database
migrations, OpenClaw adapter code, compatibility manifest source, or execute-plans
source files. The task brief confirms PR `#2214` touches only
`support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-41.md`.
No scope violation was observed. **Accepted.**

## Factual Accuracy Assessment

### Pantheon dev advancement since followup-40 closeout

The packet records a dev window from `ad09c3b2` (followup-40 closeout merge) to
`80edfc2b` containing seven files across four merges:

- `support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md`
  (AG-FE-RS sidecar packet, under `support/sidecars/AG-FE-RS-001/`)
- `support/sidecars/AG-FE-RS-001/AG-FE-RS-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-7.md`
  (AG-FE-RS sidecar packet, under `support/sidecars/AG-FE-RS-001/`)
- `.github/workflows/branch-ci.yml`
- `scripts/check_bff_live_evidence_secret_inventory.py`
- `scripts/git/resolve_commit_trailer_range.py`
- `scripts/git/test_git_workflow_helpers.py`
- `scripts/test_check_bff_live_evidence_secret_inventory.py`

The git log on this task branch is consistent with this characterisation: `80edfc2b`
is the merge of PR `#2171` (MGMT-LIVE-EVIDENCE-SECRET-INVENTORY) and the preceding
commits include the AG-FE-RS-001 FOLLOWUP-6 and FOLLOWUP-7 sidecar packet merges.
All seven files are support-only (two sidecar packets, four CI/scripting helper files,
one workflow update) with no identity, servant, BFF runtime, or contract surface
changes. **Accepted.**

### No identity/servant/Agora BFF path changed since followup-40

The packet records diff commands over:
- `services/control-plane/bff/agora/router.py`
- `services/control-plane/bff/agora/servant`
- `services/control-plane/bff/agora/identity`
- `services/control-plane/bff/main.py`
- `docs/contracts/agora`

All produced no output. This is consistent with the dev window containing only support
packet files and non-Agora CI/scripting helper files. The followup-40 BFF query ledger
for `/me`, `/capabilities`, and the full `/bff/agora/servant/*` suite carries forward
without modification. The `support/sidecars/AG-FE-ID-001/` diff also produced no
output, confirming no AG-FE-ID-001 support file was silently modified between
followup-40 closeout and this packet. **Accepted.**

### Execute-plans PR `#66` remains the parent merge/deployment blocker

The packet records `gh pr view 66` returning `OPEN` / `UNSTABLE` with head `d1ae3149`,
updated `2026-06-22T01:31:49Z`, and `gh pr checks 66` showing `integration-gate fail`
in run `27923882836`, job `82622466995`. These are identical to the values recorded in
the followup-40 review, confirming no merge-readiness improvement in the refresh window.
The gate ownership table (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) is
correctly preserved. The five-file PR `#66` diff (`AgoraApp.tsx`, `identity.ts`,
`identity.test.ts`, `servant.ts`, `servant.test.ts`) with three commits ahead / zero
behind against execute-plans `dev ee835e2e` is consistent with all prior followup
findings. PR `#63` legacy compatibility risk is also correctly carried forward as
unresolved with a separate failed gate. **Accepted.**

### Compatibility manifest remains fail-closed

The packet records the `agora_compat_manifest.py deployment-gate` command returning
the expected three blocking reasons: `compatibility_status must be compatible`,
`frontend.runtime_commit is a placeholder commit`, and
`blocking_reasons must be empty for deployment`. No deployment readiness claim is made.
**Accepted.**

### Trading-room, candidate-pool, research, and workshop remain outside Phase 1

Section 5 keeps all four surfaces in separate ledger rows with their respective
phase/scope boundaries. The operator journey in Section 8 ends at servant ensure and
does not extend into trading-room decision queues, candidate-pool widgets, research
plan/run routes, or workshop SSE streams. The absorption checklist in Section 9
includes explicit separation checks and preserves unsupported-route exclusions for
`GET /bff/agora/servant` and `POST /bff/agora/servant/reconcile`. **Accepted.**

## Additional Observations

1. The seven-file dev advancement (two AG-FE-RS-001 support packets plus five
   MGMT-LIVE-EVIDENCE / CI-helper files) is correctly characterised as non-impacting
   for AG-FE-ID-001 runtime readiness. The packet neither overclaims progress nor
   misidentifies the CI/scripting changes as Agora surface modifications.

2. The MGMT-LIVE-EVIDENCE changes (`.github/workflows/branch-ci.yml` and four new
   scripts) are correctly scoped as repository tooling/live-evidence surface updates
   with no Agora identity, servant, frontend handoff, or contract path involvement.
   This is a broader dev window than the previous three-sidecar-packet followup-40
   window, and the packet handles the additional CI/scripting changes accurately.

3. The 39 BFF identity/servant/session tests, 3 candidate-pool tests, and 24
   trading-room tests all passing provides appropriate backend regression evidence.
   The expected fail-closed manifest gate result is also correctly documented.

4. The packet correctly references the followup-40 closeout base (`ad09c3b2`) and
   prior packet/review/closeout commits and PRs without re-litigating already-settled
   scope.

5. `current-work.md` and the full `ai-activity-log.jsonl` were correctly not read,
   consistent with the task brief read discipline.

## Carry-Forward Rules For Parent

The parent (`AG-FE-ID-001`, owner Claude) should not absorb this sidecar into
completion until:

- execute-plans PR `#66` merges into execute-plans `dev`
- the compatibility manifest records a non-placeholder frontend runtime commit and
  `compatibility_status: compatible`
- the aggregate gate reruns cleanly or a formal exception is recorded by the gate
  owners (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2)

Gate ownership assignments remain active and must not be silently resolved inside
AG-FE-ID-001 closeout. The execute-plans `dev` base `ee835e2e` and the three-commits-
ahead PR `#66` head `d1ae3149` are the current deployment-readiness reference points.

## Decision

Approved. The packet is scope-disciplined, factually accurate for the followup-41
refresh window (two AG-FE-RS-001 support packet advances plus five non-Agora
MGMT-LIVE-EVIDENCE / CI-helper files, no identity/servant path changes), and provides
the correct handoff baseline for the parent task to resume once the execute-plans
aggregate gate clears.
