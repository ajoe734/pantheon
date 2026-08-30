# System Architecture: Full Product Operation Gap Remediation (2026-08-30)

## 1. Architectural Objectives & Boundary Rules

### 1.1 Development Tooling vs. Product Runtime Boundary
- **Development Tooling Authority**: Owns task lifecycle, supervisor scheduling, worker leases, and signed bridge transport under `.orchestrator/development_bridge/`. Zero product BFF routes or product runtime dependencies.
- **Product Runtime Authority**: Owns operator BFF API routing, domain state projection, and hosted `execute-plans` UI delivery. Zero task manipulation or repo file writes.
- **Delivery Infrastructure Authority**: Owns exact SHA deployment verification on `pantheon-dev` VM (`35.201.204.12`).

### 1.2 Port Namespace Consolidation
- **Sole Namespace**: `services/control-plane/bff/ports/` is the sole public interface and implementation namespace for domain ports.
- **Complete Elimination of `domain_ports`**: All 22 direct callers across tests, bff, and services are migrated to `ports/`. The 6 files under `services/control-plane/bff/domain_ports/` are deleted.
- **No Third / Compat Namespace**: Forbids creating any forwarding shims or secondary port directories. Rollback never restores deleted forwarding shims.

### 1.3 Reverse Import Elimination & Composition Root Isolation
- `services/control-plane/bff/main.py` serves strictly as the application assembly root (FastAPI app instantiation, lifespan, CORS, middleware, router mounts).
- Subrouters (including `agora/identity/router.py`, `agora/personalization/router.py`, and `command_executor.py`) must import shared authentication, session context, and idempotency utilities from `services/control-plane/bff/ports/` and shared domain helpers, with zero imports of `main.py`.

### 1.4 Command Executor Preservation
- `services/control-plane/bff/command_executor.py` is retained as the authoritative operator command execution engine.
- Its legacy reverse import (`import main as bff_main`) is completely eliminated by extracting shared command types and store ports to `services/control-plane/bff/ports/`.

### 1.5 Single-Receipt Source Contract
- In development mode, Source operates in `reconcile_only` mode by default.
- Bounded live provider egress uses a single receipt contract (`source_proof_receipt_id`) containing `connectorId` + `ingestRunId` + `sourceId` + `snapshotId`. Pre-switch stimulus occurs exactly once; post-switch access is strictly read-only reuse of existing receipt IDs with zero second egress.

---

## 2. Target Component Topology

```
+-----------------------------------------------------------------------------------+
|                                 execute-plans                                     |
|  +--------------------+  +----------------------+  +---------------------------+  |
|  |  Management AI     |  |   Agora Workshop     |  |   Trading / Ops / Alerts  |  |
|  |  Desktop & Postm.  |  |   & Consultation     |  |   Desktop Views           |  |
|  +--------------------+  +----------------------+  +---------------------------+  |
|            \                      |                      /                        |
|             +---------------------+---------------------+                         |
|                                   | (Strict Live bffClient)                       |
+-----------------------------------|-----------------------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------------------+
|                        Pantheon Control Plane (BFF)                               |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |              main.py (FastAPI Composition Root & Lifespan)                  |  |
|  +-----------------------------------------------------------------------------+  |
|         |                     |                     |                     |       |
|         v                     v                     v                     v       |
|  +--------------+     +---------------+     +---------------+     +------------+  |
|  | Personas &   |     | Agora &       |     | Governance &  |     | Management |  |
|  | Training     |     | Research      |     | Evolution     |     | & Postm.   |  |
|  +--------------+     +---------------+     +---------------+     +------------+  |
|         |                     |                     |                     |       |
|         +---------------------+---------------------+---------------------+       |
|                                   |                                               |
|                                   v                                               |
|  +-----------------------------------------------------------------------------+  |
|  |                   services/control-plane/bff/ports/                         |  |
|  |  (Canonical Public & Implementation Ports Namespace - Zero domain_ports)    |  |
|  +-----------------------------------------------------------------------------+  |
+-----------------------------------------------------------------------------------+
```

---

## 3. Bounded Context Domain Routing Model

The 441 HTTP route decorators and 421 unique handlers in `main.py` are partitioned across 18 cohesive domain router owners with no Core catch-all:

1. **BFF Core & Auth** (`OPGAP-BE-BFF-CORE-20260830`): Auth, settings, assistant routes.
2. **Persona Management** (`OPGAP-BE-PERSONA-ROUTER-20260830`): Persona CRUD, lifecycle, prompt templates.
3. **Training Engine** (`OPGAP-BE-TRAINING-ROUTER-20260830`): Training sessions, model evaluations.
4. **Agora Coordination** (`OPGAP-BE-AGORA-ROUTER-20260830`): Agora hub, identity, personalization, dashboard projections.
5. **Research & Knowledge** (`OPGAP-BE-RESEARCH-ROUTER-20260830`): Research artifacts, analysis pipelines, typed replacements for generic alias.
6. **Governance Policy** (`OPGAP-BE-GOVERNANCE-ROUTER-20260830`): Policy approvals, voting, committee decisions.
7. **Evolution Engine** (`OPGAP-BE-EVOLUTION-ROUTER-20260830`): Loop convergence, parameter tuning, experiments.
8. **Capital Allocation** (`OPGAP-BE-CAPITAL-ROUTER-20260830`): Capital pools, risk budgets, rebalances.
9. **Strategy & Ranking** (`OPGAP-BE-STRATEGY-RANKING-20260830`): Strategy registry, ranking read models.
10. **Management System** (`OPGAP-BE-MANAGEMENT-ROUTER-20260830`): Management CRUD, natural language query proxy.
11. **Postmortem System** (`OPGAP-BE-POSTMORTEM-ROUTER-20260830`): Postmortem library, root cause diagnostics.
12. **Incident Response** (`OPGAP-BE-INCIDENT-ROUTER-20260830`): Incident alerts, mitigation workflows.
13. **Event Subscriptions** (`OPGAP-BE-EVENTS-ROUTER-20260830`): SSE streams, outbox publishing.
14. **Tools & Integrations** (`OPGAP-BE-TOOLS-INTEGRATIONS-20260830`): Diagnostics, third-party connectors.
15. **Control Loops** (`OPGAP-BE-CONTROL-LOOPS-20260830`): Trigger management, loop dispatch.
16. **Command Adapters** (`OPGAP-BE-COMMAND-ADAPTERS-20260830`): Typed execution adapters & command executor.
17. **Runtime Binding** (`OPGAP-BE-RUNTIME-BINDING-20260830`): Runtime discovery, environment configs.
18. **Deployment Reliability** (`OPGAP-DEPLOY-RELIABILITY-20260830`): Release gates, deployment health.\n