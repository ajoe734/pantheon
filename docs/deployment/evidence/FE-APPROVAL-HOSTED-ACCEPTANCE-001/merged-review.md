# Task Brief: FE-APPROVAL-HOSTED-ACCEPTANCE-001

- Status: review_approved
- Owner: Human/Ops
- Reviewer: Codex2
- Repository: ajoe734/pantheon
- Delivery commit: 1f61ecc29710f8f9e373ef11b96365ddbef08068
- Evidence: docs/deployment/evidence/FE-APPROVAL-HOSTED-ACCEPTANCE-001/evidence.json

## Review

Codex2 reviewed the hosted evidence independently, running as codex-cli 0.153.0 in read-only mode. The coordinator started each run because local Human/Ops cannot hand off.

| Round | Verdict | Outcome |
|---|---|---|
| 1 | REJECT | 7 findings, all addressed |
| 2 | REJECT | 3 findings, all addressed |
| 3 | ACCEPT | No blocking findings |

## What the review accepted

The hosted run exercised the Governance approval owner through the exact released pair: BFF 9304d19c0 and FE 86a4314b, from deploy run 37105169338. The fixture was a labelled paper fixture. The review accepted these results:

- A stale version returned 409.
- The first vote stayed pending (`under_review`).
- Identical retries were normalized-JSON-equal.
- Reusing a key with changed content returned 409.
- A distinct final decider moved the approval to `decided`/`approved`.
- Readback through two routes agreed.
- A vote after finality was rejected.

## Remaining limits

The approval pages were not driven in a browser. Frontend rendering of pending versus final, the 409 refresh and failed-readback handling remain unverified by this hosted run.

An unknown approval id returns 409 stale rather than the 404 documented in `contract.md`. This is recorded only.
