# Runtime profile baseline

Status: source contract and current-dev observation (2026-10-04)

## Authority and observed baseline

`docs/plans/system-simplification-20260906/profile-contract.json` is the approved
profile contract. The current-dev baseline is the coordinator's read-only
inventory referenced in
`docs/deployment/evidence/OSS-INFRA-PROFILES-002/evidence.json`; it is not a
reconstructed inventory from source tags. At 2026-10-04T05:55:36Z it reported
57 containers (54 running, 3 exited), including actual image IDs and available
repository digests, source labels, Compose config hashes/files and mount
destinations. Exact provenance remains unknown wherever the observation omitted
a source label.

The separately accepted controller evidence binds the served Pantheon backend
`5743158b02ad5e463d4cba19d79521071daf4778` and execute-plans frontend
`7d01b1ae125246b952120ffd718b1df3ec242769`. Newer source is not evidence of the
served pair. The observed historical `COMPOSE_PROFILES` value is absent;
retain it as unknown. FE `deploymentProfile: operator-live` is not a Compose
profile selector.

## Profile composition and activation matrix

The five canonical Compose profiles (`core`, `workers`, `research`, `management-ai`, `execution`)
plus the canonical deployment profile (`root`) are converged across `docker-compose.yml` and
`docker-compose.exec.yml`. All profiles are executable and discoverable via `docker compose config --profiles`,
providing a decoupled activation matrix while preserving root deployment, dependency closure, and execution isolation.

| Profile | Count | Services & Composition | Activation Boundary & Supported Matrix |
|---|---|---|---|
| `core` | 29 | Postgres, NATS, public Caddy/Operator BFF edge, telemetry, core stores/APIs (benchmark harness `lifecycle-projector-capacity-benchmark` remains opt-in) | Dev, ephemeral staging, and VM-1 control; never execution VM |
| `workers` | 16 | Background schedulers, consumers, reconcilers, and agora projection worker (`source-ingest-agora-projector` gated by `source-ingest-scheduler` health) | Dev; conditional staging; control singletons except research-owned workers |
| `research` | 4 | Core research APIs (`research-orchestrator-svc`, `research-worker-gateway-svc`, `training-session-svc`, `policy-learning-svc`); dormant ML smoke units remain opt-in under dedicated `dormant-smoke` profile | Dev APIs only; dormant framework smoke units are isolated under `dormant-smoke` |
| `management-ai` | 3 | OpenClaw gateway, data initializer (`openclaw-data-init`), adapter; e2e smoke remains opt-in under dedicated `openclaw-activation-ready-e2e` profile | Dev; conditional staging; read-only control posture; activates with `management-ai` or `openclaw` |
| `execution` | 5 | Dev paper topology (runtime-manager, broker, capital, paper-signal-producer, paper-fleet-reconciler; `static-paper-runtime` remains opt-in), VM-2 execution stack (`docker-compose.exec.yml`; `pantheon-lean-live` remains opt-in) | Dev paper topology has no live broker authority; isolated VM-2 stack never co-activates with control |
| `root` | 56 | Full canonical nonprod deployment profile containing all persistent services and loop workers across core, workers, research, management-ai, and execution | Canonical nonprod VM deploy selector (`scripts/deploy_nonprod_vm.sh`), preserving complete twelve-loop closure |

Cross-profile `depends_on` conditions declare `required: false` so that individual business profiles
resolve and start cleanly without activating unrequested services, while full dependency satisfaction
and `condition: service_healthy` checks remain enforced when dependencies are present (e.g. under `root`).
Durable volumes, owner URLs, identity/token references, and safe write defaults are strictly preserved.
In particular, core startup does not depend on MinIO, and MinIO server/init and minio-data are intentionally preserved;
object-store retirement remains owned by `OSS-OBJECT-STORE-CUTOVER-002`.

## Image identity and limits

Use the immutable observed image ID/repository digest as deployed identity;
source tags are not deployed-version evidence. Pin external images only when
maintained upstream provenance and compatibility are independently established.
The existing root deployment and accepted release controller remain the
delivery boundary. No hosted inspection, deployment, restart, data migration,
secret read, or production mutation is authorized by this profile inventory.
