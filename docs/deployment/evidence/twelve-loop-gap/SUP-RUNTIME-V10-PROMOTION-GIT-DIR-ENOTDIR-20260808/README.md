# SUP-RUNTIME-V10 promotion `.git` ENOTDIR repair

This packet binds the source-only repair for the governed supervisor promotion
bootstrap failure. The exact base-runtime error was reproduced read-only
against the real mutable `dev-root`: the bootstrap path called the immutable
candidate handle, which requires `.git/`, even though `dev-root` is a linked
worktree with a regular `.git` gitfile.

The repair keeps the immutable candidate rule intact. Only bootstrap mutable
incumbent capture uses the new gitfile-aware binding, which binds and
revalidates the root, gitfile, external Git directory, common directory,
canonical remote, accepted HEAD/tree, index/tracked cleanliness, and governed
source identities without following symlink path components. Untracked
runtime-only files are tolerated on the mutable source because rollback is
materialized as a fresh standalone clone; they remain forbidden in immutable
candidates.

The code anchors are `3d0354bd9` (descriptor floor) and `4e06d7f28` (mutable
gitfile binding). The full promotion suite passed 234 tests, the sync contract
suite passed 7 tests, and the task source successfully bound the real dev-root
to HEAD `619acd04184e8d3fc3aef322d160e7c9106670ad`, tree
`33dbb7f25df0c9153e1417151cc712d923d53640`, and seven governed launch
sources.

Independent review must bind [evidence.json](evidence.json) to the exact PR
head. No sync promotion, live config/process mutation, manual checkout,
signal, candidate deletion, or runtime rollout is part of this task.
