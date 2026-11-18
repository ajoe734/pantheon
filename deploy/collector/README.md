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

Sources enabled in `fetch-policy.json` with `cadence_seconds < 21600` use
`pantheon-collector-hourly@.timer`: currently `collector-gdelt` and
`collector-news-rss` (both 3600 seconds). It fires every hour at `:15`, with
one-second accuracy and no randomized delay. Both timer templates invoke the
existing `pantheon-collector@<source>.service`.

All other enabled sources retain the unchanged six-hour
`pantheon-collector@.timer` (`00/6:15:00`, up to 300 seconds of randomized delay).
When changing the policy, update these source lists and ensure the chosen timer
is at least as frequent as each source's cadence; a cadence below one hour
requires a faster timer. The commands below also migrate an existing installation:
stop and disable the alternate timer before enabling the chosen timer, so each
source has exactly one active source timer.

GDELT still fetches only the single latest 15-minute export per run; hourly
scheduling does not provide backfill or full coverage of all 15-minute exports.

```bash
sudo cp /opt/pantheon-collector/systemd/pantheon-collector*.service /etc/systemd/system/
sudo cp /opt/pantheon-collector/systemd/pantheon-collector*.timer /etc/systemd/system/
sudo systemctl daemon-reload

# Enable the daily 90-day bundle pruner
sudo systemctl enable --now pantheon-collector-prune.timer

# Hourly sources: stop their old six-hour timers before enabling the faster ones.
for src in \
  collector-gdelt \
  collector-news-rss; do
  sudo systemctl disable --now "pantheon-collector@${src}.timer"
  sudo systemctl enable --now "pantheon-collector-hourly@${src}.timer"
done

# Six-hour timers for the remaining enabled sources (PTT mirrors stay disabled per O-11; collector-tw-macro-dgbas is disabled
# because www.stat.gov.tw serves a Cloudflare challenge, see `disabled_reason` in fetch-policy.json)
for src in \
  collector-tw-macro-cbc \
  collector-tw-macro-ndc \
  collector-tw-twse-t86 \
  collector-tw-twse-price-history \
  collector-tw-twse-index-breadth \
  collector-tw-twse-taiex-history \
  collector-tw-taifex-vix \
  collector-tw-tpex-price-history \
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
  collector-library-books; do
  sudo systemctl disable --now "pantheon-collector-hourly@${src}.timer"
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

## Warrants: collected while listed, skipped and deleted at expiry

Design of record: `SD_MARKET_DATA_ONE_WRITE_PER_DAY_2026-11-16` section 4.3 (decision B: 抓，到期就刪).
`fetch-policy.json` records `warrants.mode = listed_only` and the allowlist date.

- **Rule.** The exchange listing decides, never a symbol pattern (`WARRANT_SYMBOL_RE` is retired; it missed every
  TPEx warrant `7xxxxx`). A symbol is a warrant if and only if its ISIN-page row is under a warrant category
  header and its CFI starts with `RW`. Listed warrants are collected like any listed security. A code in no
  current listing (an expired warrant, an unknown code) is skipped and counted as `skipped_not_listed_count`.
  The T86, both price-history spiders and the suspension spider take the listing as `symbols_filter`.
- **Where the listing is fetched.** `CollectorRunner.refresh_security_listing(fetch_page, listing_date=...)` fetches
  `https://isin.twse.com.tw/isin/C_public.jsp?strMode=2` and `strMode=4` once per trading day with the same
  `services/source_ingestion/security_listing.py` that hosted uses (stdlib only; the runner imports it, or loads it
  from `PANTHEON_SECURITY_LISTING_MODULE`). The collector image must ship that file (see deployment note below).
  The state file is `<root>/state/security-listing.json`.
- **Fail closed.** If the listing cannot be fetched or parsed the previous allowlist is kept for at most 3 trading
  days; after that price collection raises `listing_unavailable` and fetches nothing. A host whose listing was never initialised collects unfiltered with a logged warning (rollout shadow) until the first `refresh_security_listing` succeeds. While the listing is
  unreadable no symbol is treated as expired and nothing is deleted.
- **Expiry and deletion.** `ListingState.expired_warrants()` is the expired set (warrant whose `last_seen` is older
  than the last successful listing day). `scripts/delete_expired_warrants.py` removes already expired warrants:
  dry run by default, report with digest first, real run only with `--execute --confirm-digest <digest>`.
  Four-digit (ordinary share shaped) symbols go to a review list; daily bars of delisted ordinary shares are kept.
