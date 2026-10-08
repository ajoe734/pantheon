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

Run it against the dev BFF recorded as `DEV_BFF_URL` in [§ 3.1](../../deployment/vm-dev-staging-prod-management-plan.md#31-dev); always set `BFF_BASE`, because the script has no default BFF target:

```bash
BFF_BASE=<dev-bff-url> \
BFF_TOKEN=op-dev:admin:mfa \
python3 scripts/devloop_census.py
```

Machine-readable output:

```bash
BFF_BASE=<dev-bff-url> python3 scripts/devloop_census.py --format json --output /tmp/devloop-census.json
```

The `right_half_started` flag is conservative. Empty ledgers are reported as a
valid census result, while transport failures, non-200 responses, or malformed
JSON are hard failures. Synthesized telemetry summary fallback rows do not count
as started unless they include material telemetry such as non-zero trades or
non-empty metrics.
