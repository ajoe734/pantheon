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
are converged across `docker-compose.yml` and `docker-compose.exec.yml`. All five profiles are
executable and discoverable via `docker compose config --profiles`, providing a unified
activation matrix while preserving root default owners, dependency closure, and execution isolation.

| Profile | Services & Composition | Activation Boundary & Supported Matrix |
|---|---|---|
| `core` | Postgres, NATS, public Caddy/Operator BFF edge, projectors, lifecycle benchmark harness (`lifecycle-projector-capacity-benchmark`) | Dev, ephemeral staging, and VM-1 control; never execution VM |
| `workers` | Existing schedulers, consumers, reconcilers, and agora projection worker (`source-ingest-agora-projector`) | Dev; conditional staging; control singletons except research-owned workers |
| `research` | Core research APIs always active; dormant ML smoke units (`mlflow`, `finrl`, `qlib`, `rllib`, `ray-tune`, `trl`, `experiments`) opt-in | Dev APIs only; dormant framework units remain opt-in under `research` / `dormant-smoke` |
| `management-ai` | OpenClaw gateway, data initializer (`openclaw-data-init`), adapter, and e2e smoke (`openclaw-activation-ready-e2e`) | Dev; conditional staging; read-only control posture; activates with `management-ai` or `openclaw` |
| `execution` | Dev paper topology (`pantheon-paper-runtime`), VM-2 execution stack (`docker-compose.exec.yml` with `pantheon-lean-live`) | Dev paper topology has no live broker authority; isolated VM-2 stack never co-activates with control |

These profiles preserve existing `depends_on: condition: service_healthy` relationships across
profile boundaries in the dev mono-stack. Default unprofiled root deployment continues to start all
required loop services without regression. Durable volumes, owner URLs, identity/token references,
and safe write defaults are strictly preserved. In particular, core startup does not depend on MinIO,
and MinIO server/init and minio-data are intentionally preserved; object-store retirement remains
owned by `OSS-OBJECT-STORE-CUTOVER-002`.

## Image identity and limits

Use the immutable observed image ID/repository digest as deployed identity;
source tags are not deployed-version evidence. Pin external images only when
maintained upstream provenance and compatibility are independently established.
The existing root deployment and accepted release controller remain the
delivery boundary. No hosted inspection, deployment, restart, data migration,
secret read, or production mutation is authorized by this profile inventory.
