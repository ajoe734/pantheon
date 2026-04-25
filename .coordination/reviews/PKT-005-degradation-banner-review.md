# PKT-005 Global Degradation Banner — Pantheon Review

**Reviewer:** Codex  
**Reviewed source commit:** `7406990a8311ef6865491fcdb883b677a98ff6c9` (front-ai-trading-system)  
**Verified front workspace head:** `1f179b9fd9206b97e5723649295f230f119f88f6`  
**Review date:** 2026-04-24

## Disposition: FOLLOW-UP REQUIRED

The PKT-005 request pair and feedback bundle are now present on the Git-visible
front default branch, and the shared degradation decision tree remains aligned
to the canonical contract. The loop still cannot close because the current
incident navigation leaves the mounted Operator Console route family, and the
banner label helper still does not fully humanize canonical camelCase surface
keys.

## Findings

### 1. Blocking: incident navigation still points at unmapped non-operator paths

- `IncidentHome.tsx` row clicks still call
  `navigate(\`/incidents/${incident.incident_id}\`)`
- `IncidentDetail.tsx` still sends the back action to `navigate('/incidents')`
- The mounted owner routes remain `/operator/incidents`,
  `/operator/incidents/:incidentId`, and `/operator/incidents/:incidentId/action`

### 2. Front follow-up: banner labels still do not fully humanize camelCase surface keys

- `humanizeSurfaceKey()` still splits on underscores only
- Canonical PKT-005 surface keys include `allowedActions`, so the current
  helper can still render `AllowedActions` instead of `Allowed Actions`

## What Passes

- The shared decision tree still matches the canonical PKT-005 screen spec,
  BFF contract, and example payloads
- The request pair and feedback bundle are now Git-visible on the default
  branch
- No Pantheon API gap or new endpoint work is required for this review

## Reviewed Artifacts

- `/tmp/front-prompt-publish-main/.coordination/requests/PKT-005-degradation-banner-ui-done.yaml`
- `/tmp/front-prompt-publish-main/.coordination/requests/PKT-005-degradation-banner-frontend-feedback.yaml`
- `/tmp/front-prompt-publish-main/docs/pantheon-feedback/PKT-005-degradation-banner/`
- `/tmp/front-prompt-publish-main/src/pages/operator/IncidentHome.tsx`
- `/tmp/front-prompt-publish-main/src/pages/operator/IncidentDetail.tsx`
- `/tmp/front-prompt-publish-main/src/lib/degradationBanner.ts`
- `docs/bff/PKT-005-degradation-banner.md`
- `docs/examples/PKT-005-degradation-banner.json`
- `docs/screens/PKT-005-degradation-banner.md`

## Decision

Do not mark `PKT-005-degradation-banner` loop-complete yet.

The next front-owned refresh should:

1. keep incident navigation on `/operator/incidents...`
2. humanize canonical camelCase surface keys
3. republish the unchanged request pair on the current contract

## Residual Risk

- This review did not re-run a live browser session against a deployed Pantheon
  BFF.
- Remaining blockers are front-owned route fidelity and label rendering only.
