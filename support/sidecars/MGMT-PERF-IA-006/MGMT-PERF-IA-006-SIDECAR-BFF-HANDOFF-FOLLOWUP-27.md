# MGMT-PERF-IA-006 BFF Handoff Follow-up 27

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet records the Rankings Center dependency transition and
gives the parent owner a bounded composition card. It does not define routes,
query keys, response fields, schemas, runtime behavior, frontend code, or
canonical truth.

## 1. Dependency Transition

The task-scoped status lookup now returns `MGMT-PERF-IA-004` from the archive
as `done`. Its delivery record identifies the Rankings Center consolidation,
the removal of seeded fallback rows, reproduced management tests and build,
and the merged execute-plans follow-up. This closes the task-state dependency
for this sidecar.

It does not by itself prove the parent's cross-surface journey. Before
absorption, the parent must record the execute-plans merge ancestry actually
served by the hosted frontend, the strict-live BFF base and SHA, authenticated
request/response captures, and desktop/mobile behavior. Until those facts are
recorded, classify the journey as `proof-pending`, not as a BFF defect.

## 2. Rankings Composition Card

Complete one card for every real source action that enters Rankings Center.
Persona Fleet ranking, evidence, and review actions are separate journeys.

```text
Source action and page:
Hosted origin / frontend SHA / BFF SHA / captured at:
Authenticated role and strict-live proof:
Source route and redacted response:
Source-authored persona/runtime/strategy/pool/stage/period context:
Typed navigation request:
Rankings destination route and redacted request/response:
Response-fulfilled identity, ranking scope, period, snapshot, and health:
Direct load / refresh / copied URL / back-forward result:
Applicable Human Inbox completion / cancellation return result:
Desktop / mobile evidence paths:
Healthy-empty / unavailable / invalid-id result:
First loss, if any:
Disposition:
```

Use exactly one disposition:

- `absorb`: response-authored identity and supported ranking scope survive the
  complete journey;
- `visibly-unscoped`: Rankings Center explicitly states that requested context
  is unsupported;
- `honest-unavailable`: absent, invalid, stale, unauthorized, incompatible, or
  dependency-down data stays unavailable without fixture or inferred data;
- `split-to-bff`: deployed strict-live evidence isolates the first missing
  response boundary and truthful frontend behavior cannot satisfy acceptance;
  or
- `proof-pending`: delivery exists but hosted journey evidence is incomplete.

URL retention, labels, rank, row position, similar metrics, and nearby
timestamps do not prove identity or applied scope. Pagination or continuation
must reset when effective ranking scope changes.

## 3. Truth Boundary

Rankings Center owns formal rolling and quarterly ranking presentation. Persona
and strategy details may retain compact summaries and links, but must not grow
a competing formal ranking table. Governance evidence linked from a ranking
must remain distinct from the ranking itself, and Human Inbox completion must
not imply that a recommendation was applied without a separate durable
operation receipt.

Exercise healthy empty, unavailable, degraded, stale/fallback, invalid
identity, unauthorized, and non-finite states. The archived dependency evidence
specifically establishes that seeded fallback rows are not acceptable; fixture
or fallback responses therefore cannot satisfy hosted strict-live proof.

## 4. BFF Split Cut-line

Create a separately owned BFF task only for `split-to-bff`. Attach the source
and destination routes and redacted responses, requested versus fulfilled
context, the first missing identity/scope/health/snapshot/link boundary, the
smallest response change requested, negative cases, fail-closed interim UI,
and named BFF/frontend owners and reviewer.

Do not infer a universal context token, generic filter expansion, browser-side
join, convenience aggregate, fixture authority, duplicate analysis page, new
mutation semantic, or canonical-contract change from this packet.

## 5. Ownership And Handoff

Parent owner `Antigravity` owns hosted evidence, selective absorption into
`execute-plans`, and assignment of any evidence-backed BFF split. Parent
reviewer `Claude` reviews the composed parent implementation. Sidecar reviewer
`Antigravity` reviews only whether this packet is accurate, useful,
fail-closed, and support-only; approval does not approve or complete the parent.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-27 Antigravity \
  "Rankings dependency transition and support-only composition card ready for review."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-27` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, umbrella handoff, archived dependency
  record, and the three immediately preceding support packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source changed.

## 7. Finalization Evidence

- Sidecar reviewer `Antigravity` approved the support-only handoff packet and
  returned the task to owner `Codex2` for formal closeout.
- Re-read the task brief, reviewer-approved scope, closeout protocol, and this
  artifact; the bounded support-only scope remains unchanged.
- Focused verification:
  - `git diff --check origin/dev...HEAD`
  - exact task diff scope equals this support artifact
  - targeted content check covers fail-closed fixture/fallback handling,
    `proof-pending`, evidence-backed `split-to-bff`, canonical non-mutation, and
    parent ownership
- GitHub PR `#3398` targets `dev`; its branch checks passed before this
  finalization update. Formal `done` follows only after the final task commit
  is pushed, the refreshed checks pass, and the PR is merged into `dev`.