- **Rollback.** `COLLECTOR_WARRANTS_MODE=off` (or `CollectorRunner(warrants_mode="off")`) removes the filter and
  restores the previous behaviour; nothing already stored becomes unreadable (the filter only decides what is
  newly written). Deleted warrant rows are not recoverable by design (nothing is archived).
- **Hosted reads the same listing.** After a successful refresh the runner publishes the state file as
  `<root>/bundles/security-listing.jsonl` (`CollectorRunner.publish_security_listing`, same bytes, replaced in one step,
  at most 20 MB; the live shape of 57,531 rows is about 3.2 MB). The read only bundle server already serves
  `bundles/*`, so hosted reads it through the bundle transport it already uses and never fetches the exchange pages.
  Hosted keeps it as one file replaced per listing day and runs it in `SOURCE_INGEST_SECURITY_LISTING_MODE`
  `off` | `shadow` (default, counts only) | `enforce`.
- **Deployment note.** The image (`docker compose build collector-worker`, build context is the repo root) ships
  `security_listing.py` at `/app/services/source_ingestion/`, byte-identical to the repo file at the same commit
  (a test fails on drift); no host-local Dockerfile edit is needed. If the module is missing the loader raises a
  `CollectorConfigError` naming the paths it looked for. The remaining step is scheduling the daily
  `refresh_security_listing` call (Human/Ops).

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
child-source bundles. Gemini video notes have an explicit ingestion exclusion: their native generated
summary has no hashed author and needs a generated-note attribution contract.
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

## Regime inputs: TWSE price indices, breadth and TAIEX history

Two independent sources use the six-hour timer template listed in Step 2.5.
Each timer run makes one request, with one connection and a minimum five-second
request interval. They do not change `collector-tw-twse-price-history`, its
`tw_price_daily` rows, or the latest stock-close projection.

- `collector-tw-twse-index-breadth` requests the latest published session's
  MI_INDEX `type=ALLBUT0999` page. It emits `tw_index_daily` for **all rows in
  價格指數(臺灣證券交易所)**, including 發行量加權股價指數 and every 類指數,
  with `index_name`, `close`, signed `change_points` and `change_percent`.
  Cross-market, Taiwan Index Company and total-return tables are separate
  upstream tables and are outside this source's output. It also emits one
  `tw_market_breadth_daily` row with `whole_market` and `stocks`, each containing
  `advancers`, `limit_up`, `decliners`, `limit_down`, `unchanged`, `untraded`,
  and `no_comparison`. Limit counts are included in their advancing/declining
  totals, not additional securities.
- `collector-tw-twse-taiex-history` requests one MI_5MINS_HIST calendar-month
  page per run and emits `tw_index_ohlc_daily`: `index_name`, `trade_date`,
  `open`, `high`, `low`, `close`. The default month is that of the latest
  published session. ROC dates are converted to Gregorian dates; only dates
  returned by TWSE are emitted. There is no holiday synthesis or price filling.

All rows include `exchange=TWSE`, `source_dataset`, `trade_date`, request `url`
and `fetched_at`. `published_at` uses the existing collector session-close
convention (06:30 UTC), not a claim about the upstream publication timestamp.
Original field names, row strings (including commas and HTML change signs),
and table title are retained both in `raw_values` and the raw JSON file.
Signed change points apply TWSE's explicit +/- sign; percent keeps its source
sign. A missing table, changed fields, malformed value, mismatched date or
inconsistent row invalidates the **entire run**: the bundle is partial, contains
zero rows, and records `parse_failed: <specific reason>`. No failed month is
marked completed.

For the initial 2012-01 to present TAIEX backfill, run from the collector install
with the existing compose configuration (see the price-history section above):

```bash
docker compose run --rm collector-worker \
  --root-dir /data backfill --source collector-tw-twse-taiex-history \
  --start-month 2012-01 --dry-run
docker compose run --rm collector-worker \
  --root-dir /data backfill --source collector-tw-twse-taiex-history \
  --start-month 2012-01
# Pin an inclusive end month for a bounded batch:
docker compose run --rm collector-worker \
  --root-dir /data backfill --source collector-tw-twse-taiex-history \
  --start-month 2012-01 --end-month 2012-03
```

`--end-month` defaults to the current UTC month. At 2026-11 this range is 179
requests, one per month, sequentially at least five seconds apart. There is one
bundle per month (`backfill-YYYY-MM`), while every row retains its own session
date. The existing `/data/state/backfill/<source>.json` ledger skips completed
months on rerun. The current month stays refreshable until a later invocation
fetches it after month-end. Month options cannot be combined with daily range,
trading-day-count or `--two-years` options. MI_INDEX index/breadth backfills use
the existing daily `--start-date` / `--end-date` options and a separate ledger.

