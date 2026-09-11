# GCP VM Remote Development

Last updated: 2026-09-11.

Use [the environment plan § 3.1](deployment/vm-dev-staging-prod-management-plan.md#31-dev)
for the current product Dev VM, repository variables and FE/BFF origins.
That is the single environment identity reference. This runbook does not
assign a second set of hostnames, IPs, Linux users or project defaults.

## Development host versus product deployment host

The development checkout, supervisor and TaskStore are development tooling.
The product deployment VM serves the BFF and frontend. Do not assume these
are the same host or copy a developer's home, canonical task journal, provider
credentials or worker state onto the product VM to deploy a release.

For VS Code Remote SSH, select the development host/account already configured
for the current machine. Open that account's verified Pantheon checkout and
check `git status -sb` and `git remote -v` before editing. There is no canonical
developer username or `/home/lupin/...` checkout path.

The product VM backend path is `DEV_REMOTE_DIR` in the environment plan
(the deploying user's `~/pantheon`). Hosted FE source remains the separate
`ajoe734/execute-plans` repository; never copy it into Pantheon.

## Remote dev inspection

`scripts/dev_vm_ssh.sh` is the existing direct-SSH transport. A configured
caller provides the host, account, private key file and pinned known_hosts
file; nothing is guessed from old VM defaults:

```bash
export DEV_DEPLOY_SSH_HOST="$(gh variable get DEV_DEPLOY_SSH_HOST --repo ajoe734/pantheon)"
export DEV_DEPLOY_SSH_USER="$(gh variable get NONPROD_REMOTE_USER --repo ajoe734/pantheon)"
# DEV_DEPLOY_SSH_KEY_FILE and DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE point to
# the already provisioned deployment credentials. Do not print their contents.
bash scripts/dev_vm_ssh.sh exec 'hostname; docker compose version'
```

This helper does not create a VM, change SSH metadata or issue another
approval credential. For deployment, use `Pantheon Nonprod Deploy` and the
[frontend hosting contract](frontend/execute-plans-dev-hosting.md). Passing a
connection check is not product readiness or served-release evidence.

## Local canonical task maintenance

Use `scripts/human-ops-status.sh` on the development host. It derives the same
canonical TaskStore binding as the live supervisor from:

```text
${PANTHEON_DEPLOY_ROOT:-$HOME/pantheon-ci-deploy}/runtime/live-supervisor-mainroot-config.json
```

An explicit `PANTHEON_LIVE_SUPERVISOR_CONFIG` override remains available for
isolated environments/tests. The default no longer points into another
account's home. Product BFF login and a separate MFA issuer are not needed to
maintain local development tasks. Existing operator holds remain effective.

For supervisor diagnosis and updates, use the current runtime commands
documented in [the development-tooling boundary](02-architecture/development-tooling-product-boundary.md).
Do not restart unrelated workers or promote a mutable checkout as part of a
product deploy.

## Retired migration instructions

The former VM migration and Benjamin cutover executables have been deleted:

- `scripts/gcp_dev_vm_migrate.sh`
- `scripts/migrate_to_benjamin_cutover.sh`

Their former source and runbooks remain in Git history, not another runnable
legacy directory. Do not re-create their old VMs, overwrite repository variables
with their defaults, probe their released addresses or copy Docker volumes.

Other older utilities such as `pull_old_vm_dev_state.sh`, `sync_remote_dev.sh`
and `sync_remote_handoff.sh` are **not current product deployment steps**.
Their historical source is not authority for a live host or account. Any
separately requested migration needs explicit current source/target inspection;
this cleanup does not run migration, copy credentials, move TaskStore state,
change cloud resources or resume a stopped deployment.
