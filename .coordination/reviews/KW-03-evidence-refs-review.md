# KW-03 Evidence Refs Review Packet

## Date

2026-04-24

## Reviewer

Codex

## Scope

Review the dispatched `KW-03-evidence-refs` `ui-done` handoff against:

- `docs/bff/KW-03-evidence-refs.md`
- `docs/examples/KW-03-evidence-refs.json`
- `docs/pantheon-handoffs/KW-03-evidence-refs/FRONTEND_CHANGE_SPEC.md`
- the sibling `front-ai-trading-system` branch `pkt-004-detail-fix`
- the current remote ref `origin/pkt-004-detail-fix`

## Findings

### 1. Medium: degraded empty list responses still render authoritative empty copy

- `docs/bff/KW-03-evidence-refs.md` and
  `docs/pantheon-handoffs/KW-03-evidence-refs/FRONTEND_CHANGE_SPEC.md` both
  require degraded list responses to keep the non-dismissable degradation
  banner and explicitly say an empty `evidence_refs[]` array is not
  authoritative while degraded.
- The reviewed list page still routes any empty array to the normal empty
  result alert at
  `../front-ai-trading-system/src/pages/knowledge/EvidenceRefList.tsx:237-242`.
- That branch remains reachable when the surface is degraded because the page
  only splits out `unavailable`, while degraded responses still flow through
  the normal content branch:
  `../front-ai-trading-system/src/pages/knowledge/EvidenceRefList.tsx:194-200`
  and `../front-ai-trading-system/src/pages/knowledge/EvidenceRefList.tsx:220-227`.
- The canonical degraded example in `docs/examples/KW-03-evidence-refs.json`
  still publishes `evidence_refs: []` with
  `meta.surfaces.evidence_refs_list = "degraded"`, so this is the expected
  contract fixture, not a hypothetical edge case.

Impact:

- Operators can still be shown the normal `No evidence refs returned` copy when
  Pantheon has only said the list is stale.
- The replay/source-commit truth is now fixed, but the UI still does not meet
  the published degraded empty-state semantics, so the loop cannot close yet.

## Verified Positives

