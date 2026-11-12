# COLLECTOR-HOST-PROVISION-20261110 receipt

Read-only host/HTTPS/GitHub-metadata probes were run by Human/Ops at 2026-11-12T06:01:39Z; see `evidence.json`.

1. Provider: DMI reports DigitalOcean Droplet. Collector containers have no GCP credential env/mount flags and no root gcloud config. The "no shared GCP login or payment card" fact is the operator's direct answer (2026-11-12T05:07:37Z), not inferred from DMI.
2. Authenticated `/status.json` -> 200 (16 sources, verified HTTPS, no redirects). Unauthenticated `/status.json` and `/bundles/index.json` -> 401.
3. GitHub dev variable `PANTHEON_COLLECTOR_HOST` and secret `PANTHEON_COLLECTOR_BUNDLE_TOKEN` are set; the secret value was never read or logged.

Limitations: credential check is scoped to Collector container env/mounts and root gcloud config, not a filesystem scan. Not activation, steady-pull restore, or three governed ingestion days.
