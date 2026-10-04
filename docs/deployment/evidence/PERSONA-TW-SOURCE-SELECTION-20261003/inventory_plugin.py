import json
import os
from pathlib import Path

rows = []


def pytest_collection_finish(session):
    Path(os.environ["TW_INVENTORY"] + ".nodes.json").write_text(
        json.dumps([item.nodeid for item in session.items], indent=2) + "\n")


def pytest_runtest_logreport(report):
    if report.when == "call" or report.failed or report.skipped:
        rows.append({"node": report.nodeid, "phase": report.when,
                     "outcome": report.outcome})


def pytest_sessionfinish(session, exitstatus):
    Path(os.environ["TW_INVENTORY"] + ".results.json").write_text(
        json.dumps({"exit": int(exitstatus), "results": rows}, indent=2) + "\n")
