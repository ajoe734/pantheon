#!/usr/bin/env python3
"""Force-run a persona's registered OpenClaw OODA cron job and close it into a
real, persisted OODA packet.

This is the live proof/ops entrypoint for OPENCLAW-OODA-PACKET-CLOSURE: it
exercises the full evidence chain -- `cron.run` (force) -> `cron.runs` (status
`ok`) -> a persisted packet in the OODA store whose refs carry that same
cron run id -- against a real OpenClaw gateway. See
`services/persona/cron_ooda_closure.py` for the design rationale and the
honesty guardrails (no packet on a non-ok run; packet stays at `observing`,
never fabricated forward, if the live agent turn errors).

Usage (adapter-proxy transport, the default in the pantheon-openclaw-gateway-
adapter compose service):

    PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL=http://127.0.0.1:18104 \\
    OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789 \\
    OPENCLAW_GATEWAY_TOKEN=pantheon-local-token \\
      python3 scripts/openclaw-close-persona-cron-ooda.py \\
        --persona-id persona-tw-equity --workflow-id pantheon.deploy

Docker-exec transport (host has a docker socket, no adapter proxy):

    OPENCLAW_PAPER_ADAPTER_ENABLED=true \\
      python3 scripts/openclaw-close-persona-cron-ooda.py \\
        --persona-id persona-tw-equity --workflow-id pantheon.deploy
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_CRON_DIR = REPO_ROOT / "services" / "control-plane" / "cron"
if str(_CRON_DIR) not in sys.path:
    sys.path.insert(0, str(_CRON_DIR))

from persona_cron_registrar import PersonaCronRegistrar  # noqa: E402

from services.persona.cron_ooda_closure import (  # noqa: E402
    CronOodaClosureError,
    close_persona_cron_dispatch,
    find_persona_cron_job,
)


def _build_runtime():
    # Reuses PersonaCronRegistrar's transport auto-detection (adapter-proxy
    # HTTP first via PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL, else a docker-exec
    # runtime when explicitly enabled) instead of re-implementing it here.
    registrar = PersonaCronRegistrar()
    runtime = registrar._get_runtime()  # noqa: SLF001 -- intentional reuse, no public accessor exists
    if runtime is None:
        raise SystemExit(
            "No OpenClaw gateway runtime available. Set "
            "PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL (adapter-proxy) or "
            "OPENCLAW_PAPER_ADAPTER_ENABLED=true plus OPENCLAW_GATEWAY_* "
            "(docker-exec)."
        )
    return runtime


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persona-id", required=True)
    parser.add_argument("--workflow-id", default="pantheon.deploy")
    parser.add_argument("--job-id", default=None, help="Skip cron.list lookup if already known.")
    parser.add_argument("--poll-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=1.0)
    parser.add_argument("--store-path", default=None, help="Override the OODA packet store path.")
    args = parser.parse_args()

    runtime = _build_runtime()

    job_id = args.job_id
    if not job_id:
        job_id = find_persona_cron_job(runtime, args.persona_id, args.workflow_id)
        if not job_id:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": (
                            f"No registered cron job found for persona_id={args.persona_id!r} "
                            f"workflow_id={args.workflow_id!r}"
                        ),
                    }
                )
            )
            return 1

    try:
        result = close_persona_cron_dispatch(
            args.persona_id,
            args.workflow_id,
            job_id=job_id,
            runtime=runtime,
            store_path=args.store_path,
            poll_timeout_seconds=args.poll_timeout_seconds,
            poll_interval_seconds=args.poll_interval_seconds,
        )
    except CronOodaClosureError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

    print(json.dumps({"status": "ok", **result.to_dict()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
