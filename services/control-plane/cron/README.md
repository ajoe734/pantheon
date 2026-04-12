# Pantheon Governed Cron Workflows

This package implements `OC-002`: the governed cron wrapper that Pantheon uses to
register and run upstream OpenClaw workflows without turning this repo into a local
OpenClaw rewrite.

## Scope

- upstream source: `https://github.com/openclaw/openclaw`
- integration mode: separate runtime / service dependency
- local responsibility: define versionable workflow manifests, attach governance
  context, validate workflow handoffs, and route deploys through canonical
  `DeploymentPlan` planning

## Workflow Catalog

| Workflow | Purpose | Execution Context | Output |
|---|---|---|---|
| `pantheon.ingest` | Discover approved research inputs and emit governed intake handoffs | `research` | `research_package` handoff |
| `pantheon.review` | Package candidate review into an approval request instead of auto-approving | `paper` | `approval_request` handoff |
| `pantheon.retrain` | Trigger batch retraining from governed feedback / datasets | `research` | `registry_submission` handoff |
| `pantheon.deploy` | Create a governed `DeploymentPlan` for `paper`, `canary`, `live`, or `frozen` | `paper`, `live`, or `status` | deployment-plan-backed deployment request |

## Governance Guarantees

- cron workflows always use `channel=cron` / `role=system`
- only declared tool classes are exposed to each workflow
- deploy never calls LEAN directly; it must create a first-class DeploymentPlan and execution projection first
- review creates approval packages, but does not self-approve
- manifests are explicit JSON envelopes that can be pinned, audited, and replayed

## Runtime Pinning

The upstream runtime pin is carried in every prepared dispatch envelope:

- `repository_url`
- `release_tag`
- `commit_sha`
- `image_ref`

Use real pinned values when wiring a live transport. The smoke test uses a fixed
fake pin to keep the local path deterministic.

## Local Commands

```bash
python3 services/control-plane/cron/smoke_test.py
python3 -m unittest discover -s services/control-plane/cron -p 'test_*.py'
python3 services/control-plane/cron/cli.py --workflow pantheon.ingest --payload-file payload.json
```

## Relationship To Adjacent Tasks

- `OC-001`: provides the deny-first permission model used by the workflow catalog
- `OC-003`: provides `StrategySpec` and `WorkflowHandoff` schemas validated here
- `GOV-001`: deploy requires `ApprovalDecision` alignment before planning
- `DEP-001`: deploy creates `DeploymentPlan` before emitting any deployment request
- `RS-001`: ingest produces governed intake handoffs that research workflows can consume
