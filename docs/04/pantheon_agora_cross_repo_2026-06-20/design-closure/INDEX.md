# Pantheon Agora Design Closure Pack — 2026-06-20

本包將 SA/SD 後仍未能安全派工的設計空白全部收斂。

## 決議與派工

- [00 — 設計收斂總決議](./00_design_closure_decision.md)
- [14 — Dispatch Unblock Matrix](./14_dispatch_unblock_matrix.md)

## A. 產品／量化

- [A1 — Next-Best-Question 評分](./A1_next_best_question_scoring_spec.md)
- [NBQ Golden Cases](./next_best_question_gold_cases.json)
- [A2 — Candidate ScoringRecipe](./A2_candidate_scoring_recipe_spec.md)
- [Candidate Scoring JSON Schema](./candidate_scoring_recipe.schema.json)
- [Winner Branch Default Recipe](./candidate_scoring_recipe.winner_branch.default.json)
- [A3 — WidgetRegistry / ChartSpec](./A3_widget_registry_and_chart_grammar_spec.md)
- [Widget Registry v1](./widget_registry.v1.json)
- [WidgetSpec Schema](./widget_spec.schema.json)
- [ChartSpec Schema](./chart_spec.schema.json)
- [A4 — Shadow Human Actual Sourcing](./A4_shadow_human_actual_sourcing.md)

## B. 法遵／隱私／治理

- [B1 — Information Lead Proxy Policy](./B1_information_lead_proxy_policy.md)
- [B2 — Institutional Learning Privacy Model](./B2_institutional_learning_privacy_model.md)
- [B3 — Alpha Contribution Governance](./B3_alpha_contribution_governance.md)

## C. 平台子設計

- [C1 — OpenClaw Skills Master Spec](./C1_agora_openclaw_skills_master_spec.md)
- [C2 — Persona Schema Normalization](./C2_persona_schema_normalization_plan.md)
- [C3 — execute-plans Monorepo Migration](./C3_execute_plans_monorepo_migration_plan.md)
- [C4 — Dev Market Data / Signal Wiring](./C4_dev_market_data_signal_wiring_plan.md)

### 9 OpenClaw Skill Specs

- [strategy-dialogue](./skills/agora/strategy-dialogue/SPEC.md)
- [strategy-completeness](./skills/agora/strategy-completeness/SPEC.md)
- [research-planning](./skills/agora/research-planning/SPEC.md)
- [expert-consult](./skills/agora/expert-consult/SPEC.md)
- [result-synthesis](./skills/agora/result-synthesis/SPEC.md)
- [dashboard-compose](./skills/agora/dashboard-compose/SPEC.md)
- [shadow-review](./skills/agora/shadow-review/SPEC.md)
- [personalization](./skills/agora/personalization/SPEC.md)
- [journal-replay](./skills/agora/journal-replay/SPEC.md)

## D. P3

- [D1 — Custom Widget Plugin Pipeline](./D1_custom_widget_plugin_pipeline.md)
- [D2 — Cross-user Aggregate Learning](./D2_cross_user_aggregate_learning_design.md)

## 結論

A1–A4、B1–B3、C1–C4 已具可驗收設計，不應再以「規格未決」阻擋對應 execution task。B1 的法遵簽核只阻擋 production activation；其工程可立即開發。
