# Development Tooling, Product Runtime, and Delivery Boundary

## Purpose

Pantheon has three operational domains. They may exchange evidence, but they do
not share authority. This document records the existing boundary; it does not
add a service, state machine, gate, credential, or compatibility path.

The machine-readable, human-auditable ownership contract is
`docs/02-architecture/component-boundary.yaml`. It records each component's
domain, runtime requirement, removal condition, and cross-domain direction.

## 1. Development tooling control plane

Development tooling turns engineering tasks into reviewed source changes.

It owns:

- canonical development task lifecycle and assignment in V2 TaskStore;
- supervisor planning, durable dispatch intents, worker leases, and delivery;
- auto-worker execution and review records;
- local Human/Ops maintenance through `scripts/human-ops-status.sh` and
  `scripts/ai_status.py`;
- development-tooling health and dispatch evidence.

Primary components are `.orchestrator/`, `ai-task-archive/`,
`scripts/ai_status.py`, and `scripts/human-ops-status.sh`. The local task-packet
transport lives at `.orchestrator/development_bridge/`; it is excluded from the
product container build context.

There is no hosted development ingress. The product BFF neither defines nor
imports task-packet, dev-doc, supervisor-status, or repair-worktree modules.
It does not receive a development bridge signing key, a task-state mount, or a
repair-worktree mount. Product deployment also does not start, stop, provision,
or health-check the supervisor or dashboard.

Product login, product control mode, and product readiness are not prerequisites
for local canonical task maintenance. There is no product-hosted development
bridge route. Local Human/Ops maintenance and local task-packet transport are
the only development-task ingress paths.

Development-tooling success means that tasks can be stored and dispatched. It
does not mean that the product has been built, deployed, or accepted.

## 2. Product runtime

Product runtime serves the user-facing and business behavior of Pantheon.

It owns:

- business and operator BFF APIs;
- source ingestion, lifecycle projection, and domain services;
- product data contracts and product readiness;
- the hosted `execute-plans` frontend and its interaction with the BFF.

Primary components are `services/` business services, the business-facing BFF
routes, and the separate `ajoe734/execute-plans` repository.

Product readiness must be derived from live service readiness, product data
contracts, real scenarios, and the exact deployed frontend/backend pair. A
task marked done, a healthy supervisor, or a repository evidence file cannot
substitute for those checks.

## 3. Delivery infrastructure

Delivery infrastructure installs exact source identities and records what is
actually hosted.

It owns:

- build and deployment workflows;
- immutable source and configuration identities;
- deployment manifests, switch evidence, and bounded rollback;
- post-deploy probes against the deployed frontend and backend.

Primary components are `.github/workflows/`, deployment scripts, and hosted
deployment manifests. Delivery infrastructure consumes source from the first
two domains but does not become their authority.

## Cross-boundary rules

1. Report development-tooling health, product health, and deployment status as
   separate results. Never collapse them into one `healthy` or `done` claim.
2. Local canonical task mutation uses local Human/Ops tooling. Do not route it
   through product authentication or product control mode.
3. Local development tooling may submit task packets, but canonical validation
   and materialization remain in development tooling.
4. Product acceptance requires exact hosted identities plus live product
   readiness and scenario verification. Static evidence-presence checks are
   insufficient.
5. Deployment success proves only that an exact candidate was installed and
   passed the declared hosted probes. It does not rewrite canonical tasks.
6. A supervisor restart or task-board cleanup must not be presented as a
   product deployment. A product deployment must not be presented as evidence
   that auto-workers are running.

## Existing acceptance entry points

| Concern | Authoritative entry point |
| --- | --- |
| Canonical development task maintenance | `scripts/human-ops-status.sh` / `scripts/ai_status.py` |
| Dispatch and worker state | V2 supervisor runtime state (`queue.events` plus worker leases) |
| Development-tooling process health | `scripts/supervisor_runtime_health.py` |
| Backend lifecycle readiness | `scripts/wait_for_bff_lifecycle_readiness.py` |
| Hosted product scenarios | `scripts/verify_hosted_scenarios.py` |
| Hosted frontend serving | `scripts/verify_e2e_fe_serving.py` |
| Exact deployed versions | hosted deployment manifest and deployment checks |

Generated evidence manifests are audit artifacts. Their existence, status
text, or task metadata must never be used as the product readiness evaluator.

## Removing development tooling after product release

Development tooling is intentionally optional at product runtime. Product
completion does not require deleting it from the same commit. When the team is
ready to remove it, use the `removal_order` in the boundary manifest:

1. archive/close engineering tasks and confirm there are no workers, leases, or
   queued intents;
2. disable the supervisor/watchdog and remove `.orchestrator/`,
   `scripts/ai_status.py`, `scripts/human-ops-status.sh`, and
   `ai-task-archive/`;
3. build and deploy the product runtime without any development-tooling paths;
   and
4. rerun live product readiness, hosted scenarios, and exact identity checks.

The BFF business APIs, source ingestion, lifecycle projector, product services,
frontend, and delivery acceptance remain. The local dev bridge is removed with
development tooling because it transports engineering work; it is not a
product API. Historical task/review evidence may be retained as an archive,
but it is not loaded by the product runtime.
