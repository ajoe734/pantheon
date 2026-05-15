# PKT-003 Inspiration Graph Review Packet

## Date

2026-04-20

## Reviewer

Codex

## Findings

1. High: the returned `ui-done` handoff is still not replay-clean because the published `source_commit` values do not resolve to real front-repo commits.
   - The dispatched `ui-done` payload published at front commit `97cf8dedfe0fe8089deb8a3889aaf2938b7fbef5` still sets `source_commit: 8ec0315146f8fcb526f02fe0d17c6f2d6a63a25b` in `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml:5`, but that SHA does not exist in `../front-ai-trading-system`. The actual malformed-payload fix commit is `8ec0315cef4febeb1badc2d15221d3370098c89a`.
   - The latest local refresh at front `HEAD` (`000d1d6b0ee61fd18c464ff2a781900b89be06ed`) changes that field to `08680fd13baa4227460f72ebf662478fd0d3838a` in the same file, but that full SHA also does not resolve. The actual copy-polish commit is `08680fde32bf232043dd170619d172620e38e9f3`.
   - None of `97cf8dedfe0fe8089deb8a3889aaf2938b7fbef5`, `000d1d6b0ee61fd18c464ff2a781900b89be06ed`, or `08680fde32bf232043dd170619d172620e38e9f3` are present on any fetched remote branch in the sibling repo.
   - Impact: Pantheon can verify the local UI behavior, but it still cannot reconstruct the returned handoff truthfully from the published coordination payload.

## Verified Positives

- The prior malformed-payload blocker is fixed in the actual reviewed UI source. `validateResponse()` now rejects malformed edges, missing meta, non-array `strategy_tags`, and unsupported `meta.surfaces.inspiration` values in `src/pages/evolution/InspirationGraph.tsx:70-135`, while `loadGraph()` commits only `validation.data ?? null` in `:252-289`. The page therefore stays on the explicit validation alert instead of deriving graph state from invalid payloads.
- The live read boundary remains correct. `lineageApi.getInspirationGraph()` still uses only `GET /api/v1/lineage/inspiration/{artifact_id}` in `src/lib/bffClient.ts:556-564`, and the screen still mounts at `/evolution/inspiration` plus `/evolution/inspiration/:artifact_id` in `src/App.tsx:132-134`.
- The reviewed UI keeps the contract-required positive behavior for valid payloads: strategy tags render from `strategy_tags[]`, graph-edge selection opens the read-only drawer, `meta.snapshot_at` is shown as the data timestamp, and the `404`, `stale`, `unavailable`, and explicit empty-graph branches remain intact in `src/pages/evolution/InspirationGraph.tsx`.
- Static verification passed from an isolated worktree of the actual latest reviewed source commit `08680fde32bf232043dd170619d172620e38e9f3`: targeted `npx eslint src/pages/evolution/InspirationGraph.tsx src/pages/evolution/InspirationEdgeDetail.tsx src/pages/evolution/types.ts src/lib/bffClient.ts src/pages/inspiration/Graph.tsx` produced no findings, and `npm run build` succeeded with only the existing non-blocking Browserslist staleness note plus Vite chunk-size warning.
- Pantheon's local EW-04 contract remains valid. `python3 -m pytest services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q` passed with `3 passed`.

## Decision

Follow-up required. The UI behavior is now contract-aligned, but the returned
`ui-done` metadata is still not truthful or replay-clean.

## Required Follow-up

1. Front repo: republish `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` with a real full `source_commit` that resolves in Git. If the latest reviewed source is intended, use `08680fde32bf232043dd170619d172620e38e9f3`; if the earlier malformed-payload fix commit is intended, use `8ec0315cef4febeb1badc2d15221d3370098c89a`.
2. Front repo: push the corrected publication commit to the truthful publish branch and re-dispatch from that Git-visible commit instead of leaving the fix local-only.

## Verification

