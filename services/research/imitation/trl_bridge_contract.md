# IMT-008 TRL Preference-Pair Bridge Contract

Status: IMT-008 research-plane contract

## Scope

`trl_bridge.py` converts governed IMT-002 preference data into the explicit
standard preference format consumed by TRL `DPOTrainer`.

The bridge:

- accepts `PreferenceExample` mappings or model instances
- accepts `CorrectionTrace` mappings or model instances
- returns plain Python rows with exactly `prompt`, `chosen`, and `rejected`
- serializes prompt context and artifacts as deterministic JSON strings
- does not import TRL, write datasets, start training, mutate registry state, or
  influence live execution

## Entry Points

```python
from trl_bridge import from_correction_traces, to_trl_pairs

rows_from_preferences = to_trl_pairs(preference_examples)
rows_from_traces = from_correction_traces(correction_traces)
```

`to_trl_pairs(preference_examples)` expects each `PreferenceExample` to contain
both `chosen_artifact` and `rejected_artifact`. This is naturally true for
IMT-002 edit examples. Non-paired approve/reject examples are rejected because
TRL DPO rows require both sides of the preference pair.

`from_correction_traces(correction_traces)` maps:

- `after_artifact` -> `chosen`
- `before_artifact` -> `rejected`

## Output Shape

Each row is a `dict[str, str]`:

```python
{
    "prompt": "{\"source_type\":\"preference_example\",...}",
    "chosen": "{\"artifact_id\":\"alpha-spec:edited\",...}",
    "rejected": "{\"artifact_id\":\"alpha-spec\",...}",
}
```

The bridge uses explicit prompts rather than implicit prompts. This matches the
TRL `DPOTrainer` documented standard format, where a preference row has
`prompt`, `chosen`, and `rejected` columns. Reference:
https://huggingface.co/docs/trl/dpo_trainer#expected-dataset-type-and-format

## Governance Boundary

The bridge is a research-plane data adapter only. It preserves lineage context
inside the prompt string but does not:

- approve artifacts
- promote behavior policies
- write registry records
- deploy or bind a runtime
- grant paper/canary/live execution authority

Downstream dataset materialization, DPO training, evaluation, registry writeback,
and deployment gates remain separate governed steps.

## Verification

Focused verification:

```bash
pytest -q services/research/imitation/test_trl_bridge.py
```

The deterministic unit tests cover five `PreferenceExample` samples and five
`CorrectionTrace` samples, assert the exact `prompt`/`chosen`/`rejected` row
keys, and verify the corrected artifact is always placed in `chosen`.
