# COLLECTOR-HOURLY-TIMER-CADENCE-20261113 source evidence

Owner: Codex. Assigned independent reviewer: Claude. Source-only delivery.

`pantheon-collector-hourly@.timer` invokes the existing per-source service every
hour at `:15`, with `Persistent=true`, one-second accuracy and no randomized
delay. README Step 2.5 moves exactly the enabled sources with policy cadences
below 21600 seconds to that timer: `collector-gdelt` and `collector-news-rss`,
both 3600 seconds today. It stops and disables the alternate timer before
enabling the selected timer, for fresh installation and migration alike.

The remaining enabled sources retain the existing six-hour timer, including
its calendar and 300-second random delay. The README also includes the three
already-enabled exchange sources previously missing from the installation
list: `collector-tw-twse-t86`, `collector-tw-twse-price-history`, and
`collector-tw-tpex-price-history`. Neither the six-hour unit nor the policy
is changed.

The regression in `deploy/collector/tests/test_compose_and_docs.py` derives the
enabled and faster-source sets from `fetch-policy.json`, reads both committed
timer units, and executes the actual Step 2.5 shell loops with `sudo` replaced
by a logger. No host commands execute. Starting with both timers marked active
for every enabled source, it verifies disable-before-enable ordering, exactly
one active timer per source, complete enabled-source coverage, exclusion of
disabled sources, exact faster-source membership, and an interval no greater
than each source's policy cadence. A future sub-hour cadence fails until its
schedule and regression are updated.

GDELT fetching and startup validation remain unchanged: hourly / 3600 seconds,
at most two requests per run, one latest official 15-minute export. This
improves polling frequency without backfilling intervening exports or claiming
exhaustive event coverage.

## Validation

The full terminal transcript and exit statuses are in `validation.txt`:

```text
PYTHONPATH=deploy/collector .venv-pantheon/bin/python -m pytest deploy/collector/tests
176 passed, 2 skipped in 11.87s
Command exit status: 0
```

The initial bare `pytest` command exited 127 because the checkout-scoped venv
inherits the pytest module but has no pytest launcher. The successful command
uses that provisioned interpreter's module entry point; both attempts are
preserved in the transcript. The two existing optional skips concern the
browser runtime and opt-in image build; neither covers timer scheduling.

The focused compose/docs tests pass (3 passed); `git diff --check` passes.
`systemd-analyze calendar --iterations=4 --base-time='2026-11-13 22:00:00 UTC'
'*-*-* *:15:00'` exits 0 and yields 22:15, 23:15, 00:15 and 01:15 UTC.
The fetch policy, collector implementation, and original six-hour timer have
no diff against the task base `c1738cd5f`.

## Rollout boundary

After independent review and merge, the coordinator installs the source
artifacts and runs README Step 2.5 on the collector host. Hosted acceptance
should confirm one active timer per enabled source and hourly next-trigger
times for GDELT and news RSS. This worker performed no SSH, collector-host
installation, systemctl change, source run, source resume, or hosted validation.
Local calendar parsing and tests do not prove that the host is serving this
schedule. The task makes no deployment or hosted collection claim.
