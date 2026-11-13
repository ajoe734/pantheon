# Third-Location Collector Stack (`deploy/collector/`)

This directory contains the self-contained configuration, container stack, systemd schedules, and Python implementation for Pantheon's third-location external knowledge collector host (`SD-STRATEGY-DISCOVERY-SOURCES-20261110` §6, `DEV_PLAN_STRATEGY_DISCOVERY_2026-11-10` §1–§2).

---

## 1. Security & Operational Boundary

1. **Isolated Provider & Billing**: Provision the host on a non-GCP provider (e.g., DigitalOcean Singapore 2 vCPU / 4 GB droplet or Hetzner Singapore CPX22) under the operator's separate account and payment method, never linked to GCP billing.
2. **Zero GCP / Pantheon Credentials**: Never place any GCP service account key, Pantheon database credential, or Pantheon control-plane secret on this host.
3. **Pull-Only Interface**: This host never calls Pantheon. It writes raw files and verifiable manifests under `/var/lib/pantheon-collector/bundles/` and serves them read-only over HTTPS (`443/tcp`) behind a bearer token (`COLLECTOR_BUNDLE_TOKEN`).
4. **Hard Start-Up Safety Gate**: Start-up (`validate-config`) fails closed if stealth fetching, Cloudflare/Turnstile bypass, proxy rotation, CAPTCHA solving, or logged-in sessions/cookies are enabled anywhere in `fetch-policy.json` or the environment.
5. **Personal Data Protection (Taiwan PDPA Art. 19)**:
   - Collection purpose is fixed to `"aggregate market attention and sentiment research"`.
   - Author handles are replaced on the collector host by `HMAC-SHA256(salt, platform + "+" + handle)` using a host-local salt (`/etc/pantheon-collector/author_hmac.salt`) that never leaves the host.
   - Display names, avatars, follower lists, and profile pages are stripped and never written to disk.
   - Raw bundles older than 90 days are automatically pruned (`pantheon-collector-prune.timer`).
6. **Automatic Source Pause**: Two consecutive runs encountering HTTP `429`, HTTP `403`, or a login redirect automatically pause that source and emit a `paused` manifest entry (`status: "paused"`). A paused source resumes only when its fetch policy configuration changes.

---

## 2. Step-by-Step Installation on a Fresh Linux Host (Ubuntu 24.04 / Debian 12)

### Step 2.1 — Install Docker Engine, Compose Plugin, and UFW Firewall

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg ufw openssl git

# Install Docker Engine and docker-compose-plugin
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Restrict inbound traffic to SSH (22/tcp) and HTTPS bundle server (443/tcp) ONLY
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
```

### Step 2.2 — Create Data Directories and Host-Local Secrets

```bash
# Data directories for bundles, per-source state, and operator-owned book library
sudo mkdir -p /var/lib/pantheon-collector/{bundles,state,library/books}
# The data root itself must be owned by uid 10001 too: the worker writes status.json at its top level
sudo chown -R 10001:10001 /var/lib/pantheon-collector
sudo chmod 0755 /var/lib/pantheon-collector /var/lib/pantheon-collector/bundles

# Secret directory (root-owned; salt never leaves this host)
sudo install -d -m 0700 /etc/pantheon-collector

# Generate the host-local 32-byte HMAC salt for author handle hashing
openssl rand -hex 32 | sudo tee /etc/pantheon-collector/author_hmac.salt > /dev/null
sudo chown 10001:10001 /etc/pantheon-collector/author_hmac.salt
sudo chmod 0400 /etc/pantheon-collector/author_hmac.salt

# Optional: place non-GCP YouTube Data API v3 and Gemini API keys
sudo touch /etc/pantheon-collector/youtube_data_api_key /etc/pantheon-collector/gemini_api_key
sudo chown 10001:10001 /etc/pantheon-collector/youtube_data_api_key /etc/pantheon-collector/gemini_api_key
sudo chmod 0400 /etc/pantheon-collector/youtube_data_api_key /etc/pantheon-collector/gemini_api_key
```

### Step 2.3 — Copy `deploy/collector/` and Configure `.env`

```bash
sudo mkdir -p /opt/pantheon-collector
# Copy this directory (deploy/collector/*) into /opt/pantheon-collector/
cd /opt/pantheon-collector
sudo cp .env.example .env
sudo chmod 0600 .env

