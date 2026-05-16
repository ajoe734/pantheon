#!/usr/bin/env python3
"""
PER-003: Integration smoke — persona registry write -> BFF read path.

Replays: seed 3 personas into PersonaRegistry (write to disk) -> BFF
ReadSurfaceStore reads all 3 back from the live registry store
(source=service_store, not local_snapshot / fixture_backed).

Run:
    python3 services/control-plane/persona/integration_smoke.py

Task:  PER-003
Owner: Claude2
Reviewer: Codex2
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "bff"))

from persona_registry import Persona, PersonaRegistry

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


def run_smoke() -> None:
    from read_store import ReadSurfaceStore

    with tempfile.TemporaryDirectory() as td:
        registry_path = Path(td) / "personas.json"

        # ------------------------------------------------------------------ #
        # Step 1: Seed 3 personas into PersonaRegistry (registry write)
        # ------------------------------------------------------------------ #
        reg = PersonaRegistry(path=registry_path)
        seed_personas = [
            Persona(
                persona_id="smoke-persona-001",
                name="Smoke Alpha",
                mandate="Integration smoke test alpha persona",
                lifecycle_state="research_only",
                created_at="2026-05-16T00:00:00Z",
            ),
            Persona(
                persona_id="smoke-persona-002",
                name="Smoke Beta",
                mandate="Integration smoke test beta persona",
                lifecycle_state="consultable",
                created_at="2026-05-16T00:01:00Z",
            ),
            Persona(
                persona_id="smoke-persona-003",
                name="Smoke Gamma",
                mandate="Integration smoke test gamma persona",
                lifecycle_state="paper_owner",
                created_at="2026-05-16T00:02:00Z",
            ),
        ]
        for persona in seed_personas:
            reg.create(persona)

        check("registry writes personas.json to disk", registry_path.exists())
        raw_data = json.loads(registry_path.read_text())
        check("registry file contains all 3 persona entries", len(raw_data) == 3)
        check("smoke-persona-001 present in registry file", "smoke-persona-001" in raw_data)
        check("smoke-persona-002 present in registry file", "smoke-persona-002" in raw_data)
        check("smoke-persona-003 present in registry file", "smoke-persona-003" in raw_data)

        # ------------------------------------------------------------------ #
        # Step 2: Point BFF service adapter to the registry file (strict mode)
        # ------------------------------------------------------------------ #
        original_env = os.environ.get("PANTHEON_BFF_PERSONA_REGISTRY_STORE")
        os.environ["PANTHEON_BFF_PERSONA_REGISTRY_STORE"] = str(registry_path)
        try:
            store = ReadSurfaceStore(
                os.path.join(td, "bff_read_surfaces.json"),
                allow_local_snapshot_fallback=False,
            )

            # ------------------------------------------------------------------ #
            # Step 3: BFF list read
            # ------------------------------------------------------------------ #
            personas = store.list_personas()
            check("BFF list_personas returns non-empty list", len(personas) > 0)
            returned_ids = {
                str(p.get("persona_id") or p.get("id") or "")
                for p in personas
            }
            check("smoke-persona-001 readable via BFF list", "smoke-persona-001" in returned_ids)
            check("smoke-persona-002 readable via BFF list", "smoke-persona-002" in returned_ids)
            check("smoke-persona-003 readable via BFF list", "smoke-persona-003" in returned_ids)

            # ------------------------------------------------------------------ #
            # Step 4: BFF detail read for each seeded persona
            # ------------------------------------------------------------------ #
            for pid, expected_name in (
                ("smoke-persona-001", "Smoke Alpha"),
                ("smoke-persona-002", "Smoke Beta"),
                ("smoke-persona-003", "Smoke Gamma"),
            ):
                detail = store.get_persona(pid)
                check(f"get_persona({pid!r}) returns non-None", detail is not None)
                if detail is not None:
                    check(
                        f"get_persona({pid!r}) name matches seed",
                        detail.get("name") == expected_name,
                    )

            # ------------------------------------------------------------------ #
            # Step 5: Verify source is service_store (not local_snapshot / fixture)
            # ------------------------------------------------------------------ #
            source = store.dataset_source("personas")
            check(
                "dataset_source('personas') == 'service_store' (service_backed, not fixture_backed)",
                source == "service_store",
            )

        finally:
            if original_env is None:
                os.environ.pop("PANTHEON_BFF_PERSONA_REGISTRY_STORE", None)
            else:
                os.environ["PANTHEON_BFF_PERSONA_REGISTRY_STORE"] = original_env


if __name__ == "__main__":
    run_smoke()
    total = PASS + FAIL
    print()
    print("SUMMARY")
    print(f"{PASS}/{total} checks passed")
    if FAIL:
        sys.exit(1)
