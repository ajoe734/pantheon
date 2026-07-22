# AG-UIPOL-004: Reconcile readiness/completeness display; performance tab honesty

## Scope

Two state-consistency defects visible on hosted dev (2026-07-13):

1. Strategy Workshop: the completeness rail shows "Dimension coverage 0%"
   while the completeness card in the same view reports overall grade
   "complete / RESEARCH READY Yes"; the readiness list shows all three gates
   "Ready" but renders their labels greyed as if disabled. One state, one
   story: the rail, the card, and the readiness list must read from the same
   snapshot and agree.
2. Performance tab: an "Unassigned" strategy row (6,841 trades, PNL $0,
   monitoring "not linked") renders above the real strategy with no
   explanation, and the table mixes "$0" with "not reported" as if
   interchangeable. Zero-because-measured and absent-because-unreported must
   render differently, and the Unassigned bucket needs an explicit
   explanation row or a filter default.

## Work (execute-plans, plus BFF only if the payload is the contradiction)

1. Trace the workshop rail's coverage computation vs the completeness card
   source; fix the divergence (likely the rail reads the 7-dimension shape
   while the card reads the snapshot grade — align on the completeness
   snapshot; note AG-GAP-012's 12-block mapping when relevant).
2. Fix readiness label styling: Ready gates render as active, not disabled.
3. Performance table: distinct rendering for measured-zero vs not-reported;
   "Unassigned" row gets a tooltip/description of what it aggregates and
   moves below named strategies or behind a toggle.

## Acceptance

- Hosted screenshots: rail percentage, card grade, and readiness list agree
  for the same workshop; Ready gates look active.
- Performance tab distinguishes $0 from not-reported; Unassigned bucket is
  explained and not the first row.
- Component tests for the rail/card consistency and the table formatting.

## Delivery record (refreshed 2026-07-14)

Implementation landed in `ajoe734/execute-plans@dev` through PRs
[#290](https://github.com/ajoe734/execute-plans/pull/290),
[#295](https://github.com/ajoe734/execute-plans/pull/295),
[#325](https://github.com/ajoe734/execute-plans/pull/325), and
[#335](https://github.com/ajoe734/execute-plans/pull/335). The corrective
chain covers the original display defects, workshop-scoped state reset,
measured-versus-missing metrics, the real missing-`subject` payload, and
deployment-manifest BFF identity.

The accepted hosted capture serves frontend
`936f252e09fa3bb887c88e733e24b6941cac644e` and BFF
`8de9ed3b09ae1002edc74256f33b9bec1fe3b717`. The frontend manifest and live
`GET /bff/version` agree on the full BFF SHA. Frontend Branch CI
[29304416763](https://github.com/ajoe734/execute-plans/actions/runs/29304416763),
frontend deploy
[29304416758](https://github.com/ajoe734/execute-plans/actions/runs/29304416758),
Pantheon Branch CI
[29303213895](https://github.com/ajoe734/pantheon/actions/runs/29303213895),
and BFF deploy
[29303223159](https://github.com/ajoe734/pantheon/actions/runs/29303223159)
all passed. Focused Vitest at the accepted frontend passed 54/54.

Hosted proof against those exact identities records:

- card `complete / Research ready: Yes` aligned with rail
  `Complete / 100% / Research ready: Yes` for one exact workshop snapshot;
- three readiness gates rendered as active Ready states;
- named strategies sorted above `Unassigned`, whose attribution-only meaning
  is explained;
- measured `$0` remained distinct from `not reported` values;
- the Performance page left loading state and made the implemented
  `/bff/management/performance-attribution/by-strategy` request successfully;
- live/strict mode used safe write defaults and no embedded bearer token.

See
[AG-UIPOL-004 hosted evidence](./evidence/AG-UIPOL-004-hosted-evidence.md)
for screenshots, machine-readable readback, checksums, deployment identity,
validation commands, and explicit residual workflow failures. This delivery
record proves the objective defects only and makes no design-parity claim.
