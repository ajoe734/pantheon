# System Architecture — Pre-shutdown Gap Closure

Status: proposed target architecture

Baseline: `origin/dev` at `4889e498fbe5c3b87e7a66b3ca19897e030bbcc1`

Companion documents: [GAP_REPORT.md](GAP_REPORT.md), [SD.md](SD.md)

## 1. Objective

Converge the delivered 2026-09-01 baseline and the four residual implementation
branches into one operable system while making host reconstruction a committed,
repeatable development-tooling capability.

The target must preserve three independent truth planes:

1. product runtime truth from product APIs and stores;
2. development-task truth from the V2 TaskStore; and
3. deployment truth from exact hosted release identity.

Health in one plane never proves health in another.

## 2. Non-goals

- No frontend source is added to Pantheon.
- No product BFF endpoint may manipulate repositories, task packets, workers,
  worktrees, or the canonical task journal.
- No automatic production deployment, credential minting, secret rotation, or
  capital-affecting operation.
- No reintroduction of Yahoo/Anue scraping through a fallback alias.
- No second task store, scheduler, merge owner, BFF composition root, or queue.
- No reconstruction of task truth from `ai-status.json`.

## 3. Architecture invariants

1. `services/control-plane/bff/main.py` composes routers and cross-cutting
   middleware; domain behavior belongs to domain routers/services.
2. Release gates consume typed readiness contracts, not arbitrary health keys.
3. Optional providers may degrade without blocking release unless explicitly
   declared required.
4. External data connectors are schedulable only when build support and source
   authorization are established.
5. Public ingress is operator opt-in; a worker has no standing publication
   authority.
6. Telemetry replay requires a caller-supplied operator credential and never
   upgrades a service credential.
7. SQLite corruption recovery treats the main file, WAL, and SHM as one
   recoverable family.
8. Supervisor Authority V2 runs from an immutable command root and uses one
   authoritative external journal.
9. Runtime bootstrap may create an empty genesis journal only when no journal
   event exists; it may not import a derived projection.
10. A branch, commit, test result, release tag, and hosted deployment are
    separate evidence states.

## 4. Target architecture

```text
                       delivery control plane
    Git branch -> PR/review/checks -> dev -> release -> hosted manifest
                            |                         |
                            | exact SHA               | exact SHA
                            v                         v
 development control plane                 product runtime plane
 signed packet / Human-Ops                 operator BFF composition
             |                             /       |         \
        V2 TaskStore                 Agora     Research   Management ...
             |                             \       |         /
 singleton supervisor                     typed readiness contracts
      /      |      \                               |
 dispatch  worker  recovery                    deploy gate

 source-ingestion plane
 licensed/official catalog -> active-universe scheduler -> distillation queue
             |                                              |
     unsupported sources off                    DB + WAL + SHM recovery family

 optional operator actions
 operator token -> telemetry replay
 operator decision -> dashboard public tunnel
```

## 5. Component ownership

| Concern | Canonical owner | Architectural rule |
|---|---|---|
| BFF assembly | `services/control-plane/bff/main.py` | composition and middleware only |
| domain HTTP behavior | domain `router.py` and `service.py` | no duplicate inline handlers |
| release readiness | `readiness_release_contract.py` | declared fields only |
| Agora operational facts | `agora/operational_readiness.py` | live binding with typed degradation |
| external source support | connector definitions and active universe | build-disabled sources cannot schedule |
| distillation queue recovery | `distillation_worker.py` | quarantine database family |
| telemetry replay orchestration | `scripts/bootstrap.sh` | caller credential, bounded environment forwarding |
| public tunnel | dashboard scripts plus permission broker | opt-in and no standing worker grant |
| task authority | V2 TaskStore | journal is canonical; projections are derived |
| runtime promotion | supervisor promotion scripts | clean immutable command root |
| host reconstruction | runtime bootstrap scripts | idempotent and machine-neutral |

