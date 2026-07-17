# OPS-ACTIVITY-READER-HARDENING-001

## Objective

Make the shared activity reader safe for early-stopping callers, consolidate
the historical exception definition, make critical exception tests hermetic,
and reject ambiguous duplicate-key JSON in every active or gzip source.

Owner: `Codex`. Reviewer: `Claude`. Target: `pantheon/dev`. Auto-merge: off.
Depends on: `OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001` merged.

## Scope

- `.orchestrator/common.py`
- direct consumers whose API must change
- direct tests and redacted evidence

Do not absorb the historical dispatcher task or change rotation semantics
owned by the dependency. Compose with dispatcher draft PR #3799: its direct
active/archive zero-write regressions must pass through the shared reader,
not through a private dispatcher parser.

## Requirements

1. Provide an API that cannot return a successful partial logical result
   before source identity/content stability is verified. A bounded temporary
   store or explicit validation-complete visitor is acceptable; loading full
   history into memory is not.
2. Keep a streaming full-drain API only when completion is explicit and
   abandonment is tested not to be mistaken for validation success.
3. Reject duplicate JSON object keys at every nesting depth in active and gzip
   sources before event IDs, payloads, or control records are interpreted.
4. Move the pinned 999-line exception identity, compressed/decompressed
   hashes, byte/line counts, and source pair into one immutable typed registry
   consumed by every check.
5. Remove dead/duplicated exception state where safe without weakening any
   rejection.
6. Build self-contained exact-pair fixtures. The core pinned tests must run on
   any CI host and must not skip when central archives are absent.
7. Retain an optional central read-only integration check, clearly separated
   from hermetic acceptance.

## Verification and delivery

Cover normal completion, deliberate early stop, consumer exception, source
replacement/mutation, duplicate keys in active/gzip sources, registry mismatch,
generic 999 rejection, exact pinned acceptance, and bounded resource use. Run
full activity/status/control-plane suites, py_compile, and range diff-check.
Commit with `LLM-Agent: Codex`, this task ID, and `Reviewer: Claude`;
independent Claude exact-head approval is required and auto-merge stays off.

## Coordination Root

- Auto workers inherit `PANTHEON_STATUS_ROOT` from the supervisor.
- Run `./scripts/ai-status.sh` normally from this worktree; governed status,
  activity, archive and lock writes are routed to the validated central root.
