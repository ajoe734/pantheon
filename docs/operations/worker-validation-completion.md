# One-shot worker validation completion

One-shot auto workers must finish validation in bounded foreground batches. A
worker may report completion, hand off work, or make a task-state transition
only after every required batch has produced terminal output and an exit
status.

If a command tool returns a background handle, the worker must use that tool's
blocking output collection until the command reaches a terminal state. For
Claude Code this is `TaskOutput(block=true)`. Starting a command, beginning
collection, or waiting for a notification is not completion. Commands whose
results are required for acceptance must not be detached with `nohup` or `&`.

Timeout, killed, and still-collecting results are never passing evidence. When
a batch cannot finish within its bound, preserve meaningful progress with an
anchor commit and use a formal handoff or a genuine blocker. Keep each batch
small enough that its complete output and exit status can be recorded.

## Claude one-shot delivery setting

The repository provider entries `claude` and `claude2` set
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in their existing `runtime.env`.
The existing Claude adapter passes that value to the one-shot CLI child and
the live-config renderer projects it from repository config. The setting is
scoped to supervisor-delivered one-shot sessions; it does not change an
interactive user's shell, merge the two Claude identities, or affect other
providers.

This vendor setting also disables background subagents, and it is not a
general shell parser capable of rejecting every raw background construct.
The wakeup completion contract and terminal evidence remain required. Worker
process-group cleanup, leases, and canonical task completion authority are
unchanged.

## Delivery boundary

Merging this source change does not prove that live policy is active. The
existing exact-runtime promotion flow must project the reviewed repository
config and pass its normal preflight before a live supervisor uses it. A later
real one-shot dispatch can provide spawned-environment and terminal-result
evidence; this source task does not promote or restart the live supervisor.
