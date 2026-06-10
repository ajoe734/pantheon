# Non-Prod Development Workflow

Status date: 2026-06-08

## Purpose

Pantheon no longer uses the original single VM as both the editor workstation
and the dev runtime. Dev and staging-live are separated now, so agents must not
infer runtime ownership from where VS Code is connected.

This document is the operating rule for humans and LLM agents working in the
non-prod environments.

CI/CD automation for these environments is tracked in
`docs/deployment/nonprod-ci-cd.md`.

## Current Roles

| Role | VM | Compose project | Use |
| --- | --- | --- | --- |
| Workstation / orchestration | usually the existing VS Code VM | none required | edit code, run local checks, push/sync changes |
| Dev runtime | `pantheon-lupin-dev` | `pantheon` | dev backend smoke and integration checks |
| Staging VM1 | `pantheon-lupin-staging-control` | `pantheon-control` | staging-live control plane, BFF, telemetry, governance |
| Staging VM2 | `pantheon-lupin-staging-exec` | `pantheon-exec` | staging-live execution, broker, exchange adapter, TWS/IBKR |

VS Code does not need to connect directly to `pantheon-lupin-dev`. It may stay on
the existing workstation VM. What changed is where runtime commands should be
executed.

## Hard Rules

- Do not run the root `pantheon` compose project on `pantheon-lupin-staging-control`.
- Do not run `pantheon-exec` on `pantheon-lupin-staging-control`.
- Do not put broker secrets, TWS credentials, or live broker state on the
  workstation or frontend.
- Do not treat the workstation checkout as the active dev backend unless a task
  explicitly says the dev stack has been moved back.
- Do not treat Lovable publish state as Pantheon dev frontend hosting or
  acceptance evidence.
- Do not rebuild staging-live from a dirty worktree unless the operator
  explicitly asks for an emergency direct patch.
- Staging-live should consume a verified commit or a clearly documented hotfix,
  not whatever happens to be open in an editor.

## Normal Backend Workflow

1. Edit code on the workstation or in the local checkout the operator is using.
2. Run fast local checks on the workstation when possible.
3. Sync or push the verified change to `pantheon-lupin-dev`.
4. Rebuild/restart the relevant dev services on `pantheon-lupin-dev`.
5. Run dev smoke checks against `pantheon-lupin-dev`.
6. Promote only verified commits or explicitly approved patches to staging VM1.
7. Rebuild/restart staging VM1 only when the change is intended for
   staging-live.

## Dev VM Commands

Check dev runtime:

```bash
gcloud compute ssh lupin@pantheon-lupin-dev --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && docker compose ps && curl -fsS http://127.0.0.1:18001/health'
```

Rebuild only the dev BFF after a BFF change:

```bash
gcloud compute ssh lupin@pantheon-lupin-dev --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && PANTHEON_ENV=dev PANTHEON_LIVE_BROKER_ENABLED=false docker compose up -d --build operator-bff'
```

Dev must keep live broker scope disabled:

```env
PANTHEON_ENV=dev
PANTHEON_LIVE_BROKER_ENABLED=false
```

## Staging VM1 Commands

Check staging control/BFF:

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-control --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon && docker compose -f docker-compose.control.yml ps && curl -fsS http://127.0.0.1:38001/health'
```

Check VM1 to VM2 runtime-manager reachability:

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-control --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'curl -fsS http://10.50.0.21:28081/__health__'
```

Staging-live may enable live broker scope only through the staging control
stack:

```env
PANTHEON_ENV=staging-live
PANTHEON_LIVE_BROKER_ENABLED=true
```

## Staging VM2 Commands

Check execution/broker runtime:

```bash
gcloud compute ssh lupin@pantheon-lupin-staging-exec --zone=asia-east1-b --project=pantheon-benjamin-20260528 -- \
  'cd /home/lupin/code/pantheon-ep5 && docker compose -p pantheon-exec -f docker-compose.exec.yml ps && curl -fsS http://127.0.0.1:28081/__health__'
```

## Frontend / Execute-Plans

Current Pantheon dev frontend work must follow:

- `docs/frontend/execute-plans-dev-hosting.md`

Summary:

- active frontend repo: `ajoe734/execute-plans`
- local checkout: `/home/lupin/code/execute-plans` or a clean task worktree
- do not use `front-ai-trading-system` for new frontend work
- do not use Lovable publish status as dev frontend acceptance

Current browser-reachable HTTPS BFF URLs:

- dev FE: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- dev BFF: `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io`
- staging-live: `https://pantheon-lupin-staging-bff.104.155.223.192.sslip.io`

Do not point any browser frontend at GCP internal IPs or unsecured
`http://<external-ip>:<port>` endpoints. Staging-live Lovable context remains in
`docs/deployment/lovable-dev-staging-operating-rules.md`, but that file does
not override the current dev frontend rule above.

## LLM Agent Checklist

Before changing deployment, runtime, BFF, frontend, Lovable, or broker behavior:

- read this file
- read `docs/frontend/execute-plans-dev-hosting.md` for dev frontend work
- read `docs/deployment/staging-live-topology.md`
- identify which VM owns the runtime you are touching
- state whether you are editing workstation files, dev runtime files, staging
  VM1 files, or staging VM2 files
- verify health on the target VM after restart
- leave staging-live untouched unless the user explicitly asked for a staging
  change