Recorded development-host endpoint checks, fixture hashes and row counts are in
`docs/deployment/evidence/REGIME-DATA-COLLECTOR-INDEX-BREADTH-20261114/evidence.json`.
These checks do not claim collector-host deployment or Pantheon intake behavior;
those are owned by the separate deployment and intake tasks.

## Taiwan VIX (TAIFEX)

`collector-tw-taifex-vix` archives the 臺指選擇權波動率指數 from the official
TAIFEX pages (no browser needed; confirmed live 2026-11-14). TAIFEX only serves the
current month plus the prior three, so the first run takes all of it and later
runs are incremental:

| series | listing page | file |
| --- | --- | --- |
| daily close (+ 收盤前1分鐘平均) | `/cht/7/vixDaily3MNew` | `/file/taifex/Dailydownload/vix/log2data/YYYYMMnew.txt` |
| intraday, current month | `/cht/7/vixMinNew` | `/cht/7/getVixData?filesname=YYYYMMDD` (one file per day) |
| intraday, prior 3 months | `/cht/7/vixMin3MNew` | `/file/taifex/Dailydownload/vix/logdata/YYYYMM歷史紀錄波動率指數_新VIX.zip` (members `tvix_YYYYMMDD`) |

Files are Big5 tab-separated text; the raw file (or zip) is archived byte-for-byte
and the items carry parsed `rows` (`trade_date`, `time` HH:MM:SS.ff plus `time_raw`, value).
Datasets: `tw_vix_daily` (one item per month file), `tw_vix_intraday` (one per
session day) and `tw_vix_intraday_archive` (the monthly zip). Intraday sessions
end with a `Last 1 min AVG` line, kept as `last_1min_avg`, not as a time row.
`getVixData` answers 200 with an HTML page for an unknown date, so only dates listed
on the pages are requested and HTML is treated as a format change. A changed header,
bad row, missing link, bad zip or unexpected member invalidates the **whole run**
(partial bundle, zero rows, `parse_failed: <reason>`). Finished files (past-month
daily files, complete sessions, zips) are recorded in
`/data/state/taifex-vix/ledger.json` only after the bundle is published, so reruns
are idempotent and request just the new/changing files. A first run is about 30
requests at least five seconds apart. Run once with
`python -m collector_stack.cli --root-dir /data run --source collector-tw-taifex-vix`.

## Survivorship Completeness: Non-Traded/Suspended Rows, Official Holiday Schedule, and Trading Suspensions

### 1. Preserving Non-Traded and Suspended Securities in Price-History Bundles

Both `collector-tw-twse-price-history` (`TwseDailyPriceHistorySpider`) and
`collector-tw-tpex-price-history` (`TpexDailyPriceHistorySpider`) retain every listed
security row present in the dated all-market quote table even when its session close
price is `"--"` (TWSE `MI_INDEX`) or `" ---"` / `"---"` (TPEx `dailyQuotes`):

- Such rows are emitted as explicit `tw_price_daily` items with `open=null`,
  `high=null`, `low=null`, `close=null`, `change=null` (no price is invented or
  interpolated), `not_traded=true`, `suspended_or_not_traded=true`,
  `trading_status="not_traded"`, and `raw_close` preserving the original upstream
  close string (`"--"` or `"---"`).
- Normal traded securities carry `not_traded=false`,
  `suspended_or_not_traded=false`, and `trading_status="traded"`.
- Existing downstream consumers that require a numeric price (`candidate_pool_market_feed._bar`,
  `LatestMarketSnapshotStore._point_from_record`, `feature_catalogue.observations_from_row`,
  and `TaiwanOfficialMarketDatasetAdapter.records_from_price_history_payload`) skip
  `close=null` / `not_traded=true` rows and leave their outputs unchanged.

### 2. Official Holiday Schedule and Trading Suspension Sources (Disabled by Default)

Two sources in `fetch-policy.json` collect official TWSE and TPEx governance records
covering `2024` through `2026` with single-connection (`max_connections=1`) and
`min_interval_seconds=5.0` spacing:

