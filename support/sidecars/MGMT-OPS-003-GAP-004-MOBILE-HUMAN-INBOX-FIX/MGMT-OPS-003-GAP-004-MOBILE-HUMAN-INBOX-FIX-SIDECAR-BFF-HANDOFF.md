# MGMT-OPS-003-GAP-004 Mobile Human Inbox BFF Handoff Packet

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX` |
| Sidecar task | `MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF` |
| Sidecar owner / reviewer | `Codex` / `Codex2` |
| Helper kind | `bff_handoff_packet` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet translates the failed hosted mobile Human Inbox check
into a bounded BFF/frontend handoff. It does not change Pantheon or
`execute-plans`, publish a new contract, repair the hosted deployment, or
approve the parent task. The parent owner decides what to absorb.

## 1. Evidence Boundary And Verdict

The GAP-004 rerun used hosted frontend SHA
`a74e58696c900112557b0c748c3f8c69629da106` in live/strict mode with real
writes disabled. Desktop passed; mobile reached Human Inbox and rendered
`strict: Failed to fetch · seed fallback blocked`. The recorded result is one
passed and one failed Playwright case.

The checked-in packet contains screenshots and successful authenticated API
captures for Portfolio Book, holdings, and attribution, but no Human Inbox
request/response capture, browser network log, console log, or trace. It proves
a required mobile request failed. It does **not** prove whether the cause was
the frontend request builder/lifecycle, authentication, CORS, transport, BFF
availability, or a particular upstream fanout. Any repair must first capture
the failing request URL, method, headers with secrets redacted, response or
network error, console error, timing, and BFF request/correlation id.

## 2. Current BFF Surface And Query Gap

The current read contract is:

```text
GET /bff/management/human-inbox
  ?source_type=&status=&priority=&page_token=&page_size=
GET /bff/management/human-inbox/{item_id}
```

Both routes require an authenticated read role. Contract tests cover
composition, source/status filtering, pagination, detail, missing-item 404,
and authentication. They do not reproduce a viewport-specific transport
failure; viewport is not part of this server contract.

The Portfolio workflow carries `target_id`, `target_type`, `persona_id`, and
`runtime_id` into Human Inbox. The list route has no server-side target,
persona, or runtime lookup. Consequently the frontend must fetch a page and
search locally, and cannot reliably distinguish these states:

- the requested target exists on a later page;
- the target is absent from the composed sources;
- the list request failed before any authoritative result arrived.

This is a workflow query gap, not proof that it caused the observed mobile
failure. The parent should first repair and instrument the actual failed
request. If reliable target resolution still requires a contract change, the
Pantheon owner should choose a bounded server-owned lookup (for example an
existing stable inbox id detail request, or reviewed target filters) and add
contract tests. The frontend must not invent an inbox id or infer a match from
labels, timestamps, or only the first page.

## 3. Operator Journey

1. The operator follows Portfolio Book context to Human Inbox with the four
   workflow identifiers preserved in the URL.
2. Human Inbox makes its required authenticated live request once the session
   is ready and keeps the same request semantics on desktop and mobile.
3. Loading, unavailable, healthy-empty, target-not-found, and target-found are
   distinct states. A transport/auth/server failure remains unavailable and
   never becomes an empty or unresolved-target result.
4. If the target is found, the UI focuses it while preserving the authoritative
   BFF item identity and source context.
5. If an authoritative successful query proves it absent, the UI shows an
   explicit unresolved-context state and retains the incoming identifiers for
   retry and reviewer inspection.
6. Decision actions remain governed by the existing command path; this read
   repair must not add direct capital or local-only approval behavior.

## 4. Frontend Handoff

- Reproduce with the exact hosted mobile viewport and capture network, console,
  trace, request/correlation id, and the final request URL before changing code.
- Compare desktop and mobile request count, URL, query encoding, authorization
  readiness, cancellation, and component mount/unmount timing.
- Keep `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and real writes
  disabled for repair verification. Do not use seed fallback to make the test
  pass.
- Use one shared adapter/request builder for desktop and mobile. Viewport may
  change layout, not endpoint, auth, filters, timeout, or error semantics.
- Do not convert fetch rejection, non-2xx, malformed payload, or unhealthy
  source metadata into `[]` or target-not-found.
- Preserve `target_id`, `target_type`, `persona_id`, and `runtime_id` across
  redirects, refresh, retry, responsive navigation, and back navigation.
- If using the paginated list, resolve only from authoritative returned pages;
  do not declare absence after page one when `page_info` indicates more data.
- Prefer the stable BFF inbox item id and detail route whenever the upstream
  workflow already has that identity. Do not construct an id from target text.

## 5. Parent Acceptance Handoff

The composing parent should provide all of the following:

- a regression test that reproduces the original mobile failure before the
  fix, or a documented evidence-backed root cause if deterministic reproduction
  is impossible;
- adapter/unit coverage showing desktop and mobile issue the same authenticated
  required request and preserve all workflow context;
- negative coverage for network rejection, 401/403, 5xx, malformed response,
  abort/unmount, healthy empty, target absent, target on a later page, and
  detail 404;
- proof that failures render strict unavailable state without seed data and
  that only a successful authoritative response can render healthy empty or
  unresolved target;
- a deployed `execute-plans` SHA plus a fresh authenticated two-viewport hosted
  run with zero failed required requests and zero relevant console errors;
- screenshots for the final desktop and mobile Human Inbox states, and a
  redacted request/response or trace artifact for the formerly failing mobile
  request;
- Pantheon BFF PR/check/deploy evidence only if source capture demonstrates a
  BFF defect or the parent deliberately adopts a reviewed query-contract task.

Passing a local mock, desktop-only run, seed fallback, or a screenshot without
network evidence is insufficient. A new BFF route is not required merely
because the frontend fetch failed.

## 6. Compose Boundary And Review Checklist

- Parent owner owns frontend repair, deployment, and hosted rerun.
- Pantheon BFF owner owns any separately reviewed query-contract change.
- Sidecar reviewer verifies route claims, evidence limits, strict fail-closed
  behavior, pagination/identity guidance, and canonical non-mutation.
- No L1 document, BFF/runtime implementation, registry, governance contract,
  or frontend source is changed by this packet.
- No statement in this packet claims the root cause is known or the parent is
  fixed, deployed, or accepted.

## 7. Focused Verification

```bash
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX/MGMT-OPS-003-GAP-004-MOBILE-HUMAN-INBOX-FIX-SIDECAR-BFF-HANDOFF.md
rg -n 'bff_management_human_inbox|source_type|page_token|page_size' services/control-plane/bff/main.py
python3 -m pytest -q services/control-plane/bff/tests/test_bff_b3_human_inbox.py
```

Sources inspected were the task brief, the GAP-003 and GAP-004 hosted evidence
READMEs, the Human Inbox handlers/tests, and comparable sidecar packets.
`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned.
