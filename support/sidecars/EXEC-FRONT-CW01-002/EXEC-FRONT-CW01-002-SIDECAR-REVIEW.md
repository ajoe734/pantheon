# EXEC-FRONT-CW01-002 Sidecar Review Packet

Date: `2026-04-21`
Sidecar task: `EXEC-FRONT-CW01-002-SIDECAR-REVIEW`
Parent task: `EXEC-FRONT-CW01-002`
Sidecar owner / reviewer: `Codex2` / `Codex`
Parent owner / reviewer: `Claude` / `Codex`
Helper kind: `review_packet`
Scope: support-only review packet and reviewer handoff; no canonical truth, core contract docs, runtime implementation, or main frontend files are modified here

## Parent Status Snapshot

- `ai-status.json` currently records the parent as `review`.
- The current parent review artifact is `.coordination/reviews/CW-01-consult-request-review.md`.
- The latest durable returned support artifact is `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml`.
- This sidecar was reopened because the latest packet chain overstates what `d9d64fe` proves:
  - `d9d64fe` does contain the CW-01 request pair, feedback bundle, and consult-request page files
  - but `d9d64fe:src/App.tsx` does **not** register `/consultation/requests` or `/consultation/requests/:request_id`
  - those routes appear later in `f672af2` and remain present at `f00791b`
- The review question is therefore no longer "did `a93cd85` fix the original wrong SHA?" alone. The real question is whether the request pair now points at one truthful immutable commit whose tree matches the handoff claims. Current evidence says not yet.

## Sidecar Conclusion

This sidecar should **not** be used to argue that the parent is already closeout-ready.

The corrected reading is:

1. The six CW-01 contract fixes remain verified in the front tree.
2. `a93cd85` did correct the handoff pair away from the older bad `d51274b` reference.
3. But the new advertised `source_commit` value, `d9d64fe`, still does not satisfy the request files' own route-wiring claim because `src/App.tsx` at that commit lacks both CW-01 routes.
4. `f672af2` is the first verified commit in the inspected chain that contains:
   - the request pair
   - the feedback bundle
   - the consult-request page files
   - `src/App.tsx` route wiring for `/consultation/requests*`
5. `f00791b` is a later verified descendant that still contains the same route wiring and packet files, but the request pair at `a93cd85`, `f672af2`, and `f00791b` still advertises `source_commit: d9d64fe...`, so the handoff is still not replay-clean against its own stated evidence.

Reviewer takeaway:

- preserve the blocker
- do not move the parent to `review_approved` on the current packet chain alone
- require one later truthful repoint commit, likely targeting `f672af2` or another later commit whose tree actually contains the full handoff set and route wiring together

## Corrected Review Arc

1. `.coordination/reviews/CW-01-consult-request-review.md` on `2026-04-20` found one blocker:
   the request pair still pointed `source_commit` at `d51274b...`, which did not contain the full published handoff set.
2. The front repo then landed `a93cd85`, which repointed both request files to `d9d64fe`.
3. `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml` interpreted that repoint as enough to mark the loop replay-clean and closeable.
4. A direct re-check against the sibling front repo shows the remaining mismatch:
   - `d9d64fe` contains the request pair, feedback bundle, `src/pages/consultation/ConsultRequestList.tsx`, `src/pages/consultation/ConsultRequestDetail.tsx`, and `src/App.tsx`
   - but `d9d64fe:src/App.tsx` still lacks `/consultation/requests` and `/consultation/requests/:request_id`
   - `f672af2:src/App.tsx` is the first later verified tree in the inspected chain that does register both routes and still contains the request pair plus feedback bundle
   - `f00791b:src/App.tsx` is a later descendant that still preserves those routes
5. Because the request pair still says `source_commit: d9d64fe...`, the machine-readable handoff currently points at a commit that does not support one of its explicit claims.

## Evidence Chain

### 1. What `d9d64fe` actually proves

`git -C ../front-ai-trading-system ls-tree -r --name-only d9d64fe9494b34265e9aaff9e97d65238ab4688a -- src/App.tsx src/pages/consultation .coordination/requests/CW-01-consult-request-ui-done.yaml .coordination/requests/CW-01-consult-request-frontend-feedback.yaml docs/pantheon-feedback/CW-01-consult-request`

confirms that `d9d64fe` contains:

