#!/bin/bash
# usage: runfile.sh <side> <root> <relfile>
side=$1; root=$2; f=$3; S=/tmp/claude-1802994780/-tmp-pantheon-worker-worktrees-pantheon-bff-sentinel-removal-001/56d68929-0a99-4726-8cc1-5a7e8e3af5aa/scratchpad
out=$S/res/$side/$(echo "$f" | tr / _)
mkdir -p $S/res/$side
[ -f $out.exit ] && exit 0
cd $root
GOV_APPROVAL_TEST_DSN=postgresql://postgres@127.0.0.1:25432/gov_approval_test TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:25432/gov_approval_test PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=$root timeout 400 /tmp/clean-bff-venv/bin/python -m pytest -p no:cacheprovider -q "$f" --junitxml=$out.xml > $out.log 2>&1
echo $? > $out.exit