## 6. Architecture decisions

### ADR-PSD-01 — finish the router migration; do not preserve dual ownership

The V3 BFF cleanup is accepted only if the old handler bodies and `read_store`
production path are deleted in the same delivery that proves router parity.
A forwarding facade or copied legacy block would preserve ambiguity and is not
the target architecture.

### ADR-PSD-02 — readiness is contract driven

The release gate reads a versioned set of required readiness fields. Optional
provider health is diagnostic unless named by that contract. Missing required
evidence fails closed; unknown diagnostic keys do not acquire gating authority.

### ADR-PSD-03 — deny unverified egress by construction

A connector marked `DISABLED_BY_BUILD` cannot enter the active schedule or be
advertised as a quota fallback. Inert catalog templates may remain for future
operator configuration but do not constitute permission to poll a source.

### ADR-PSD-04 — privileged replay authority comes from the operator

Bootstrap can forward an operator token supplied by its caller. It cannot mint
or transform a service token into operator authority. Absence means the
best-effort step is skipped; rejection of a supplied token is a real error.

### ADR-PSD-05 — queue recovery is family-atomic

SQLite main, WAL, and SHM files share one recovery identifier. Quarantine names
must prevent reopening at the original path from attaching any prior sidecar.
Receipts identify what existed and what moved without treating missing optional
sidecars as failure.

### ADR-PSD-06 — host bootstrap reconstructs mechanisms, not historical truth

The repository may recreate directories, command roots, local bridge keys,
watchdog units, and an empty authoritative journal. Historical task events must
come from the original journal or an explicitly verified backup. Derived status
files are never accepted as a canonical recovery source.

## 7. Security and authority boundaries

| Operation | Required authority | Forbidden substitute |
|---|---|---|
| open dashboard tunnel | explicit operator decision | worker standing grant |
| telemetry DLQ replay | operator/admin bearer token | telemetry service token |
| schedule external source | supported build state plus approved source contract | placeholder URL |
| merge implementation | repository PR/check/review path | local commit only |
| mutate development tasks | canonical local tooling | product BFF route |
| seed empty task journal | explicit recovery source and empty journal | stale `ai-status.json` |

Credentials must not appear in command output, process arguments beyond the
bounded container environment contract, evidence files, or Git history.

## 8. Failure semantics

| Failure | Required result |
|---|---|
| BFF route parity changes unexpectedly | block integration and emit inventory diff |
| optional provider unavailable | degrade diagnostics; do not block unless required |
| required readiness evidence missing/unverified | block release |
| unsupported connector selected | configuration/test failure before scheduling |
| tunnel decision absent | remain loopback/private; do not publish |
| telemetry token absent | visible replay skip; continue bootstrap |
| supplied telemetry token rejected | stop bootstrap with sanitized error |
| WAL/SHM quarantine partially fails | fail recovery; retain diagnostic state |
| prior canonical journal exists | genesis tool refuses to write |
| promoted runtime identity differs | health fails closed; do not claim supervisor ready |

## 9. Deployment architecture

Implementation branches land independently through exact-head PR review. The
accepted `dev` SHA is then published and promoted. Hosted acceptance reads the
deployment manifest and verifies the BFF/runtime identities actually served;
the newest remote SHA alone is not deployment evidence.

The rebuilt development supervisor may dispatch and integrate work, but its
health does not prove that the BFF, ingestion, frontend, or release is healthy.

## 10. Architecture acceptance

- One BFF composition root and one owner per route.
- One V2 task authority and one supervisor singleton.
- One release contract determining required readiness.
- Unsupported sources are structurally unschedulable.
- Public ingress and privileged replay remain operator-controlled.
- Queue recovery covers the complete SQLite file family.
- Runtime layout can be recreated under a new host home.
- Every accepted capability is traceable from commit to PR, `dev`, release,
  hosted identity, and relevant smoke evidence.
