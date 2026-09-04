# Structural Closure Traceability Matrix

This matrix prevents implementation packets from closing symptoms while
leaving the audited root cause in place.

| Audit finding | Root-cause design | Primary packet | Required removal | Closure proof |
|---|---|---|---|---|
| ENV-01 exact pair unknown | SA ADR-07; SD §10 | DEV-DELIVERY-001 | stale hosted identity claims | manifest + FE/BFF served readback |
| ENV-02 target not reconcilable | SD §10.2 | DEV-DELIVERY-001 | stale current environment facts | merged target + successful preflight |
| ENV-03 environment docs conflict | SA ADR-07; SD §10.2 | DEV-DELIVERY-001 | retired current-tense targets | one merged authoritative identity |
| ENV-04 no FE ingress acceptance | SD §§9-10 | FE-STRICTLIVE-001 / DEV-DELIVERY-001 | live mock/fallback reachability | HTTPS/auth/CORS browser evidence |
| ENV-05 no rollback baseline | SD §10.2-10.3 | DEV-DELIVERY-001 | implicit/fabricated fallback baseline | checksum-bound rehearsal |
| ENV-06 staging/prod unavailable | SA §3.2; SD §10 | ENV-STAGING-PROD-PLAN-001 | retired/reused environment assumptions | independently authorized environment packets and later admitted environments |
| 12 loops only 2 passed/25 skipped | SA §8; SD §12.4 | L12-HOSTED-001 | mandatory skip conditions | one correlation chain, no skips |
| MGMT-01 hosted Management absent | SA ADR-05; SD §7 | MGMT-READ-001 / MGMT-AGORA-E2E-001 | fixture-only desktop acceptance | authenticated exact-pair journey |
| MGMT-02 current loop truth absent | SA ADR-05; SD §7 | LOOP-TRUTH-001 | static/incident success substitution | twelve owner-derived rows |
| MGMT-03 incomplete facade wiring | SA ADR-01; SD §4 | BFF-COMPOSITION-001 | global `read_store` | production factory route execution |
| MGMT-03A absent methods | SD §4.5 | DOMAIN-WRITERS-001 | mutation-through-read-facade | one typed owner per route |
| MGMT-04 legacy action adapter | SA invariant 8; SD §8.4 | STRUCT-RETIRE-001 | deprecated adapter after caller zero | typed domain command receipts |
| MGMT-05 overlays | SA ADR-03; SD §5 | OVERLAY-RETIRE-001 | five audited state authorities | restart/multi-replica readback |
| MGMT-06 AI chain unverified | SD §7.3/§12.4 | MGMT-AGORA-E2E-001 | provider/task-authority conflation | authenticated query + safe command receipt |
| MGMT-07 stale task projection | SA invariant 12; SD §7.3 | MGMT-READ-001 | product use of `ai-status.json` | development/product authority boundary test |
| MGMT-08 FE reachability unknown | SD §9 | FE-STRICTLIVE-001 | production mock/seed imports | bundle graph + browser failures |
| AGORA-01 complete journey absent | SD §6/§12.4 | AGORA-CHAIN-001 | fake/manual-only handoffs | workshop-to-suggestion same IDs |
| AGORA-02 provenance hosted open | SA ADR-06; SD §6.2 | AGORA-CHAIN-001 | client trust boolean | canonical backend receipt resolution |
| AGORA-03 natural suggestion caller absent | SD §6.4 | AGORA-CHAIN-001 | separate scheduler/manual-only path | telemetry/evaluation-triggered suggestion |
| AGORA-04 two journals | SA ADR-04; SD §5.3 | JOURNAL-OWNER-001 | unselected implementation | zero callers/table writes after migration |
| AGORA-05 worker lifecycle unverified | SD §6.1 | AGORA-CHAIN-001 | synchronous fallback completion | lease/retry/DLQ/SSE/restart evidence |
| AGORA-06 decision/learning handoffs | SD §6.3 | AGORA-CHAIN-001 | direct or duplicate handoff paths | one decision and one next-owner receipt |
| AGORA-07 FE/BFF compatibility open | SD §§9-10 | FE-STRICTLIVE-001 / DEV-DELIVERY-001 | mutable/unpaired compatibility claims | exact contract/release digests |
| DUP-01 two Decision Journals | SA ADR-04; SD §5.3 | JOURNAL-OWNER-001 | unselected implementation | one writer and fresh-reader parity |
| DUP-02 208 duplicate groups | SA ADR-02; SD §8.2 | STRUCT-RETIRE-001 | classified copied bodies | AST inventory reaches accepted zero/allowlist |
| DUP-03 legacy action adapter | SA invariant 8; SD §8.4 | STRUCT-RETIRE-001 | adapter after caller count zero | forbidden import/caller test |
| DUP-04 conflicting environment truth | SA ADR-07; SD §10.2 | DEV-DELIVERY-001 | superseded environment claims | one target registry and deploy preflight |
| DUP-05 overlays | SA ADR-03; SD §5.1 | OVERLAY-RETIRE-001 | overlay definitions/tests | owner-only writes |
| 17 unreachable tails | SD §8.1 | BFF-DEADCODE-001 | all dead bodies | control-flow gate |
| main reverse import | SA invariant 7; SD §8.3 | BFF-COMPOSITION-001 | import from composition root | architecture test |
| route tests time out | SD §12.3 | BFF-COMPOSITION-001 | global-state/import-time coupling | bounded suite completes |
| stale facade tests/references | SD §§4, 8.4 | BFF-COMPOSITION-001 | direct global patching | domain protocol test doubles |
| 218 tests import `main` | SA ADR-10; SD §8.5 | BFF-TEST-ARCH-001 | non-composition imports/global patches | classified test layers and allowlist |
| 323 path mutations; 18 production | SA ADR-09; SD §4.1A | BFF-PACKAGE-001 | domain/runtime path surgery | one stable package root |
| production dynamic `globals()` wiring | SA ADR-09; SD §§4.1A, 8.6 | BFF-PACKAGE-001 | namespace forwarding/service lookup | explicit constructor injection |
| router factories up to 3,383 lines | SA ADR-11; SD §4.2A | BFF-ROUTER-STRUCT-001 | proxy closures/copied handlers | bounded cohesive sub-routers |
| stale superseded PRs | SD §2 | Wave-0 delivery hygiene | obsolete open delivery lines | closed/superseded PR inventory |
| suspected worker/scheduler overlap | SD §3.3 | STRUCT-OWNERSHIP-001 | undeclared consumers | one lease/partition owner per subject |

## Anti-layering review questions

Every packet and PR must answer `no` to all of these:

1. Does this add a second writer, store, journal, projection authority,
   scheduler, deploy lane or loop orchestrator?
2. Does it add mutation methods to a read abstraction?
3. Does it introduce a generic facade/service locator to avoid explicit
   dependencies?
4. Does it retain the replaced body after callers move?
5. Does it use dual-write as the permanent end state?
6. Does it accept from process memory when the canonical owner fails?
7. Does it convert CI, task status, fixtures or historical evidence into live
   product truth?
8. Does it allow frontend/client input to assert trusted provenance?
9. Does it expand Management AI into development or deployment authority?
10. Does it count skipped, timed-out or unexecuted tests as acceptance?

Any `yes` blocks the packet until the architecture is revised.
