# Development Tooling, Product Runtime, and Delivery Boundary

## Purpose

Pantheon has three operational domains. They may exchange evidence, but they do
not share authority. This document records the existing boundary; it does not
add a service, state machine, gate, credential, or compatibility path.

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
`scripts/ai_status.py`, and `scripts/human-ops-status.sh`.

Product login, product control mode, and product readiness are not prerequisites
for local canonical task maintenance. A product-hosted assistant dev-bridge
route is only a transport adapter into this domain. It is not canonical task
authority, and its availability is not required for direct local Human/Ops
maintenance.

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
3. Dev-bridge endpoints may submit signed development requests, but canonical
   validation and materialization remain in development tooling.
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
| Dispatch and worker state | V2 supervisor runtime and durable event queue |
| Development-tooling process health | `scripts/supervisor_runtime_health.py` |
| Backend lifecycle readiness | `scripts/wait_for_bff_lifecycle_readiness.py` |
| Hosted product scenarios | `scripts/verify_hosted_scenarios.py` |
| Hosted frontend serving | `scripts/verify_e2e_fe_serving.py` |
| Exact deployed versions | hosted deployment manifest and deployment checks |

Generated evidence manifests are audit artifacts. Their existence, status
text, or task metadata must never be used as the product readiness evaluator.
