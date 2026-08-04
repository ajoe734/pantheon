# Dashboard recovery persistence

The collaboration dashboard is served locally on `127.0.0.1:4180` and may be
published through a Cloudflare quick tunnel. Both processes run in tmux so they
survive the launching shell, while a persistent recovery probe recreates them
after a VM reboot or process loss.

## Install

On the dev VM, install the recovery probe from the live Pantheon checkout:

```bash
python3 scripts/dashboard_autostart_install.py \
  --repo /home/lupin/pantheon \
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
cat /home/lupin/pantheon/.orchestrator/logs/cloudflared-dashboard.url
```

The URL file is the current tunnel identity. Do not recover an address from old
log lines: every quick-tunnel restart creates a new URL, and historical log
entries remain after the old DNS name expires.

## Remove

```bash
python3 scripts/dashboard_autostart_install.py --method systemd --uninstall
```

Use `--method cron --uninstall` when the cron fallback was installed.
