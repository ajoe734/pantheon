# Mobile Human Inbox BFF Handoff Follow-up 3

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX` |
| Sidecar task | `MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Owner / reviewer | `Codex` / `Codex2` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only BFF/frontend handoff increment |
| Mutates canonical truth | `false` |

## Purpose And Current Verdict

The original packet and follow-up 2 already define the accepted fail-closed
boundary. This increment gives the parent owner concrete frontend observation
points and an evidence return shape. It does not diagnose the hosted failure,
change a BFF contract, modify `execute-plans`, or approve the parent.

The only checked-in failure evidence remains frontend SHA
`a74e58696c900112557b0c748c3f8c69629da106`: desktop passed while mobile
rendered `strict: Failed to fetch · seed fallback blocked`. There is still no
captured Human Inbox request/response, network log, console log, or trace in
that evidence directory. Therefore the repair lane must preserve
`REQUEST_CHANGES` until the failed boundary is observed and a new deployment
passes both viewports.

## Concrete Frontend Observation Points

The parent owner should inspect these `execute-plans` locations in a clean task
worktree based on current `main`; the shared checkout was inspected read-only
and was dirty with unrelated task work, so it must not be reused for repair.

| Concern | Current location | Observation or regression to add |
|---|---|---|
| Route construction | `src/lib/bff-v1/paths.ts` (`mgmtHumanInbox`, `mgmtHumanInboxItem`) | Assert desktop and mobile resolve the same BFF base, list path, and encoded detail path. |
| Live request and adaptation | `src/lib/bff-v1/management.ts` (`humanInbox.list`, `humanInbox.get`, `adaptHumanInboxList`) | Capture the actual request outcome before adaptation. Test rejection, non-2xx, malformed envelope, and valid empty separately; none may be silently treated as authoritative absence. |
| Page request lifecycle | `src/management/pages/oversight/_core.tsx` (`HumanInboxPage`, `useV5Live`) | Instrument mount, session-ready state, request start, abort/unmount, completion, and retry at desktop and 390x844. Responsive layout must not change request semantics. |
| Incoming workflow context | Human Inbox URL parsing in `_core.tsx` and links in `personaFleetLinks.ts` / related workflow pages | Preserve the parent workflow's `target_id`, `target_type`, `persona_id`, and `runtime_id`; document any compatibility alias such as `persona` rather than silently dropping or rewriting it. |
| Strict-live status | `src/lib/bff/liveTransport.ts` and `src/components/layout/LiveStatusBanner.tsx` | Keep fetch/auth/server failures typed as unavailable with fallback blocked. Do not add a mobile-only seed or empty-array recovery. |

These are inspection targets, not a root-cause finding. In particular,
`HumanInboxPage` currently calls `mgmt.humanInbox.list()` without a viewport
argument, while viewport is not part of the Pantheon BFF contract. A difference
between desktop and mobile must therefore be demonstrated at request creation,
auth readiness, browser transport/CORS, component lifecycle, or response
handling before assigning ownership to the BFF.

## BFF Boundary And Query Decision

The reviewed server surface remains:

```text
GET /bff/management/human-inbox
  ?source_type=&status=&priority=&page_token=&page_size=
GET /bff/management/human-inbox/{item_id}
```

The list route has no `target_id`, `target_type`, `persona_id`, `runtime_id`, or
viewport query. Do not send unsupported filters and then interpret a successful
unfiltered first page as target-not-found. If the workflow already carries a
stable Human Inbox item id, use the detail route. If a successful capture proves
that stable identity or pagination cannot serve the journey, return a separate
BFF proposal with an owner, exact query semantics, pagination interaction,
authorization behavior, compatibility plan, and contract tests. This sidecar
does not authorize that change.

## Evidence Return Contract

The frontend repair handoff back to the parent should include one compact table
with a desktop row and a mobile row containing:

- viewport, deployed frontend SHA, BFF origin, and live/strict/write posture;
- final frontend route including preserved workflow query keys;
- Human Inbox request count, method, fully resolved redacted URL, start/end or
  abort timing, HTTP/network outcome, and request/correlation id;
- adapter outcome classification: unavailable, healthy-empty,
  authoritative-target-absent, or target-found;
- relevant console errors, failed required requests, and fallback-data count;
- screenshot and trace/network artifact paths.

The two rows must show the same authenticated required-request semantics and
zero failed required requests. A screenshot alone, a local mock, a desktop-only
pass, or a successful response later converted to local seed data is not enough.

## Ownership And Reviewer Gate

- Parent/frontend owner: reproduce, repair `execute-plans`, deploy, and return
  the two-row evidence table.
- Pantheon BFF owner: act only if the captured request proves a server defect or
  the parent opens a separately reviewed query-contract task.
- Codex2: verify that this packet preserves evidence uncertainty, strict
  unavailable semantics, stable identity/pagination guidance, and canonical
  non-mutation.
- Parent owner: decide whether and how to absorb this support material.

No L1 truth, BFF/runtime implementation, registry, governance, capital-action
path, or frontend source is changed here.

## Focused Verification

```bash
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
python3 -m pytest -q services/control-plane/bff/tests/test_bff_b3_human_inbox.py
```

Inputs inspected: the task brief, both earlier sidecar packets, the GAP-004
hosted evidence README, current Pantheon Human Inbox routes/tests, and the
read-only `execute-plans` observation points above. `current-work.md` and the
full `ai-activity-log.jsonl` were intentionally not scanned.
