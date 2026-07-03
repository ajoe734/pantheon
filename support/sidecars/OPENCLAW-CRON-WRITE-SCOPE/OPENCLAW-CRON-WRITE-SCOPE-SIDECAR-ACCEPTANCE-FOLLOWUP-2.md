# OPENCLAW-CRON-WRITE-SCOPE Sidecar Acceptance Follow-up 2

**Sidecar Task ID**: `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-2`
**Parent Task**: `OPENCLAW-CRON-WRITE-SCOPE`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-07-03

> Scope constraint: this is support material only. It does not edit canonical
> truth, L1 policy, runtime contracts, router/governance implementation, the
> OpenClaw gateway adapter, cron registrar code, or supervisor cadence. The
> parent owner decides whether this packet is absorbed into
> `OPENCLAW-CRON-WRITE-SCOPE` closeout.

---

## 1. Current Read

This follow-up uses the live status root (`$PANTHEON_STATUS_ROOT/ai-status.json`)
for dispatch truth because the per-worktree `ai-status.json` copy does not yet
contain the current parent/helper entries. No status file was edited by this
packet.

Current parent state:

| Field | Current read |
|---|---|
| Parent | `OPENCLAW-CRON-WRITE-SCOPE` |
| Owner / reviewer | `Claude` / `Codex` |
| Status | `blocked` |
| Waiting for | `Human/Ops` |
| Real scope | Approve or otherwise provide cron-write scope for the adapter device so BFF -> adapter persona OODA cron registration can call upstream `cron.add` live. |

Current helper state:

| Field | Current read |
|---|---|
| Helper | `OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-2` |
| Owner / reviewer | `Codex2` / `Codex` |
| Status | `in_progress` |
| Artifact | `support/sidecars/OPENCLAW-CRON-WRITE-SCOPE/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md` |

Current PR/dependency read:

- PR #2837 (`task/OPENCLAW-CRON-WRITE-SCOPE`) is open, not merged, with green
  GitHub checks at the time this packet was written.
- `origin/dev` does not contain
  `scripts/openclaw-approve-adapter-cron-scope.sh` or
  `scripts/openclaw-cron-write-scope-smoke.sh`; those files exist only on
  `origin/task/OPENCLAW-CRON-WRITE-SCOPE`.
- `git diff origin/dev..origin/task/OPENCLAW-CRON-WRITE-SCOPE` shows the PR
  branch is stale relative to current `dev` and includes unrelated deletions /
  changes outside the cron-scope deliverable. Parent closeout should refresh or
  scope-clean the PR before merge.

---

## 2. Sources Used

| Source | Role |
|---|---|
| `$PANTHEON_STATUS_ROOT/ai-status.json` | Current parent/helper task state and parent acceptance list |
| `.orchestrator/task-briefs/openclaw_cron_write_scope.md` | Parent real-scope brief |
| `support/sidecars/OPENCLAW-CRON-WRITE-SCOPE/OPENCLAW-CRON-WRITE-SCOPE-SIDECAR-ACCEPTANCE.md` | Earlier sidecar; especially the scope-drift addendum |
| `services/openclaw-gateway-adapter/assistant_openclaw_provider.py` | Adapter `gateway_cron_call` whitelist and CLI gateway-call proxy |
| `services/openclaw-gateway-adapter/main.py` | `POST /api/openclaw-adapter/gateway/cron` adapter endpoint |
| `services/control-plane/cron/persona_cron_registrar.py` | BFF-compatible `AdapterCronRuntime` and four-workflow registration logic |
| `services/control-plane/bff/main.py` | Full BFF persona-create path calling `_try_register_persona_cron()` |
| `docker-compose.yml` | Adapter/gateway service names, ports, and `openclaw-data` / `openclaw-adapter-data` volumes |
| `docs/runbooks/openclaw-adapter-device-pairing.md` | Device pairing persistence model and PR #2837 cron-scope addendum |
| `origin/task/OPENCLAW-CRON-WRITE-SCOPE:scripts/openclaw-approve-adapter-cron-scope.sh` | Proposed privileged scope-upgrade helper, not yet merged |
| `origin/task/OPENCLAW-CRON-WRITE-SCOPE:scripts/openclaw-cron-write-scope-smoke.sh` | Proposed live cron.add/list/remove smoke, not yet merged |
| PR #2837 metadata from `gh pr view 2837` | Confirms open/unmerged PR state and green checks |

