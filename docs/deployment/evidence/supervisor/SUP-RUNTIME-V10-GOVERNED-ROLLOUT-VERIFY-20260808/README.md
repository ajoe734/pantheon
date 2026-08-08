# SUP-RUNTIME-V10 governed rollout verification

This evidence records the authorized 2026-08-08 governed promotion attempt
from `origin/dev` commit
`619acd04184e8d3fc3aef322d160e7c9106670ad`.  It was executed only through
`sync-dev-root.sh`, which handed the candidate to the existing transactional
promotion operator.

The operator failed closed before it captured an incumbent, changed live
configuration, signalled a process, or launched a candidate.  Its durable
transaction record is:

```text
/home/lupin/pantheon-ci-deploy/runtime/promotion-evidence/
supervisor-runtime-promotion-20260808T134307753860Z-3911075.json
SHA-256: 1c252fb6a3691ac47a92e2a770782b503372b4825bb1f71031828efe8e92fe0e
```

The precise rejection was `Candidate Git directory is a symlink or has the
wrong type: [Errno 20] Not a directory: '.git'`.  A later read-only candidate
discovery successfully bound that same candidate's real `.git` directory,
commit, tree, remote, and bytecode-clean working tree.  That discrepancy is a
source-only follow-up: do not retry, alter the candidate, edit live config, or
signal the incumbent outside the promotion transaction.

`evidence.json` contains the command, hashes, process observations, and the
explicit non-claims for candidate launch, status-child proof, and loop
recovery.  It also records the source-only repair packet requested through the
assistant dev bridge.
