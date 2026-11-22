# Trading Pulse hosted acceptance

Owner: Codex. Independent reviewer: Claude2. RC6 WP6.5, feeding
`MGMT-GATE-FULL-PASS-HOSTED-ACCEPTANCE-20261114`.

The four source changes are **merged**. Hosted acceptance is pending.
The candidate Root [45046460903](https://github.com/ajoe734/pantheon/actions/runs/45046460903)
stopped before switch: its exact backend dev-push CI was cancelled. The
candidate was neither deployed nor accepted. No rollback was needed.
See `evidence.json` for exact source, candidate and served identities,
observed CI states, deidentified rehearsal facts and residual ownership.

The previously served release's `deploymentState: accepted` does not prove
these repairs. The fresh viewer rehearsal still read three business Personas
and excluded one validation Persona; its 2317 source recorded zero fills and
0/452 quantity. The two other sources each recorded one fill and negative
PnL. Their missing currency and different valuation periods do not authorize
a subtotal. This rehearsal is preparation on the old pair, not a repaired
page pass. No candidate API, desktop/mobile, locale or navigation result is
claimed.

Resume this same task after a normal protected release containing all four
changes. Inspect normal reconciliation before dispatching another release.
Record terminal deployment/acceptance results and verify every expected
steady service at the expected image/revision, in addition to the public
manifest, actual frontend assets and `/bff/version`.

Run the existing read-only probes on that exact pair:

```sh
./acceptance/probes/trading-pulse-operator
# In the separate execute-plans checkout, using its viewer stdin contract:
node scripts/accept-persona-strategy-hosted-readonly.mjs \
  --mode trading-pulse --out-dir <private-dir>
```

Supply the API probe's existing viewer environment securely; the browser
receives credentials in RAM on stdin. Its fresh independent registry,
binding and telemetry witness must be within 15 minutes, with a separately
grounded historical rejection witness. See the helper's input contract and
the C evidence manifest referenced by `evidence.json`. Preserve the source's
exact instrument label from the authoritative strategy source. Do not infer
one from the display name. Collect terminal exits; 77, timeout, missing
witnesses and failed roundtrips remain unaccepted.

The required browser contexts are zh-TW desktop at 1440px, zh-TW narrow at
390px, and en-US desktop at 1440px. Verify keyboard Persona entry and return,
then actual historical refusal detail, source event/time/cause, tenant and
paper scope, and return. Refresh current figures if the roster or trading
facts changed. Keep current quantities distinct from the immutable 2317
refusal. Functional failure after a healthy switch means **deployed, not
accepted**, and returns to its existing owning chain.

Retain one deidentified summary and at most two masked screenshots per exact
pair, at most 5 MiB in total. This is on demand, with routine daily growth
zero. Replace duplicate temporary bundles; retain no credentials, raw source
bodies, browser profiles, traces or storage state. The manifest records the
measured directory size. This pending packet has no screenshots.

Residual finding: the pre-switch CI rejection belongs to RC1 WP1.4, the
existing `DEV-GREEN-COMMIT-DEPLOY-TRIGGER-20261117` work package and Release
and hosted operations domain. It requires normal CI/release progress, not
another operator execution grant. This task does not change the pipeline,
business data, trading rules or historical events.
