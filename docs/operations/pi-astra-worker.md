# Pi Astra auto-worker

`PiAstra` is the `piastra` logical agent, using provider `pi_astra`.
The supervisor launches Pi in the
leased task worktree through the existing `worker_runner`, with its existing
heartbeat, task binding, process termination, and bubblewrap boundary.
The adapter calls `openai-codex/gpt-6-astra` with `high` thinking. It does not
invoke Codex CLI. Project instructions still load; project extensions and
settings are not automatically trusted, and extension discovery is disabled.

## Host setup

The integration was validated with Node 22.23.2 and Pi 0.87.1:

```bash
npm install -g --prefix "$HOME/.local" --ignore-scripts @earendil-works/pi-coding-agent@0.87.1
PI_CODING_AGENT_DIR="$HOME/.pi/pantheon-astra" "$HOME/.local/bin/pi"
```

Run `/login openai-codex` in Pi and complete the browser or device login.
Pi owns its credentials; do not copy Codex refresh tokens or put credentials
in repository configuration. Update the model catalog if necessary:

```bash
PI_CODING_AGENT_DIR="$HOME/.pi/pantheon-astra" "$HOME/.local/bin/pi" update --models
```

`providers.pi_astra.pi` supplies the executable, agent directory, provider,
model and thinking level. The configured agent directory must exist before
launch. Only that directory is added to the existing writable tool directories;
the Pi executable and other worktrees remain read-only inside the sandbox.
Each invocation starts a new Pi session. Its session ID, log and usage are
recorded by the supervisor; the existing task lifecycle remains completion
authority.

## Capacity and account

`agents.piastra.max_parallel` is one. On the configured host, the independently
logged-in Pi account was compared with the existing Codex account on
2026-09-23 and matched. Consequently `providers.pi_astra.account` is `codex1`:
Pi and that Codex lane share its existing account cap of two. Fleet capacity
is unchanged. Verify the account mapping when installing on another host or
changing the login; a different client name does not establish another account.

The Pi model probe disables tools, extensions, project context, skills and
session persistence, and requires the requested model to return `OK`. The
existing delivery-health scheduler owns probe cadence and pause/recovery.

## Results and rollout

Pi JSON mode can exit with code zero after a model error. Interpret the final
assistant result only after `agent_settled`; `agent_end` can precede automatic
retry. User text and tool output are never control envelopes. An incomplete
stream after process exit is a worker failure, while promotion termination
continues to use the existing planned-drain behavior. Stream chatter alone
does not extend the work lease; source and commit progress still do.

Deliver changes on `dev`, then promote the exact supervisor runtime with the
existing promotion script. Preserve the existing task journal and watchdog
state. Validate a Pi-owned task's dispatch receipt, sandboxed file/tool work,
heartbeat, session and terminal result before expanding adoption. To stop new
Pi dispatch, set its `max_parallel` to zero through the normal config delivery
flow; an executable installation alone does not activate a worker.
