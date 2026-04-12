# F-042 Promotion Review

## User Goal

Let an operator review whether a candidate can move into paper deployment without reconstructing governance or runtime state in the browser.

## Page Sections

- Header with feature title, artifact identity, target stage, and current readiness badge
- Review summary with governance outcome, risk summary, and last run progress
- Allowed actions panel whose CTA visibility comes only from backend-shaped `allowedActions`
- Supporting evidence section with example payload and trace references
- Loading, empty, degraded, and error states with no hidden mock fallback

## Interaction Rules

- All production data comes from Pantheon BFF routes only
- CTA visibility and enabled/disabled state comes from backend-shaped fields
- If required data is missing, the front-end must emit a `bff-gap` handoff instead of inventing local state

## Acceptance

- Page renders with no mock data
- `Promote to paper` CTA visibility is backend-driven only
- Loading, empty, degraded, and error states are explicit and visually distinct
