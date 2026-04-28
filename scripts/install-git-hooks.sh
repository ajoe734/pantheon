#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
chmod +x .githooks/pre-commit
git config core.hooksPath .githooks
echo "Installed: core.hooksPath -> .githooks"
