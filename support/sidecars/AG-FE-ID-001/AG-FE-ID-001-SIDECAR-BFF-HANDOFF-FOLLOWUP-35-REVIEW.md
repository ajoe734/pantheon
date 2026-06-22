# AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35 Review

| Field | Value |
|---|---|
| Reviewer | `Claude` |
| Review date | `2026-06-22` |
| Task | `AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35` |
| Packet commit | `79910193` |
| Outcome | **Approved** |

## Scope Discipline Check

- `mutates_canonical: false` confirmed in packet header.
- Scope constraint is explicit: no L1 policy docs, BFF runtime source, OpenAPI/contract files, route registries, governance policy, database migrations, OpenClaw adapter code, compatibility manifest source, or execute-plans source files were edited.
- Verification section confirms only the generated task brief was initially untracked; packet artifact is limited to `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-35.md`.

All scope constraints satisfied.

## Factual Accuracy Check

| Claim | Verified |
|---|---|
| Pantheon dev base `e01f19e7a4b7` | ✓ Consistent with `git fetch origin --prune && git rev-parse origin/dev` recorded in §10. |
| Followup-34 archived `done` at `2026-06-22T01:59:00Z`; PR `#2174` merged | ✓ Consistent with §2 task state snapshot and §4 delta baseline. |
| Dev delta includes PRs `#2176`–`#2182` | ✓ Documented in §4; breakdown is accurate (RS/TR sidecars, DB task brief/review, XR-CP-001, BE-CP-001, DB-002 sidecar). |
| Identity/servant/main/docs contract paths — no diff | ✓ `git diff --name-status` over the identity/servant/main/docs-contract pathset produced no output, as confirmed in §10. |
| Research/candidate-pool paths — additive changes from AG-XR-CP-001/AG-BE-CP-001 | ✓ Confirmed via diff in §10; files are candidate-pool contract/spec additions and research router/store changes. |
| Execute-plans PR `#66` OPEN/UNSTABLE, head `d1ae3149`, integration-gate failed (run `27923882836`, job `82622466995`) | ✓ Matches §7 gate table and §10 `gh pr checks` output. |
| Execute-plans PR `#63` OPEN/UNSTABLE, head `e1cb9125`, updated `2026-06-20T16:53:49Z` | ✓ Consistent with §10 `gh pr view 63` evidence. |
| Execute-plans `dev` at `ee835e2e`, PR `#66` is 3 ahead / 0 behind, touching only 5 files | ✓ Confirmed by `gh api compare` in §10. |
| BFF 39 tests passed (identity/servant/router/openclaw suite) | ✓ Documented in §10. |
| Candidate-pool 3 tests passed | ✓ Documented in §10. |
| Compat manifest fail-closed (not compatible, placeholder runtime commit, non-empty blocking reasons) | ✓ Documented in §10 with expected-fail notation. |
| Parent `AG-FE-ID-001` blocked, waiting for `Gemini` | ✓ Confirmed via `ai_status.py show AG-FE-ID-001` in §10. |
| `GET /bff/agora/servant` and `POST /bff/agora/servant/reconcile` unsupported | ✓ Consistent with prior followup baseline; no new runtime support has landed. |

## BFF Route Ledger

Route table in §5 is accurate. The ledger correctly distinguishes:
- Implemented routes (identity `/me`, capabilities, servant ensure, servant sessions family)
- Unsupported routes (servant preflight `GET /servant`, reconcile)
- Newly available candidate-pool routes (explicitly separated as outside Phase 1 shell)
- Separate-phase surfaces (workshop SSE, research plan/run)

The addition of the explicit `candidate-pools*` row with "Separate candidate-pool/research context" handoff rule is an improvement over prior followups and correctly reflects the AG-XR-CP-001/AG-BE-CP-001 delta.

No route inflation or premature claims.

## Candidate-Pool Delta Separation

The packet correctly scopes the AG-XR-CP-001 and AG-BE-CP-001 additions:
- §4 explicitly differentiates: identity/servant paths unchanged, candidate-pool/research paths added.
- §5 route ledger provides a dedicated row with a clear "Separate" handoff rule.
- §8 provides a separate candidate-pool journey clearly labeled for later research/candidate UI scope.
- §9 absorption checklist includes a standalone "Candidate-pool separation" check.
- The packet correctly self-corrects: it no longer says "no Agora path changes" (as would have been appropriate for prior followups without candidate-pool activity) but instead accurately says "candidate-pool/research changed, identity/servant did not."

Separation is clean and correctly enforced throughout.

## Operator Journey

The §8 journey is conservative and honest. Session controls remain deferred to frontend scope with strict session clients and UI tests. The candidate-pool journey is labeled as later scope. Management/broker/capital controls are correctly excluded.

## Parent Absorption Checklist

The §9 checklist is comprehensive (14 checks) and covers all required gate evidence. Notable additions from prior followups:
- "Candidate-pool separation" explicitly checks that AG-XR-CP-001/AG-BE-CP-001 routes are not silently absorbed.
- "Research/workshop separation" explicitly guards against Phase 3/4 surface bleed.

The checklist does not lower the bar for the aggregate release gate.

## Gate Ownership

§7 correctly preserves the gate failure table from followup-34 with assigned owners (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) and does not absorb them into AG-FE-ID-001.

## Reviewer Decision

This packet is a factually accurate, scope-disciplined refresh of the AG-FE-ID-001 BFF/frontend handoff. The key delta from followup-34 — that candidate-pool surfaces from AG-XR-CP-001/AG-BE-CP-001 are now on Pantheon dev — is correctly captured and cleanly separated from Phase 1 identity/servant shell scope. No canonical truth was mutated. Identity/servant BFF facts from followup-34 remain valid because those paths show no diff in the new dev window.

**Approved.** Parent `AG-FE-ID-001` remains blocked pending execute-plans PR `#66` merge or a formal aggregate-gate disposition from repository governance.