# Generate the read-only bundle bearer token (copy this value to Pantheon's PANTHEON_COLLECTOR_BUNDLE_TOKEN secret)
BUNDLE_TOKEN="$(openssl rand -hex 32)"
sudo sed -i "s/REPLACE_WITH_OPENSSL_RAND_HEX_32_ON_HOST/${BUNDLE_TOKEN}/" /opt/pantheon-collector/.env
# Edit COLLECTOR_DOMAIN and COLLECTOR_CONTACT_EMAIL in /opt/pantheon-collector/.env
```

### Step 2.4 — Validate Policy and Launch the Container Stack

```bash
cd /opt/pantheon-collector

# Build the worker image (installs from the committed requirements.lock: CPU-only torch, no resolver run)
# and verify start-up policy checks
sudo docker compose build collector-worker
sudo docker compose run --rm collector-worker --policy-file /app/fetch-policy.json validate-config

# Start the unmodified RSSHub container and the HTTPS Caddy bundle server
sudo docker compose up -d rsshub bundle-server
```

> **Changing the Caddyfile:** `bundle-server` bind-mounts `Caddyfile` as a single file, which pins the old inode.
> After any edit (including `git pull`), run `sudo docker compose up -d --force-recreate bundle-server`; a plain
> `restart` or `caddy reload` keeps serving the old file. The token check must stay inside `route { }` because Caddy
> otherwise orders `handle` (file_server) before `respond`, which once served files without a token.

The data root is mounted whole at `/data` in the worker (`docker-compose.yml`) and read-only at `/srv/collector`
in Caddy, so `status.json` and `bundles/` written by the worker are what the bundle server serves. Set
`COLLECTOR_GEMINI_MODEL` in `.env` to a model your key can list (default `gemini-2.5-flash`). Source runs are
rate-limited for real (GDELT waits 5 s between requests).

### Step 2.5 — Install and Enable Systemd Timers

```bash
sudo cp /opt/pantheon-collector/systemd/pantheon-collector*.service /etc/systemd/system/
sudo cp /opt/pantheon-collector/systemd/pantheon-collector*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Enable the daily 90-day bundle pruner
sudo systemctl enable --now pantheon-collector-prune.timer

# Enable timers for enabled sources (PTT mirrors stay disabled per O-11; collector-tw-macro-dgbas is disabled
# because www.stat.gov.tw serves a Cloudflare challenge, see `disabled_reason` in fetch-policy.json)
for src in \
  collector-tw-macro-cbc \
  collector-tw-macro-ndc \
  collector-gdelt \
  collector-macro-dbnomics \
  collector-papers \
  collector-paper-arxiv \
  collector-paper-openalex \
  collector-paper-nber-rss \
  collector-tw-threads \
  collector-tw-instagram-popular \
  collector-tw-cmoney-forum \
  collector-tw-ptt-stock \
  collector-youtube-finance \
  collector-youtube-gemini-notes \
  collector-library-books \
  collector-news-rss; do
  sudo systemctl enable --now "pantheon-collector@${src}.timer"
done
```

### Step 2.6 — Supply Operator Books and Verify HTTPS Output

1. Place owned or licensed `.pdf` or `.epub` files in `/var/lib/pantheon-collector/library/books/`.
2. Trigger a one-shot run and verify the bundle server over HTTPS:

```bash
sudo systemctl start pantheon-collector@collector-tw-macro-cbc.service

curl -fsSL \
  -H "Authorization: Bearer ${BUNDLE_TOKEN}" \
  "https://${COLLECTOR_DOMAIN}/status.json"

curl -fsSL \
  -H "Authorization: Bearer ${BUNDLE_TOKEN}" \
  "https://${COLLECTOR_DOMAIN}/bundles/index.json"
```

---

## 3. Local Offline Test Suite

All spiders, pollers, manifest hashers, HMAC scrubbers, start-up safety checks, and auto-pause state transitions run offline against captured fixtures in `deploy/collector/tests/fixtures/` with socket connections blocked:

```bash
# requirements.lock is regenerated with the `uv pip compile` command in its header
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest -q deploy/collector
```

## Price-history backfill (TWSE / TPEx / T86)

Daily timers fetch only the newest published trading day. History is loaded once with the
`backfill` command: one request per trading day (per-date all-stock page, one connection,
>= `min_interval_seconds` = 5 s apart), one bundle per date (`run_id=backfill-<date>`), each row's
`published_at` is its own trade date so the bundle connector ingests it as point-in-time history.

```
docker compose run --rm collector-worker --policy-file /app/fetch-policy.json --root-dir /data \
  backfill --source collector-tw-twse-price-history --two-years --dry-run   # request count + wall-clock
