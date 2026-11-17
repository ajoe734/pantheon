#!/usr/bin/env python3
"""Turn `service|oneoff|state|image_id` lines (docker inspect of the pantheon
Compose project) into the JSON row list that
``fetch_dev_artifact_evidence.py --steady-state-file`` consumes. One-off
containers are dropped."""
import json
import sys


def rows(lines):
    out = []
    for line in lines:
        parts = line.rstrip("\n").split("|")
        if len(parts) == 4 and parts[0] and parts[1] != "True":
            out.append({"service": parts[0], "state": parts[2], "image_id": parts[3]})
    return out


if __name__ == "__main__":
    json.dump(rows(sys.stdin), sys.stdout)