- the CW-01 request pair
- the feedback bundle
- `src/pages/consultation/ConsultRequestList.tsx`
- `src/pages/consultation/ConsultRequestDetail.tsx`
- `src/pages/consultation/types.ts`
- `src/App.tsx`

But `git -C ../front-ai-trading-system show d9d64fe9494b34265e9aaff9e97d65238ab4688a:src/App.tsx`
shows that commit still only wires the older placeholder consultation route and does not register:

- `/consultation/requests`
- `/consultation/requests/:request_id`

So `d9d64fe` is a partial publication truth, not the full route-wired closeout commit.

### 2. What `a93cd85` actually changes

`a93cd85` is a metadata correction commit:

- commit subject:
  `EXEC-FRONT-CW01-001: repoint source_commit to transport commit d9d64fe`
- both request files at `a93cd85` now advertise:
  `source_commit: d9d64fe9494b34265e9aaff9e97d65238ab4688a`

That resolves the earlier `d51274b` mismatch, but it does not make the handoff replay-clean if the newly advertised commit is itself incomplete relative to the request content.

### 3. What `f672af2` proves

`git -C ../front-ai-trading-system ls-tree -r --name-only f672af2 -- src/App.tsx src/pages/consultation .coordination/requests/CW-01-consult-request-ui-done.yaml .coordination/requests/CW-01-consult-request-frontend-feedback.yaml docs/pantheon-feedback/CW-01-consult-request`

confirms that `f672af2` contains:

- the request pair
- the feedback bundle
- the consult-request page files
- `src/App.tsx`
- the later committee pages

And `git -C ../front-ai-trading-system show f672af2:src/App.tsx`
shows that this later commit wires:

- `/consultation/requests`
- `/consultation/requests/:request_id`

Path-limited history from `d9d64fe..f00791b` also shows `f672af2` is the first later commit in the inspected chain that touches `src/App.tsx`, which makes `f672af2` the first verified commit in that chain that actually matches the route-registration claims carried by the handoff files. `f00791b` is a later verified descendant whose tree still satisfies the same route-wiring condition.

### 4. Why the blocker still exists

The request files at `a93cd85`, `f672af2`, and `f00791b` still advertise:

- `source_commit: d9d64fe9494b34265e9aaff9e97d65238ab4688a`

The `ui-done` request also explicitly lists:

- `routes_registered: /consultation/requests`
- `routes_registered: /consultation/requests/:request_id`

Those route-registration claims are false when replayed against `d9d64fe`.

That means the current machine-readable packet chain remains internally inconsistent:

- the handoff claims route wiring
- the advertised immutable commit does not contain that wiring
- a later commit does contain the wiring, but the request pair has not been repointed to it

## Acceptance Crosswalk

| Parent acceptance item | Current evidence | Status |
|---|---|---|
| One Git-visible front commit contains the CW-01 UI files | `d9d64fe` contains the consult-request files; `f672af2` and later `f00791b` contain them too | PASS |
| `ui-done`, `frontend-feedback`, and the feedback bundle are included in the same publication set | both `d9d64fe` and `f672af2` contain the request pair and feedback bundle | PASS |
| Pagination and degraded-state contract findings are resolved | prior review and later response both verify the six fixes in the front tree | PASS |
| Both request payloads point `source_commit` at the same truthful immutable publication commit | current request pair points at `d9d64fe`, but its `src/App.tsx` does not contain the advertised request routes | FAIL |

Parent posture from this sidecar:

- code-state fixes: accepted
- publication-truth closeout: still blocked

## Reviewer Attention Points

### 1. Treat the current frontend-feedback response as over-closed

`.coordination/responses/CW-01-consult-request-frontend-feedback.yaml` says:

- `review_result: all-contract-findings-resolved-publication-replay-clean`
- `can_close: true`

That conclusion depends on `d9d64fe` being a truthful full handoff commit. Direct Git inspection shows it is not, because its `src/App.tsx` does not wire the two CW-01 request routes.

### 2. Split code-state truth from publication truth

The latest evidence still supports the code-state positives:

- six contract findings are fixed
- build passed in the sibling front repo
- the current front head `f00791b` does wire the request routes

But those positives are not enough to mark the handoff replay-clean until the request pair points at a commit whose tree matches those claims.

### 3. The likely resolution path is small and mechanical