---

## 3. Acceptance Checklist

| Parent acceptance item | Current disposition | Evidence / required proof |
|---|---|---|
| `cron.add` via adapter proxy returns `status: ok` with a job id, not scope/pairing error | **BLOCKED** until PR #2837 is merged/refreshed and Human/Ops approves the adapter device scope | After the human grant, run `scripts/openclaw-cron-write-scope-smoke.sh`. The script must call `POST /api/openclaw-adapter/gateway/cron`, create a disabled probe job through `cron.add`, see it through `cron.list`, and clean it up through `cron.remove`. |
| Full BFF path creates a persona and registers its four OODA cron jobs in `cron.list`, not `dry_run` | **READY AFTER ITEM 1** | Create a persona through `POST /bff/personas` with live BFF configured with `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL`. Response meta should show `cron_registration_mode=gateway_rpc` and `cron_registered_count=4`; adapter `cron.list` should include the four `_job_name(workflow_id, persona_id)` jobs for `pantheon.ingest`, `pantheon.review`, `pantheon.retrain`, and `pantheon.deploy`. |
| Scope survives `openclaw-data` volume / gateway container recreate | **NEEDS OWNER DECISION** | Container recreate without deleting volumes should preserve the existing device approval. An `openclaw-data` volume wipe is different: PR #2837's helper is reproducible after a rebuild, but intentionally requires rerunning the privileged approval step. Parent owner/reviewer should decide whether acceptance means "survives normal gateway container recreate" or "can be restored by rerunning the explicit approval helper after volume wipe." |
| Existing tests stay green; no docker-exec-from-BFF; no supervisor cadence change | **SUPPORTED BY CURRENT DESIGN** | BFF uses `AdapterCronRuntime` over HTTP to the adapter, not docker exec. The proposed scripts use `docker exec` only as operator-side VM maintenance/smoke helpers. No supervisor cadence files are in scope. |

---

## 4. Dependency Map

### Blocking dependencies

| Dependency | Current state | Why it matters |
|---|---|---|
| Human/Ops privileged grant | Pending | `openclaw devices approve <requestId>` widens the adapter device scope. The parent task is correctly blocked because an auto worker should not silently grant a shared gateway device `operator.admin`. |
| PR #2837 merge readiness | Open, unmerged, checks green, stale against current `dev` | The parent scripts and runbook update are not on `dev`. The branch also includes unrelated stale diffs relative to current `dev`, so merging as-is risks deleting or changing unrelated current artifacts. |
| Running OpenClaw gateway + adapter | Required for live proof | Acceptance is explicitly live, not mock. The adapter default host port is `${OPENCLAW_GATEWAY_ADAPTER_PORT:-18104}` and the endpoint is `/api/openclaw-adapter/gateway/cron`. |

### Runtime/config dependencies

| Dependency | Expected value / behavior |
|---|---|
| `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` in BFF | Points to `http://openclaw-gateway-adapter:8104` in compose so BFF persona creation reaches the adapter cron proxy. |
| Adapter gateway URL/token | `OPENCLAW_GATEWAY_URL` and `OPENCLAW_GATEWAY_TOKEN` configured in the adapter container; token alone is insufficient without device approval. |
| `openclaw-adapter-data` volume | Persists the adapter device identity under `/root/.openclaw`; normal adapter container recreate should not require re-pairing. |
| `openclaw-data` volume | Persists gateway-side state. If wiped, gateway-side approval state may be lost and the explicit approval helper must be rerun unless parent chooses a different persistence design. |

