# Claude / Codex Session Operating Notes

Status: operating rule for any AI session (Claude Code, codex exec, …)
that talks to bash through a tool-call channel.
Last updated: 2026-05-17

This file is a short list of **session-level workarounds** for the
"tool result missing" / IO transport glitches observed during the
2026-05-17 git redesign work. Future AI sessions should read this
once at the start and follow the patterns below by default. Save the
"why" so the rules can be relaxed once the harness bug is fixed.

---

## The symptom

Some `Bash` tool calls return a `[Tool result missing due to internal
error]` marker even though the server-side command completed (fully or
partially). Pattern is not purely correlated with command shape:

- Long heredocs in argv (`git commit -m "$(cat <<EOF…<2KB>…EOF)"`) — failed often
- Many-arg commands (`git add file1 file2 … file14`) — failed often
- Pipelines into `jq` / `python3 -c` — sometimes
- Even trivial fast commands (`git add -u`) — occasionally

Root cause is almost certainly on the Claude Code / harness side
(stream multiplexing, buffering). It cannot be fixed from inside bash.

---

## Workarounds (proven during 2026-05-17 redesign)

### Commits

✅ **Use `git commit -F /tmp/<task>-msg.txt`** — pre-write the message
to a file with the `Write` tool, then reference the file.

❌ Do **not** use:

```bash
git commit -m "$(cat <<EOF
…multi-line message…
EOF
)"
```

The heredoc-in-argv form triggers the bug almost every time on
messages > ~500 bytes.

### Staging

✅ **`git add` no more than 3–4 paths per call**:

```bash
git add path/a path/b path/c
git add path/d
git add path/e
```

✅ **Or use a wrapper that stages from a known scope**:

```bash
scripts/git/safe_pr.sh <TASK-ID> \
    --message-file /tmp/<TASK-ID>-msg.txt \
    --scope path/a path/b path/c …
```

❌ Do **not**:

```bash
git add file1 file2 file3 file4 file5 file6 file7 file8 file9 file10 \
       file11 file12 file13 file14
# ↑ 14 args; failed during OPS-GIT-REDESIGN-001
```

### gh / jq / python3 pipelines

✅ **Two steps: save to /tmp, then read**:

```bash
gh pr view 59 --json state,mergeable > /tmp/pr.json
python3 -c "import json; print(json.load(open('/tmp/pr.json')))"
```

❌ Do **not**:

```bash
gh pr view 59 --json state,mergeable | python3 -c "…"
```

The pipe sometimes drops the result before the python interpreter
finishes parsing.

### Long-running commands

✅ **Use `run_in_background: true`** for anything > ~10 s and check
back later with the BashOutput tool. Push, fetch, deploy etc.

✅ **Capture sub-command output to a log**, return a single line:

```bash
{ <complex pipeline> } > /tmp/op.log 2>&1
tail -5 /tmp/op.log
```

The wrapper `scripts/git/safe_pr.sh` is built this way.

### When a tool result IS missing

When you see `[Tool result missing due to internal error]`:

1. **Do not blindly retry the same command.** The server side may have
   completed it. A blind retry can create a duplicate commit, a
   second PR, an extra tag.
2. **Inspect actual state with cheap idempotent reads**:
   - `git log -1 --oneline` — was the commit made?
   - `git status --short` — staging cleared?
   - `git ls-remote origin <branch>` — did the push succeed?
   - `gh pr list --head <branch>` — was the PR opened?
3. **Only retry once you know the prior call did NOT complete.**

### Heredoc as a SCRIPT, not as an argv

If you need a multi-step operation, write the whole thing to a script
with the `Write` tool and `bash /tmp/op.sh` it in **one** Bash call.
That collapses N tool calls into 1, dramatically reducing the failure
surface.

This is what `scripts/git/safe_pr.sh` does for the task-PR flow:
4–5 tool calls became 1.

---

## Anti-patterns to never use

| Anti-pattern | Why bad | Use instead |
|---|---|---|
| `git commit -m "$(cat <<EOF…EOF)"` | argv ≥ ~1KB blows the channel | `git commit -F /tmp/msg.txt` |
| `git add f1 f2 … f10+` | many-arg failure mode | `git add` in batches of 3–4 |
| `gh … --json … \| python3 …` | pipe drops result | save to `/tmp/x.json`, read separately |
| `git add .` / `git add -A` | also stages runtime mirrors | explicit paths only |
| Heredoc in argv | same channel issue | `Write` tool → file |
| Retrying on "missing tool result" | duplicates state | check state first with cheap reads |

---

## Quick reference — the one-line task PR

For 90% of task-closeout cases:

```bash
# 1. Write commit message to a file (Write tool)
echo "OPS-FOO-001: implement foo

LLM-Agent: Claude
Task-ID: OPS-FOO-001
Reviewer: Codex
Verified: pytest tests/foo -q
" > /tmp/OPS-FOO-001-msg.txt

# 2. One Bash call — does start + commit + push + PR + auto-merge
scripts/git/safe_pr.sh OPS-FOO-001 \
    --message-file /tmp/OPS-FOO-001-msg.txt \
    --scope services/foo/

# 3. (No step 3. PR is open with auto-merge; CI gates the rest.)
```

That's the entire happy path. If `safe_pr.sh` exits non-zero, look at
`/tmp/safe-pr-OPS-FOO-001.log` for the captured transcript.

---

## When to escalate

If the same flaky failure blocks a critical task for > 2 retries:

1. Stop. Don't keep retrying.
2. Save the failure context to a chair-review note.
3. File a follow-up `OPS-CLAUDE-HARNESS-*` task for human investigation.
4. The fix is on the Anthropic / Claude Code harness side; from
   inside bash we can only document workarounds.

---

## References

- `scripts/git/safe_pr.sh` — the wrapper this doc orbits around
- `scripts/git/task_start.sh`, `task_finalize.sh`, `worker_commit.py`
  — the underlying primitives `safe_pr.sh` composes
- `docs/conventions/GIT_WORKFLOW.md` — the broader branching model
