import asyncio
import os
from services.loop_control.writer import LoopControllerWriter

DB_DSN = os.environ.get("DATABASE_URL") or "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"


async def main():
    writer = LoopControllerWriter(
        dsn=DB_DSN,
        tenant_id="default",
        environment="dev",
        controller_id="ctrl-source-ingest-v1",
        controller_name="SourceIngestionController",
        deployment_sha="sha-durable-substrate-test-001"
    )

    print("Writing heartbeat for source_ingestion...")
    await writer.record_heartbeat(
        loop_id="source_ingestion",
        truth_level="reconciled_live_proof",
        desired_state_query="SELECT count(*) FROM desired_sources",
        actual_state_query="SELECT count(*) FROM active_schedules",
        backlog=5,
        lag=0,
        evidence_refs=["ref-reconciliation-proof-source-001"],
        lease_duration_seconds=900,  # 15 minutes lease
        payload={"notes": "Written via LoopControllerWriter SDK"}
    )
    print("Heartbeat written successfully.")


if __name__ == "__main__":
    asyncio.run(main())
