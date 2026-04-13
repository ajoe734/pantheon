# BG-007 Review

Reviewer: Codex
Date: 2026-04-13
Outcome: reopen

## Findings

1. The glossary claims to translate canonical truth, but many "L1 Source" citations are task IDs or vague labels instead of concrete source documents.
   - Examples include `OC-003`, `REG-001`, `REG-002`, `RS-001`, `EV-001`, `LP-004`, and the plain-text label `Market Data Scope Plan` in the source column and Appendix A ([docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:33](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:33), [docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:57](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:57), [docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:93](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:93), [docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:127](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:127), [docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:260](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:260)).
   - The repo already has concrete canonical sources for these concepts, such as the StrategySpec contract, registry contract, and promotion-gate README ([services/control-plane/specs/contract.md](/home/ajoe734/code/pantheon/services/control-plane/specs/contract.md:21), [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md:12), [services/registry/promotion/README.md](/home/ajoe734/code/pantheon/services/registry/promotion/README.md:20)).
   - As written, the document is not self-traceable back to the semantic source it claims to translate.

2. The persona lifecycle language is materially incorrect and incomplete.
   - The status table says persona management should expose `lifecycle_state: active` and `lifecycle_state: inactive` ([docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:179](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:179)).
   - The canonical persona lifecycle is `draft`, `research_only`, `consultable`, `paper_owner`, `live_owner`, `frozen`, `retired` in both the L1 persona runtime model and the persona contract ([PERSONA_RUNTIME_MODEL.md](/home/ajoe734/code/pantheon/PERSONA_RUNTIME_MODEL.md:200), [services/control-plane/persona/contract.md](/home/ajoe734/code/pantheon/services/control-plane/persona/contract.md:150)).
   - GAP-07 specifically needs operator-readable persona lifecycle wording; the current draft replaces the real lifecycle with a simplified pair instead of translating the real states.

3. The binding status section collapses two different truth layers without saying which one is being translated.
   - The language pack only presents `active` / `inactive` binding states ([docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:212](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:212)).
   - Canonical binding truth distinguishes governance status (`pending`, `active`, `suspended`, `revoked`, `expired`) from the coarse execution DB projection (`active` / `inactive`) ([BINDING_AND_DEPLOYMENT_SEMANTICS.md](/home/ajoe734/code/pantheon/BINDING_AND_DEPLOYMENT_SEMANTICS.md:424)).
   - A product-facing language pack can simplify this, but it has to say whether it is showing governance truth or a derived DB/read-model projection; otherwise operators lose important admissibility meaning.

4. The action-to-object map still contains non-canonical or incorrect object semantics.
   - "Submit Artifact" targets `CandidateArtifact`, but the current cross-plane canonical backbone uses `ArtifactRecord` / registry entry as the governed artifact object ([docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:145](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:145), [TARGET_ARCHITECTURE.md](/home/ajoe734/code/pantheon/TARGET_ARCHITECTURE.md:115), [services/registry/contract.md](/home/ajoe734/code/pantheon/services/registry/contract.md:36)).
   - "Freeze Artifact" says the target is "Artifact state", but `frozen` is a deployment stage and freeze is an evolution/governance action, not an `artifact_state` value ([docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:151](/home/ajoe734/code/pantheon/docs/PRODUCT_FACING_GLOSSARY_AND_LANGUAGE_PACK.md:151), [TARGET_ARCHITECTURE.md](/home/ajoe734/code/pantheon/TARGET_ARCHITECTURE.md:21), [services/registry/promotion/README.md](/home/ajoe734/code/pantheon/services/registry/promotion/README.md:48)).
   - This is exactly the kind of operator-facing ambiguity GAP-07 is supposed to remove: what action touches which canonical object.

## Notes

- The document structure is directionally correct: it already has glossary, action map, and stage/status sections in one place.
- The Qwen handoff note claims 20 action rows, 12 error/alert messages, and 8 tooltips, but the current draft contains 18 action rows, 8 alerts, and 7 tooltips. Reconcile the artifact summary with the actual content while fixing the semantic issues above.
