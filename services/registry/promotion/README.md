# Promotion Gate (REG-002)

This directory is the canonical home for the promotion gate implementation.

Current contents:

- `gate.py`: lifecycle transition and metadata checks
- `cli.py`: CLI entrypoint for promoting a registry entry JSON document

Legacy compatibility files remain at repo root:

- `gate.py`
- `cli.py`

Those wrappers exist to avoid breaking older commands while task-board artifacts converge on the
service-local path.

## Scope

The promotion gate enforces:

- allowed lifecycle transitions
- candidate requirements
- paper requirements
- live requirements

It does not replace:

- registry storage
- execution loader checks
- experiment backend lineage

Those are defined in:

- `services/registry/contract.md`
- `services/registry/lineage/contract.md`
- `services/execution/artifact-loader/contract.md`
