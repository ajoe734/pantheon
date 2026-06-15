# E2E-R9 — Evolution-loop integrity (incident → evolution → artifact)

**Round:** E2E-R9 of the e2e business-flow verification campaign
**Date:** 2026-06-15
**Branch / PR:** task/e2e-r9-evolution
**Business flow:** telemetry/incident → evolution decision/program → new artifact
(the closing arc of the OODA loop).

## Verification program

`scripts/verify_e2e_evolution_loop.py` (+ unit test), wired into
`run-acceptance.sh` full mode as `e2e-evolution-loop-verifier`. FAILs on a
malformed open incident (missing `runtime_id` or untitled); reports the
open-incident backlog vs evolution-program count.

## Live result (dev, 2026-06-15)

```
evolution-loop integrity: incidents=3 open=3 evolution_programs=0
NOTE: 3 open incidents with 0 evolution programs — incident → evolution arc not closing
FAIL: 3 malformed open incidents (no runtime_id, title='Untitled Incident')
```

## Finding

The three incidents in the system are all `open`, **untitled, and carry no
`runtime_id`** — malformed records that no operator can action and that no
evolution step can attach to. And **0 evolution programs** exist, so the
incident → evolution → artifact arc is not closing: incidents accumulate with no
evolution response. The evolution surface itself is reachable
(`evolution_programs` source `service_client`, status ok) — it is simply empty.

This is the closing-arc counterpart to the V10 capstone (loops not demonstrably
live): not only do loops not run end-to-end, the incident/evolution feedback that
should drive adaptation is empty and the few incidents present are malformed.

## Disposition

- **Shipped (code/CI):** the evolution-loop integrity verifier + logic test + CI
  gate, so malformed open incidents are caught going forward (currently FAILs on
  the 3 untitled/runtime-less incidents).
- **Flagged (upstream build):** incidents must be created with an attributable
  `runtime_id` + a meaningful title, and the incident → evolution arc needs a
  producer so open incidents drive evolution decisions.

## Next round

E2E-R10: consolidation — master index of the E2E-R1..R10 verifiers + findings,
and confirmation that all rounds are merged.
