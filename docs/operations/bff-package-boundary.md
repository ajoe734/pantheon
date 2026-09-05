# BFF package boundary

The canonical Python import root for the existing BFF source tree is
`services.control_plane.bff`. Internal imports, including imports inside error,
permission, provider-unavailable, and worker paths, must use that root or an
unambiguous package-relative import. Bare imports such as `from models import`
are not supported.

Canonical internal imports fail closed. Production modules must not catch a
missing BFF module to retry the same namespace, substitute `None`, or define a
copied enum/model. Optional third-party integrations may still expose an
explicit unavailable adapter; a missing internal module is a packaging error.

Run diagnostic entrypoints as modules from the repository root. For example:

```bash
python -m services.control_plane.bff.contract_snapshots.report_execute_plans_bff_coverage
python -m services.control_plane.bff.reproduce_sse_gap
```

The entrypoints must not mutate `sys.path`, create `sys.modules` aliases, or
copy shared model/enumeration classes as an import fallback. The physical
source remains `services/control-plane/bff`; no duplicate package tree or shim
is part of this contract.

## Verification

```bash
uv run --with-requirements services/control-plane/bff/requirements.txt \
  python -m pytest -q scripts/test_bff_package_boundary_prerequisite.py
uv run --with-requirements services/control-plane/bff/requirements.txt \
  python -m compileall -q services/control-plane/bff
```

The focused regression checks the production-file AST, a fresh-process
cross-user 403 branch, representative worker/provider modules, and diagnostic
entrypoints. It does not replace `BFF-TEST-ARCH-001` or its migrated negative
path suites.

## Scope boundary

This corrective changes import resolution only. Persona namespace/global
ownership remains with `BFF-ROUTER-STRUCT-001`; Management projection ownership
remains with `MGMT-READ-001`. It provides no hosted-deployment or product
readiness evidence.
