# Evolution Journal Producer Chain Gap - 2026-07-13

Status: gap specification and execution source of truth

Owner: Human/Ops (spec), fleet (execution)

Scope:

- `services/evolution/` (sweep, scheduler, postmortem bridge)
- `services/incidents/` (threshold telemetry consumer)
- `services/telemetry/` (performance summary read path used by the new producer)
- `services/governance/` (freeze order / rollback canonical store)
- `services/control-plane/bff/main.py` + `read_store.py` (evolution journal aggregate, surface statuses)
- `execute-plans:src/management/pages/oversight/_core.tsx` (Evolution Journal page)
- `execute-plans:src/platform/components/TopBar.tsx` + i18n (data-source badge semantics)
- `docker-compose.yml` (`evolution-daily-sweep-scheduler` profile gate, new producer service)

Related prior packets:

- `docs/04/pantheon_management_console_mutation_evolution_gap_2026-07-10/PERSONA_FLEET_MUTATION_EVOLUTION_GAP.md`
  (labeling honesty — done; this packet supplies the *real data* that packet assumed would eventually exist)
- `docs/05/testing-principles/` (producer-chain testing: test the verb, not the noun)

Execution packet:

- `docs/bff/execution-tasks/2026-07-13-evolution-journal-producer-gap/INDEX.md`

## Problem Statement

The management console Evolution Journal (演化日誌) renders, is honestly
labeled, and is served live by the BFF — but it has effectively no real
content. Verified live on dev (2026-07-13):

- `/bff/management/evolution-journal` returns exactly 2 items, both derived
  from the vertical-slice seed `evo-vslice-1`
  (`services/evolution/seed_data.py`), latest timestamp 2026-06-15.