- The old transport blocker is resolved. `origin/pkt-004-detail-fix` now
  resolves to `1a1a42eebda033a1fbda4696df5b81271f5eed9b`, and that remote head
  contains:
  - `.coordination/requests/KW-03-evidence-refs-ui-done.yaml`
  - `.coordination/requests/KW-03-evidence-refs-frontend-feedback.yaml`
  - `docs/pantheon-feedback/KW-03-evidence-refs/*`
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/components/WorkbenchBreadcrumb.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/knowledge/EvidenceRefList.tsx`
  - `src/pages/knowledge/EvidenceRefDetail.tsx`
  - `src/pages/knowledge/EvidenceRefTypes.ts`
- Both remote-visible request files now advertise the truthful reviewed UI
  commit `6321613cff3c49b11a7619e0f9170217a27a7b17`, and
  `docs/pantheon-feedback/KW-03-evidence-refs/API_GAP_REQUESTS.json` points at
  the same SHA.
- `6321613cff3c49b11a7619e0f9170217a27a7b17` is contained in
  `origin/pkt-004-detail-fix`, and the diff from that source commit to the
  current remote branch head only changes the request pair and feedback bundle,
  not the reviewed KW-03 UI files.
- `npm run build` passed from an isolated worktree of the reviewed UI source
  commit `6321613cff3c49b11a7619e0f9170217a27a7b17`. The build emitted only the
  existing non-blocking Vite chunk-size warning.
- The reviewed UI still stays on the published Pantheon routes through the
  shared BFF client only:
  - `GET /api/v1/knowledge/evidence`
  - `GET /api/v1/knowledge/evidence/{ref_id}`
- The route family is mounted in the app shell:
  - `/knowledge/evidence`
  - `/knowledge/evidence/:ref_id`
- List rows render `resolved_link.display_label` from the BFF and navigate to
  detail via the BFF-provided `route_href`.
- The detail screen keeps `resolved_link`, `linked_decisions`,
  `source_note_context`, and `source_memory_context` backend-owned and does not
  construct links from `source_ref`, `storage_ref`, or raw entity ids.
- `python3 -m pytest -q services/control-plane/bff/test_kw03_evidence_refs_contract.py`
  passed in the current Pantheon workspace (`4 passed`).

## Decision

`KW-03-evidence-refs` remains **follow-up-required**.

No new Pantheon endpoint, contract change, or example-payload rebaseline is
required for this cycle. The remaining blocker is front-owned degraded
empty-list handling in `EvidenceRefList.tsx`; transport truth, source-commit
replayability, and buildability are now verified.

## Remaining Follow-up

1. Front repo: update `EvidenceRefList.tsx` so degraded empty payloads remain
   explicitly non-authoritative instead of showing the normal empty-result
   alert.
2. Front repo: publish a new reviewed KW-03 UI commit with that fix on
   `pkt-004-detail-fix`.
3. Front repo: refresh the KW-03 request pair and feedback bundle so their
   `source_commit` / `reviewed_source_commit` fields point at the new fix
   commit.
4. Redispatch Pantheon review against the unchanged KW-03 contract.

## Verification

- `git -C ../front-ai-trading-system rev-parse origin/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system branch -r --contains 6321613cff3c49b11a7619e0f9170217a27a7b17`
- `git -C ../front-ai-trading-system show 1a1a42eebda033a1fbda4696df5b81271f5eed9b:.coordination/requests/KW-03-evidence-refs-ui-done.yaml`
- `git -C ../front-ai-trading-system show 1a1a42eebda033a1fbda4696df5b81271f5eed9b:.coordination/requests/KW-03-evidence-refs-frontend-feedback.yaml`
- `git -C ../front-ai-trading-system show 1a1a42eebda033a1fbda4696df5b81271f5eed9b:docs/pantheon-feedback/KW-03-evidence-refs/API_GAP_REQUESTS.json`
- `git -C ../front-ai-trading-system ls-tree -r --name-only origin/pkt-004-detail-fix -- .coordination/requests/KW-03-evidence-refs-ui-done.yaml .coordination/requests/KW-03-evidence-refs-frontend-feedback.yaml docs/pantheon-feedback/KW-03-evidence-refs/LOVABLE_CHANGE_FEEDBACK.md docs/pantheon-feedback/KW-03-evidence-refs/API_GAP_REQUESTS.json docs/pantheon-feedback/KW-03-evidence-refs/UI_DECISIONS.md docs/pantheon-feedback/KW-03-evidence-refs/QA_STATUS.md src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/lib/bffClient.ts src/pages/knowledge/EvidenceRefList.tsx src/pages/knowledge/EvidenceRefDetail.tsx src/pages/knowledge/EvidenceRefTypes.ts`
- `git -C ../front-ai-trading-system diff --name-only 6321613cff3c49b11a7619e0f9170217a27a7b17..origin/pkt-004-detail-fix -- src/App.tsx src/components/AppSidebar.tsx src/components/WorkbenchBreadcrumb.tsx src/lib/bffClient.ts src/pages/knowledge/EvidenceRefList.tsx src/pages/knowledge/EvidenceRefDetail.tsx src/pages/knowledge/EvidenceRefTypes.ts docs/pantheon-feedback/KW-03-evidence-refs .coordination/requests/KW-03-evidence-refs-ui-done.yaml .coordination/requests/KW-03-evidence-refs-frontend-feedback.yaml`
- `tmpdir=$(mktemp -d /tmp/front-kw03-review-XXXXXX) && git -C ../front-ai-trading-system worktree add --detach "$tmpdir" 6321613cff3c49b11a7619e0f9170217a27a7b17 && ln -s /home/lupin/code/front-ai-trading-system/node_modules "$tmpdir/node_modules" && (cd "$tmpdir" && npm run build) && git -C ../front-ai-trading-system worktree remove "$tmpdir" --force`
- `python3 -m pytest -q services/control-plane/bff/test_kw03_evidence_refs_contract.py`
