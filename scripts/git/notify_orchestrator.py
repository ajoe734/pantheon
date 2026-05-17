#!/usr/bin/env python3
"""Classify and forward git events to the Pantheon orchestrator.

Two roles:

  classify   — read env vars set by GitHub Actions (GH_EVENT, REF, REFNAME,
               PR_*, etc.) and emit a normalized JSON event document on
               stdout. Used to record what happened.

  send       — POST the JSON document at --payload-file to the orchestrator
               sync endpoint (env: SYNC_URL, SYNC_SECRET). HMAC-SHA256
               signature in `X-Pantheon-Signature` header.

  Standalone:
    notify_orchestrator.py --event <name> --payload-json <inline-json>
                           POST the inline document directly.

Designed so the orchestrator can implement any handler it wants; we only
guarantee the event shape:

  {
    "event": "<wave_open|wave_freeze|wave_close|publish_cut|"
             "promote_pr_opened|promote_merged|hotfix_merged|"
             "tag_pushed|branch_pushed|pr_labeled>",
    "actor": "<github actor>",
    "ref":  "<refs/heads/... or refs/tags/...>",
    "ref_name": "<short ref>",
    "sha": "<commit sha>",
    "version": "<vYYYY.WW.P or null>",
    "wave_id": "<YYYY-WNN or null>",
    "pr": { "number": int|null, "title": str|null, "label": str|null, "action": str|null }
  }
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VERSION_RE = re.compile(r"(v\d{4}\.\d{2}\.\d+)")
WAVE_RE = re.compile(r"(\d{4}-W\d{2})")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default) or default


def classify_event() -> dict:
    gh_event = env("GH_EVENT")
    ref = env("REF")
    ref_name = env("REFNAME")
    ref_type = env("REFTYPE")
    sha = env("SHA")
    actor = env("ACTOR")

    payload = {
        "event": "branch_pushed",
        "actor": actor,
        "ref": ref,
        "ref_name": ref_name,
        "sha": sha,
        "version": None,
        "wave_id": None,
        "ts": int(time.time()),
        "pr": {"number": None, "title": None, "label": None, "action": None},
    }

    if gh_event == "pull_request":
        action = env("PR_ACTION")
        payload["pr"] = {
            "number": int(env("PR_NUMBER", "0")) or None,
            "title": env("PR_TITLE", None),
            "label": env("PR_LABEL", None),
            "action": action,
        }
        if action == "opened":
            payload["event"] = "pr_opened"
        elif action == "labeled":
            payload["event"] = "pr_labeled"
        elif action == "closed":
            payload["event"] = "pr_closed"
        m = VERSION_RE.search(env("PR_TITLE", ""))
        if m:
            payload["version"] = m.group(1)
        return payload

    if ref_type == "tag":
        if ref.startswith("refs/tags/release/"):
            payload["event"] = "publish_cut"
        elif ref.startswith("refs/tags/prod/"):
            payload["event"] = "promote_merged"
        elif ref.startswith("refs/tags/archive/"):
            payload["event"] = "branch_archived"
        else:
            payload["event"] = "tag_pushed"
        m = VERSION_RE.search(ref_name)
        if m:
            payload["version"] = m.group(1)
        m = WAVE_RE.search(ref_name)
        if m:
            payload["wave_id"] = m.group(1)
        return payload

    if ref.startswith("refs/heads/"):
        branch = ref_name
        if branch.startswith("wave/"):
            payload["event"] = "wave_push"
            m = WAVE_RE.search(branch)
            if m:
                payload["wave_id"] = m.group(1)
        elif branch.startswith("publish/"):
            payload["event"] = "publish_branch_push"
            m = VERSION_RE.search(branch)
            if m:
                payload["version"] = m.group(1)
        elif branch == "dev":
            payload["event"] = "dev_push"
        elif branch == "master":
            payload["event"] = "master_push"
        else:
            payload["event"] = "branch_pushed"

    return payload


def send_payload(payload: dict) -> int:
    url = os.environ.get("SYNC_URL")
    if not url:
        print("SYNC_URL not set; nothing to send", file=sys.stderr)
        return 0
    secret = os.environ.get("SYNC_SECRET", "")
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    signature = ""
    if secret:
        signature = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "pantheon-orchestrator-sync/1",
            **({"X-Pantheon-Signature": f"sha256={signature}"} if signature else {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"orchestrator sync: HTTP {resp.status}", file=sys.stderr)
            return 0
    except Exception as exc:  # noqa: BLE001 — best-effort notify
        print(f"orchestrator sync failed: {exc}", file=sys.stderr)
        return 0  # do not fail the workflow on transient sync issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", nargs="?", choices=["classify", "send"], default=None)
    parser.add_argument("--payload-file")
    parser.add_argument("--event")
    parser.add_argument("--payload-json")
    args = parser.parse_args()

    if args.mode == "classify":
        json.dump(classify_event(), sys.stdout)
        return 0

    if args.mode == "send":
        path = args.payload_file
        if not path:
            print("send requires --payload-file", file=sys.stderr)
            return 2
        payload = json.loads(Path(path).read_text())
        return send_payload(payload)

    # standalone usage: --event + --payload-json
    if args.event:
        try:
            extra = json.loads(args.payload_json) if args.payload_json else {}
        except json.JSONDecodeError as e:
            print(f"bad --payload-json: {e}", file=sys.stderr)
            return 2
        payload = {"event": args.event, "ts": int(time.time()), **extra}
        return send_payload(payload)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
