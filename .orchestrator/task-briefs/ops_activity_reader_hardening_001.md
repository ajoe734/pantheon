# OPS-ACTIVITY-READER-HARDENING-001

## Objective

Make the shared activity reader safe for early-stopping callers, consolidate
the historical exception definition, and make critical exception tests
hermetic.

Owner: `Codex2`. Reviewer: `Claude`. Target: `pantheon/dev`. Auto-merge: off.
Depends on: `OPS-ACTIVITY-ROTATION-OVERLAP-PREVENTION-001` merged.

## Scope

- `.orchestrator/common.py`
- direct consumers whose API must change
- direct tests and redacted evidence

Do not absorb the historical dispatcher task or change rotation semantics
owned by the dependency.

## Requirements

1. Provide an API that cannot return a successful partial logical result
   before source identity/content stability is verified. A bounded temporary
   store or explicit validation-complete visitor is acceptable; loading full
   history into memory is not.
2. Keep a streaming full-drain API only when completion is explicit and
   abandonment is tested not to be mistaken for validation success.
3. Move the pinned 999-line exception identity, hashes, byte/line counts, and
   source pair into one immutable typed registry consumed by every check.
4. Remove dead/duplicated exception state where safe without weakening any
   rejection.
5. Build self-contained exact-pair fixtures. The core pinned tests must run on
   any CI host and must not skip when central archives are absent.
6. Retain an optional central read-only integration check, clearly separated
   from hermetic acceptance.

## Verification and delivery

Cover normal completion, deliberate early stop, consumer exception, source
replacement/mutation, registry mismatch, generic 999 rejection, exact pinned
acceptance, and bounded resource use. Run full activity/status/control-plane
suites, py_compile, and range diff-check. Commit with
`LLM-Agent: Codex2`, this task ID, and `Reviewer: Claude`; independent Claude
exact-head approval is required and auto-merge stays off.
