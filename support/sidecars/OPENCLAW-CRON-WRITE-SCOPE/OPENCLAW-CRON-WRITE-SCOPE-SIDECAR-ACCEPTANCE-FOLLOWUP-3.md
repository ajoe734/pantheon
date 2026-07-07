# OPENCLAW-CRON-WRITE-SCOPE Sidecar Acceptance Follow-up 3

**Sidecar Task ID**: `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-3`
**Parent Task**: `OPENCLAW-CRON-WRITE-SCOPE`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-07-04

> Scope constraint: this is support material only. It does not edit canonical
> truth, L1 policy, runtime contracts, router/governance implementation, the
> OpenClaw gateway adapter, cron registrar code, or supervisor cadence. The
> parent owner decides whether this packet is absorbed into
> `OPENCLAW-CRON-WRITE-SCOPE` closeout.

---

## 1. What Changed Since Follow-up 2

Follow-up 2 (`support/sidecars/OPENCLAW-CRON-WRITE-SCOPE/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`)
recorded PR #2837 as **open, unmerged, stale against `dev`, and carrying
unrelated diff noise**. That has changed:

| Field | Follow-up 2 read | Current read (this packet) |
|---|---|---|
| PR #2837 state | `OPEN`, checks green, stale base | **`MERGED`** into `dev` at `2026-07-04T05:36:51Z` |
| PR #2837 diff shape | "includes unrelated deletions / changes outside the cron-scope deliverable" | Merged diff is exactly 4 files, **+286/-0**, no deletions: `.orchestrator/task-briefs/openclaw_cron_write_scope.md`, `docs/runbooks/openclaw-adapter-device-pairing.md`, `scripts/openclaw-approve-adapter-cron-scope.sh`, `scripts/openclaw-cron-write-scope-smoke.sh` |
| Scripts/runbook location | only on `origin/task/OPENCLAW-CRON-WRITE-SCOPE` | now on `origin/dev` (confirmed via `git show origin/dev:<path>`) |

This resolves Follow-up 2's §6 "PR freshness risk" concern: the branch was
refreshed/scope-cleaned before merge rather than merged as-is.

Confirmed via:

```bash
gh pr view 2837 --json mergeCommit,mergedAt,files,additions,deletions,changedFiles
# mergedAt: 2026-07-04T05:36:51Z, changedFiles: 4, additions: 286, deletions: 0

git fetch origin dev
git show origin/dev:scripts/openclaw-approve-adapter-cron-scope.sh   # present
git show origin/dev:scripts/openclaw-cron-write-scope-smoke.sh       # present
git show origin/dev:docs/runbooks/openclaw-adapter-device-pairing.md | tail -80
# includes the new "Follow-up: cron.* WRITE methods need a second scope
# upgrade (operator.admin)" section
```

---

## 2. Current Parent State (status root, not this worktree's copy)