docker compose run --rm collector-worker ... backfill --source collector-tw-twse-price-history --two-years
# also: collector-tw-tpex-price-history, collector-tw-twse-t86
# ranges: --start-date YYYY-MM-DD [--end-date YYYY-MM-DD] | --trading-days N
```

- Known closures (`collector_stack/tw_exchange_holidays.json`, extend with `--holidays-file`) are never requested.
  An unlisted closure costs one request; the exchange's "no data" answer is recorded as a holiday.
- Resume: completed dates are recorded in `/data/state/backfill/<source>.json`; re-running the same command
  skips them. A block (429/403) or fetch failure stops the run (exit 2) and the date is retried next time.
- `run --source ... --start-date/--end-date/--trading-days` passes the same dates to one ordinary run bundle.
- Two years ≈ 487 requests per source ≈ 41 minutes; all three sources ≈ 123 minutes (run sequentially).

## Same-day close capture (14:40-17:00 Taipei)

OpenAPI publishes a day's rows only the next morning, so `pantheon-collector-close.timer` runs
`capture-close` every 10 minutes Mon-Fri from 14:40 to 17:00 Asia/Taipei (first attempt after 14:40, last
at 17:00). Each of the TWSE and TPEx price-history sources is requested once per trading day for *today's*
date (one connection, >= 5 s between the two requests, same fetch policy); success is recorded in
`/data/state/same-day-close/<source>.json` and later ticks skip it. A page that is not published yet or a
failed fetch exits 2 (accepted by the unit) and is retried by the next tick. Outside the window, on weekends
and on known holidays the command does nothing. Bundles are named `close-<date>-<HHMM>`.
The existing 6-hourly `pantheon-collector@<source>.timer` keeps running unchanged.

### Hosted intake continuation and offered sources

Hosted market bundle intake retains one Collector pull per controller scheduler
request and the 15-second operation read budget. A successful, persisted market
prefix capped by rows or time now requests a continuation after one second,
until its ordered sweep completes. Newest price-session rows still precede the
history sweep; held symbols retain their existing ordering. Producer partial or
paused bundles and failed pulls retain normal cadence. At most four guarded raw
reads overlap for newest sessions and sixteen for historical sweeps; bundle order, verification, cursor acknowledgement
and persistence remain ordered. This does not change Collector requests to exchanges.

Each status publication includes **all fetch-policy sources**, including sources
that have never run. Existing `status`, run, cadence and pause fields remain;
`offered`, `offered_reason`, `policy_enabled`, and `timer_enabled` are additive.
Disabled policy or missing required YouTube/Gemini credentials makes a source
not offered. Credentials are checked only for presence and never published.
Provisioning can supply `COLLECTOR_SCHEDULED_SOURCE_IDS` as a comma-separated
inventory of enabled source timers. An omitted source is then not offered with
`no_timer`; without this inventory, `timer_enabled` is `null` (unknown). The
inventory describes installed timers and must not be used to enable any timer.
DGBAS remains disabled for its recorded 403; this change does not enable YouTube.

Hosted readback exposes `collector_offering`. An explicit `offered: false`
prevents enqueue/claim and controller selection, without creating a failed run
or DLQ entry. Polling a later `offered: true` automatically restores selection.
One status read per host per readback/scheduler snapshot is bounded to two seconds.
Legacy status files without this flag retain their previous scheduling behaviour;
missing/unverified offering metadata does not fabricate a disabled source.

DBnomics and the individual arXiv/OpenAlex/NBER bundle sources have independent
hosted definitions and desired admission. `strategy-seed-paper-corpus` reads
`collector-papers` composite bundles only; it does not consume separately written
child-source bundles. Gemini notes remain subject to the community test-volume cap.
Books retain their existing operator hold. Actual hosted throughput/availability
is accepted separately by `COLLECTOR-HOSTED-INTAKE-ACCEPTANCE-20261113`; synthetic
rate evidence is in the throughput task's deployment evidence manifest.

Ingest also reuses a thread-local distillation ledger connection for one native
batch. Each record still uses the queue's existing FULL-synchronous transaction,
identity validation, commit and rollback. Connection setup/teardown is amortized;
no queued work or per-record durability is removed, and other threads/processes
continue using independent connections to the same ledger.

A verified market row replay reuses an existing normalized partition only when
its tenant/item/hash/event/availability identity is present in that partition
and the producing ingest receipt completed. New and changed rows still use the
normal writer. Receipt storage summaries add `new_normalized_row_count` and
`reused_record_count`; the existing reference counts describe all referenced
objects, including reused objects. A raw cache or cursor alone cannot suppress a write.