The remaining work is not a new CW-01 implementation slice. It is a truthful repoint:

- publish one later commit whose tree contains the request pair, feedback bundle, consult-request files, and route wiring together
- repoint both request files' `source_commit` to that commit
- then return the parent to review

`f672af2` is the earliest verified target in the inspected chain. `f00791b` is a later descendant that could also work if the owner prefers to anchor the packet to the current verified head, but this sidecar does not assume that repoint has already happened.

## Recommended Review Flow For Codex

1. Read the parent acceptance and lifecycle state in `ai-status.json`.
2. Use `.coordination/reviews/CW-01-consult-request-review.md` to confirm the original blocker was publication truth.
3. Read `.coordination/responses/CW-01-consult-request-frontend-feedback.yaml` as the latest returned claim, but not as sufficient proof by itself.
4. Spot-check `d9d64fe`, `f672af2`, and optionally the later descendant `f00791b` in `../front-ai-trading-system`, especially `src/App.tsx`.
5. Keep the parent in a blocked review posture until the request pair points at one truthful route-wired publication commit.

## Suggested Sidecar Disposition

- Approve this sidecar if it accurately corrects the chronology and preserves the remaining blocker.
- Recommended parent posture:
  - accept that the six UI contract findings are resolved
  - reject the current claim that publication truth is replay-clean
  - return the parent for one more truthful request-pair repoint before `review_approved`

## Verification Commands

- `git -C ../front-ai-trading-system show --stat --oneline --decorate --no-patch d9d64fe9494b34265e9aaff9e97d65238ab4688a`
- `git -C ../front-ai-trading-system show --stat --oneline --decorate --no-patch a93cd8500a7b045436436e956003dece461aff38`
- `git -C ../front-ai-trading-system show --stat --oneline --decorate --no-patch f672af2`
- `git -C ../front-ai-trading-system show --stat --oneline --decorate --no-patch f00791b217e5550d80c1add72a8560b42bc3a056`
- `git -C ../front-ai-trading-system ls-tree -r --name-only d9d64fe9494b34265e9aaff9e97d65238ab4688a -- src/App.tsx src/pages/consultation .coordination/requests/CW-01-consult-request-ui-done.yaml .coordination/requests/CW-01-consult-request-frontend-feedback.yaml docs/pantheon-feedback/CW-01-consult-request`
- `git -C ../front-ai-trading-system ls-tree -r --name-only f672af2 -- src/App.tsx src/pages/consultation .coordination/requests/CW-01-consult-request-ui-done.yaml .coordination/requests/CW-01-consult-request-frontend-feedback.yaml docs/pantheon-feedback/CW-01-consult-request`
- `git -C ../front-ai-trading-system ls-tree -r --name-only f00791b217e5550d80c1add72a8560b42bc3a056 -- src/App.tsx src/pages/consultation .coordination/requests/CW-01-consult-request-ui-done.yaml .coordination/requests/CW-01-consult-request-frontend-feedback.yaml docs/pantheon-feedback/CW-01-consult-request`
- `git -C ../front-ai-trading-system show d9d64fe9494b34265e9aaff9e97d65238ab4688a:src/App.tsx`
- `git -C ../front-ai-trading-system show f672af2:src/App.tsx`
- `git -C ../front-ai-trading-system show f00791b217e5550d80c1add72a8560b42bc3a056:src/App.tsx`
- `git -C ../front-ai-trading-system show a93cd8500a7b045436436e956003dece461aff38:.coordination/requests/CW-01-consult-request-ui-done.yaml`
- `git -C ../front-ai-trading-system show a93cd8500a7b045436436e956003dece461aff38:.coordination/requests/CW-01-consult-request-frontend-feedback.yaml`
- `git -C ../front-ai-trading-system log --oneline --decorate --reverse d9d64fe9494b34265e9aaff9e97d65238ab4688a..f00791b217e5550d80c1add72a8560b42bc3a056 -- src/App.tsx .coordination/requests/CW-01-consult-request-ui-done.yaml .coordination/requests/CW-01-consult-request-frontend-feedback.yaml src/pages/consultation/ConsultRequestList.tsx src/pages/consultation/ConsultRequestDetail.tsx`

## Sidecar Acceptance Check

- Support artifact created only: yes
- Canonical truth modified: no
- Reviewer handoff ready: yes
