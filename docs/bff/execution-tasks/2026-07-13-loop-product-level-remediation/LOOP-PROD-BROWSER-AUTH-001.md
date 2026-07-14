# LOOP-PROD-BROWSER-AUTH-001 — Coordinated credential-free browser auth cutover

Status: starts only after auth bootstrap, strict BFF auth, credential-free FE, delivery provenance, lease, and auth operations are done

Canonical catalog: `tasks.json`

Canonical contract SHA-256: `d80517344e65e03b0699b56b95a74eec00dc797a2ab62d528b3d69d6173c0688`
The complete catalog task contract is machine-authoritative;
the prose sections below are explanatory renderings.

The checked-in route fixture is a planner seed with
`coverage_status=planner_seed_incomplete_blocked`. The admitted fleet must
generate and merge the complete explicit route/callsite matrix (27 historical
GETs, 16 privileged negatives, all 13 attack classes, cookie/logout/refresh/SSE
and CORS rows, plus redacted evidence references) before this task can be done;
the seed is not hosted browser completion proof.

Source addendum:
`docs/04/pantheon_loop_product_level_remediation_2026-07-13/archive/REMEDIATION_GAP_ADDENDUM_2026-07-13.md`

## Assignment

| Field | Value |
| --- | --- |
| Owner | Codex2 |
| Reviewer | Codex |
| Wave | 1 |
| Fleet lane | `coordinated-browser-auth-cutover` |
| Repository | `pantheon` |
| Merge target | `dev` |
| Current maturity | FE/BFF can activate independently; 3557/323 paired a strict-only public subject with a permissive hosted BFF and failed before route RBAC |
| Target maturity | product-level |
| Human/Ops security sign-off | required |

## Product outcome

BFF strict auth、execute-plans credential-free build、完整 browser route matrix 與 hosted
驗收必須綁定同一個 protected cutover lease。瀏覽器沒有 reusable bearer 或 secret；
viewer 只能讀、privileged writes 使用另一組短效 identities。任一側不合格就不切換，
切換後任一 probe 失敗就把 FE 與 BFF 一起回滾並重驗。

## Dependencies

- `LOOP-PROD-AUTH-BOOT-001`
- `LOOP-PROD-AUTH-001`
- `LOOP-PROD-FE-001`
- `LOOP-PROD-DELIVERY-001`
- `LOOP-PROD-LEASE-001`
- `LOOP-PROD-AUTH-OPS-001`

Only `done` satisfies a dependency. `AUTH-001` or `FE-001` alone is never
activation authority for this cutover.

## Loop scope

- `bff_health_monitoring`
- `per_persona_ooda`

## Declared artifacts

- `.github/workflows/public-browser-auth-cutover.yml`
- `scripts/qualify_public_browser_auth_cutover.py`
- `scripts/test_qualify_public_browser_auth_cutover.py`
- `docs/deployment/public-browser-auth-cutover.md`
- `docs/deployment/evidence/loop-product-level/LOOP-PROD-BROWSER-AUTH-001`

## Acceptance

- bundle, source maps, storage, DOM, URL, body, screenshots, logs, traces, videos, and third-party requests contain no reusable bearer, signing key, client secret, fixed browser credential, or privileged capability
- read-only console access uses an authorized short-lived server-mediated Secure HttpOnly SameSite session; unauthenticated public viewer mode is separately reviewed, capability-free, disabled by default, and cannot mint, refresh, upgrade, or write
- the catalog binds `fixtures/browser-auth-incidents.v1.json` at
  `4b9077480aad612145e78e691404f93a2f6c4ac983c952af75bf9606292b1624`
  and `fixtures/browser-auth-route-matrix.v1.json` at
  `8e465fc657e09e8be982181de5fd5929d2719392fcac472245df2c30563d3531`
- the generated exact route union covers every browser callsite, all 27
  historical GET probes, session routes, and enumerated privileged negatives;
  `/bff/management/shell-summary` is canonical and the nonexistent
  `/bff/dashboard/summary` is not accepted
- auth decision, router status, and product success are distinct: `HEAD
  /bff/me` 405 and anonymous SSE 200 are not boot-green
