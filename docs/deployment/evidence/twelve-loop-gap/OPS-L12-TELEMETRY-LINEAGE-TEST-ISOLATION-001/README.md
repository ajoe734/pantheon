# OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001

Isolate the telemetry lineage full-stack test from ambient runtime-manager
configuration.

- Owner: Claude
- Reviewer: Codex2 (reassigned from Antigravity by Human/Ops at
  2026-07-26T20:24:36Z; earlier evidence cuts named the previous reviewer)
- Phase: Twelve-loop verification hardening
- Manifest: [`evidence.json`](evidence.json)
- Changed implementation file: `services/telemetry/test_lineage_write_path.py`
- Delivery: [PR #4213](https://github.com/ajoe734/pantheon/pull/4213), squash-merged
  into `dev` as `0410a89f0e4ac3c53e7bc5192aebe6925423b4da` on 2026-07-26T20:18:15Z
  with all Branch CI Gate and Orchestrator Sync checks green; the evidence re-cut
  followed as [PR #4214](https://github.com/ajoe734/pantheon/pull/4214), merged as
  `f687d7aeb7b9f9ebabb85247a11afbfd7c3fc16b`

## Root cause

`TestLiveLineageWritePathFullStackHTTPRoute` reached the authoritative
runtime-manager through two paths that both resolve their transport from the
ambient environment:

1. The test itself constructed `RuntimeManagerClient()` with no `allow_local`
   opt-in.
2. It imported `services.incidents.main`, whose module body runs
   `reference_validator = CanonicalReferenceValidator()`, which builds the same
   default client via `_RuntimeBindingLookup()`.

After `RuntimeManagerClient` gained its fail-closed default, both paths raise in
any workspace that does not export `PANTHEON_RUNTIME_MANAGER_URL`:

```
runtime_manager_client.RuntimeManagerClientError: PANTHEON_RUNTIME_MANAGER_URL
is required; refusing an in-process RuntimeManager fallback unless
allow_local=True is explicit.
```

The test's `setUp` popped `PANTHEON_RUNTIME_MANAGER_URL`, so the failure was
unconditional in a clean checkout while remaining invisible on any machine whose
shell happened to export a runtime-manager URL — the test's result depended on
operator environment rather than on the code under test.

Two secondary leaks travelled with it:

- `services/incidents/main.py` bootstraps its incident store from
  `INCIDENTS_DATA_DIR`, defaulting to the shared `/tmp/pantheon/incidents`. The
  import therefore mutated state outside the test's tempdir.
- An ambient `PANTHEON_RUNTIME_MANAGER_TOKEN_FILE` pointing at a missing file
  would fail the client constructor for a reason unrelated to the behaviour
  under test.

## Fix

The full-stack case now owns its entire runtime-manager configuration:

- `_isolated_runtime_manager_client()` constructs
  `RuntimeManagerClient(allow_local=True)` — the client's documented test/local
  opt-in — and asserts no ambient URL is present first.
- `setUp` pins `PANTHEON_RUNTIME_BINDING_STORE_PATH`,
  `PANTHEON_SINGLE_RUNTIME_ENFORCED`, `PANTHEON_RUNTIME_MANAGER_TOKEN`, and
  `INCIDENTS_DATA_DIR` into a per-test tempdir, and clears
  `PANTHEON_RUNTIME_MANAGER_URL`, `PANTHEON_RUNTIME_MANAGER_TOKEN_FILE`, and
  `PANTHEON_RUNTIME_MANAGER_TIMEOUT_SECONDS`.
- The `services.incidents.main` import is wrapped in a scoped patch binding an
  explicitly unroutable URL (`http://127.0.0.1:9/lin003-inert-never-contacted`).
  The route's validator is replaced immediately afterwards with the test's live
  one, so the URL is never dialled; it exists so the test declares its own
  configuration instead of inheriting the operator's.

Three regression guards were added:

| Test | Guarantee |
| --- | --- |
| `test_default_runtime_manager_client_stays_fail_closed_without_url` | `RuntimeManagerClient()` and `RuntimeManagerClient(require_remote=True)` still raise without a URL — the isolation is an `allow_local` opt-in, not a relaxed production default |
| `TestFullStackFixtureIsolation.test_fixture_overrides_ambient_config_then_restores_it` | Hostile ambient values are overridden, then `os.environ` is byte-identical after teardown and the tempdir is removed |
| `TestFullStackFixtureIsolation.test_full_stack_case_passes_under_hostile_ambient_config` | The whole full-stack case passes nested under hostile ambient config and leaves no environment residue |

## Explicitly rejected alternatives

- Relaxing `RuntimeManagerClient` to fall back to an in-process
  `RuntimeManagerService` when no URL is configured.
- Requiring operators or CI to export `PANTHEON_RUNTIME_MANAGER_URL` so the test
  can inherit it.
- Skipping or deleting the full-stack case.

## Verification

| Command | Result |
| --- | --- |
| `env -u PANTHEON_RUNTIME_MANAGER_URL .venv/bin/python3 -m unittest services.telemetry.test_lineage_write_path` | 7 tests, OK |
| Same file with hostile ambient URL, token file, and data dir exported | 7 tests, OK |
| `unittest discover -s services/telemetry` — baseline at `HEAD~1` | 193 tests, 3 errors, 1 skip |
| `unittest discover -s services/telemetry` — post-change | 197 tests, 2 errors, 1 skip |

All four rows were independently re-run by the owner on merged `dev` tip
`f687d7aeb` in a fresh worker session and reproduced the same counts; the exact
re-verification command lines are appended to `validation.commands`.

The two remaining discovery errors (`services.telemetry.test_capture`,
`services.telemetry.test_feedback_adapter`) are pre-existing bare-module import
failures, unchanged from baseline and outside this task's owned layer. Net
delta: +4 tests, -1 error, no new failures.

Exact command lines and conclusions are recorded in `evidence.json` under
`validation.commands`.
