# GCP VM Remote Development

This repo can be developed on the GCP VM through a small remote stack and a local sync loop.

## VS Code Remote SSH

The local SSH alias is already configured in [~/.ssh/config](/home/ajoe734/.ssh/config:1):

```sshconfig
Host pantheon-gcp
  HostName 34.68.24.220
  User edna
  IdentityFile ~/.ssh/pantheon_gcp_vm_ed25519
```

Use it like this:

```bash
ssh pantheon-gcp
cd ~/code/pantheon
```

In VS Code:

1. Install the `Remote - SSH` extension.
2. Run `Remote-SSH: Connect to Host...`.
3. Choose `pantheon-gcp`.
4. Open `/home/edna/code/pantheon`.

Recommended first checks in the remote terminal:

```bash
git status
docker --version
docker compose version
```

## Sync Local Changes To The VM

Use the sync helper from the local repo root:

```bash
bash scripts/sync_remote_dev.sh
```

What it does:

- rsyncs the local working tree to `/home/edna/code/pantheon`
- excludes local-only state such as `.git/`, venvs, `__pycache__`, and orchestrator runtime files
- refreshes git submodules on the VM
- prints the remote `git status`

Optional overrides:

```bash
PANTHEON_REMOTE_HOST=pantheon-gcp \
PANTHEON_REMOTE_PATH=/home/edna/code/pantheon \
bash scripts/sync_remote_dev.sh
```

For VM cutover or full-state handoff, use the handoff wrapper instead:

```bash
bash scripts/sync_remote_handoff.sh
```

That mode keeps only machine-local exclusions such as `.git/`, venvs, cache files, `*.pid`, and `*.lock`, so repo state like `.orchestrator/state.json`, planning state, task briefs, and evidence also move to the VM.

## Minimal Remote Dev Stack

The minimal stack is defined in [docker-compose.remote-dev.yml](/home/ajoe734/code/pantheon/docker-compose.remote-dev.yml:1).

It starts only:

- `signal-store`
- `control-plane-persona`
- `control-plane-router`

All ports bind to `127.0.0.1` on the VM, so they are not exposed directly to the public internet.

Start it from your local machine:

```bash
bash scripts/remote_dev_stack.sh up
```

Check status:

```bash
bash scripts/remote_dev_stack.sh status
bash scripts/remote_dev_stack.sh health
```

Stop it:

```bash
bash scripts/remote_dev_stack.sh down
```

## Remote Orchestrator Control

Use the helper below to manage repo-writer processes on the VM:

```bash
bash scripts/remote_orchestrator.sh status
bash scripts/remote_orchestrator.sh stop
bash scripts/remote_orchestrator.sh start
bash scripts/remote_orchestrator.sh logs
```

Recommended handoff sequence:

```bash
bash scripts/remote_orchestrator.sh stop
bash scripts/sync_remote_handoff.sh
bash scripts/remote_orchestrator.sh start
```

If you want to access the router from your local browser, open an SSH tunnel:

```bash
ssh -L 8001:127.0.0.1:8001 -L 8002:127.0.0.1:8002 pantheon-gcp
```

Then browse:

- `http://127.0.0.1:8001/health`
- `http://127.0.0.1:8002/health`

## Honest Service Stack

The full single-VM baseline for `BP5-SVC-016` now lives in [docker-compose.yml](/home/edna/code/pantheon/docker-compose.yml:1).

It boots the honest backend stack instead of the old research-worker topology:

- `runtime-manager`
- `governance`
- `telemetry`
- `incidents`
- `postmortems`
- `operator-bff`
- `signal-store`

Start it on the VM:

```bash
cd ~/code/pantheon
COMPOSE_BAKE=false docker compose up -d --build
```

Check health:

```bash
docker compose ps
curl -fsS http://127.0.0.1:18081/__health__ && printf '\n'
curl -fsS http://127.0.0.1:18082/health && printf '\n'
curl -fsS http://127.0.0.1:18083/__health__ && printf '\n'
curl -fsS http://127.0.0.1:18090/__health__ && printf '\n'
curl -fsS http://127.0.0.1:18091/__health__ && printf '\n'
curl -fsS http://127.0.0.1:18001/health && printf '\n'
```

Run the compose-backed smoke path from inside the same topology:

```bash
cd ~/code/pantheon
COMPOSE_BAKE=false docker compose --profile smoke up --build --abort-on-container-exit smoke-stack
```

That smoke profile proves:

- runtime-manager can create a canonical runtime binding
- telemetry can ingest an event against that binding
- incident and postmortem evidence services can persist linked records
- operator BFF stays in `fresh` mode and replays SSE without local fallback data

When you are done:

```bash
cd ~/code/pantheon
docker compose down --remove-orphans
```

## Dashboard Via `/dashboard/`

If you want the dashboard on the standard web port instead of exposing `4173`, install the nginx reverse proxy from the repo:

```bash
bash scripts/install_dashboard_proxy.sh
```

That maps:

- `http://<vm-host>/dashboard/` -> `http://127.0.0.1:4173/`

Recommended dashboard launch on the VM:

```bash
cd ~/code/pantheon
HOST=127.0.0.1 PORT=4173 bash scripts/run-dashboard.sh
```

Important: if `http://<external-ip>/dashboard/` still times out after nginx is up, the remaining issue is outside the VM, usually GCP ingress rules or missing instance network tags such as `http-server`.

## Dashboard Via Cloudflare Quick Tunnel

If you want a temporary public URL like `https://<random>.trycloudflare.com`, you can tunnel the local web port without opening any GCP firewall ports.

Install `cloudflared` once:

```bash
curl -fsSL -o /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i /tmp/cloudflared.deb
```

Then start the tunnel:

```bash
cd ~/code/pantheon
bash scripts/start_dashboard_tunnel.sh
```

The helper tunnels `http://127.0.0.1:80`, so the public entrypoint will redirect to `/dashboard/`.
