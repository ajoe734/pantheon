# Canonical review admission hold

Historical hold, resolved 2026-11-12 at 22:35Z: Human/Ops added all nine paths
through the governed artifact contract and reopened this same task. The owner
re-read the canonical row through the assigned command runtime before resuming.
Fresh exact-head independent review remains required; the original rejection
and corrective path below are retained as the audit record.

PR: https://github.com/ajoe734/pantheon/pull/7274

The governed owner handoff used `AI_NAME=Codex`, reviewer Claude, PR 7274,
head `a2a26fd4c7e337ebd27eca831e2f22f60e7b187b`, and this directory's committed
`evidence.json`. It failed before entering review:

```text
PERSONA-OODA-CONTROLLER-20261112: changed file
'dependencies/locks/services-control-plane-persona.txt' is outside the task
artifact contract for repository 'pantheon'
```

Comparison of the complete PR diff with the governed `show` task row identifies
these nine paths requiring an audited artifact-contract extension:

- `dependencies/locks/services-control-plane-persona.txt`
- `services/agora/store.py`
- `services/control-plane/bff/control_loops/router.py`
- `services/control-plane/bff/ports/ooda_management.py`
- `services/control-plane/cron/test_cron.py`
- `services/control-plane/persona/main.py`
- `services/control-plane/persona/requirements.txt`
- `services/openclaw-gateway-adapter/main.py`
- `services/openclaw-gateway-adapter/test_main.py`

These are the existing-process startup, shared writer dependency, existing
adapter allowlist/read guard, consented teaching-reference owner, packet tenant
admission and focused catalog/guard regression tests described in the design
and implementation. Removing them to fit the incomplete path list would remove
runtime activation, required dependency installation or confidentiality checks.

The configured command runtime's `command_artifact_contract` explicitly says
`Only Human/Ops can revise task artifact contracts` and accepts only `todo` or
`blocked` rows. The worker has no authority to rewrite that contract, impersonate
Human/Ops or alter the guard. The existing task must be placed in `blocked`
with `waiting_for=Human/Ops` and blocker kind `external` (all three declared
task dependencies are satisfied). Human/Ops can then use its existing governed
`artifact-contract <task> add <path> <reason>` command for these paths and
`reopen` the same task. No replacement task, dispatcher or dependency edge is
needed.

After the revision, the owner must revalidate the current dev composition and
repeat exact-head handoff against the live PR head and committed manifest.
Claude remains the assigned reviewer. Neither this hold record nor CI is a
review decision; no approval/proof tag, merge or `done` has been manufactured.
The actual three-persona hosted cycle/private-memory/consultation obligation
also remains pending.
