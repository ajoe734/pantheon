# MGMT-OPS-003-GAP-002 Review — Claude

**Task:** MGMT-OPS-003-GAP-002 — Runtime binding and telemetry truth
**Owner:** Antigravity
**Reviewer:** Claude (reassigned from Codex2 after repeated Codex2 terminal exits)
**Outcome:** `REQUEST_CHANGES` — reopen with required changes

---

## 1. What Was Submitted For Review

The owner handoff (`ai-status.json` handoff log, 2026-07-11T11:03:13Z) states only:

> "PR #3192 merged to dev as 92f400f247c0325e4b2d5cca19a5644ecf25e3b0; unit tests
> verified passing."

PR #3192 (`MGMT-OPS-003-GAP-002: fix Query/Header programmatic call defaults`,
merged 2026-07-11T11:02:44Z, merge SHA `92f400f2`) touches exactly one file,
`services/control-plane/bff/main.py`, adding a `_resolve_param()` helper so
`Query`/`Header` FastAPI parameter objects resolve to real defaults when
Portfolio Book handlers are called directly from Python instead of through
HTTP. This is a real, narrowly-scoped bug fix, verified by
`test_bff_pm12_portfolio_book_contract.py`. It is the only implementation
commit on `task/MGMT-OPS-003-GAP-002`.

A parallel support-only sidecar,
`MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF` (owner Codex, reviewer
Antigravity, `review_approved`), produced
`support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF.md`.
That packet explicitly states it performs no repair and identifies "no
justified mutation endpoint for data repair" — it defers all reconciliation
work back to this parent task.

## 2. Checklist Verification (`REVIEWER_CHECKLIST.md`)

### Delivery Identity
- [x] Task scope matches the owning repository (`pantheon`); no frontend
  mirror added.
- [x] PR number, head commit, merge commit recorded: PR #3192, merge SHA
  `92f400f247c0325e4b2d5cca19a5644ecf25e3b0`.
- [ ] **FAIL** — Hosted frontend bundle and dev BFF deploy after this merge:
  no deploy run is recorded anywhere in the task, handoff log, or
  `docs/deployment/evidence`. `nonprod-deploy.yml` only redeploys dev on a
  `publish/v*`/tag push, not on a `dev` merge, so a deploy cannot be assumed.
- [ ] **NOT CHECKED** — tested hosted commit ancestry: no deployed SHA is
  recorded to compare against.

### Contract-To-UI Difference
- [ ] **NOT CHECKED** — no authenticated Portfolio Book core/holdings/
  positions/attribution responses are attached anywhere in the repo or task
  record.
- [ ] **NOT CHECKED** — no UI-vs-API count comparison exists.
- [ ] **NOT CHECKED** — no filter/reload evidence exists.
- [ ] **NOT CHECKED** — no capital-scope (paper/canary/live/unknown) hosted
  evidence exists.

### Runtime Truth
- [ ] **FAIL** — I sampled `services/runtime-manager/main.py`,
  `services/runtime-manager/service.py`, `services/persona/*.py`,
  `services/telemetry/*.py`, and `services/control-plane/bff/main.py` for a
  persona/broker/ledger/capital-scope/telemetry reconciliation or repair
  path. None exists. The only "quarantine" hits in `runtime-manager` are the
  unrelated kill-switch corrupt-snapshot recovery path. The only
  "reconcil*" hits are unrelated systems (order/fill reconciliation,
  OpenClaw agent reconciliation, persona tracking readback reconciliation).
  There is no code that traces a missing persona-capital binding to its
  runtime/deployment-plan/capital-pool/artifact/bootstrap path, and no code
  that repairs or quarantines it with an audit trail.
- [ ] **NOT CHECKED** — no reconciliation report exists (searched
  `docs/deployment/evidence` and the full `dev` history since
  2026-07-11T00:00Z for any added file matching `reconcil*` or
  `MGMT-OPS-003*`; only the two sidecar handoff docs and the task-brief
  docs exist).
- [ ] **NOT CHECKED** — no unresolved-record incident/quarantine trail
  exists to sample.

