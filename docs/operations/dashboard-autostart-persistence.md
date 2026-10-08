# Dashboard recovery persistence

The collaboration dashboard is served locally on `127.0.0.1:4180` and may be
published through a Cloudflare quick tunnel. Both processes run in tmux so they
survive the launching shell, while a persistent recovery probe recreates them
after a VM reboot or process loss.

## Served status root

The canonical status root comes from the live supervisor config, and the dashboard
serves its own checkout only when no live config exists.

An underivable live config makes the launcher fail closed. dashboard_keepalive.sh
then retries every 2 seconds and each attempt appends the derivation failure and
a Python traceback to `.orchestrator/logs/dashboard-run.log` until the config is
fixed, so that log is the first place to check. Add no rate limiting.

## Install

On the dev VM, install the recovery probe from the live Pantheon checkout:

```bash
python3 scripts/dashboard_autostart_install.py \
  --repo $HOME/code/pantheon \
  --method auto \
  --start-now
```

`auto` prefers a user-systemd timer and falls back to a tagged per-minute cron
entry. User systemd must have linger enabled for reboot persistence:

```bash
sudo loginctl enable-linger "$USER"
```

The normal dev root deployment performs this installation after provisioning
the supervisor watchdog and verifies that the timer plus local dashboard are
healthy.

## Verify

```bash
systemctl --user status pantheon-dashboard-autostart.timer --no-pager
curl -fsS http://127.0.0.1:4180/index.html | rg '協作看板'
cat $HOME/code/pantheon/.orchestrator/logs/cloudflared-dashboard.url
```

The URL file is the current tunnel identity. Do not recover an address from old
log lines: every quick-tunnel restart creates a new URL, and historical log
entries remain after the old DNS name expires.

## Remove

```bash
python3 scripts/dashboard_autostart_install.py --method systemd --uninstall
```

Use `--method cron --uninstall` when the cron fallback was installed.
