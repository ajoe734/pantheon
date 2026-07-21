# APP-003-PKT001-PKT003-FOLLOWUP-001-SIDECAR-ACCEPTANCE Review

Reviewer: Claude
Date: 2026-04-24
Status: approved (after reopen→fix cycle)

## Result

Reopened for a narrow factual correction. The packet's PKT-003 replay chain
and scope disposition are correct, but the PKT-001 republish commit hash is
stale and must be updated before the parent reviewer can rely on this packet.

## Scope Compliance

- Support artifact only; no L1 canonical truth mutated. Sidecar scope respected.
- Artifact set limited to `support/sidecars/APP-003-PKT001-PKT003-FOLLOWUP-001/`.

## Findings

### Finding R-1 — PKT-001 republish commit is stale (must fix)

The acceptance packet (Executive Summary §1, Evidence Snapshot, Reviewer
Checklist §1) states PKT-001's checked-in request pair is republished by
commit `675f1cc59be537455e776113be9ad8a45fa44208`. That hash matches the
CW-04-redteam-memo publish cycle, not the current PKT-001 cycle.

Current canonical truth for PKT-001 is
`.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`:

- line 8: `request_pair_commit: 139081f0e4d516494819003bd95968ecb9b86c99`
- line 11: `verified_remote_publish_commit: 139081f0e4d516494819003bd95968ecb9b86c99`
- line 17: `Front publish commit 139081f0e4d516494819003bd95968ecb9b86c99 on`
- loop_close_condition (line 166) pins origin/pkt-004-detail-fix to
  `139081f0e4d516494819003bd95968ecb9b86c99` republishing source_commit
  `c94f63082eae1667ed919353d62c85180d7bafba`.

The reviewed snapshot hash `c94f63082eae1667ed919353d62c85180d7bafba` is
still correct; only the republish commit needs to be updated from
`675f1cc59be537455e776113be9ad8a45fa44208` to
`139081f0e4d516494819003bd95968ecb9b86c99` in the three packet locations
that cite it.

### Finding V-1 — PKT-003 replay chain verified

`c9b03d7ba1439db4f956c56106925675a98f8512` → `1df4a64047055ca3ea802d61c1df78211884aee2`
is consistent with `.coordination/requests/PKT-003-post-incident-review-ui-done.yaml`
and `.coordination/responses/PKT-003-post-incident-review-frontend-feedback.yaml`.
No change required on PKT-003 lines.

### Finding V-2 — Scope disposition verified

"No new PKT-001 or PKT-003 BFF gap is justified" is consistent with
`api_gaps: []` in both frontend-feedback response files and no open
`*-bff-gap.yaml` request for this follow-up cycle.

## Required Change

Update the three PKT-001 republish references in
`support/sidecars/APP-003-PKT001-PKT003-FOLLOWUP-001/APP-003-PKT001-PKT003-FOLLOWUP-001-SIDECAR-ACCEPTANCE.md`
from `675f1cc59be537455e776113be9ad8a45fa44208` (and short form `675f1cc`)
to `139081f0e4d516494819003bd95968ecb9b86c99`:

1. Executive Summary §1
2. Evidence Snapshot (first bullet)
3. Acceptance Read table (PKT-001 `source_commit` note)
4. Reviewer Checklist §1

No other packet content needs to change.

## Next Action

Return to owner (Codex) for the narrow hash correction, then re-hand off.

## Re-review (2026-04-24, post-fix)

Finding R-1 is resolved. All four call sites in
`support/sidecars/APP-003-PKT001-PKT003-FOLLOWUP-001/APP-003-PKT001-PKT003-FOLLOWUP-001-SIDECAR-ACCEPTANCE.md`
now cite republish commit `139081f0e4d516494819003bd95968ecb9b86c99`:

- Executive Summary §1 (line 28)
- Acceptance Read table, PKT-001 `source_commit` row (line 64)
- Evidence Snapshot, first bullet (line 73)
- Reviewer Checklist §1 (line 89)

Repo-wide `rg 675f1cc` against the sidecar directory returns no matches. The
reviewed snapshot hash `c94f63082eae1667ed919353d62c85180d7bafba` is unchanged
and still matches `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml:7,11`.
PKT-003 chain `c9b03d7b...` → `1df4a64` is unchanged and still aligned with
the PKT-003 frontend-feedback response. Findings V-1 and V-2 still hold.

Approving. Task moves to `review_approved` and returns to Codex for
finalization. This approval is scoped to the sidecar support artifact only;
the parent `APP-003-PKT001-PKT003-FOLLOWUP-001` lifecycle remains with its own
owner/reviewer.
