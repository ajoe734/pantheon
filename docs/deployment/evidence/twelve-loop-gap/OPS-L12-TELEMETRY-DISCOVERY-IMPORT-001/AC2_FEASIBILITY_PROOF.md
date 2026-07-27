# AC2 feasibility proof — OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001

**Task:** OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 · **Owner:** Claude · **Reviewer:** Codex2
**Requested by:** Human/Ops in-progress audit, `2026-07-26T22:41:34Z`
**Subject head:** `408d6d9a57c583b8b0762dd60e00842948aebe84`
**Status of this document:** resolved through Option A. The analysis below is
retained as the decision record; `OPS-L12-PYTHON-PACKAGING-PROVISION-001`
implemented the only admissible mechanism and closed as `done`.

## Resolution — Option A implemented

Human/Ops authorized the packaging path as the dependency task
`OPS-L12-PYTHON-PACKAGING-PROVISION-001`. Its reviewed implementation merged
through PR #4232 as
`3802799f81778c93728d9dbbe4028289f153c718` on
`2026-07-27T14:27:20Z`, and its governed task archive closed at
`2026-07-27T14:41:36Z`.

The root `pyproject.toml` now exposes only `services`, `integrations`, and
`scripts`. `scripts/dev/provision_python_distribution.py` installs the
checkout-specific mapping into a governed interpreter without adding the
repository root to `sys.path`, and fails closed unless that interpreter can
also import the test dependencies. The regression class
`TestAC2ModesUnderInstalledDistribution` drives the shipped provisioner and
asserts all four modes as unconditional passes.

Fresh owner verification from a foreign `/tmp` cwd under `env -i`, with no
`PYTHONPATH`, produced:

- dotted `unittest`: 75 tests, `OK`;
- direct-file execution: 35 tests and 40 tests, both `OK`;
- `pytest -c /dev/null --noconftest`: 75 passed;
- repository-root discovery: zero loader errors.

The unprovisioned control still fails at `services`, proving the passing modes
come from the installed distribution rather than ambient cwd state. AC2 is
therefore met without any `sys.path` mutation in the repaired modules.

## The audit instruction

> Canonical AC2 requires direct pytest and unittest execution of both repaired
> files to PASS without PYTHONPATH/cwd dependence or process-global `sys.path`
> mutation. New `TestRepositoryRootResolutionWithoutPythonPath` instead
> explicitly permits foreign-cwd dotted unittest and direct-file execution to
> fail on `ModuleNotFoundError: services`, asserting only that they no longer
> fail on bare sibling aliases. That proves a weaker contract and cannot close
> the task. Implement a packaging/launcher/import solution that makes every
> named mode in AC2 pass, or formally prove the canonical acceptance itself
> impossible and obtain Human/Ops scope revision; do not silently redefine pass
> as expected failure. A new repair PR is required because #4222 is already
> merged. No config/process change.

The audit is correct on the facts. The narrowing has been withdrawn: the manifest
now records AC2 as `blocked_pending_scope_decision`, not as any kind of pass, and
the two boundary tests are labelled as recording an **unresolved** gap.

## The four AC2 modes

| Mode | Command (foreign cwd `/tmp`, `env -i`, no `PYTHONPATH`) | Current result |
|---|---|---|
| M1 | `pytest -q <abs>/services/telemetry/test_capture.py …` | **PASS** (75 passed; also with `-c /dev/null --noconftest`) |
| M2 | `python -m unittest services.telemetry.test_capture …` | FAIL — `ModuleNotFoundError: No module named 'services'` |
| M3 | `python <abs>/services/telemetry/test_capture.py` | FAIL — same |
| M4 | `python -m unittest discover -s services/telemetry -t .` from the repository root, no `PYTHONPATH` | **PASS** (35 / 40) |

M1 and M4 pass with no import help of any kind. M2 and M3 are the gap.

## Proof: no change to the repaired files can close M2

1. **`services` is a top-level name, and top-level names are resolved only from
   `sys.path`.** CPython's `PathFinder` walks `sys.path` entries; there is no
   other route to a top-level package short of an already-populated
   `sys.modules` entry or an installed meta-path finder.

2. **`sys.path` in the M2 environment contains no repository entry.** Observed:

   ```
   cd /tmp && env -i PATH=/usr/bin:/bin HOME=/tmp <venv>/bin/python3 -c 'import sys, json; print(json.dumps(sys.path))'
   ["", "/usr/lib/python312.zip", "/usr/lib/python3.12",
    "/usr/lib/python3.12/lib-dynload",
    "/home/lupin/pantheon/.venv/lib/python3.12/site-packages"]
   ```

   `""` is the working directory (`/tmp`). The remaining entries are the standard
   library and `site-packages`. Nothing points at the repository.

3. **The repaired file is never executed in M2.** The failure occurs while
   `unittest` resolves the dotted name, before the module body runs. Observed:
   asking for a module that does not exist at all produces the byte-identical
   error, so resolution stops at `services` and never reaches `test_capture`:

   ```
   cd /tmp && env -i … python -m unittest services.telemetry.does_not_exist_at_all
   ImportError: Failed to import test module: services
   ModuleNotFoundError: No module named 'services'
   ```

4. **Therefore no edit to `test_capture.py` or `test_feedback_adapter.py` — or to
   any repository file that is not on `sys.path` — can change the M2 outcome.**
   Code that never runs cannot repair anything. This is not a limitation of the
   repair; it is arithmetic on the import algorithm.

