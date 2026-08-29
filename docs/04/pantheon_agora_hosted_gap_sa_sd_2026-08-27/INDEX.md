# Pantheon Agora Hosted GAP / SA / SD Re-baseline — 2026-08-27

Status: **current cross-repository design baseline; implementation is not yet
closed**

Tier: L2 working design index; not an L1 authority

Scope: Agora hosted product GAP, SA, SD, Demo proof, and cross-repository
acceptance delta only

Conflict rule: L1 policy and owning canonical contracts win. This package
supersedes older Agora completion/status conclusions only for the exact frozen
source and hosted evidence described below.

This package converts the 2026-08-27 authenticated, page-by-page Agora audit
and the bounded Demo write-proof into an implementation-ready GAP, System
Analysis (SA), and System Design (SD) delta.

## Executive verdict

The hosted Agora deployment is currently safe to browse, but it is not a
functionally accepted Agora product:

- the exact hosted FE/BFF pair is marked `accepted` under a `read-only`
  deployment profile;
- PR #662 is present in the hosted frontend and the major Agora routes render;
- strict BFF authentication succeeds, but the login gate synchronously probes
  the OpenClaw assistant provider and adds 16–20 seconds of avoidable latency;
- existing Demo-created Proposal, Persona, and Workshop records persist and
  read back, but the Persona-to-Workshop navigation and interaction submission
  do not complete;
- the market snapshot is stale, `paper-signal-producer` is unhealthy, and the
  Trading Room, signal, inbox, journal, interaction, and decision-event
  surfaces have no current data;
- Strategy Performance calls an Agora route that the hosted BFF does not
  expose; and
- Persona List and Persona Fleet do not share one resolvable identity set.

The correct product statement is therefore:

> Agora is currently an accepted read-only shell with partially durable Demo
> artifacts. It is not accepted for login latency, authenticated streaming,
> end-to-end interaction, current market data, performance truth, or the full
> Demo create-to-readback journey.

## Documents

Read and implement in this order:

1. [`CURRENT_GAP_2026-08-27.md`](CURRENT_GAP_2026-08-27.md) — hosted evidence,
   page-by-page results, Demo transaction results, old-scenario reconciliation,
   and the prioritized closure matrix.
2. [`SA_AGORA_PRODUCT_CLOSURE_2026-08-27.md`](SA_AGORA_PRODUCT_CLOSURE_2026-08-27.md)
   — revised product outcome, actors, authority boundaries, target journeys,
   state semantics, release levels, work packages, and rollout order.
3. [`SD_AGORA_PRODUCT_CLOSURE_2026-08-27.md`](SD_AGORA_PRODUCT_CLOSURE_2026-08-27.md)
   — file-level backend/frontend/workflow changes, additive contracts, worker
   design, migration, tests, and exact hosted acceptance.

## Frozen baselines

| Surface | Ref / identity |
|---|---|
| Pantheon source and hosted BFF | `ajoe734/pantheon@3c79a185a97d920f41005bd41675433a046b6ece` |
| execute-plans source `dev` | `ajoe734/execute-plans@3010ee6e164e962791c94a044c19d6e79465a230` |
| Hosted frontend during the browser audit | `ajoe734/execute-plans@31623e783f7a08f94df7099c207390b317077d61` |
| Current served frontend after the audit | `ajoe734/execute-plans@3010ee6e164e962791c94a044c19d6e79465a230` |
| Current hosted deployment state | `accepted`, `read-only`, accepted at `2026-08-27T03:54:30Z` |
| Browser audit window | 2026-08-27, approximately 02:47–03:10 UTC |
| Demo write-proof source | execute-plans Actions run `33031829879` |

The frontend source repository remains separate. Every
`execute-plans:<path>` reference in this package means a path in
`ajoe734/execute-plans`; no frontend source may be copied into Pantheon.

The post-audit FE change is PR #664, which removes authenticator-code login UI.
The authenticated page-by-page timings and domain findings remain bound to
`31623e...` until rerun on `3010ee...`; PR #664 does not change the BFF
readiness, Agora interaction, SSE, Persona, data, or Performance paths.

## Relation to existing authority

This package is an L2/L3 implementation delta. It does not override L1 policy
or create a second Agora authority. In conflicts, follow:

- [`TARGET_ARCHITECTURE.md`](../../../TARGET_ARCHITECTURE.md);
- [`PERSONA_RUNTIME_MODEL.md`](../../../PERSONA_RUNTIME_MODEL.md);
- [`OPENCLAW_RUNTIME_CONTRACT.md`](../../../OPENCLAW_RUNTIME_CONTRACT.md);
- [`DATA_SOURCE_SCOPE_MATRIX.md`](../../../DATA_SOURCE_SCOPE_MATRIX.md);
- [`BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`](../../../BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md);
- the Agora v1.13 compatibility bundle; and
- the 2026-08-13 Agora product design package.

The [2026-08-13 Agora GAP/SD](../pantheon_agora_product_gap_sd_2026-08-13/INDEX.md)
remains the broad product-authority design. This package updates its hosted
truth, closes findings that now have evidence, adds newly observed operational
gaps, and narrows the next implementation to the shortest truthful functional
closure path.

## Completion boundary

Publishing or merging this document package does not close Agora. Product
closure requires the SD changes to be implemented in their owning
repositories, deployed as one exact FE/BFF pair, and proven by a newly created
Demo transaction. Historical record IDs or a read-only `accepted` manifest are
not substitutes for that proof.
