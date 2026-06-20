# Task Brief: AG-BE-RS-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Consult/committee/red-team ContextBundle workflows
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Closeout complete. 17/17 skill tests + 21/21 adapter tests verified. Finalizing to done.

## Closeout Verification
- Verified: python3 -m pytest integrations/openclaw/skills/agora/expert_consult/test_skill.py -v → 17 passed
- Verified: python3 -m pytest integrations/openclaw/adapter/test_agora_context_bundle.py -v → 21 passed
- Artifacts committed: ae354f08 AG-BE-RS-003 (skill.py, test_skill.py, SPEC.md, __init__.py)
- Privacy boundary immutable (raw_prompt_included=Literal[False])
- B1 policy enforced on all memo conclusions
- No capability allowlist extended, no order route created

## Summary
依 SD §5.6/§7.3 與 design-closure C1 expert-consult SPEC、B1 information-lead-proxy policy 做 consult/committee/red_team workflow:中央人格只收受限 ContextBundle(沿用 AG-BE-ID-004 redaction,raw_prompt_included=false),產出 ConsultMemo/RiskNote/CritiqueResult/EvidenceBundle。資訊領先只能產出 B1 允許的 proxy 並附 disclaimer,不得斷言內線/操縱。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
