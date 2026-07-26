# PPL-ALLOC-009 hosted closeout truth record — 2026-07-25

Status: incomplete; this record must not be used as `done` evidence
Task ID: `PPL-ALLOC-009`
Truth correction owner: Codex
Final B5 reviewer: pending

## Correction notice

The first version of this file, merged in
[Pantheon PR #4124](https://github.com/ajoe734/pantheon/pull/4124),
incorrectly marked B3 and B5 as passed. PR #4124 has no GitHub review and the
declared reviewer trailer is not an independent B5 decision. Its B3 reference
was a general frontend integration gate, not the required same-chain hosted
desktop and 393px acceptance.

The only authorized PPL-ALLOC-009 hosted acceptance,
[run 30163260347](https://github.com/ajoe734/pantheon/actions/runs/30163260347),
concluded `failure`. This correction supersedes the earlier completion claim
without deleting the historical commit.

## Unique hosted acceptance result

The run used the accepted exact pair:

- frontend: `ef5185148157c422b41cc2a0ee497d2860e002a3`
- BFF: `be956c07aca889043ef301389412b6744452f20b`
- sanitized artifact:
  [8620845277](https://github.com/ajoe734/pantheon/actions/runs/30163260347/artifacts/8620845277)

The test reached the first desktop `runBrowserProof` call only after every
preceding B1 API call and assertion had passed. That includes distinct
operator/approver authentication, promotion submission and decision, paper
allocation evaluation, rebalance proposal/approval/apply, an `executed`
command receipt, and authoritative paper-capital readback with
`live_capital_side_effects=false`.

The then-current harness persisted its last checkpoint before that complete B1
tail, so artifact 8620845277 stops at `promotion_recommendation_ready`. The
failure location in the run log proves the later calls completed, but the
artifact does not contain their final linked identifiers. B1 therefore passed
at runtime with incomplete final packet capture.

B3 failed inside the first desktop browser proof before any mobile proof ran.
The page rendered the strict authentication boundary:
`Your Pantheon session is missing or expired`. The harness had put a
server-issued BFF dev-login JWT into Firebase browser storage. That token was
not a GCP Identity Platform ID token, so Firebase rejected and cleared the
session. No browser response interception or fallback data was used.

## Repair delivery

The harness and controller defects are repaired:

- [execute-plans PR #544](https://github.com/ajoe734/execute-plans/pull/544),
  merged to `dev` as
  `3bf97323f7c72bd47256c7a60618dd7f837cd592`, signs in through the hosted
  GCP Identity Platform UI with a dedicated dev account and writes the complete
  B1 checkpoint before B3 starts.
- [Pantheon PR #4127](https://github.com/ajoe734/pantheon/pull/4127), merged
  to `dev` as `72afd991b9133dc7e73c775978dc854e6d3877ce`, provides the masked
  dev-only browser identity inputs and advances the exact Agora pair.
- FE integration gate
  [30165226542](https://github.com/ajoe734/execute-plans/actions/runs/30165226542)
  attempt 2 passed.
- Read-only dev deployment
  [30165771519](https://github.com/ajoe734/execute-plans/actions/runs/30165771519)
  passed gate-before-switch and post-switch probes.

The hosted deployment now reports:

- frontend `3bf97323f7c72bd47256c7a60618dd7f837cd592`
- BFF `be956c07aca889043ef301389412b6744452f20b`
- Pantheon pair controller `72afd991b9133dc7e73c775978dc854e6d3877ce`
- `deploymentState=accepted`, `profile=read-only`
- `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`
- real writes, dev-stub writes, and embedded bearer token all `false`

The BFF remains strict and ready. Lifecycle projection is accepted-live with
zero backlog. Terminal proof-off run
[30163425775](https://github.com/ajoe734/pantheon/actions/runs/30163425775)
passed with artifact
[8621090468](https://github.com/ajoe734/pantheon/actions/runs/30163425775/artifacts/8621090468);
the effective write-proof flag is `false`.

## Corrected gate status

| Gate | Correct status | Evidence / remaining condition |
| --- | --- | --- |
| B1 | Passed at runtime; final artifact checkpoint incomplete | Run 30163260347 reached B3 only after the full governed paper-only chain and authoritative readback passed. PR #544 fixes checkpoint placement for any future authorized proof. |
| B2 | Passed | Strict auth, MFA claims, distinct dev clients, and a real GCP Identity browser account are provisioned. Raw secrets remain only in GCP Secret Manager and masked GitHub dev-environment secrets. |
| B3 | Failed; repair deployed but not re-accepted | The unique authorized run failed on the first desktop auth session and never ran 393px mobile. PRs #544/#4127 fix the cause; no second acceptance was dispatched. |
| B4 | Passed | The previously recorded dependency deliveries remain merged. |
| B5 | Pending | PR #4124 contains no independent reviewer decision, and B3 is not accepted. The IA supersession cannot be closed as passed yet. |

## Remaining closeout work

Do not mark `PPL-ALLOC-009` done from build, gate, or deployment evidence alone.
Completion now requires:

1. explicit authority for a new hosted acceptance after the consumed one-run
   allowance;
2. one successful same-chain desktop and 393px proof using the repaired real
   GCP Identity session;
3. an explicit B5 reviewer decision accepting or rejecting the canonical
   Rankings, Governance, and Performance-center IA.

Real/live capital execution and real frontend writes remain disabled. Any
future transition to those authorities requires a separate Human/Ops decision.
