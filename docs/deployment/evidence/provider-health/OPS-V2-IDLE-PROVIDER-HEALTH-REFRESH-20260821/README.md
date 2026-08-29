# Idle V2 provider-health topology reconciliation evidence

This task was reopened after PR #5113 merged and exact command runtime
`8dce056fe86b3bcb7bd3347497a4e4a241b2806e` was promoted. The bounded idle
refresh worked: live readback showed fresh healthy `codex1` and `claude2`
observations on separate configured accounts. Acceptance still failed because
the runtime projection retained the retired
`delivery_health.accounts.claude_account_shared_max_1` row even though no
configured delivery endpoint referenced that account.

The follow-up adds supervisor-owned topology reconciliation. The same
`_configured_delivery_endpoints` projection used by idle refresh and topology
fingerprinting now defines the only endpoint and account rows which may remain
in runtime delivery health. During the existing post-dispatch maintenance
transaction, after live observations are applied, the reconciler removes only
endpoint rows absent from that endpoint set and account rows absent from the
accounts referenced by those endpoints. A provider declaration without a
delivery endpoint owns no health row.

Configured endpoint/account records remain byte-equivalent, as do unrelated
runtime and health metadata. The reconciler returns `False` without replacing
the document when no orphan exists, so an unchanged topology does not create
an every-cycle health mutation. Applying observations before reconciliation
also prevents an obsolete in-flight or provider-only projection from
recreating an orphan in the committed snapshot.

Persistence remains under the existing reserved `post_dispatch_maintenance`
runtime phase and whole-state CAS transaction. This change adds no TaskStore,
state-file, provider-config, credential, V1 pause/recovery, product, production,
or capital write authority.

The committed [evidence.json](evidence.json) and companion
[evidence.sha256](evidence.sha256) form the review artifact. They bind the
implementation anchor, source blobs, deterministic diff hash, migration and
idempotence regressions, and full supervisor/provider/runtime-state suites.
Antigravity must independently review and approve the exact final PR head.

After merge, deployment authority must promote the exact merged command
runtime, run one bounded supervisor cycle through the governed runtime path,
and read back `delivery_health`. Acceptance requires fresh healthy configured
`codex1` and `claude2` rows and proves
`claude_account_shared_max_1` is absent without manually editing state JSON.
