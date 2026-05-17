# Background Worker Git Push Credentials

Task: `OPS-WORKER-PUSH-CRED-001`

## Decision

Pantheon background workers should use a worker-specific SSH key for normal `git push` publication. HTTPS with a GitHub token remains an explicit fallback for hosted environments where SSH keys cannot be mounted.

No raw credential value belongs in the repository, `ai-status.json`, task archives, review notes, logs, or generated docs. The repo stores only loader code, setup code, variable names, and local filesystem paths.

## Runtime Contract

Runtime code calls `.orchestrator/auth_loader.py`:

```python
from auth_loader import load_git_credentials

credentials = load_git_credentials()
env = credentials.as_subprocess_env()
```

The returned `env` is passed to non-interactive git commands. The loader sets `GIT_TERMINAL_PROMPT=0` so a missing or rejected credential fails fast instead of hanging a background worker.

Supported modes:

| Mode | Required configuration | Git environment returned |
| --- | --- | --- |
| `ssh` | `PANTHEON_WORKER_GIT_SSH_KEY_PATH` pointing at a `0600` private key | `GIT_SSH_COMMAND`, `GIT_TERMINAL_PROMPT=0` |
| `pat` | `PANTHEON_WORKER_GIT_ASKPASS` plus `PANTHEON_WORKER_GIT_TOKEN`, `GITHUB_TOKEN`, or `PANTHEON_WORKER_GIT_TOKEN_FILE` | `GIT_ASKPASS`, `GIT_TERMINAL_PROMPT=0`, username/token-file refs |
| `auto` | SSH path or PAT refs | Resolves SSH first, then PAT |

Default local env file:

```text
~/.config/pantheon/worker-git.env
```

Set `PANTHEON_WORKER_GIT_ENV_FILE` to point at another env file. Values from the live process environment override env-file values.

## SSH Setup

Create a dedicated key for the worker host or worker identity, add the public key to GitHub with write access to `ajoe734/pantheon`, then run:

```bash
PANTHEON_WORKER_GIT_SSH_KEY_PATH=/secure/path/id_ed25519 \
  scripts/setup_worker_credentials.sh --mode ssh
```

If the private key is delivered as an environment secret instead of a mounted file:

```bash
PANTHEON_WORKER_GIT_SSH_KEY="$(secret-manager-read worker_git_ssh_key)" \
  scripts/setup_worker_credentials.sh --mode ssh
```

The setup script writes local user-only files under `~/.config/pantheon/worker-git/`, records non-secret path references in `worker-git.env`, and runs the loader validation. It does not print private key material.

## PAT Fallback Setup

Use a fine-grained GitHub token scoped to the repository with the minimum permission needed for normal non-force push. Prefer secret-manager injection; if persistence is required on a worker host, let the setup script write a `0600` local token file outside the repo:

```bash
PANTHEON_WORKER_GIT_TOKEN="$(secret-manager-read worker_git_pat)" \
  scripts/setup_worker_credentials.sh --mode pat
```

The generated askpass helper reads `PANTHEON_WORKER_GIT_TOKEN_FILE`, `PANTHEON_WORKER_GIT_TOKEN`, or `GITHUB_TOKEN` at git runtime. The token value is never written to stdout.

## Rotation Policy

Rotate worker git credentials at least every 90 days and immediately after suspected exposure, worker-host replacement, or access-scope changes.

SSH rotation:

1. Generate or mount a new worker key.
2. Add the new public key to GitHub with write access.
3. Re-run `scripts/setup_worker_credentials.sh --mode ssh`.
4. Restart the affected worker process.
5. Verify `python3 .orchestrator/auth_loader.py --json` and a non-force push path.
6. Remove the old public key from GitHub and delete the old local private key.

PAT rotation:

1. Mint a new fine-grained token with the same or narrower scope.
2. Re-run `scripts/setup_worker_credentials.sh --mode pat`.
3. Restart the affected worker process.
4. Verify `python3 .orchestrator/auth_loader.py --json` and a non-force push path.
5. Revoke the old token in GitHub.

## Failure Behavior

Missing credentials, missing askpass helper, missing SSH key, permissive SSH key file modes, and missing token files raise `CredentialError` before git starts. This prevents background workers from blocking on interactive prompts and keeps raw secrets out of logs.
