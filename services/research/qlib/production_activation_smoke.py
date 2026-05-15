"""Qlib production-data activation packet smoke.

The smoke is explicit about backend selection:
- stub: deterministic CI/backend contract path
- real: upstream Qlib LightGBM path, or an explicit install/config error
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parent
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

from adapter import (  # noqa: E402
    QlibLightGBMBackend,
    StubLightGBMBackend,
    TrainingConfig,
    build_production_activation_packet,
    persist_production_activation_packet,
    run_qlib_workflow,
)
from adapter.qlib_adapter import QlibWorkflowError, persist_qlib_run_artifacts  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Qlib production activation packet smoke.")
    parser.add_argument("--dataset", required=True, help="Governed OHLCV dataset JSON path.")
    parser.add_argument("--proof", required=True, help="Production dataset proof JSON path.")
    parser.add_argument(
        "--backend",
        choices=("stub", "real"),
        required=True,
        help="Select deterministic stub or upstream real Qlib backend.",
    )
    parser.add_argument("--output-dir", help="Optional directory for review handoff artifacts.")
    args = parser.parse_args(argv)

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    proof = json.loads(Path(args.proof).read_text(encoding="utf-8"))
    backend = QlibLightGBMBackend() if args.backend == "real" else StubLightGBMBackend()

    try:
        result = run_qlib_workflow(
            dataset,
            backend=backend,
            config=TrainingConfig(
                version="1.0.0",
                requested_by="P2-QLIB-PROD-DATA-ACTIVATION-001",
                n_estimators=10 if args.backend == "stub" else 200,
            ),
            enforce_activation_ready=True,
        )
        packet = build_production_activation_packet(result, proof)
    except QlibWorkflowError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    manifest = None
    packet_path = None
    if args.output_dir:
        manifest = persist_qlib_run_artifacts(result, args.output_dir)
        packet_path = persist_production_activation_packet(packet, args.output_dir)

    output = {
        "packet_id": packet["packet_id"],
        "artifact_state": packet["artifact_state"],
        "requested_artifact_state": packet["requested_artifact_state"],
        "deployment_stage": packet["deployment_stage"],
        "backend": packet["backend_smoke"]["backend"],
        "order_route": packet["order_route"],
        "registry_write_authority": packet["registry_write_authority"],
        "dataset_floor_summary": packet["dataset_floor_summary"],
        "production_dataset_proof": {
            "provider": packet["production_dataset_proof"]["provider"],
            "entitlement_ref": packet["production_dataset_proof"]["entitlement"][
                "entitlement_ref"
            ],
            "freshness_status": packet["production_dataset_proof"]["freshness"]["status"],
            "pit": packet["production_dataset_proof"]["pit"]["point_in_time"],
            "storage_ref": packet["production_dataset_proof"]["storage"]["dataset_ref"],
        },
        "artifact_manifest": manifest,
        "production_activation_packet_path": packet_path,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
