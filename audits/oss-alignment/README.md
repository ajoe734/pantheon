# OSS Alignment Audits

This folder contains the per-LLM audit notes for the rebaseline that clarified named OSS components are real upstream integrations, not just architecture labels.

Each audit should:

1. inspect the assigned prior work
2. classify each item as:
   - valid as-is
   - valid but only local wrapper/contract
   - missing upstream integration step
   - needs new spike task
3. list concrete follow-up work
4. update `ai-status.json` through `scripts/ai-status.sh`

Canonical references:

- `OSS_INTEGRATION_AUDIT.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `WORK_REBASELINE.md`

