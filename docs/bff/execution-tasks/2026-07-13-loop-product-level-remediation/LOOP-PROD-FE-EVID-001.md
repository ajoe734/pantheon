# LOOP-PROD-FE-EVID-001 — Fail-closed protected-attestation consumer

Status: ready for fleet dispatch after dependencies are done

Canonical catalog: `tasks.json`

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 3 |
| Fleet lane | `fe-protected-evidence-consumer` |
| Repository | `execute-plans` |
| Merge target | `dev` |
| Current maturity | dormant candidate-authored evidence assertions |
| Target maturity | product-level |

## Product outcome

execute-plans release gate 只接受 Pantheon protected controller attestation；
候選 repository 的 booleans、expected zero counts、fixture 或 self-signed manifest
都不能解鎖部署或 product maturity。

## Dependencies

- `LOOP-PROD-FE-001`
- `LOOP-PROD-ATTEST-001`
- `LOOP-PROD-AGORA-003`
- `LOOP-PROD-TJ-002`
- `LOOP-PROD-MAI-002`

## Loop scope

- `bff_health_monitoring`

## Declared artifacts

- `execute-plans/scripts/release-gate-contract/evidence-contract.mjs`
- `execute-plans/scripts/release-gate-contract/schema.mjs`
- `execute-plans/src/test/release-gate-evidence-contract.test.ts`
- `execute-plans/docs/04/loop-product-level/LOOP-PROD-FE-EVID-001`

## Acceptance

- release gate consumes the canonical protected-attestation schema and verifier result without a candidate override path
- exact FE SHA, BFF SHA, run/job/attempt, target, lease, artifact digest, policy, signer, issued time, and expiry are mandatory
- tamper, replay, omission, wrong SHA/BFF/run/job/lease/target, stale, unknown signer/policy, duplicate assertion, and unexpected extra assertion fail before deploy
- candidate-authored pass booleans, zero counters, fixtures, snapshots, and unsigned manifests are explicitly rejected
- error output exposes stable non-secret reason IDs and never prints credentials, signing material, or raw sensitive evidence
- clean checkout CI and hosted candidate gate consume the same verifier and immutable artifact
- positive protected attestation and complete negative matrix pass on exact release SHA
- independent reviewer confirms no dormant/alternate flag can bypass verification

## Required proof

- clean-environment unit and release-gate tests
- protected positive artifact plus adversarial mutation corpus
- exact candidate and hosted deployment identity
- merged PR, merge SHA, checks, reviewer verdict, and checksummed evidence

Reviewer approval must set `review_file` under:

`execute-plans/docs/04/loop-product-level/LOOP-PROD-FE-EVID-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- replace rather than enable the self-authored assertion model from dormant PR `#310`
- keep all artifacts routed to the execute-plans repository
- missing protected evidence is a block, never a skip
- reviewer tests alternate flags and direct script invocation on the exact head
