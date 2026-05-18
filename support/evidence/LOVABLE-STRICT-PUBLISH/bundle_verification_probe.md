# Bundle Verification Probe

Task: `LOVABLE-STRICT-PUBLISH`
Probe script: `scripts/audit_lovable_strict_publish.py`

## Probe Contract

`verify_strict_publish(deployment_url)` fetches the deployed Lovable document,
extracts linked Vite/Lovable assets, fetches those assets, and returns an
`AuditResult` dictionary with:

- `strict_env_confirmed`: true only when all required build flags are observed
  in the deployed document or bundles.
- `missing_flags`: required `VITE_*` keys whose required values were not
  confirmed.
- `bundle_urls`: hosted bundle assets discovered from the document.
- `bundle_hashes`: `sha256` and byte size for each fetched bundle asset.
- `forbidden_runtime_paths`: request path literals matching `/mocks/` or a path
  segment beginning with `seed`.
- `passed`: true only when strict env is confirmed, no forbidden runtime paths
  are present, and every fetch succeeds.

## Required Strict Env

```json
{
  "VITE_BFF_MODE": "live",
  "VITE_BFF_FALLBACK": "strict",
  "VITE_BFF_REAL_WRITES": "false"
}
```

## Forbidden Runtime Paths

The probe fails if the deployed document or fetched bundle contains request path
literals that would route the app to fallback data at runtime:

- any path containing `/mocks/`
- any path segment that begins with `seed`, such as `/seed.json`,
  `/assets/seed-fallback.json`, or `/data/seeded-users.json`

## Operator Usage

```bash
python3 scripts/audit_lovable_strict_publish.py \
  <lovable-deployment-url> \
  --output support/evidence/LOVABLE-STRICT-PUBLISH/strict-publish-audit.json \
  --report support/evidence/LOVABLE-STRICT-PUBLISH/strict-publish-audit.md
```

Exit code `0` means the audit passed. Exit code `1` means at least one strict
flag, forbidden runtime path, or fetch check failed.
