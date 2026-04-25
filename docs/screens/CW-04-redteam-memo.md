# CW-04 Red-team Memo Screen Spec

## Status

**Route-live with published frontend handoff** — this screen spec is derived from
`docs/bff/CW-04-redteam-memo.md` and synchronized with
`docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md`. UI work
may proceed against the live memo routes.

Task: `CW-04-REDTEAM-MEMO-001`

## Pages

### Memo List (`/consultation/memos`)

**What the screen renders from BFF:**

- Paginated list of `ConsultMemo` rows from `GET /api/v1/consult/memos`
- Per row: `memo_id`, `memo_type`, `status`, `linked_request_id`,
  `recommendation_count`, `published_at`, `created_at`
- Filter bar: `status` filter (`draft` | `published`); pagination via `page_token`
- Row navigation must use BFF-provided `route_href`
- Freshness comes from `meta.staleness.status`
- Degradation: when `meta.surfaces.redteam_memo.state` is `degraded` or
  `unavailable`, show the canonical non-dismissable degradation banner
  (inherited from `PKT-005`); never show "no memos" as authoritative when the
  surface is degraded

**Non-goals:**

- Do not derive memo list from raw consultation session reads
- Do not show "no results" when `meta.surfaces.redteam_memo.state != "ok"`
- Do not assume list rows contain `author_ref`; that field is detail-only

### Memo Detail (`/consultation/memos/:memo_id`)

**What the screen renders from BFF:**

#### Header

- `memo_id`, `memo_type` badge, `status` badge
- `author_ref`, `linked_request_id`, `linked_session_id`
- When `status = "draft"`: draft watermark overlay
- When `status = "published"`: `published_at` timestamp

#### Summary

- `summary` text block from BFF

#### Recommendation List

- Ordered plain list from `recommendations[]`
- Per row: `index`, `body`
- No per-recommendation severity or workflow status — out of scope

#### Evidence Drawer

- Expandable memo-level drawer showing linked evidence entries from `evidence_refs[]`
- Per evidence row: `evidence_type` label, `description`, tappable `link` navigating to canonical evidence surface
- Client must use BFF-provided `link` — must not construct from `id` or `artifact_ref`

#### Session-to-Memo Mapping Panel

- Displays `mapping_id`, `source_session_id`, `transcript_id`,
  `transcript_version`, `mapping_status`, `created_at`, and `created_by`
- Client must not derive this from raw session data; reads from `session_to_memo_mapping` object

#### Governance Handoff CTA

- Visible and tappable only when `allowedActions.canInitiateGovernanceReview = true`
- Navigates operator to the Governance Workbench review queue with the memo pre-filtered
- Hidden (not disabled) when signal is absent or false
- Client must not show CTA based on `status = "published"` alone

#### Degradation

- When `meta.staleness.status = "stale"`: show a non-dismissable staleness banner
- When `meta.surfaces.redteam_memo.state = "degraded"`: show last-known memo
  state with a degraded banner; do not force the governance CTA on
- When `meta.surfaces.redteam_memo.state = "unavailable"`: show canonical
  unavailable banner with no memo content; hide all CTAs

## Shared Primitives

- Degradation banner: non-dismissable; inherited from `PKT-005` substrate
- Evidence links: always BFF-resolved; no client-side URL construction
- `allowedActions` authority: always from BFF field; never inferred from visible data
- `meta.staleness.status`: freshness signal only; never treat it as a
  `meta.surfaces.redteam_memo.state` substitute