- `git -C ../front-ai-trading-system show 97cf8dedfe0fe8089deb8a3889aaf2938b7fbef5:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml | nl -ba | sed -n '1,40p'`
- `git -C ../front-ai-trading-system show HEAD:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml | nl -ba | sed -n '1,40p'`
- `git -C ../front-ai-trading-system rev-parse 8ec0315`
- `git -C ../front-ai-trading-system rev-parse 08680fd`
- `git -C ../front-ai-trading-system branch -r --contains 97cf8dedfe0fe8089deb8a3889aaf2938b7fbef5`
- `git -C ../front-ai-trading-system branch -r --contains 000d1d6b0ee61fd18c464ff2a781900b89be06ed`
- `git -C ../front-ai-trading-system branch -r --contains 08680fde32bf232043dd170619d172620e38e9f3`
- `git -C ../front-ai-trading-system show 08680fd:src/pages/evolution/InspirationGraph.tsx | nl -ba | sed -n '70,310p'`
- `git -C ../front-ai-trading-system show 8ec0315:src/App.tsx | nl -ba | sed -n '125,138p'`
- `git -C ../front-ai-trading-system show 8ec0315:src/lib/bffClient.ts | nl -ba | sed -n '556,564p'`
- `git -C ../front-ai-trading-system worktree add --detach -f /tmp/front-pkt003-rereview 08680fde32bf232043dd170619d172620e38e9f3`
- `ln -s /home/lupin/code/front-ai-trading-system/node_modules /tmp/front-pkt003-rereview/node_modules`
- `(cd /tmp/front-pkt003-rereview && npx eslint src/pages/evolution/InspirationGraph.tsx src/pages/evolution/InspirationEdgeDetail.tsx src/pages/evolution/types.ts src/lib/bffClient.ts src/pages/inspiration/Graph.tsx)`
- `git -C ../front-ai-trading-system worktree add --detach -f /tmp/front-pkt003-rereview-build 08680fde32bf232043dd170619d172620e38e9f3`
- `ln -s /home/lupin/code/front-ai-trading-system/node_modules /tmp/front-pkt003-rereview-build/node_modules`
- `(cd /tmp/front-pkt003-rereview-build && npm run build)`
- `python3 -m pytest services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q`

## 2026-04-21 Re-review Addendum

Re-reviewed after front publication commit
`521bcb87139139a8157ecf4cf63aaa4bc89118e1` was pushed to
`origin/pkt-004-detail-fix`.

- `git -C ../front-ai-trading-system show 521bcb87139139a8157ecf4cf63aaa4bc89118e1:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml`
  now shows
  `source_commit: 08680fde32bf232043dd170619d172620e38e9f3`
- `git -C ../front-ai-trading-system branch -r --contains 521bcb87139139a8157ecf4cf63aaa4bc89118e1`
  returns `origin/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system branch -r --contains 08680fde32bf232043dd170619d172620e38e9f3`
  also returns `origin/pkt-004-detail-fix`
- Fresh isolated verification at `08680fde32bf232043dd170619d172620e38e9f3`
  still passes targeted ESLint and `npm run build`
- `python3 -m pytest services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q`
  still passes with `3 passed`

The earlier `source_commit` replay blocker is therefore resolved. Two front-owned
loop-closure blockers remain:

1. High: the required frontend-feedback request and feedback bundle are still absent from the published front branch.
   - `git -C ../front-ai-trading-system ls-tree -r --name-only 521bcb87139139a8157ecf4cf63aaa4bc89118e1 | rg 'PKT-003-inspiration-graph-frontend-feedback|docs/pantheon-feedback/PKT-003-inspiration-graph'`
     returns no matches.
   - Impact: the front return is no longer transport-broken, but it is still not
     closed-loop complete under the coordination bus rules because Pantheon
     cannot replay the required feedback artifacts from the published branch.

2. Medium: the live Inspiration route is still labelled as `Soon` in the shell.
   - `git -C ../front-ai-trading-system show 08680fde32bf232043dd170619d172620e38e9f3:src/components/AppSidebar.tsx | nl -ba | sed -n '83,90p'`
     still shows
     `{ title: 'Inspiration', url: '/evolution/inspiration', icon: Network, comingSoon: true },`
   - Impact: the page is live and mounted, but the shell still contradicts the
     route-live packet state and the frontend change spec.

## Updated Decision

Follow-up required.

The UI behavior and the `ui-done` publication tuple are now contract-aligned,
but the packet cannot close until the front repo publishes the required
`frontend-feedback` bundle and removes the stale `Soon` badge from the live
Inspiration nav entry.

## Updated Required Follow-up

1. Publish `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`
   plus the required four-file feedback bundle under
   `docs/pantheon-feedback/PKT-003-inspiration-graph/`.
2. Remove the `Soon` badge from the Inspiration shell entry and republish
   `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` from that
   same follow-up commit so the cited implementation snapshot includes the
   route-live shell state.
3. Return the packet for re-review after that follow-up republish.

## 2026-04-21 Publication Re-review Addendum

Re-reviewed after front publication commit
`227557621c972ee051819ad3cef876bf2b6acbf8` was pushed to
`origin/pkt-004-detail-fix`.

- The front-owned loop-closure artifacts now exist on the published branch.
  `git -C ../front-ai-trading-system show --stat --summary 82172389d88a49513c5e4ba0951b206ab09bd29a`
  confirms that the real follow-up commit added
  `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`,
  the four-file feedback bundle under
  `docs/pantheon-feedback/PKT-003-inspiration-graph/`, and the
  `AppSidebar.tsx` shell-state fix.
- The live shell state is now correct in the published branch. `git -C
  ../front-ai-trading-system show
  227557621c972ee051819ad3cef876bf2b6acbf8:src/components/AppSidebar.tsx`
  shows the Inspiration nav entry without `comingSoon: true`.
