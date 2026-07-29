# OPS-L12 Claude Dispatch Smoke Evidence

Observation window: `2026-07-28T13:24:40Z` to
`2026-07-28T13:25:24Z`.

## Verdict

The smoke passed its dispatch acceptance. The supervisor admitted a real
`claude_cli` worker on provider slot `claude1-1`, and the Claude stream
identified CLI `2.1.220`, model `claude-opus-5[1m]`, and session
`6964721c-0447-4925-9d88-39036582a893`.

The worker made seven `Bash` or `Read` tool calls. None of their tool results
reported an error, and the terminal result recorded no permission denials.
The request was then interrupted; the runner ended by signal 15 with exit 143
and `terminal_reason=aborted_streaming`. That terminal result is evidence of
the explicit interruption, not an authentication, quota, startup, or
tool-permission failure.

## Boundaries

- The smoke changed no runtime configuration or product code.
- The runtime configuration predated the run and selected
  `claude_cli` for both the Claude adapter and delivery mode.
- The task worktree remained at the bound runtime SHA with no tracked or
  staged diff during the smoke. Only the supervisor-materialized task brief
  was untracked.
- The later supervisor reconciliation, preemption, and priority-supersede
  records occurred after the runner had already finished, so they are not
  attributed as the cause of the run termination.
- This evidence proves supervisor dispatch-to-running and truthful provider
  reporting. It does not claim the interrupted worker completed application
  work or prove twelve-loop product completion.

The machine-readable evidence, external artifact hashes captured during owner
closeout, acceptance mapping, and exact verification commands are in
`evidence.json`. `evidence.sha256` binds this README and the manifest.

## Exact-head review state

PR #4300 was reopened after `dev` advanced. The owner composed
`9f7f95e1a10a46248a81b26159e373f75525222f` and reran the focused evidence
checks. The earlier Codex2 approval and canonical review status were bound to
PR head `607df32e1dc658080a282858aa1441967c3df700`; they remain historical
evidence only and must not authorize the refreshed head. The task therefore
remains `in_progress` until Codex2 independently reviews and approves the new
exact PR head.
