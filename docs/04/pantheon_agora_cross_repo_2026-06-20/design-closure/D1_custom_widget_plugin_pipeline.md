# D1 — Custom Widget Plugin Pipeline

> 狀態：P3 Design Frozen；不阻擋首發 WidgetSpec/ChartSpec  
> 原則：Agent 能提出新呈現能力，但不能自行生成／部署 production code。

## Pipeline

```text
WidgetPluginProposal
→ triage
→ UX/data/security review
→ sandbox implementation in execute-plans feature branch
→ unit/visual/performance/privacy tests
→ registry entry candidate
→ review/approval
→ versioned WidgetRegistry release
→ controlled activation
→ usage/performance monitoring
→ deprecate/retire
```

## Proposal Required Fields

```text
problem
strategy lenses
why existing widgets insufficient
required data sources
visual grammar
interactions
sensitivity
performance estimate
example mock
```

## Gates

- Data source already governed or separately approved。
- No arbitrary remote code/iframe。
- Renderer follows semantic tokens/a11y。
- Max render budget and row/node limits。
- Security/privacy review。
- Contract/schema/version tests。

## Versioning

```text
widget_type immutable
plugin_version semver
registry_version pins plugin version
recipe stores plugin version
```

Breaking change requires new major and recipe migration.

## Rollout

```text
dev internal
→ selected Agora users
→ broader opt-in
→ default eligible
```

Agent 只能在 registry status `active` 後使用。
