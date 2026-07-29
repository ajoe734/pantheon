# AG-CAND-TRUTH-001-FE — Reviewer Approval Record

- Task: AG-CAND-TRUTH-001-FE — Stop mixing live candidates with sample fields
- Owner: Codex
- Reviewer: Claude
- Review date: 2026-07-22
- Repository: `ajoe734/execute-plans`
- Branch: `task/AG-CAND-TRUTH-001-FE`
- Reviewed HEAD: `f9fb01d6adaba41045178571d3d006e2ed1e6b05` (PR #506, base `dev`)
- Consumed backend contract: Pantheon PR #3980 at merge
  `5004450c5493aa8aef284cf42439c9b27ef54235` (AG-CAND-TRUTH-001-BE, bundle v1.12)
- Decision: **APPROVED** — return to owner Codex for closeout
  (PR #506 → execute-plans `dev` merge → `done`)

## Scope reviewed (vs `origin/dev`, merge-base `0cfc3058`)

```
e2e/agora-candidate-truth.spec.ts                    (new, 238 lines)
src/agora/pages/trading-room/TradingRoomPage.test.tsx (modified)
src/agora/pages/trading-room/TradingRoomPage.tsx      (modified)
src/lib/bff-v1/agora/candidatePool.test.ts            (modified)
src/lib/bff-v1/agora/candidatePool.ts                 (modified)
```

Two commits, both with correct `LLM-Agent`/`Task-ID`/`Reviewer` trailers:
`eb320e09` (anchor, client types + mapping layer) and `f9fb01d6` (final,
presentation + tests + e2e).

## Acceptance criteria verification

1. **No production live mapping reads from `DEFAULT_CANDIDATES`** — the old
   `staticFallback` blend (live item + `DEFAULT_CANDIDATES.find(...)` rationale /
   concerns / evidence / details with `isSampleData=false`) is deleted.
   `DEFAULT_CANDIDATES` is now derived from `DEFAULT_CANDIDATE_FIXTURES` with
   `dataSource: "sample"` stamped on every record, and repo-wide grep confirms
   no other production usage. In `VITE_BFF_MODE=live` the mapping path is
   `mapCandidatePoolMember` only, which reads exclusively from the v1.12
   `fields` / `score_semantics` / `as_of` truth projection.
2. **Mixed live/sample rows rejected by tests** — `mapCandidatePoolMember`
   fail-closes: missing `fields`/`score_semantics`/`as_of` throws; every field's
   `provenance.source_ref` must embed the row's `artifact_id` and carry a
   non-blank `as_of` (`assertSameCandidateProvenance`); `details.strategy_ref`
   must match the member's `strategy_ref`; an available `effective_score`
   semantics `source_ref` must embed the `artifact_id`. A thrown mismatch lands
   in the `.catch` → `candidate-error-state`, never a rendered row. Vitest
   "rejects a live row whose field provenance belongs to another identity"
   proves the mixed row renders as an error with no candidate rows and no
   sample warning.
3. **Empty/failed BFF responses render explicit states in strict mode** —
   `candidateLoadState` machine (`loading | ready | sample | empty | error`);
   error path renders `role="alert"` with the BFF message, empty path renders
   "Sample data was not substituted" `role="status"`; both tested with the
   sample-warning testid asserted absent. Demo data appears only when
   `VITE_BFF_MODE !== "live"`, is whole-dataset labeled ("Sample dataset"
   header badge, per-row `data-candidate-source="sample"`, drawer "Sample
   candidate" badge), and the live client is proven un-called in mock mode.
4. **Provenance/freshness displayed, lens filtering preserved** — per-field
   provenance lines (`source_type · source_ref · as of`) for rationale,
   concerns, next event, and evidence in the drawer; unavailable fields render
   typed reasons (`score_not_run`, `no_governed_source`, `not_recorded`) that
   match the BE `_unavailable_field` enum; list-level freshness banner shows
   `as_of` + `data_cutoff` and honors `meta.read_state === "stale"`
   (board "STALE" + drawer "Stale · as of <as_of>", tested). Lens fetch remains
   per-lens (`listCandidatePoolMembers(activeLensId)`), mapped rows carry the
   active `lensId`, and lifecycle filtering is unchanged; live rows only render
   lens-detail columns that actually exist, otherwise "Unavailable"
   (`candidateDetailValue`) instead of inheriting fixture metrics.
5. **Desktop and 393px accessibility** — new Playwright spec runs on chromium
   and mobile-chromium (393px): asserts live labeling, same-identity drawer
   content, no fixture text leakage, no horizontal overflow
   (`scrollWidth <= innerWidth`), zero console errors, and Axe WCAG 2 A/AA with
   no serious/critical violations on the drawer. The drawer scroll region
   gained `tabIndex={0}` + `aria-label` (scrollable-region-focusable fix).
6. **PR records the consumed backend contract** — commit body and
   `candidatePool.ts` header both cite PR #3980 / merge `5004450c5`.

## Backend contract cross-check (Pantheon `5004450c5`)

- All four BE provenance `source_ref` formats (`candidate-pool-member:…`,
  `candidate-review:…`, `candidate-score:…`, `candidate-monitoring:…`) embed
  `artifact_id`, so the FE same-identity assertion accepts every well-formed BE
  projection; the FE deliberately does not assert on `sharpe_summary`
  `source_ref`, which the BE may set to a bare `run_ref`.
- FE lifecycle enum (`candidate | review | approved | rejected`) exactly matches
  the BE `_REVIEW_DECISION_TO_LIFECYCLE` image plus the initial state; the
  `lifecycleState` switch is exhaustive under tsc.
- FE unavailable-reason enum and field-state discriminated union mirror
  `_available_field` / `_unavailable_field`; list envelope parsing matches the
  BE members endpoint (`page_info` with `order_by "created_at,artifact_id"`,
  `meta.freshness.pool_snapshot_at/data_cutoff/last_score_run_at`, optional
  `read_state`/`warnings`).

## Independent verification (commands run by reviewer)

```
npm ci                                                     # clean install
npx vitest run src/agora/pages/trading-room/TradingRoomPage.test.tsx \
  src/lib/bff-v1/agora/candidatePool.test.ts               # 91 passed (77 + 14)
npx tsc --noEmit                                           # clean
npx eslint <5 changed files>                               # 0 errors, 1 pre-existing-style warning
npx playwright test e2e/agora-candidate-truth.spec.ts      # 2 passed (chromium + mobile-chromium 393px)
```

The Playwright run required a local live-mode Vite server with matching
`VITE_SUPABASE_URL` in both server and runner env; initial failures were
reviewer-environment setup, not the spec. PR #506 CI is green (Commit trailers,
Generated files guard, Smoke acceptance, integration-gate) and the PR is
MERGEABLE against `dev`.

Note: the review-dispatch message cited "99 focused Vitest tests"; the owner's
final commit trailer and my re-run both record 91 focused tests across the two
suites. The delivered verification claim in the commit is accurate.

## Non-blocking observations (no change requested)

1. `assertSameCandidateProvenance` uses substring `source_ref.includes(artifactId)`;
   a colon-delimited segment match would be stricter against prefix-id
   collisions. Current BE ref formats make this safe today.
2. Truth-state strings (`Unavailable — …`, freshness lines, error/empty copy)
   are hardcoded English while surrounding copy uses i18n `t()`. Acceptable for
   provenance/diagnostic text; could be localized in a follow-up.

## Decision

APPROVED. The reviewed HEAD honestly renders same-identity candidate truth,
fail-closes on cross-identity provenance, isolates sample data to a fully
labeled whole-dataset demo mode, and proves empty/error/stale states plus
desktop/393px accessibility. Task returns to owner Codex for closeout per
`.orchestrator/skills/task-closeout-finalization.md`: merge PR #506 into
execute-plans `dev`, then `done` with delivery metadata.
