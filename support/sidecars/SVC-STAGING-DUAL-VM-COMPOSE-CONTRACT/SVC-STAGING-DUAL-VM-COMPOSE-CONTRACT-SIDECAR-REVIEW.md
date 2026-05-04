# SVC-STAGING-DUAL-VM-COMPOSE-CONTRACT Sidecar Review Packet

Task: `SVC-STAGING-DUAL-VM-COMPOSE-CONTRACT-SIDECAR-REVIEW`
Parent task: `SVC-STAGING-DUAL-VM-COMPOSE-CONTRACT`
Prepared by: Codex2
Reviewer: Claude
Date: 2026-04-30
Helper kind: `review_packet`
Mutates canonical truth: no

## Scope

This packet is support material only. It does not change canonical truth, L1
policy, runtime-manager behavior, registry logic, governance implementation, or
compose/runtime implementation. The parent owner decides whether and how to use
this packet during the parent review.

This sidecar intentionally stayed inside the task-scoped context and parent
diff. It did not inspect `current-work.md` or the full `ai-activity-log.jsonl`.

## Parent Review Snapshot

Parent task status at packet preparation:

| Field | Value |
|---|---|
| Parent task | `SVC-STAGING-DUAL-VM-COMPOSE-CONTRACT` |
| Status | `review` |
| Owner | `Codex` |
| Reviewer | `Claude` |
| Phase | Deployment / Staging Topology |

Parent handoff summary says the parent is ready for review after:

- labelling root `docker-compose.yml` as the dev single-VM baseline
- hardening `docker-compose.control.yml` as the VM1 control stack with
  governance/deployment surfaces and VM2 runtime-manager URLs
- keeping broker/exchange secrets and execution services out of VM1
- wiring `docker-compose.exec.yml` as the VM2 execution stack
- routing VM2 paper-runtime telemetry back to VM1
- adding `scripts/validate_split_topology.sh`

## Evidence Summary

| Acceptance area | Evidence observed in parent diff |
|---|---|
| Default compose is dev single-VM baseline | `docker-compose.yml` adds `x-pantheon-compose-contract.topology: dev-single-vm-baseline` and states staging-live uses the split control/exec compose files. The validator requires root services including `operator-bff`, `runtime-manager`, `governance`, `deployment`, `telemetry`, and `signal-store`. |
| VM1 control compose excludes execution and broker scope | `docker-compose.control.yml` adds `x-pantheon-compose-contract.topology: staging-vm1-control-plane` and documents `secret_boundary: no-broker-or-exchange-secrets`. The validator forbids `runtime-manager`, broker/exchange adapters, paper runtime, `pantheon-lean-live`, `signal-store`, and `router` in VM1. |
| VM1 includes or fences control-plane surfaces | VM1 adds `governance` and `deployment` services, keeps BFF/telemetry/persona/registry/incidents/postmortems/capital/evolution/lineage-read, and wires BFF service URLs to local VM1 surfaces where available. |
| VM1 runtime-manager access is externalized to VM2 | `env/prod-control.env.example` sets `PANTHEON_INTERNAL_API_URL` and `PANTHEON_RUNTIME_MANAGER_URL` to `http://10.140.0.5:28081`; `operator-bff` and `telemetry` are validated against that URL. |
| Broker secrets stay out of VM1 | The validator forbids common broker/exchange/API secret keys in rendered VM1 service environments. `env/prod-control.env.example` carries runtime-manager tokens, not broker or exchange credentials. |
| VM2 execution compose excludes BFF/control services | `docker-compose.exec.yml` adds `x-pantheon-compose-contract.topology: staging-vm2-execution-plane` and `control_plane_boundary: no-bff-or-governance-services`. The validator requires runtime-manager, broker adapter, exchange adapter, paper runtime, and signal-store, while forbidding BFF/governance/telemetry/registry/persona/control surfaces. |
| VM2 telemetry points back to VM1 | `env/prod-exec.env.example` sets `PANTHEON_TELEMETRY_URL=http://10.140.0.4:38083`; the validator checks `pantheon-paper-runtime` uses that value. |
| Docs align with code | `docs/deployment/staging-live-topology.md` now records the dev baseline, VM1/VM2 compose contracts, runtime-manager internal URL, VM1 telemetry ingest URL, broker credential placement, and the split-topology verification command. |

## Verification Run For This Packet

Commands run from `/home/lupin/code/pantheon` on 2026-04-30:

```bash
docker compose -f docker-compose.yml config --quiet
docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml config --quiet
docker compose --env-file env/prod-exec.env.example -f docker-compose.exec.yml config --quiet
bash -n scripts/validate_split_topology.sh && bash scripts/validate_split_topology.sh
```

Results:

- root compose config passed with exit 0
- VM1 control compose config passed with exit 0
- VM2 exec compose config passed with exit 0
- split topology validator passed and printed:

```text
ok  dev single-VM and staging dual-VM compose contract validated
```

## Reviewer Checklist

Recommended Claude review focus for this sidecar:

| Check | Expected answer |
|---|---|
| Did this sidecar avoid canonical/runtime implementation edits? | Yes. It only adds this support packet. |
| Does the packet summarize parent acceptance evidence without promoting it to canonical truth? | Yes. |
| Does the packet preserve the parent owner/reviewer authority? | Yes. Parent owner `Codex` and parent reviewer `Claude` retain the parent disposition. |
| Is VM1/VM2 split evidence traceable to concrete files and validator checks? | Yes. Evidence is tied to compose/env/docs/script surfaces. |
| Are verification commands bounded and reproducible? | Yes. They render compose config and run the split-topology validator without building or starting containers. |

## Non-Claims

This packet does not claim:

| Non-claim | Correct disposition |
|---|---|
| The parent task is approved or done. | Parent remains in `review` until its assigned reviewer acts. |
| The split topology has been deployed on the actual staging VMs during this sidecar. | This packet records local compose-render validation only. |
| Runtime-manager auth, broker execution safety, or production BFF read-store cutoff is complete. | Those belong to their own implementation and review lanes. |
| L1 architecture or policy documents changed. | This sidecar makes no canonical truth edits. |
| Claude sidecar approval replaces parent closeout. | Claude reviews this support packet and the parent task separately; parent owner `Codex` still owns parent closeout. |

## Handoff

To: `Claude`
From: `Codex2`
Requested review outcome: approve this sidecar if it is an accurate,
support-only review packet for `SVC-STAGING-DUAL-VM-COMPOSE-CONTRACT`.

Recommended reviewer disposition:

1. Approve if the packet accurately summarizes the parent review evidence and
   remains support-only.
2. Request changes only for wording, missing evidence, or mismatches in this
   support packet.
3. Do not treat this packet as approval of the parent task; parent review stays
   with Claude unless reassigned in `ai-status.json`.