- `/bff/incidents` contains exactly 1 incident — the same 2026-06-15 seed
  family (`inc-87c655c3e3c9`, "TW momentum candidate — paper drawdown
  breach"), still `open`.
- Personas focused from Persona Fleet fall through to the synthesized
  `persona-fleet-summary:*` fallback card because no formal mutation entry
  exists for any real persona.
- The journal aggregate surface reports `degraded` because the
  `freeze_orders` and `rollbacks` source surfaces are `unavailable/missing`,
  which the frontend renders as a `SNAPSHOT DATA` badge even though the data
  is live-composed.

A month of live paper trading has produced zero incidents, zero postmortems,
zero evolution decisions, zero freeze orders, and zero rollbacks in the
journal. The read side is healthy; the producer chain never fires.

## Root Causes (each independently verified)

The designed event chain is:

```text
paper telemetry / performance
  -> threshold breach event
  -> IncidentCase (services/incidents consumer)
  -> postmortem (on incident resolution)
  -> evolution daily sweep / postmortem bridge
  -> EvolutionDecision proposal
  -> review / approve / execute (retrain | freeze | rollback | ...)
  -> Evolution Journal formal entries
```

Break points:

1. **No threshold-breach producer.**
   `services/incidents/consumer.py` (`ThresholdTelemetryIncidentConsumer`)
   is a complete adapter with zero callers. Nothing evaluates live paper
   telemetry/performance against thresholds and posts breach payloads.
2. **Evolution daily sweep is deployed but never runs.**
   `evolution-daily-sweep-scheduler` in `docker-compose.yml` is gated behind
   `profiles: ["evolution-daily-sweep-scheduler"]`, which `docker compose
   up -d` does not start. Even the one open seed incident has never been
   swept into a decision proposal.
3. **No postmortem publisher.**
   `services/evolution/postmortem_bridge.py` is a pure transformation with a
   done contract (`postmortem_bridge_contract.md`) — but nothing publishes
   postmortem events, so the bridge never fires. Live postmortem count: 0.
4. **`freeze_orders` / `all_rollbacks` have no canonical backend.**
   `read_store.list_freeze_orders` / `list_all_rollbacks` read only
   `_local_fallback(...)` (the local snapshot). In strict/live mode that
   returns `None`, so the surfaces report `missing` → the journal aggregate
   is permanently `degraded`.
5. **BFF journal endpoint ignores `?persona=` / focus params server-side**;
   filtering is entirely client-side in the FE.
6. **FE badge wording conflates degraded with snapshot.**
   `degraded` renders as `SNAPSHOT DATA` even when `source: bff_composed`
   (live). Misleading for operators.

## Approved Design Decisions (2026-07-13, sponsor-confirmed)

1. **Threshold source of truth:** reuse the governance threshold schema
   (`ThresholdEvaluator` + `services/evolution/fixtures/
   threshold_breach_daily_sweep.json` shape). Threshold *values* live in
   live config (mounted config / env, changeable without image rebuild).
2. **Seed data:** keep `evo-vslice-1` in dev, but journal entries derived
   from registered seeds must carry an explicit `origin: seed` marker and
   the FE must render a `fixture` badge on them. Do not delete.
3. **Producer cadence:** the new threshold-breach producer defaults to one
   sweep per day (aligned with the evolution daily sweep). It owns its own
   interval env var. **Do not touch any existing supervisor/poll cadence.**
4. **Owners:** Claude / Codex / Codex2 (live-config enabled agents).
   Antigravity is not an owner in this packet (2026-07-12 quota-storm
   residue); it may pick up review overflow only if it proves healthy.

## Target Contract

### New producer: threshold-breach sweep (EVOCHAIN-001)

- Reads per-binding / per-persona paper performance aggregates from the
  telemetry read path (same summaries that feed the performance console).
- Evaluates governance-schema thresholds (initially: max drawdown, rolling
  PnL floor) from live config.
- On breach, POSTs the canonical threshold-breach payload to the incidents
  service consumer endpoint.
- **Idempotent:** one incident per (binding, metric, breach-window); re-runs
  must not duplicate open incidents (dedupe key recorded in the incident).
- **Fail-closed:** on missing/ambiguous telemetry it emits nothing and logs
  a diagnostic; it must never fabricate a breach.
- Ships as a compose service with its own daily interval env
  (`EVOCHAIN_THRESHOLD_SWEEP_INTERVAL_SECONDS`, default 86400).

### Sweep activation (EVOCHAIN-002)

- `evolution-daily-sweep-scheduler` runs by default on dev (remove the
  profile gate or provide a committed override + ops runbook).
- Evidence: scheduler tick log + the pre-existing open incident swept into
  a decision proposal visible in the journal.

### Postmortem publisher (EVOCHAIN-003)

- Incident resolve/close emits a postmortem record and routes it through
  `postmortem_bridge.on_postmortem_published`, admitting the returned
  proposal via `POST /api/evolution/proposals`.
- Bridge stays pure; this task adds the missing caller only.

### Freeze / rollback canonical store (EVOCHAIN-004, -005)

- Governance service owns `freeze_orders` and `rollbacks` datasets with a
  service-backed read API.
- BFF `read_store` gains a `service_client` path for both datasets
  (local snapshot remains fallback-only).
- BFF governance write endpoints (freeze/rollback approve/execute) persist
  to the canonical store with full audit fields.
- Surface statuses for both datasets become `ok` on dev after deploy.

### Mutation review wiring (EVOCHAIN-006)

- Console review/approve/reject/execute actions on a proposal go through
  BFF commands to the existing evolution service APIs
  (`/api/evolution/proposals/{id}/review|approve|reject|execute`), and the
  outcome projects back into the journal as status transitions on the same
  formal entry.

### Read-side honesty (EVOCHAIN-007, -008, -009)

- `/bff/management/evolution-journal` applies `persona` /
  `mutation_review` / `decision` filters server-side with correct paging;
  seed-derived entries carry `origin: seed`.
- FE badge: `degraded` + live-composed source renders as a live-degraded
  state naming the degraded surfaces; `SNAPSHOT DATA` is reserved for
  actually snapshot-served data.
- Journal cards render formal-entry fields (risk_level, action_type,
  target version, approval state) and a `fixture` badge for seed entries.
  The 2026-07-10 fallback-card contract is unchanged.

## Definition of Done (packet-level)

Producer-chain proof on hosted dev (test the verb, not the noun):

```text
inject/observe a real threshold breach
  -> incident appears with dedupe key
  -> daily sweep creates a decision proposal
  -> proposal visible as a formal journal entry
  -> Persona Fleet 最近 MUTATION for that persona links to the formal entry
  -> freeze_orders / rollbacks surfaces report ok
  -> journal aggregate surface: ok (badge no longer SNAPSHOT DATA)
```

All merged PRs listed, dev redeployed, live curl + hosted screenshots
archived under the execution packet, residual risks recorded with owner and
expiry.
