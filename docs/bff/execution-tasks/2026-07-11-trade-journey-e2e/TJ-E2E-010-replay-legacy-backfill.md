# TJ-E2E-010 - Historical Replay And Legacy Backfill

Owner: Antigravity  
Reviewer: Claude  
Wave: 4  
Repository: `ajoe734/pantheon`  
Dependencies: `TJ-E2E-004`, `TJ-E2E-005`

## Goal

Provide as-of replay and confidence-labelled legacy mapping without converting
inference into false audit truth.

## Required work and acceptance

- Replay historical Persona/model/strategy/policy/binding/risk/broker versions.
- Preserve occurred-at vs recorded-at and correction overlays.
- Backfill reliable mappings; mark inferred confidence and queue orphans.
- Publish before/after completeness, conflict and orphan evidence.
- Prove deterministic historical cases and merge to Pantheon `dev`.
