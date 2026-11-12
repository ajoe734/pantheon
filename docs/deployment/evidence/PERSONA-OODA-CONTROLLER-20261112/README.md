# Per-persona daily OODA controller

Source implementation for design §6.1 / D1 / D2 / D13 in
`docs/04/pantheon_persona_cycle_reconnect_2026-11-12/SD_PERSONA_CYCLE_RECONNECT_2026-11-12.md`.
Owner: Codex. Assigned independent reviewer: Claude. Hosted acceptance is pending.

The existing Persona service lifespan reconciles active, tenant-bound
`paper_running`, `paper_owner` and `consultable` personas. OpenClaw remains the
only schedule executor. `pantheon.persona.daily-cycle` runs at `0 10 * * 1-5`;
the consumer poll only reads actual terminal run identities and never computes
a due time or force-runs a job. Exact readback repairs schedule drift and
duplicates. The four legacy recurring jobs retire after daily-job readback;
provisioning's first-evaluation schedule is preserved.

The pinned Gateway accepts system events on its main session. That event is
the dispatch receipt, not a second persona turn. The consumer makes the one
synchronous `/v1/responses` call to `openclaw/<persona_id>`, using the persona's
already provisioned restricted tool policy. It refreshes canonical workspace
memory through the existing authenticated adapter before the turn and includes
the validated active harness in the prompt. The adapter's existing cron proxy
now permits bounded `cron.runs` reads only for an exactly verified daily job;
it still refuses `cron.run`. The legacy force-run closure refuses this workflow.

Packet identities hash tenant, persona, workflow, job and `runId` (or the real
Gateway `runAtMs`). An fsynced observing snapshot claims the run before external
work. Kernel locking fences concurrent consumers over the shared packet volume.
An interrupted claim becomes an explicit terminal observing failure; it never
retries an uncertain turn or risk action. JSON and JSONL stores retain their
existing BFF shapes. Multiple snapshots represent one logical packet, never a
second packet for a repeated run.

Observe joins telemetry through the registry-owned persona capital binding,
not a guessed persona field on runtime summaries. It records member books,
pool health (including unknown metrics), incidents for the exact runtime
binding, memory materialization/source references and harness version. The
Agora owner supplies only consented, learning-eligible, unexpired teaching
references for the canonical tenant/persona; private content and user IDs stay
in Agora. A timestamp-plus-ID cursor bounds each read to 100 references without
discarding a same-timestamp remainder. Human-facing Agora reads retain their
existing tenant-plus-user policy. Owner-read outages stay explicitly unavailable.

Orient/proposals are validated against `CycleTurnOutput`. The model cannot
choose a URL, a command, or a gate verdict. The existing pool owner runs its
deterministic reweight/removal rules independently of the model. Other proposals
remain staged with `requires_owner_gate`; they do not bypass consultation,
admission, harness adoption, envelope publication or governance. Pool-version
receipts are owner records, not evidence that an execution envelope was
published. Memory candidates are staged in the packet, not falsely labelled
canonical memory writes. Provider/schema failure leaves the packet observing
with its reason and risk result.

Management's existing persona-health projection includes `persona_cycle_health`
(last packet, reached stage, failure and risk status). Private cycle packet
reads admit tenant scope from authenticated claims before applying the tenant
predicate; a caller-controlled tenant header is insufficient. The loop-control
writer publishes `per_persona_ooda` separately. The canonical health list remains
exactly twelve rows; catalog implementation metadata is not hosted liveness proof.

`PERSONA_DAILY_CYCLE_ENABLED` controls the existing process consumer (Compose
default true). Persona and BFF share the existing `bff-data` packet volume/path.
Gateway/adapter/telemetry credentials and the existing registry read-principal
settings are reused; this change issues no credentials and introduces no HTTP
route. The persona image lock adds the loop-control writer's existing asyncpg
dependency. Missing registry credentials, active harness, canonical memory or
own telemetry produce truthful failure/degraded records, never fallback data.

Local evidence uses real registrar/controller entry points, persistent Persona,
harness and pool application owners, registry record fixtures and the actual
telemetry projection store. Only OpenClaw HTTP is simulated in the consecutive
cycle integration. Fixtures do not prove hosted calls or market results.
Coverage includes two consecutive cycles, duplicate/concurrent consumption,
provider outage, process interruption/restart, both packet formats, deterministic
drawdown removal during outage, teaching-reference privacy, tenant-header
spoofing and the twelve-row invariant. Exact commands/results are in
`evidence.json` and the adjacent terminal-output logs.

Two broader BFF tests also fail on the untouched starting base `b59cc3d8f`:
the consultation catalog test still expects `not_implemented`, and the downstream
DLQ replay fixture lacks the concrete monitor tenant. `baseline-tests.txt`
records the isolated baseline reproduction. They are excluded by name from the
otherwise passing focused BFF batch, not counted as passed. No unrelated BFF
policy or fixture was changed to hide those failures.

Before terminal closeout, obtain independent exact-head source review, native
merge/check evidence, the admitted hosted FE/BFF identity, real cycles/private
memory for the actual three personas and the required consultation evidence.
Neither local tests nor source merge waives that remaining hosted obligation.