- The UI behavior remains contract-aligned. The screen still validates the
  response before setting renderable state in
  `src/pages/evolution/InspirationGraph.tsx:70-135` and `:252-268`, keeps the
  explicit `404` / validation / `stale` / `unavailable` / empty branches in
  `:403-515`, mounts only on `/evolution/inspiration` plus
  `/evolution/inspiration/:artifact_id` in `src/App.tsx:134-135`, and reads
  only `GET /api/v1/lineage/inspiration/{artifact_id}` through
  `src/lib/bffClient.ts:596-602`.
- Fresh isolated verification at the real feedback-bundle commit
  `82172389d88a49513c5e4ba0951b206ab09bd29a` still passes targeted ESLint and
  `npm run build`. The build completed with the existing non-blocking
  Browserslist staleness note and Vite chunk-size warning only.
- `python3 -m pytest services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q`
  still passes with `3 passed`.

One transport blocker remains:

1. High: the republished `ui-done` and `frontend-feedback` payloads still cite a
   non-existent full `source_commit`.
   - `git -C ../front-ai-trading-system show 227557621c972ee051819ad3cef876bf2b6acbf8:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml`
     and the matching `frontend-feedback` request both still set
     `source_commit: 821723875404d5f5cfb3b522438886df3cc66d25` on line 5.
   - `git -C ../front-ai-trading-system rev-parse --verify --quiet 821723875404d5f5cfb3b522438886df3cc66d25^{commit}`
     returns nothing, so Pantheon still cannot replay the packet from the
     published metadata.
   - The actual Git-visible feedback-bundle commit is
     `82172389d88a49513c5e4ba0951b206ab09bd29a`, as confirmed by
     `git -C ../front-ai-trading-system rev-parse 8217238`.
   - Impact: the implementation is ready, but the packet is still not
     replay-clean under the coordination bus rules.

## Re-reviewed Decision

Follow-up required.

The prior feedback-bundle and shell-state blockers are closed, but the latest
published coordination payloads still point at an invalid `source_commit`, so
Pantheon cannot advance this packet to final closure yet.

## Re-reviewed Required Follow-up

1. Front repo: republish both
   `.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml` and
   `.coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`
   with a real full `source_commit`.
2. Use the actual feedback-bundle snapshot
   `82172389d88a49513c5e4ba0951b206ab09bd29a` if the intent is to point at the
   commit that added the feedback bundle and removed the stale `Soon` badge.
3. Return the packet for one final replay check after that republish.

## 2026-04-21 Dispatch Re-check Addendum

Re-checked after the new owner handoff recorded in `ai-status.json` claimed that
publication commit `22755761ff18e84992f685fe2ec0f9af4f533d4b` now points at a
truthful `source_commit`.

- `git -C ../front-ai-trading-system rev-parse --verify --quiet 22755761ff18e84992f685fe2ec0f9af4f533d4b^{commit}`
  returns nothing, while
  `git -C ../front-ai-trading-system rev-parse --verify --quiet 227557621c972ee051819ad3cef876bf2b6acbf8^{commit}`
  resolves. The handoff message is still citing a non-existent full publication
  SHA.
- `git -C ../front-ai-trading-system show 227557621c972ee051819ad3cef876bf2b6acbf8:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml`
  and the matching `frontend-feedback` request still both contain
  `source_commit: 821723875404d5f5cfb3b522438886df3cc66d25`.
- `git -C ../front-ai-trading-system rev-parse --verify --quiet 821723875404d5f5cfb3b522438886df3cc66d25^{commit}`
  still returns nothing, while the actual published feedback-bundle snapshot
  remains `82172389d88a49513c5e4ba0951b206ab09bd29a`.
- `git -C ../front-ai-trading-system log --oneline -n 2 origin/pkt-004-detail-fix -- .coordination/requests/PKT-003-inspiration-graph-ui-done.yaml .coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml`
  still ends at `2275576` and `8217238`; no newer published commit has corrected
  the metadata.

## Dispatch Re-check Decision

Follow-up still required.

The owner handoff message is newer, but the published coordination payloads have
not changed and remain non-replayable because both request files still cite an
invalid full `source_commit`.

## 2026-04-21 Feedback-Bundle Replay Re-check

Re-reviewed after front commits `82172389d88a49513c5e4ba0951b206ab09bd29a` and
`64bea4a0b1b7f8783c64ad9aeb334be289b5eb76` became visible on
`origin/pkt-004-detail-fix`.

- `git -C ../front-ai-trading-system show 64bea4a0b1b7f8783c64ad9aeb334be289b5eb76:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml`
  and the matching `frontend-feedback` request now both resolve
  `source_commit: 82172389d88a49513c5e4ba0951b206ab09bd29a`