### Hosted Browser Evidence
- [ ] **NOT CHECKED** — no desktop/mobile screenshots.
- [ ] **NOT CHECKED** — no console/network failure counts.
- [ ] **NOT CHECKED** — no cold-navigation/lazy-chunk evidence.
- [ ] **NOT CHECKED** — no strict-live-mode fallback-data check.

## 3. Verdict

**`REQUEST_CHANGES`**

Per the task's own `review_contract.approval_forbidden_without`, approval is
blocked because none of the following exist: `deployed_sha_ancestry`,
`authenticated_api_capture`, `desktop_and_mobile_hosted_evidence`,
`ui_to_api_count_and_label_comparison`, `console_and_network_failure_counts`.
Per `review_contract.request_changes_when`, `evidence_is_mock_only_or_stale`
applies — there is no evidence at all, mock or otherwise, for the
reconciliation/repair scope.

The submitted work is a legitimate, narrowly-useful bug fix
(`_resolve_param`), but it does not implement any part of the task's actual
required scope: tracing missing persona-capital bindings, repairing or
quarantining them with an auditable reason, normalizing broker/paper-ledger/
canary-sleeve/live-capital-pool identity propagation, restoring telemetry
coverage or publishing explicit incidents for uncovered runtimes, or keeping
downstream attribution blocked until required joins are trustworthy. Per
`AI_COLLABORATION_GUIDE.md` §5, "Reviewers must not approve based only on
unit tests, a green PR, a successful deployment job, or the existence of a
rendered page" — the handoff message offered exactly that (green PR + unit
tests) and nothing else.

## 4. Required Changes For The Owner

1. Implement the actual reconciliation/repair path: trace each of the
   hosted-baseline missing-binding holdings (10 at last recorded baseline)
   and telemetry-uncovered runtimes (4 of 6) to runtime, deployment plan,
   capital pool, artifact, and bootstrap/reconciliation identity; repair
   where authoritative identifiers agree, quarantine with an auditable
   reason otherwise. No row may be dropped to improve counters.
2. Normalize broker / paper-ledger / canary-sleeve / live-capital-pool
   identity propagation from runtime creation through telemetry and
   Portfolio Book, per the task's `capital_scope` requirement.
3. Add contract tests covering normal, missing, stale, quarantined, and
   repaired paths (existing tests cover normal/stale/degraded read
   projection only — they do not exercise repair or quarantine because no
   such code path exists yet).
4. Make reconciliation idempotent with an audit trail (run id, before/after
   issue codes, disposition, reason, evidence refs) — see the sidecar
   packet's §4 "Reconciliation Disposition Contract" for the field set the
   parent report should expose.
5. Produce a reconciliation report accounting for every hosted
   missing-binding/telemetry-gap row.
6. Capture authenticated before/after BFF evidence (runtime count,
   telemetry-runtime count, degraded rows, missing bindings, broker
   identity, capital scope) after the fix is deployed — note that
   `nonprod-deploy.yml` does not auto-redeploy dev on a `dev` merge; a
   deploy dispatch (human-approved `workflow_dispatch`) is required before
   this evidence can be captured, so plan for that dependency explicitly
   rather than assuming the merge alone updates the hosted environment.
7. Once repaired/deployed, resubmit for review with the completed
   `REVIEWER_CHECKLIST.md` items backed by the evidence above; a reviewer
   must independently sample raw runtime/binding/telemetry records, not just
   aggregate counters.

## 5. Verification Performed By This Review

```bash
git log origin/dev --oneline --since="2026-07-11T00:00:00" | grep -iE "GAP-002|reconcil"
git show f16bc8b14 --stat            # PR #3192 sole implementation commit
grep -rn "quarantine" services/runtime-manager services/telemetry services/persona services/control-plane/bff --include="*.py"
grep -rn "reconcil" services/control-plane/bff/main.py services/runtime-manager/*.py services/persona/*.py services/telemetry/*.py
find docs/deployment/evidence -iname "*mgmt-ops-003*" -o -iname "*gap-002*"
```

No reconciliation, repair, or quarantine implementation was found for
persona-capital binding or telemetry identity; no evidence artifacts were
found for this task.
