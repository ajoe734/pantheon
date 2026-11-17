#!/usr/bin/env python3
"""Turn `service|oneoff|state|image_id[|restart_count|oom_killed]` lines (docker
inspect of the pantheon Compose project) into the JSON row list that
``fetch_dev_artifact_evidence.py --steady-state-file`` consumes. One-off
containers are dropped. The restart fields are optional so four-field input
keeps working."""
import json
import sys


def _bool(value):
    return value.strip().lower() == "true"


def rows(lines):
    out = []
    for line in lines:
        parts = line.rstrip("\n").split("|")
        if len(parts) in (4, 6) and parts[0] and parts[1] != "True":
            row = {"service": parts[0], "state": parts[2], "image_id": parts[3]}
            if len(parts) == 6:
                try:
                    row["restart_count"] = int(parts[4])
                except ValueError:
                    continue
                row["oom_killed"] = _bool(parts[5])
            out.append(row)
    return out


if __name__ == "__main__":
    json.dump(rows(sys.stdin), sys.stdout)
