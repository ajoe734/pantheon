# OpenClaw restart-readiness convergence evidence

This task fixes the narrow deployment race where the OpenClaw gateway is
recreated after its configuration changes, while the first actual provider
answer is still warming up.  The live smoke previously made one 20-second
`auth_probe=true` request and treated a timeout as a release failure even when
the provider became ready immediately afterward.

The smoke now uses only a bounded readiness convergence loop:

- the default deployment budget is 90 seconds, with a hard 90-second maximum;
- each auth-probe request is capped at 20 seconds and can use only the time
  remaining in the total budget;
- it retries connection refusal (curl 7), per-request timeout (curl 28), and
  HTTP 503; it fails immediately on every other transport, response, or
  `ready=false` outcome;
- it returns immediately after the first `ready=true` answer;
- upstream details are not emitted: known error codes are retained and all
  other reasons become deterministic SHA-256 identifiers;
- the subsequent live invoke and OpenResponses stream remain individual calls;
  they are never retried by the readiness loop.

## Focused validation

```bash
git diff --check
bash -n scripts/openclaw-assistant-openclaw-live-smoke.sh
PYTHON_BIN=$(python3 scripts/dev/provision_python_distribution.py --print-python)
"${PYTHON_BIN}" -m pytest -q scripts/test_openclaw_assistant_openclaw_live_smoke.py
```

The focused test substitutes only the smoke client.  It proves that HTTP 503,
connection refusal, and per-request timeout converge if a later readiness
answer is ready; a never-ready 503 fails inside the configured total budget;
and the invoke and stream are each called once.

## Exact deployed proof

After this task branch is reviewed, merged, and selected by the protected dev
deployment workflow, that workflow runs the four-stage smoke under the normal
environment lease.  Its exact deployed backend identity, run URL, and
sanitized four-stage outcome are the post-merge delivery evidence.  The command
is intentionally unchanged:

```bash
OPENCLAW_GATEWAY_ADAPTER_URL=http://localhost:18104 \
  bash scripts/openclaw-assistant-openclaw-live-smoke.sh
```

No provider credential, gateway token, raw response, broker command, source
write, frontend change, or capital authority is included in this evidence.
