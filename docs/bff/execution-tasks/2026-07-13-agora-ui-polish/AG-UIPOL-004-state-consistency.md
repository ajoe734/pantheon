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
