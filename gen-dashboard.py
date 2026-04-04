#!/usr/bin/env python3
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent

if __name__ == "__main__":
    subprocess.run(["python3", str(ROOT / "scripts" / "ai_status.py"), "sync"], check=True)
