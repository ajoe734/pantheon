# LOOP-AUTO-EVO-002 Evidence

Task: Bridge postmortems into evolution proposals

## Delivered Scope

- Added a published-postmortem bridge builder in `services/evolution/postmortem_bridge.py`.
- Added `POST /api/evolution/proposals/from-postmortem-published`.
- The route admits exactly one proposed `EvolutionDecision` per
  `target_type + target_id + incident_cluster` bridge key.
- Duplicate publish events return the existing proposal with HTTP 200.
- Created decisions remain proposal-only and review-gated:
  `decision_state=proposed`, empty review chain, no execution result, and
  metadata flags that disallow runtime, broker, and capital-binding mutation.

## Acceptance Mapping

- Published postmortem creates one evolution proposal per target and incident
  cluster:
  `test_propose_from_postmortem_published_creates_review_gated_decision` and
  `test_propose_from_postmortem_published_is_once_per_target_and_cluster`.
- Duplicate publish events are idempotent:
  `test_propose_from_postmortem_published_duplicate_event_is_idempotent`.
- Proposal carries evidence links and review gate state:
  evidence ref, `review_gate_state`, `proposal_only`, and mutation-blocking
  metadata assertions in the focused route tests.

## Verification

```bash
python3 -m pytest services/evolution/test_postmortem_bridge.py services/evolution/test_evolution_service.py -q
```

Result on 2026-06-27: `86 passed in 14.16s`.