5. **The complete set of mechanisms that could put the repository root on
   `sys.path` in M2 is therefore:**

   | Mechanism | Admissible under AC2? |
   |---|---|
   | `PYTHONPATH=<repo root>` | **No** — AC2 forbids PYTHONPATH dependence |
   | working directory = repository root | **No** — AC2 forbids cwd dependence (and M4 already covers it) |
   | runtime `sys.path` mutation in the test module | **No** — AC2 forbids process-global `sys.path` mutation, and per (3) the code never runs anyway |
   | an entry in `site-packages`: an installed distribution or a `.pth` file | **Yes** — the only remaining candidate |

   The enumeration is exhaustive: `sys.path` at interpreter start is built from
   the script/cwd entry, `PYTHONPATH`, and the site directories. There is no
   fifth source.

## Constructive proof that the packaging mechanism is sufficient

The remaining candidate was tested rather than assumed. A throwaway virtualenv
was created and a single `.pth` line — exactly what `pip install -e .` writes —
was placed in its `site-packages`, with **no change of any kind to the repaired
files**:

```bash
/home/lupin/pantheon/.venv/bin/python3 -m venv --system-site-packages "$S/pthvenv"
echo "<repo root>" > "$S/pthvenv/lib/python3.12/site-packages/pantheon-editable-demo.pth"

# M2
cd /tmp && env -i PATH=/usr/bin:/bin HOME=/tmp "$S/pthvenv/bin/python3" \
  -m unittest services.telemetry.test_capture services.telemetry.test_feedback_adapter
# → Ran 75 tests in 0.528s / OK

# M3
cd /tmp && env -i PATH=/usr/bin:/bin HOME=/tmp "$S/pthvenv/bin/python3" \
  <repo>/services/telemetry/test_capture.py          # → Ran 35 tests / OK
cd /tmp && env -i PATH=/usr/bin:/bin HOME=/tmp "$S/pthvenv/bin/python3" \
  <repo>/services/telemetry/test_feedback_adapter.py # → Ran 40 tests / OK

# control: same interpreter, .pth removed
cd /tmp && env -i … -m unittest services.telemetry.test_capture   # → FAILED (errors=1)
```

So AC2 is **not logically impossible**. It is satisfiable by exactly one
mechanism, and that mechanism is entirely outside the repaired files.

## Why that mechanism is a config-and-process change

Making the `.pth` real rather than a scratch demonstration requires both halves:

1. **Repository config.** A root `pyproject.toml` (or `setup.py`/`setup.cfg`)
   declaring the distribution and its package discovery. The repository has never
   had one — `git ls-files` matches no `pyproject.toml`, `setup.py`, or
   `setup.cfg` anywhere. Adding one at the root of a monorepo that contains
   `services/` (with per-service `main.py`, `auth.py`, `models.py` name
   collisions), `scripts/`, `integrations/`, `lean/`, and a submodule is a
   repository-wide packaging decision. It is also read by tooling that this task
   does not own: dependency scanning, the deploy workflows, and pytest's own
   config/rootdir resolution (`pytest.ini` currently owns that).

2. **A provisioning rule.** The distribution must actually be installed in every
   interpreter that runs the suite — each developer environment, each auto-worker
   worktree, and each CI job — or M2 and M3 still fail. The repo change alone
   satisfies nothing. That is a process change to how the test environment is
   provisioned.

Both halves are outside this task's declared scope ("Do not edit config or
production behavior"), and the same audit line that requires AC2 also says
**"No config/process change."** Under that constraint the set of admissible
mechanisms in the table above is empty, and M2/M3 cannot be closed by this task.

## Decision requested

Two coherent options. Either is implementable; the choice is a scope decision,
not an engineering one.

**Option A — authorize packaging (AC2 stands as written).**
Human/Ops authorizes, as an explicit exception to the no-config constraint,
either this task or a new one to add root packaging metadata and the
provisioning rule. Scope: a root `pyproject.toml` with explicit package
discovery limited to `services`, `scripts`, and `integrations`; an editable
install step in the CI test job and the worker environment bootstrap; and a
regression that asserts M1–M4 all pass. Deliverable then closes AC2 verbatim.
Cost: a repository-wide packaging surface and an environment provisioning
requirement, owned by whichever lane owns build/CI, not by a telemetry
test-loading task. Recommended **if** AC2 must stand verbatim, and recommended as
its own task rather than folded into this one.

**Option B — revise AC2 to the contract that is provable without packaging.**
Replace AC2 with: *"Direct pytest execution of both files passes from any working
directory with no PYTHONPATH; unittest discovery and dotted unittest execution
pass from the repository root with no PYTHONPATH; neither module mutates
process-global `sys.path`; and no import failure in any environment is
attributable to a bare sibling name."* Every clause is already proven and fenced
by `services/telemetry/test_discovery_imports.py` (15 tests). The residual —
dotted and direct-file execution from a foreign cwd — is then recorded as a
tracked packaging gap owned by the build/CI lane, not as a silent pass.
Recommended if the intent of AC2 was "the defect is gone and nothing depends on
being inside `services/telemetry`", which the delivered repair does satisfy.

Option A was chosen and delivered by
`OPS-L12-PYTHON-PACKAGING-PROVISION-001`. The current evidence cut records AC2
as `pass`; the historical blocked state described above is retained only to
show why the dependency was necessary.

## What is not being claimed

- Not claiming a bare, unprovisioned interpreter can resolve this checkout from
  an arbitrary cwd. The unprovisioned failure remains the negative control.
- Not claiming AC2 was logically impossible. The analysis proved packaging was
  sufficient, and the dependency implemented it.
- Not claiming the old boundary was acceptable. It remained blocking until the
  governed distribution was independently reviewed and merged.
- Not implementing a `sys.path` or `sys.modules` shim inside the test modules. It
  could close M3 alone, but it cannot touch M2, it reintroduces the
  duplicate-module-identity hazard this task closed, and it is the same class of
  process-global mutation AC2 forbids.
