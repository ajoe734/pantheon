# EW-05 Mutation Review — UI Decisions

- The page stays mounted at `/evolution/mutation-review/:decision_id` and
  treats `decision_id` as required route context; no empty review shell is
  rendered without it.
- Runtime contract validation now checks both top-level and nested required
  fields so the screen fails closed before rendering partial mutation evidence.
- Approve and Reject remain hidden unless the backend explicitly returns the
  corresponding `allowedActions` boolean and the mutation-review surface is not
  `unavailable`.
- The screen keeps a confirmation dialog with optional note input for both
  commands, but the page always re-fetches the read route after command
  acceptance instead of updating `decision_state` locally.
- `stale` keeps content visible under a warning banner; `unavailable`
  suppresses the evidence panels and both CTAs entirely.
- A transport-level `503 evidence_unavailable` response is treated as the same
  unavailable-state placeholder, so operators do not see a generic load error
  for the published degradation branch.
- Linked postmortems route through
  `/operator/post-incident-review?postmortem=...` so the destination screen
  consumes the real identifier contract rather than a fabricated path segment,
  and the destination keeps that query until it resolves the matching resolved
  incident.
