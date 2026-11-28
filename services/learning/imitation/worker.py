"""Offline LP-002 imitation smoke harness entrypoint (NOT run as a runtime service).

IMITATION-TRADER-SHADOW-COMPARISON-20261127:
``services/learning/imitation/`` is retained solely as the offline LP-002 OSS
package-pin and behavior-cloning smoke harness. It is not built by
``cloudbuild.yaml`` and is not run by any Docker Compose or hosted service.
Production/hosted trader behaviour imitation runs in ``services/policy-learning``
and its existing hourly ``policy-learning-shadow-eval-scheduler``.
"""
from __future__ import annotations

import os

from smoke_test import main as smoke_main


def main() -> int:
    mode = os.environ.get("PANTHEON_IMITATION_MODE", "idle").strip().lower()
    if mode == "smoke":
        backend = os.environ.get("PANTHEON_IMITATION_BACKEND", "stub").strip().lower() or "stub"
        return smoke_main(["--backend", backend])

    print(
        "services/learning/imitation/worker.py is an offline LP-002 smoke harness and is not run as a runtime worker. "
        "Active trader imitation runs in services/policy-learning (policy-learning-svc + policy-learning-shadow-eval-scheduler). "
        "Set PANTHEON_IMITATION_MODE=smoke to execute the offline BC smoke test."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
