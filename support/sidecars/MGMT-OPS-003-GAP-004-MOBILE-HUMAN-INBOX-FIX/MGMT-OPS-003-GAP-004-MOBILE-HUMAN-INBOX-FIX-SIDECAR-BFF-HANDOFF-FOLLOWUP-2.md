# Mobile Human Inbox BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX` |
| Sidecar task | `MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Owner / reviewer | `Codex` / `Codex2` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only handoff increment |
| Mutates canonical truth | `false` |

## Handoff Baseline

The original support packet is already merged through Pantheon PR `#3330` at
`support/sidecars/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF.md`.
Codex2 independently approved that packet in commit `596068b49`: the current
route claims, evidence limits, strict fail-closed guidance, pagination and
stable-identity guidance, and canonical non-mutation were accepted.

This follow-up does not replace that packet and does not diagnose the hosted
mobile failure. It gives the parent owner a short absorption sequence so the
approved handoff can compose with the frontend repair without turning an
unobserved transport failure into a speculative BFF change.

## Parent Absorption Sequence

1. In `execute-plans`, reproduce the failure at the same hosted mobile
   viewport and deployed frontend SHA. Capture the Human Inbox request URL,
   method, redacted auth state, response or browser network error, console
   output, timing, and request/correlation id.
2. Compare the capture with the passing desktop request. The desktop and mobile
   paths must use one authenticated adapter/request builder; viewport changes
   may affect layout but not endpoint, query, auth readiness, timeout, or error
   interpretation.
3. Route the repair by observed boundary:
   - If mobile does not issue the same request, issues it before session
     readiness, aborts it because of responsive lifecycle, or suppresses the
     response, repair `execute-plans` only.
   - If both clients issue the same valid request but the BFF or an upstream
     returns a reproducible contract/server failure, open a separately scoped
     Pantheon task with the redacted capture and focused BFF regression.
   - If target resolution is the remaining problem after a successful list
     response, prefer the existing stable inbox-item detail route. Propose new
     target/persona/runtime filters only as a separately reviewed BFF contract
     task; this sidecar does not authorize them.
4. Keep live/strict mode and seed fallback disabled. A fetch rejection, auth
   failure, non-2xx response, malformed envelope, or unhealthy source remains
   unavailable; it cannot become healthy-empty or target-not-found.
5. Deploy the repaired `execute-plans` SHA and rerun authenticated desktop and
   mobile checks. Record zero failed required requests, zero relevant console
   errors, screenshots, and a redacted capture for the formerly failing mobile
   request before asking for parent review.

## BFF Facts To Preserve

The reviewed BFF read surface remains:

```text
GET /bff/management/human-inbox
  ?source_type=&status=&priority=&page_token=&page_size=
GET /bff/management/human-inbox/{item_id}
```

Viewport is not a BFF contract input. The list route does not currently accept
`target_id`, `target_type`, `persona_id`, or `runtime_id`. The frontend must not
invent a Human Inbox id, infer identity from labels or timestamps, or declare a
target absent after the first page when `page_info` reports more results.

## Reviewer Gate

Codex2 should verify that the parent handoff:

- cites an observed request boundary rather than assuming the BFF caused the
  mobile failure;
- preserves strict unavailable versus healthy-empty/target-not-found states;
- does not silently introduce a BFF query contract or frontend seed fallback;
- leaves canonical truth, runtime, registry, governance, and capital-action
  behavior unchanged; and
- returns the repair decision and hosted evidence to the parent owner, who
  alone decides what to absorb.

## Focused Verification

```bash
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
python3 -m pytest -q services/control-plane/bff/tests/test_bff_b3_human_inbox.py
```

The task-scoped brief, original packet, independent review, current BFF
handlers/tests, and merged history were inspected. `current-work.md` and the
full `ai-activity-log.jsonl` were intentionally not scanned.
