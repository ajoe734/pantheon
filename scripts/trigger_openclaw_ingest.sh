#!/bin/bash
# Trigger the pantheon.ingest cron workflow.
# Usage: ./scripts/trigger_openclaw_ingest.sh <payload-file> [--dispatch]

PAYLOAD_FILE=$1
DISPATCH_FLAG=$2

if [ -z "$PAYLOAD_FILE" ]; then
    echo "Usage: ./scripts/trigger_openclaw_ingest.sh <payload-file> [--dispatch]"
    exit 1
fi

CMD="python3 services/control-plane/cron/cli.py --workflow pantheon.ingest --payload-file \"$PAYLOAD_FILE\""

if [ "$DISPATCH_FLAG" == "--dispatch" ]; then
    CMD="$CMD --dispatch"
fi

echo "Running: $CMD"
$CMD
