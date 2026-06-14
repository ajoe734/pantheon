# Devloop Census

`scripts/devloop_census.py` is a repeatable, read-only progress meter for the
right half of the Pantheon dev loop. It probes the dev BFF with stub bearer auth
and counts these surfaces:

- `/api/v1/telemetry`
- `/bff/v5/loop-runs`
- `/bff/approvals`
- `/api/v1/evolution-decisions`
- `/bff/incidents`
- `/api/v1/rollbacks`

Run it against the current dev BFF:

```bash
BFF_BASE=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io \
BFF_TOKEN=op-dev:admin:mfa \
python3 scripts/devloop_census.py
```

Machine-readable output:

```bash
python3 scripts/devloop_census.py --format json --output /tmp/devloop-census.json
```

The `right_half_started` flag is conservative. Empty ledgers are reported as a
valid census result, while transport failures, non-200 responses, or malformed
JSON are hard failures. Synthesized telemetry summary fallback rows do not count
as started unless they include material telemetry such as non-zero trades or
non-empty metrics.
