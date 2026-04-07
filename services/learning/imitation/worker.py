from __future__ import annotations

import os
import time

from smoke_test import main as smoke_main


def main() -> int:
    mode = os.environ.get("PANTHEON_IMITATION_MODE", "idle").strip().lower()
    if mode == "smoke":
        backend = os.environ.get("PANTHEON_IMITATION_BACKEND", "stub").strip().lower() or "stub"
        return smoke_main(["--backend", backend])

    print(
        "Pantheon imitation worker image is ready. "
        "Set PANTHEON_IMITATION_MODE=smoke to execute the governed BC smoke test."
    )
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    raise SystemExit(main())
