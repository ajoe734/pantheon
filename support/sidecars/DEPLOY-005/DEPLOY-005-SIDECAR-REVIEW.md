# DEPLOY-005 Review Packet

**Sidecar kind:** `review_packet`  
**Sidecar task:** `DEPLOY-005-SIDECAR-REVIEW`  
**Helper parent:** `DEPLOY-005` - single-VM deployment bootstrap, migrations, and runbook  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex2`  
**Sidecar reviewer:** `Codex`  
**Prepared by:** `Codex2`  
**Date:** `2026-04-18`  
**Packet status:** `sidecar review approved; owner finalize pending`

> Scope constraint: support artifact only. This packet does not modify canonical truth, deployment
> policy, runtime contracts, or the parent implementation. It packages the current `DEPLOY-005`
> review state from durable task history plus the repo snapshot after reviewer approval.

---

## 1. Purpose

This sidecar exists to make the already-approved `DEPLOY-005` review state easy to consume:

1. restate the durable parent-task truth and current lifecycle position
2. summarize the review issues that were raised and the fixes that landed
3. point to the repo-local evidence that now satisfies the review gate
4. hand a compact packet to `Codex` without changing the parent task or canonical docs

---

## 2. Parent Task Truth

From [ai-status.json](/home/edna/code/pantheon/ai-status.json:230), `DEPLOY-005` is currently:

- owner: `Claude`
- reviewer: `Codex2`
- status: `review_approved`
- dependencies: `DEPLOY-001`, `DEPLOY-002`, `DEPLOY-003`, `DEPLOY-004`
- required artifacts:
  - `.env.example`
  - `scripts/bootstrap.sh`
  - `scripts/db_migrate.sh`
  - `docs/deployment/single-vm-runbook.md`
- accepted outcomes:
  - `bash scripts/bootstrap.sh` can bring up a fresh single-VM stack
  - migrations run without hidden errors
  - `.env.example` covers all required service variables

The durable handoff record in [ai-status.json](/home/edna/code/pantheon/ai-status.json:489) shows
the final reviewer disposition:

> Review approved: DB provisioning in `scripts/bootstrap.sh` now fails loudly without
> `2>/dev/null || true`; `minio-init` exists in `docker-compose.control.yml`; single-vm runbook
> health endpoints match compose/service definitions. Bash syntax checks pass. Ready for owner
> finalization to done.

This sidecar does not re-open or widen that disposition. It packages it.

---

## 3. Review History Summary

### 3.1 Initial review findings raised by the parent reviewer

The durable handoff history for `DEPLOY-005` records two concrete review rounds before approval:

1. the first review found that `scripts/bootstrap.sh` and the runbook claimed bucket bootstrap via
   `minio-init`, but `docker-compose.control.yml` did not define that service yet; the same review
   also flagged incorrect manual health endpoints for `capital` and `evolution`
2. the second review found hidden DB provisioning failure suppression in `scripts/bootstrap.sh`
   through `2>/dev/null || true`, which would have allowed bootstrap to continue into migrations
   after provisioning errors

Those findings matter because the accepted outcome for `DEPLOY-005` is not merely "scripts exist";
the bootstrap path must fail loudly on real infra/database errors and the runbook must reflect the
actual deployed service surface.

### 3.2 Reviewer-confirmed fixes now present

The repo snapshot aligns with the fixes described in the handoff log:

- [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:67) now defines
  `minio-init` and uses `mc mb --ignore-existing`
- [scripts/bootstrap.sh](/home/edna/code/pantheon/scripts/bootstrap.sh:107) invokes
  `docker compose ... run --rm minio-init` directly with no `|| true` suppression
- [scripts/bootstrap.sh](/home/edna/code/pantheon/scripts/bootstrap.sh:128) runs DB role/database
  provisioning with `psql -v ON_ERROR_STOP=1`, and the previous stderr suppression is absent
- [docs/deployment/single-vm-runbook.md](/home/edna/code/pantheon/docs/deployment/single-vm-runbook.md:134)
  separates `/__health__` services from the `/health` endpoints used by `capital` and `evolution`

---

## 4. Current Evidence Snapshot

### 4.1 Environment contract coverage exists

[.env.example](/home/edna/code/pantheon/.env.example:1) now provides grouped configuration for:

- Postgres app/superuser variables and service DSNs
- MinIO credentials and artifact bucket settings
- NATS ports and URL
- BFF degraded-mode/runtime-manager integration variables
- persona LLM backend selection and API keys
- service ports/data dirs for telemetry, evaluation, feedback, memory, registry, optimizer,
  promotion, incidents, postmortems, capital, evolution, and lineage-read

That is consistent with the parent handoff claim that the file covers the single-VM service surface.

### 4.2 Bootstrap now reflects a strict four-step bring-up

[scripts/bootstrap.sh](/home/edna/code/pantheon/scripts/bootstrap.sh:54) implements the expected
single-VM flow:

1. start infra services and wait for health
2. create the MinIO bucket through `minio-init`
3. provision DB role/database and run `scripts/db_migrate.sh`
4. start all application services and fail if any service is not healthy

Important review-sensitive details now present in the script:

- no failure suppression around MinIO bucket creation
- no failure suppression around DB role/database provisioning
- final unhealthy-service detection still exits non-zero instead of printing a soft warning

### 4.3 Migration helper is idempotent and narrow

[scripts/db_migrate.sh](/home/edna/code/pantheon/scripts/db_migrate.sh:14) derives its DSN from
`TELEMETRY_DB_DSN` or `DATABASE_URL`, and
[scripts/db_migrate.sh](/home/edna/code/pantheon/scripts/db_migrate.sh:26) applies three idempotent
telemetry DDL statements:

- `telemetry_events`
- `idx_telemetry_events_created_at`
- `idx_telemetry_events_event_type`

That matches the current documented migration surface in the runbook rather than claiming a broader
schema state than the script actually enforces.

### 4.4 Runbook and compose health surfaces now match

Repo-local evidence is internally consistent:

- [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:100) uses
  `/__health__` for telemetry and the other standard service set
- [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:197) and
  [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:226) use
  `/health` for BFF and persona
- [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:394) and
  [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:417) use
  `/health` for `capital` and `evolution`
- [docs/deployment/single-vm-runbook.md](/home/edna/code/pantheon/docs/deployment/single-vm-runbook.md:125)
  now tells operators to check those same endpoint families manually

---

## 5. Verification Run For This Sidecar

The following repo-local verification was run while preparing this packet:

```bash
bash -n scripts/bootstrap.sh
bash -n scripts/db_migrate.sh
rg -n 'minio-init|/__health__|/health|capital|evolution' \
  docker-compose.control.yml docs/deployment/single-vm-runbook.md scripts/bootstrap.sh