Read from `$PANTHEON_STATUS_ROOT/ai-status.json` (per
[[reference_pantheon_status_root]] this is the live status file, not the
per-task worktree's stale copy):

| Field | Current value |
|---|---|
| Owner / reviewer | `Claude` / `Codex` |
| Status | `blocked` |
| `waiting_for` | `Human/Ops` |
| `last_update` | `2026-07-03T14:11:35Z` — **predates** the PR #2837 merge (`2026-07-04T05:36:51Z`); the parent's own status entry has not yet been refreshed to reflect the merge |

The parent's `next` field (unchanged since before merge) already documents
*why* it is blocked on a human rather than an agent: a prior worker attempt to
directly run the privileged `openclaw devices approve <requestId>` grant was
blocked by the harness's own safety classifier as an agent-inferred permission
grant on a shared gateway device. That reasoning still holds after the merge —
merging the diagnostic/approval scripts does not itself grant the scope; only
running them (as a deliberate human/operator action) does.

No source, runtime, registry, governance, router, or cron implementation file
was read for the purpose of changing it in this packet — only for verification.

---

## 3. Has The Privileged Step Actually Run?

Searched `$PANTHEON_STATUS_ROOT/ai-activity-log.jsonl` for any record of the
approval or smoke script actually executing (not just being read/reviewed):

```bash
grep -c "openclaw-cron-write-scope-smoke\|openclaw-approve-adapter-cron-scope" ai-activity-log.jsonl
# 9 matches, all PR-review / file-read / merge-check activity
# (e.g. "gh pr view 2837 ...", "gh api ... contents/scripts/openclaw-approve-adapter-cron-scope.sh")
```

**No matching log entry shows the scripts being executed against a live
gateway** (no `cron.add`/`cron.list`/`cron.remove` output, no
`device.pair.approve` result, no smoke-script stdout). Acceptance criterion 1
in the parent's own acceptance list therefore remains unproven as of this
packet, consistent with the parent's own `status: blocked` /
`waiting_for: Human/Ops`.

---

## 4. Acceptance Checklist (parent's own acceptance array, verbatim order)

| # | Parent acceptance item (verbatim from `ai-status.json`) | Disposition |
|---|---|---|
| 1 | `cron.add` via adapter proxy returns `status: ok` with a job id (not scope/pairing error) | **BLOCKED** — no evidence of a live run since PR merge (§3). Requires Human/Ops to run `bash scripts/openclaw-approve-adapter-cron-scope.sh`, then `bash scripts/openclaw-cron-write-scope-smoke.sh`. |
| 2 | Full BFF path: creating a persona registers its 4 OODA cron jobs in `cron.list` (not `dry_run`) | **BLOCKED ON #1** — this needs the scope grant in place first; no evidence it has been attempted. |
| 3 | Scope survives `openclaw-data` volume / gateway container recreate (evidence: re-add after recreate) | **BLOCKED ON #1** — the merged runbook documents the approval as reproducible after a volume rebuild (rerun the approval script), but that reproducibility itself has not yet been demonstrated live. |
| 4 | Existing tests stay green; no docker-exec-from-BFF; no supervisor cadence change | **PASS (read-only re-verification this packet)** — see §5. Design still uses `AdapterCronRuntime` over HTTP, not docker exec; no supervisor cadence file is touched by PR #2837. |

Only item 4 can be closed by an automated worker; items 1–3 are explicitly
live-only per the parent brief (`.orchestrator/task-briefs/openclaw_cron_write_scope.md`
§"驗收（唯一標準：live，非 mock）") and require the Human/Ops privileged grant
first.

---

## 5. Verification Evidence (read-only, no source files touched)

```bash
PYTHONPATH="$PWD/services/control-plane/cron:$PWD/services/control-plane/router" \
  python3 -m pytest \
    services/control-plane/cron/test_cron.py \
    services/control-plane/router/test_main.py \
    services/control-plane/cron/test_persona_cron_registrar.py -q
# 40 passed in 6.97s
```

This re-confirms Follow-up's original finding (21 + 19 = 40 passing) still
holds after PR #2837 merged into `dev` — no regression introduced by the
diagnostic/runbook/script addition.

---

## 6. Dependency Map

### Blocking dependency (now the only one)

| Dependency | Current state | Why it matters |
|---|---|---|
| Human/Ops privileged grant | **Still pending** | `bash scripts/openclaw-approve-adapter-cron-scope.sh` must be run by an operator against the live gateway. This is deliberately not automatable: OpenClaw's own operator-scopes model requires explicit human approval for a device scope upgrade, and the harness's safety classifier independently blocks an agent from inferring/self-granting it. |

Follow-up 2's PR-merge-readiness blocker is now resolved (§1) — it should be
removed from the parent's open-blocker list once the parent's own
`ai-status.json` entry is refreshed.

### Runtime/config dependencies (unchanged from Follow-up 2, still accurate)

| Dependency | Expected value / behavior |
|---|---|
| `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` in BFF | Points to `http://openclaw-gateway-adapter:8104` in compose so BFF persona creation reaches the adapter cron proxy. |
| Adapter gateway URL/token | `OPENCLAW_GATEWAY_URL` / `OPENCLAW_GATEWAY_TOKEN` configured in the adapter container; token alone is insufficient without device scope approval. |
| `openclaw-adapter-data` volume | Persists the adapter device identity; normal adapter container recreate should not require re-pairing. |
| `openclaw-data` volume | Persists gateway-side device/scope state. Per the merged runbook, a wipe re-triggers a fresh pending scope request automatically; only the approval step must be rerun by an operator. |

### Downstream/adjacent work (unchanged)

| Task / surface | Relationship |
|---|---|
| `OPENCLAW-PERSONA-CRON-BACKFILL` brief | Should wait for this scope grant if it wants the BFF/adapter route rather than gateway-container-local writes. |
| `reconcile_persona_ooda_cron.py` | Useful for existing personas once scope is fixed; separate from creation-time acceptance. |

---

## 7. Suggested Live Verification Sequence (unchanged in substance from Follow-up 2, now unblocked on merge)

1. Human/Ops runs the privileged grant (now available on `dev`, no longer
   only on the task branch):

   ```bash
   bash scripts/openclaw-approve-adapter-cron-scope.sh
   ```

2. Prove adapter-proxy write access:

   ```bash
   OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
     bash scripts/openclaw-cron-write-scope-smoke.sh
   ```

3. Prove creation-time BFF wiring (full persona-create path, 4 cron jobs,
   `cron_registration_mode=gateway_rpc`).

4. Recreate `openclaw-gateway` / `openclaw-gateway-adapter` containers without
   deleting volumes and rerun step 2 to prove container-recreate durability.

5. If parent acceptance criterion 3 is read as requiring survival across an
   `openclaw-data` **volume wipe** (not just container recreate), record that
   the current design requires rerunning step 1 after a wipe — the runbook
   states this is reproducible, but reproducibility itself still needs a live
   demonstration, not just documentation.

---

## 8. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| That `OPENCLAW-CRON-WRITE-SCOPE` is complete | Parent owner, after live proof |
| That Human/Ops has performed the scope-approval grant | Human/Ops |
| That acceptance criteria 1–3 have been demonstrated live | Parent owner, after the grant and smoke run |
| That the parent's `ai-status.json` entry has been refreshed to reflect the PR #2837 merge | Parent owner (`Claude`) |
| That this packet supersedes Follow-up or Follow-up 2 | Both remain accurate historical record of prior state; this packet only updates what changed |

---

## 9. Handoff

**To**: `Claude`
**From**: `Claude2`
**Requested review outcome**: Approve this sidecar if it accurately reflects
that PR #2837 has merged cleanly (resolving Follow-up 2's freshness concern)
while the substantive blocker — the Human/Ops privileged scope grant and its
live proof — remains open and unclaimed by this packet.

Recommended reviewer checks:

1. Confirm PR #2837 merge state and diff shape (§1) against current GitHub
   state.
2. Confirm no evidence exists yet of the approval/smoke scripts having been
   run live (§3), so acceptance items 1–3 are correctly left BLOCKED rather
   than claimed PASS.
3. Consider refreshing the parent's own `ai-status.json` `last_update` /
   `next` fields to note the merge, since that is parent-owner state this
   sidecar must not edit directly.
