# Development Tooling Four-Gap Plan — 2026-08-30

Status: **code- and runtime-audited SA/SD; not execution-task materialization**

Scope: Pantheon development tooling only. Product runtime, the twelve business
loops, Management product behavior, hosted deployment, and capital pathways are
out of scope.

This package plans the four residual findings from the 2026-08-30 tooling audit:

1. unavailable immutable V1 TaskStore archive;
2. incomplete tooling CI selection and integration-authority coverage;
3. repeated auto-integrator evaluation after a PR is already merged; and
4. `supervisor.py` / `ai_status.py` responsibility concentration plus historical
   evidence-retention classification.

The package does not reopen the retired chair/sidecar scheduler, add another
queue, create another task authority, or materialize supervisor tasks.

## Documents

- [GAP_AUDIT.md](GAP_AUDIT.md) — current facts, exact gaps, dispositions, and
  non-gaps.
- [SA.md](SA.md) — system architecture, canonical owners, boundaries, and
  architecture decisions.
- [SD.md](SD.md) — file-level design, migration waves, validation, rollback,
  and planning package DAG.
- [THREE_PASS_REVIEW.md](THREE_PASS_REVIEW.md) — three independent review
  passes and the corrections made between them.

## Authority and conflict rule

`docs/02-architecture/supervisor-authority-v2.md` remains canonical for runtime
authority. `docs/operations/development-tooling-code-audit-2026-08-29.md`
remains the record of the fixes merged in PR #5419. This package narrows the
remaining work; it does not supersede either document.

The historical `development-tooling-architecture-gaps-2026-08-24.md` remains
non-executable. Its generic task amendment, parent/sidecar scheduler, and second
ownership-layer proposals must not be revived by work derived from this package.