| Source ID | Dataset | Official Endpoints | Official Publication References | Default Policy State |
| --- | --- | --- | --- | --- |
| `collector-tw-trading-holidays` | `tw_trading_holidays` | TWSE: `https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule?date={yyyy}0101&response=json`<br>TPEx: `https://www.tpex.org.tw/www/zh-tw/bulletin/tradingDate?date={yyyy}&response=json` | TWSE: `https://www.twse.com.tw/zh/trading/holiday.html`<br>TPEx: `https://www.tpex.org.tw/zh-tw/announce/market/holiday.html` | `"enabled": false` (6 requests/run across 2024–2026) |
| `collector-tw-trading-suspension` | `tw_trading_suspension` | TWSE: `https://www.twse.com.tw/rwd/zh/afterTrading/TWTAWU?startDate={start_yyyymmdd}&endDate={end_yyyymmdd}&response=json`<br>TPEx: `https://www.tpex.org.tw/www/zh-tw/bulletin/sprcHis?date={yyyy}&cate=1&response=json` | TWSE: `https://www.twse.com.tw/zh/trading/historical/twtawu.html`<br>TPEx: `https://www.tpex.org.tw/zh-tw/announce/market/halt/historical.html` | `"enabled": false` (4 requests/run across 2024–2026) |

Both sources retain the raw upstream values (`raw_values` and `raw/*.json`) and the
official publication reference (`official_publication_ref`), and fail closed (`partial`
bundle with `0` items and `parse_failed: <reason>`) if a table is missing or any header
or field format changes. They do not alter the pinned 2026 TWSE schedule
(`twse-2026-schedule-v1`) used by market admission.

### 3. Operator Deployment, Timer Enablement, and Rollback Step

Both sources are disabled by default (`"enabled": false` in `deploy/collector/fetch-policy.json`)
until the operator deploys them on the collector host:

```bash
# Deploy / enable after setting "enabled": true for both sources in /opt/pantheon-collector/fetch-policy.json:
for src in \
  collector-tw-trading-holidays \
  collector-tw-trading-suspension; do
  sudo systemctl disable --now "pantheon-collector-hourly@${src}.timer"
  sudo systemctl enable --now "pantheon-collector@${src}.timer"
done

# One-shot verification run:
sudo systemctl start pantheon-collector@collector-tw-trading-holidays.service
sudo systemctl start pantheon-collector@collector-tw-trading-suspension.service
```

**Rollback step** (disable timers, set `"enabled": false` in `fetch-policy.json`, and
verify config):

```bash
for src in \
  collector-tw-trading-holidays \
  collector-tw-trading-suspension; do
  sudo systemctl disable --now "pantheon-collector@${src}.timer"
  sudo systemctl disable --now "pantheon-collector-hourly@${src}.timer"
  sudo systemctl stop "pantheon-collector@${src}.service" || true
done
# Restore "enabled": false in /opt/pantheon-collector/fetch-policy.json and validate:
docker compose run --rm collector-worker --policy-file /app/fetch-policy.json validate-config
```

### 4. Two-Year History Recovery Analysis and Bounded Re-Collection Plan

Nothing is re-collected by `COLLECTOR-TW-SUSPENSION-HOLIDAYS-20261115`:

1. **What can be recovered from retained raw captures without new requests**:
   - Existing per-item price-history bundle raw files
     (`/data/bundles/collector-tw-{twse,tpex}-price-history/<date>/<run_id>/raw/*.json`)
     cannot recover previously dropped non-traded/suspended securities (`0` dates
     recoverable from per-item raw files), because the prior spiders skipped
     `close_price is None` rows at `spiders.py` lines `993–994` (TWSE) and
     `1447–1448` (TPEx) before building `CollectedItem` or writing `raw/<item_id>.json`,
     and each `raw/<item_id>.json` contains only that single security's row rather than
     the full upstream quote table.
   - Where full-page upstream `MI_INDEX` / `dailyQuotes` JSON captures are retained on
     disk within the 90-day retention window (`raw_retention_days = 90`), those
     retained dates can be re-parsed offline with `0` new HTTP requests.
2. **Bounded re-collection plan where full-page raw captures do not exist**:
   - Published rate limits (`fetch-policy.json`): `max_connections = 1`,
     `min_interval_seconds = 5.0` (at most 1 request every 5 seconds per exchange).
   - `collector-tw-twse-price-history`: `488` per-date all-stock `MI_INDEX` requests
     for 2 years (`244` trading days/year × 2) = `2,440` seconds (~`40.7` minutes).
   - `collector-tw-tpex-price-history`: `488` per-date all-stock `dailyQuotes` requests
     for 2 years (`244` trading days/year × 2) = `2,440` seconds (~`40.7` minutes).
   - `collector-tw-trading-holidays` (2024–2026): `6` requests (`3` TWSE + `3` TPEx) =
     `30` seconds.
   - `collector-tw-trading-suspension` (2024–2026): `4` requests (`1` TWSE `TWTAWU` +
     `3` TPEx `sprcHis`) = `20` seconds.
   - Total bounded re-collection budget: `986` requests (`4,930` seconds ≈ `82.2`
     minutes sequential wall-clock time).