### Downstream/adjacent work

| Task / surface | Relationship |
|---|---|
| `OPENCLAW-PERSONA-CRON-BACKFILL` brief | Should wait for the adapter write-scope path if it wants the BFF/adapter route rather than gateway-container-local writes. |
| Existing `reconcile_persona_ooda_cron.py` | Useful for existing personas after scope is fixed; it is not the same as full BFF creation-time acceptance. |
| Prior PRs #2812 and #2818 | Already merged foundations for adapter cron proxy and JSON parsing; this follow-up does not reopen them. |

---

## 5. Suggested Live Verification Sequence

After PR #2837 is refreshed/scope-cleaned and merged:

1. Human/Ops runs the privileged scope grant on the dev/staging VM:

   ```bash
   bash scripts/openclaw-approve-adapter-cron-scope.sh
   ```

2. Prove adapter-proxy write access, not gateway-container-local access:

   ```bash
   OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
     bash scripts/openclaw-cron-write-scope-smoke.sh
   ```

3. Prove creation-time BFF wiring:

   ```bash
   # Use the environment's normal authorized operator/admin token.
   curl -sS -X POST "$BFF_URL/bff/personas" \
     -H "Authorization: Bearer <operator-admin-token>" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: openclaw-cron-write-scope-$(date +%s)" \
     -d '{"name":"Cron Scope Smoke","archetype":"generalist","risk":"low"}' | jq .
   ```

   Required observation: response meta reports `cron_registration_mode` as
   `gateway_rpc` and `cron_registered_count` as `4`. Then verify adapter
   `cron.list` contains the four persona workflow jobs.

4. Recreate containers without deleting volumes and rerun item 2:

   ```bash
   docker compose up -d --force-recreate openclaw-gateway openclaw-gateway-adapter
   OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
     bash scripts/openclaw-cron-write-scope-smoke.sh
   ```

5. If parent acceptance explicitly requires an `openclaw-data` volume wipe,
   record that the current proposed design is not automatic after a wipe: rerun
   the explicit Human/Ops approval helper, then rerun item 2. If "no reapproval
   after volume wipe" is required, PR #2837 is not sufficient by itself.

---

## 6. Review Risks / Questions

1. **PR freshness risk**: PR #2837 contains the needed scripts but is stale
   against `dev` and includes unrelated diff noise. It should be refreshed
   before merge.
2. **Acceptance wording risk**: "scope survives openclaw-data volume rebuild"
   currently conflicts with the proposed manual approval model if interpreted
   as "no operator reapproval after gateway-state wipe." If interpreted as
   "the approval process is reproducible after rebuild," PR #2837 aligns.
3. **BFF acceptance gap**: The cron-write smoke proves adapter proxy write
   authority, but not the full persona-create path. The parent still needs a
   separate BFF-create proof with `gateway_rpc` and four jobs.

---

## 7. Non-Claims

This packet does not claim:

| Non-claim | Correct owner |
|---|---|
| That `OPENCLAW-CRON-WRITE-SCOPE` is complete | Parent owner after live proof and review |
| That Human/Ops approval has been performed | Human/Ops |
| That PR #2837 is merge-ready as currently based | Parent owner |
| That OpenClaw should auto-approve future adapter scope upgrades | Parent owner / Human/Ops policy |
| That existing persona cron backfill is complete | Separate backfill task |

---

## 8. Handoff

**To**: `Codex`
**From**: `Codex2`
**Requested review outcome**: Approve this sidecar if it accurately separates
the parent task's live acceptance gates, current PR/Human-Ops blockers, and
the volume-rebuild semantic question without changing canonical truth.

Recommended reviewer checks:

1. Confirm this packet is support-only and does not imply parent completion.
2. Confirm the PR #2837 dependency notes match current GitHub/branch state.
3. Confirm the acceptance item 3 ambiguity is worth sending back to the parent
   owner before final closeout.
