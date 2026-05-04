# DEPLOY-008 Review Packet

**Sidecar kind:** `review_packet`  
**Sidecar task:** `DEPLOY-008-SIDECAR-REVIEW`  
**Helper parent:** `DEPLOY-008` - VM-2 execution-plane compose split  
**Parent owner:** `Codex`  
**Parent reviewer:** `Claude`  
**Sidecar reviewer:** `Claude`  
**Prepared by:** `Codex`  
**Date:** `2026-04-18`  
**Packet status:** `review approved and finalized`

> Scope constraint: support artifact only. This packet does not modify canonical truth, deployment
> policy, runtime contracts, or the parent implementation. It packages the already-completed
> `DEPLOY-008` review state plus a fresh repo-local evidence check.

---

## 1. Purpose

This sidecar exists to make the completed `DEPLOY-008` review state easy to consume:

1. restate the archived parent-task truth and reviewer-approved disposition
2. summarize the repo-local evidence that still matches that disposition
3. record the one important support-artifact drift: the earlier acceptance packet is now historical
4. preserve the reviewer-approved packet as a compact post-completion handoff without reopening the parent task

---

## 2. Durable Parent Truth

The parent task is no longer active in `ai-status.json`; it is archived as completed in
[DEPLOY-008.json](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:1).

From that archive snapshot:

- `DEPLOY-008` was archived at
  [lines 4-6](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:4) with
  `terminal_status=done` and `terminal_outcome=completed`
- owner/reviewer/dependency and accepted artifact set are recorded at
  [lines 11-25](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:11)
- the final owner closeout message at
  [lines 45-47](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:45) states that
  `docker-compose.exec.yml`, `env/prod-exec.env.example`, and
  `docs/deployment/exec-vm-secrets-guide.md` now define the VM-2 execution-only slice
- the recorded reviewer disposition at
  [lines 48-49](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:48) confirms:
  - `docker-compose.exec.yml` contains `runtime-manager`, `pantheon-lean-paper`,
    broker/exchange adapters, and `signal-store`
  - no BFF / registry / governance control-plane services appear in the VM-2 compose
  - health endpoints consistently use `/__health__`
  - the env template and secrets guide cover the VM-2 execution boundary
  - `pantheon-lean-live` lacks `signal-store` dependency and URL, but because it is locked behind
    `profiles: live`, that was accepted as non-blocking
- the delivery record at
  [lines 52-75](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:52) ties the
  completed parent task to commit `824ca7c855b2ebcaeed8bc3b554de5072fbb5a21`
- the final review flow is preserved at
  [lines 77-95](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:77):
  implementation handoff from `Codex` to `Claude`, then review approval from `Claude` back to
  `Codex` for finalization

This sidecar does not change that truth. It packages it.

---

## 3. Current Repo-Local Evidence Snapshot

### 3.1 The VM-2 compose still matches the archived review notes

[docker-compose.exec.yml](/home/lupin/code/pantheon/docker-compose.exec.yml:1) still presents
itself as the dedicated VM-2 execution-plane stack and explicitly excludes control-plane services at
[lines 6-10](/home/lupin/code/pantheon/docker-compose.exec.yml:6).

The service set still matches the approved scope:

- [signal-store](/home/lupin/code/pantheon/docker-compose.exec.yml:15)
- [runtime-manager](/home/lupin/code/pantheon/docker-compose.exec.yml:25)
- [broker-adapter](/home/lupin/code/pantheon/docker-compose.exec.yml:45)
- [exchange-adapter](/home/lupin/code/pantheon/docker-compose.exec.yml:71)
- [pantheon-lean-paper](/home/lupin/code/pantheon/docker-compose.exec.yml:97)
- [pantheon-lean-live behind `profiles: ["live"]`](/home/lupin/code/pantheon/docker-compose.exec.yml:130)

All default healthchecks in this file still target `/__health__`:

- [runtime-manager](/home/lupin/code/pantheon/docker-compose.exec.yml:38)
- [broker-adapter](/home/lupin/code/pantheon/docker-compose.exec.yml:64)
- [exchange-adapter](/home/lupin/code/pantheon/docker-compose.exec.yml:90)
- [pantheon-lean-paper](/home/lupin/code/pantheon/docker-compose.exec.yml:123)
- [pantheon-lean-live](/home/lupin/code/pantheon/docker-compose.exec.yml:152)

Fresh command output from this sidecar pass:

```bash
docker compose --env-file env/prod-exec.env.example -f docker-compose.exec.yml config --services
```

Observed service list:

- `runtime-manager`
- `broker-adapter`
- `exchange-adapter`
- `signal-store`
- `pantheon-lean-paper`

That output still excludes BFF, registry, governance, telemetry, persona, and other VM-1 services.

### 3.2 The VM-2 env template still covers the execution-only variable surface

[env/prod-exec.env.example](/home/lupin/code/pantheon/env/prod-exec.env.example:1) still scopes
itself to the dedicated VM-2 stack and excludes VM-1 services at
[lines 5-7](/home/lupin/code/pantheon/env/prod-exec.env.example:5).

It covers:

- execution infra and runtime-manager variables at
  [lines 12-19](/home/lupin/code/pantheon/env/prod-exec.env.example:12)
- paper-runtime bootstrap settings at
  [lines 21-29](/home/lupin/code/pantheon/env/prod-exec.env.example:21)
- broker/exchange adapter ports, modes, and raw secret placeholders at
  [lines 31-48](/home/lupin/code/pantheon/env/prod-exec.env.example:31)
- execution-scoped secret-name metadata at
  [lines 50-56](/home/lupin/code/pantheon/env/prod-exec.env.example:50)

That remains consistent with the archived reviewer note that the env template covers VM-2 execution
variables without leaking VM-1 service configuration into this file.

### 3.3 The secrets guide still documents the VM-2 boundary correctly

[docs/deployment/exec-vm-secrets-guide.md](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:1)
still defines the execution-only secret boundary:

- VM-2-only secret ownership at
  [lines 16-26](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:16)
- env-file creation and minimum replacement set at
  [lines 28-47](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:28)
- nonprod secret naming precedent and rules at
  [lines 49-66](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:49)
- local injection and VM-local file usage at
  [lines 68-90](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:68)
- verification commands and acceptance bar at
  [lines 129-148](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:129)

This still matches the archived reviewer claim that broker / exchange credentials belong only on
VM-2 and that the guide describes the injection flow clearly.

---

## 4. Review Drift Note: The Acceptance Companion Is Now Historical

The companion
[DEPLOY-008-SIDECAR-ACCEPTANCE.md](/home/lupin/code/pantheon/support/sidecars/DEPLOY-008/DEPLOY-008-SIDECAR-ACCEPTANCE.md:1)
was written on `2026-04-17` before the parent implementation landed.

That packet accurately captured the pre-implementation state at the time:

- it explicitly said the sidecar existed to make `DEPLOY-008` reviewable *before the parent
  implementation starts* at
  [lines 20-25](/home/lupin/code/pantheon/support/sidecars/DEPLOY-008/DEPLOY-008-SIDECAR-ACCEPTANCE.md:20)
- it recorded the parent as `status: todo` at
  [lines 31-45](/home/lupin/code/pantheon/support/sidecars/DEPLOY-008/DEPLOY-008-SIDECAR-ACCEPTANCE.md:31)
- it documented the three required artifacts as absent at
  [lines 102-111](/home/lupin/code/pantheon/support/sidecars/DEPLOY-008/DEPLOY-008-SIDECAR-ACCEPTANCE.md:102)
- it therefore marked most acceptance checks as pending at
  [lines 125-157](/home/lupin/code/pantheon/support/sidecars/DEPLOY-008/DEPLOY-008-SIDECAR-ACCEPTANCE.md:125)