- signed viewer-cookie logout clears only that session; signed refresh-cookie
  rotation requires exact Origin+CSRF and cannot change subject/role/tenant/
  capabilities; raw literal viewer values, fixed bearer, mixed transport,
  wrong origin, missing CSRF, or upgrade attempts deny with zero state delta
- authenticated cookie SSE proves session-kind, replay support, Last-Event-ID
  reconnect, and no duplicates; anonymous liveness, fixed/query bearer,
  expired cookie, and wrong origin cannot satisfy it
- viewer cannot freeze, rollback, approve, execute tools, activate kernel control, prepare a repair worktree, or invoke any write; operator, approver, risk owner, operator A, and operator B are distinct short-lived subjects
- exact origins, CORS, redirect, callback, cookie scope, issuer, audience, tenant, and environment pass a secret-free prerequisite before protected identities are requested
- PR, fork, unprotected ref, staging, preview, and candidate asset jobs receive no live token, session, signing, database, cloud, or kernel material
- BFF and FE changes may merge dormant; only this task's protected lease can activate the pair
- candidate probes pass for exact FE/BFF identities before switch; every post-switch failure restores and re-probes both prior identities
- hosted desktop/mobile boot, navigation, reload, logout, expiry, SSE reconnect, degraded mode, and recovery produce no unexpected 401/403, console, CORS, chunk, cookie, mixed-content, or BFF error
- safe-write flags remain false; write qualification uses separate identities and Candidate Pool mutations use the captured exact quoted ETag, including weak ETags, plus unique idempotency/request IDs while wildcard and unquoted validators fail
- secret-bearing Playwright disables trace, video, and automatic screenshots; archived output is allowlisted, redacted, and leakage-scanned
- incident replay distinguishes exact 401
  `AUTH_PUBLIC_BROWSER_TOKEN_NEAR_MATCH` from exact 403
  `AUTH_PUBLIC_BROWSER_ENVIRONMENT_FORBIDDEN`; the latter was caused by a
  permissive hosted auth mode and does not prove absent viewer RBAC grants
- 3557 BFF-first and 323 FE-first abort before switch, 3587 is the single
  effective rollback, and 3588 is a zero-tree duplicate rejected with no
  second deploy
- evidence records actual deployed and candidate hashes, flags, route results, policy, lease, prior/candidate pairs, rollback, formal review, and residual risk

## Required proof

- exact current/candidate FE and BFF commit, asset, image, policy, environment, origin, and safe-write manifests
- both exact fixture digests and a complete generated
  route/method/callsite/identity/transport/origin/cookie/auth-decision/
  router-status/product-success/reason/state-delta matrix
- secret-free prerequisite and PR/fork/ref/staging/preview/bundle/source-map/storage/DOM/URL/body/log/browser no-leak evidence
- one-lease BFF-first, FE-first, stale, partial, duplicate, failure, and paired rollback incident replay
- hosted desktop/mobile boot, reload, logout, expiry, reconnect, degraded, recovery, accessibility, performance, and zero-unexpected-error evidence
- separate write-identity, quoted ETag, idempotency, request-id, two-person, and kernel negative evidence
- formal distinct-runtime exact-head review, protected attestation, Human/Ops security verdict, and residual-risk owner/expiry

Reviewer approval must set `review_file` under:

`docs/deployment/evidence/loop-product-level/LOOP-PROD-BROWSER-AUTH-001/`

## Non-goals

- No panel-only closure
- No seed fixture as live proof
- No approval gate bypass
- No synthetic receipt as terminal execution proof
- No live-capital or live-broker side effect

## Dispatch and closeout rules

- start only after every dependency is done; superseded does not satisfy a dependency
- planner authors/dispatches the contract only; admitted fleet workers implement and activate it
- `AUTH-001`, `FE-001`, PR 3557, PR 3572, execute-plans PR 311, or execute-plans PR 323 alone is input, never coordinated activation authority
- re-read repositories, manifests, fixtures, and deployments at start; audit
  each PR by exact commit graph, tree, blob, timestamp, deployment pair, and
  observed reason code
- never send a fixed bearer or privileged credential to browser code and never weaken authorization to create a green screen
- activate and roll back only under one protected lease after candidate probes; archive scanned redacted evidence only
- fixture or route-universe drift requires an explicit schema/digest update and
  fresh exact-head review