- `git -C ../front-ai-trading-system show 82172389d88a49513c5e4ba0951b206ab09bd29a:src/components/AppSidebar.tsx`
  confirms the live Inspiration nav entry no longer carries `comingSoon: true`
- `git -C ../front-ai-trading-system branch -r --contains 82172389d88a49513c5e4ba0951b206ab09bd29a`
  and
  `git -C ../front-ai-trading-system branch -r --contains 64bea4a0b1b7f8783c64ad9aeb334be289b5eb76`
  both return `origin/pkt-004-detail-fix`
- Fresh replay verification still passes:
  - `python3 -m pytest services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q`
    -> `3 passed`
  - targeted ESLint and `npm run build` from an isolated worktree of
    `82172389d88a49513c5e4ba0951b206ab09bd29a` both pass; build keeps only the
    existing Browserslist staleness note and Vite chunk-size warning

One transport blocker remains:

1. Medium: the feedback bundle is still not fully replay-clean because
   `docs/pantheon-feedback/PKT-003-inspiration-graph/API_GAP_REQUESTS.json`
   pins a non-existent `reviewed_source_commit`.
   - `git -C ../front-ai-trading-system show 64bea4a0b1b7f8783c64ad9aeb334be289b5eb76:docs/pantheon-feedback/PKT-003-inspiration-graph/API_GAP_REQUESTS.json`
     shows
     `reviewed_source_commit: 821723875404d5f5cfb3b522438886df3cc66d25`
   - `git -C ../front-ai-trading-system rev-parse --verify --quiet 821723875404d5f5cfb3b522438886df3cc66d25^{commit}`
     returns nothing
   - the actual reviewed UI source commit is
     `82172389d88a49513c5e4ba0951b206ab09bd29a`
   - Impact: the request pair and shell state are now correct, but the
     feedback bundle still overstates replay cleanliness because one canonical
     bundle file points at an invalid immutable commit

## Updated Decision

Follow-up required.

## Updated Required Follow-up

1. Front repo: correct
   `docs/pantheon-feedback/PKT-003-inspiration-graph/API_GAP_REQUESTS.json` so
   `reviewed_source_commit` resolves truthfully to
   `82172389d88a49513c5e4ba0951b206ab09bd29a` or a newer reviewed transport
   commit.
2. Republish the canonical request pair and feedback bundle from that
   Git-visible commit, then redispatch the unchanged packet for one final
   replay check.

## 2026-04-21 Final Replay Closeout

Re-reviewed after front publication commit
`93a4b58891031442133a6966d0354ae216a80b72` was pushed to
`origin/pkt-004-detail-fix`.

- `git -C ../front-ai-trading-system show 93a4b58891031442133a6966d0354ae216a80b72:.coordination/requests/PKT-003-inspiration-graph-ui-done.yaml`
  and the matching `frontend-feedback` request now both publish
  `source_commit: 82172389d88a49513c5e4ba0951b206ab09bd29a`
- `git -C ../front-ai-trading-system show 93a4b58891031442133a6966d0354ae216a80b72:docs/pantheon-feedback/PKT-003-inspiration-graph/API_GAP_REQUESTS.json`
  now publishes
  `reviewed_source_commit: 82172389d88a49513c5e4ba0951b206ab09bd29a`
- `git -C ../front-ai-trading-system branch -r --contains 93a4b58891031442133a6966d0354ae216a80b72`
  and
  `git -C ../front-ai-trading-system branch -r --contains 82172389d88a49513c5e4ba0951b206ab09bd29a`
  both return `origin/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system diff --name-only 82172389d88a49513c5e4ba0951b206ab09bd29a 93a4b58891031442133a6966d0354ae216a80b72 -- .coordination/requests/PKT-003-inspiration-graph-ui-done.yaml .coordination/requests/PKT-003-inspiration-graph-frontend-feedback.yaml docs/pantheon-feedback/PKT-003-inspiration-graph/API_GAP_REQUESTS.json`
  shows the final republish only touches those three replay-metadata files
- Fresh replay verification from this review turn still passes:
  - `python3 -m pytest services/control-plane/bff/test_ew04_inspiration_graph_contract.py -q`
    -> `3 passed`
  - targeted `npx eslint` and `npm run build` both passed from a fresh detached
    worktree of `82172389d88a49513c5e4ba0951b206ab09bd29a`; the build emitted
    only the existing Browserslist staleness note and Vite chunk-size warning
- Pantheon's local feedback mirror now exists under
  `docs/pantheon-feedback/PKT-003-inspiration-graph/` so the replay-clean
  bundle is visible in this repo alongside the closed request pair

## Final Decision

Loop complete.

The PKT-003 Inspiration Graph packet is now replay-clean and contract-aligned.
No Pantheon follow-up remains for the current packet scope beyond deferred live
browser QA.
