# Persona harness record, versions and static pre-check

Task: `PERSONA-HARNESS-PATCH-RECORD-20261112` · owner Codex2 · reviewer Claude.
Design: `docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md` §6.8 / D10, SD-02 §4.8, and LP-001 §6.

The existing persistent Persona owner stores `metadata.persona_harness` through
its atomic compare-and-set, including immutable version contents, patch proposals,
check results, the active version/experiment and actor-stamped events. Generic
registry create/patch cannot populate or overwrite this reserved metadata key.
No separate service, model invocation, memory store or trading writer is added.

## Owner interface

All routes are mounted by the deployed `services/control-plane/persona/main.py`.
They use existing authentication, exact `X-Tenant-Id` binding, and `persona.admin`
for writes. Reads also allow existing viewer roles. Mutations require an active,
tenant-bound `paper_owner`; live/frozen/unbound personas fail closed.

- `GET /api/personas/{id}/harness`: full history and active version; absent data
  returns `unavailable` / `harness_not_initialized`, without a synthetic baseline.
- `POST /api/personas/{id}/harness`: initialize from explicit complete owner
  configuration. Repeat initialization conflicts. This records configuration;
  it does not change permissions in the tool/execution owners.
- `POST /api/personas/{id}/harness/patches`: immutable typed proposal, base version,
  trajectory evidence with a moment/snapshot, expected effect and rollback target.
- `POST .../patches/{patch_id}/precheck`: resolve evidence, validate the candidate,
  compare it with the current base and persist pass/refusal reasons.
- `POST .../patches/{patch_id}/activate`: recheck the exact current base and
  evidence, require a trusted adoption verifier and append the new version.
- `POST .../harness/rollback`: reactivate the experiment's prior version, record
  the reason and clear the experiment; neither versions nor history are deleted.

Patch types retain SD-02's route_policy, consult_policy, memory, behavior and
tool_profile, adding skill, workflow, threshold and memory_policy. Each type has
an explicit component allowlist. Nested components use full replacement, checked
against strict schemas, so omitted restrictions cannot disappear through a merge.

## Pre-check boundaries

Risk/capital limits, environment/ceiling, execution authority and live permissions
are absent from the mutable schema. New tools, removed forbidden solo actions,
removed consult triggers/reviewers, increased cycle frequency, removed workflow
steps, lowered evidence/confidence thresholds and expanded memory retrieval are
refused. Consult preservation applies to every existing trigger, including high
risk actions. Skills need nonblank triggers and existing repository-confined
scripts (absolute paths and escaping symlinks are refused). All surface strings,
including component names, count toward a fixed conservative 12,000 UTF-8-byte
always-on text budget. Prose can propose changes but never grants executable
permissions; existing deterministic tool and trading gates retain authority.

The runtime profile reports the active version and content digest, or an explicit
missing/invalid reason. It never invents a harness or presents malformed data as
ready. Historical version numbers remain monotonic after rollback (1 → 2 → 1 → 3).

## Composition contracts and remaining delivery

`EvidenceVerifier(persona_snapshot, EvidenceReference) -> bool` must resolve a
reference from its real owning service and check persona/tenant binding. The
built-in resolver accepts only schema-valid reflections already persisted in
that persona's owner record, matching reflection id and immutable facts snapshot.
Packet, virtual-book, memo and lesson references fail closed until their owning
readers are connected. No unknown reference is accepted by string shape alone.

`AdoptionVerifier(persona_snapshot, {proposal, candidate_digest}, adoption_ref)`
must resolve a trusted adoption decision bound to tenant, persona, base version,
patch id and candidate digest. It must enforce the downstream shadow result,
risk-persona sign-off, cooldown and applicable governance policy. It is an
in-process owner dependency, never a caller-provided approval boolean or new
credential. Verifiers run against the same snapshot used for the CAS; they must
be read-only and safe to repeat. Absence, exception or non-True result records a
refusal and keeps the base. Rechecking and activation are atomic with the
one-experiment invariant.

Production activation intentionally stays closed by default. The positive
integration test supplies an exact-binding test receipt resolver; it proves
owner wiring and persistence, **not** real shadow performance or risk approval.
`PERSONA-HARNESS-SHADOW-EVAL-20261112` supplies comparison records;
`PERSONA-HARNESS-ADOPT-REVIEW-20261112` supplies adoption, observation, completion,
cooldown, experience writeback and policy amendments. This task does not claim
those tasks, hosted deployment or autonomous paper adoption are complete.

## Validation

See `evidence.json`. Tests exercise authenticated production owner routes,
the deployed control-plane mount, durable restart, append-only rollback history,
concurrent initialization/activation, missing evidence/adoption, immutable fields,
permission expansion, script paths, schema consistency and metadata bypass.
The opt-in hosted smoke is skipped unless explicitly configured.