```

Observed outcomes:

- both shell scripts pass syntax validation
- `minio-init` exists in the control-plane compose and is invoked by bootstrap
- runbook health-check instructions now match the compose health endpoints for `capital` and
  `evolution`

What this sidecar did not attempt:

- it did not run a full `bash scripts/bootstrap.sh` against a fresh Docker environment
- it did not perform live container health validation
- it did not change the parent task state or re-review the parent beyond packaging existing evidence

Those remaining live checks stay with the parent owner/finalization path.

---

## 6. Parent Review Gate Assessment

Based on the durable handoff history plus the current repo snapshot, the parent review gate appears
internally consistent:

| Review question | Evidence | Result |
|---|---|---|
| Does the compose actually define `minio-init`? | [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:67) | Yes |
| Can bucket creation fail loudly instead of being ignored? | [scripts/bootstrap.sh](/home/edna/code/pantheon/scripts/bootstrap.sh:107) | Yes |
| Can DB provisioning fail loudly instead of being ignored? | [scripts/bootstrap.sh](/home/edna/code/pantheon/scripts/bootstrap.sh:128) | Yes |
| Do documented health endpoints match service definitions? | [docs/deployment/single-vm-runbook.md](/home/edna/code/pantheon/docs/deployment/single-vm-runbook.md:125), [docker-compose.control.yml](/home/edna/code/pantheon/docker-compose.control.yml:394) | Yes |
| Do the parent artifacts all exist? | repo snapshot | Yes |
| Has the parent already passed reviewer approval? | [ai-status.json](/home/edna/code/pantheon/ai-status.json:272), [ai-status.json](/home/edna/code/pantheon/ai-status.json:489) | Yes |

This does not prove the entire single-VM bootstrap was executed in this sidecar pass. It does show
that the review objections recorded in durable state are now resolved in the checked-in files.

---

## 7. Reviewer Handoff Note

For `Codex` as sidecar reviewer:

1. verify this file stayed within support-artifact scope only
2. confirm the packet accurately represents the durable state: parent `DEPLOY-005` is already
   `review_approved` and waiting on owner finalization
3. confirm the repo snapshot still supports the recorded review approval reasons
4. approve this sidecar if it is a faithful review packet; do not use it to mutate the parent task

Suggested sidecar disposition:

- approve the sidecar if this packet accurately packages the review evidence
- leave the parent task `DEPLOY-005` lifecycle untouched; only the parent owner should finalize it
  from `review_approved` to `done`

---

## 8. Sidecar Scope Declaration

This file is the only artifact created by this sidecar pass.

- no canonical L1/L2 truth was edited
- no parent implementation file was modified
- no global summary/history file was edited manually
- parent-task absorption remains a parent-owner decision

---

*Generated by Codex2 as a sidecar `review_packet` helper for `DEPLOY-005`. This file is a support artifact and does not modify canonical truth.*