That support packet is now stale relative to current durable truth, because the parent task was
implemented, reviewed, and archived `done` afterwards. Reviewer guidance for this sidecar should
therefore be:

- treat the acceptance companion as a historical pre-implementation intake packet
- use the archive snapshot plus the current repo files as the source of truth for post-completion
  review packaging
- do not "fix" the older acceptance packet in this sidecar, because its purpose was historical and
  support-only

---

## 5. Non-Blocking Limitation That Remains True

The one limitation called out by the archived reviewer still appears intentional and unchanged:

- [pantheon-lean-live](/home/lupin/code/pantheon/docker-compose.exec.yml:130) exists only behind the
  optional `live` profile
- the archived review note at
  [DEPLOY-008.json:48](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:48) records
  that missing `signal-store` dependency and URL as non-blocking for `DEPLOY-008`
- later dual-VM documentation still frames
  [`pantheon-lean-paper` as a bootstrap harness](/home/lupin/code/pantheon/docs/deployment/dual-vm-acceptance-results.md:17)
  rather than final per-pool LEAN packaging, and explicitly says that this baseline does not yet
  prove the final live execution loop at
  [lines 17-25](/home/lupin/code/pantheon/docs/deployment/dual-vm-acceptance-results.md:17)

This is consistent with the completed parent scope: `DEPLOY-008` established the VM-2 execution
slice and paper bootstrap baseline, not the final live runtime package.

---

## 6. Sidecar Assessment

Based on the archived parent truth plus this fresh repo-local check, the review packet appears
internally consistent:

| Review question | Evidence | Result |
|---|---|---|
| Is the parent task already completed rather than merely ready for implementation? | [DEPLOY-008.json](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:4) | Yes |
| Do the three parent artifacts still exist? | [docker-compose.exec.yml](/home/lupin/code/pantheon/docker-compose.exec.yml:1), [env/prod-exec.env.example](/home/lupin/code/pantheon/env/prod-exec.env.example:1), [exec-vm-secrets-guide.md](/home/lupin/code/pantheon/docs/deployment/exec-vm-secrets-guide.md:1) | Yes |
| Does the current default compose surface stay execution-only? | `docker compose ... config --services`; [docker-compose.exec.yml](/home/lupin/code/pantheon/docker-compose.exec.yml:6) | Yes |
| Do the current files still support the archived reviewer rationale? | archive review note plus current file contents | Yes |
| Is there any support-artifact drift that must be called out? | [DEPLOY-008-SIDECAR-ACCEPTANCE.md](/home/lupin/code/pantheon/support/sidecars/DEPLOY-008/DEPLOY-008-SIDECAR-ACCEPTANCE.md:20) vs. [DEPLOY-008.json](/home/lupin/code/pantheon/ai-task-archive/tasks/DEPLOY-008.json:45) | Yes |

The only notable drift is in support material, not in canonical or implementation truth.

---

## 7. Recorded Reviewer Disposition

This sidecar has already passed review in the task lifecycle.

- assigned sidecar reviewer: `Claude`
- recorded approval state: `review_approved`
- recorded approval summary: `Sidecar review packet verified: DEPLOY-008 is archived done at commit 824ca7c, all three VM-2 artifacts exist and match archived review rationale, older acceptance companion correctly documented as historical pre-implementation context, support-only scope respected throughout.`

Owner closeout for this sidecar is limited to:

1. finalize `DEPLOY-008-SIDECAR-REVIEW` from `review_approved` to `done`
2. keep the parent `DEPLOY-008` archived `done` state untouched
3. leave the older acceptance companion as historical support context only

---

## 8. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no canonical L1/L2 truth was edited
- no deployment/runtime implementation file was modified
- no parent task state was changed by hand
- the older acceptance companion was left untouched and documented as historical context only

---

*Generated by Codex as a sidecar `review_packet` helper for `DEPLOY-008`. This file is a support artifact and does not modify canonical truth.*
