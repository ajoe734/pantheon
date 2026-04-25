# PKT-005 Global Degradation Banner — Contract Lock

Status: `delivered`
Locked at: 2026-04-16
Locked by: Codex2

## Review Anchor

- Pantheon payload: `.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml`
- Companion completion payload: `.coordination/requests/PKT-005-degradation-banner-ui-done.yaml`
- Reviewed front source commit: `7406990a8311ef6865491fcdb883b677a98ff6c9`
- Pantheon contract publication commit: `77443032a240a3df49c329100ef2477a72a70e53`

## Current Lock State

### Front publication state

- The reviewed front source commit contains the tracked PKT-005 UI files.
- The reviewed front source commit contains tracked canonical
  `PKT-005-degradation-banner-frontend-feedback` and
  `PKT-005-degradation-banner-ui-done` payloads.
- Pantheon mirrors the same review anchor payloads in this repo for replay and
  audit.

### Contract references reviewed

- `docs/bff/PKT-005-degradation-banner.md`
- `docs/screens/PKT-005-degradation-banner.md`
- `docs/examples/PKT-005-degradation-banner.json`
- `docs/bff/PKT-002-incident-detail.md`
- `docs/examples/PKT-002-incident-detail.json`

## Resolved Contract Deltas

### Delta 1: incident-response surface keys now match the reviewed UI lock

The published PKT-005 contract under
`pantheon-bff@77443032a240a3df49c329100ef2477a72a70e53` now names the
incident-response surfaces as:

- `incident`
- `affected_bindings`
- `kill_switch`
- `allowedActions`

These keys now match `PKT-002 Incident Detail` and the reviewed UI helper.

### Delta 2: STALE wording now matches the reviewed UI lock

The published screen spec and example payload now encode the reviewed helper
rule that STALE requires both:

- `meta.staleness.served_from in ["cache", "reconstructed"]`
- at least one degraded surface

The packet no longer implies that `served_from = "cache"` alone is sufficient
to show the STALE banner.

## Locked Outcome

No Pantheon endpoint expansion is authorized from this review, and no front-end
rework is requested. The normalized PKT-005 packet family is now published
under a real commit-backed `bff_contract_version`, and the current front source
commit remains the accepted UI review anchor for this closed loop.
