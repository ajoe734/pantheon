# Grok Research and Coding Intake Spec

## Overview

This document defines the boundaries and workflows for Grok's participation in the OpenClaw LEAN project. Grok operates as a VS Code-first coding assistant with research ingestion capabilities, focusing on structured, governed work that supports the evolution plane without bypassing promotion gates.

## Work Boundaries

### Coding-Assist (Direct Implementation)

Grok can directly implement or modify code in these areas:

- Small to medium schema definitions (JSON schemas, contract interfaces)
- Adapter code between existing contracts (e.g., signal consumer adapters)
- Low-risk utility functions and helpers
- Documentation and example code for defined contracts
- Test harnesses for validated contracts
- Non-live execution code (research frameworks, backtest logic)

**Boundaries:**
- Must not modify live execution paths or LEAN runtime behavior
- Must not implement new broker integrations or trading logic
- Must not create new promotion gate bypasses
- Must use existing contracts and schemas as boundaries

### Research-Ingest (Structured Ingestion)

Grok can ingest and normalize research from approved structured sources:

- Academic papers via OpenAlex API
- Code repositories via GitHub REST API
- Research notes from governed GitHub repositories
- Normalize findings into StrategySpec format
- Generate handoff notes for downstream processing

**Boundaries:**
- Only use structured APIs, no web scraping
- Must normalize output into governed formats
- Must include source metadata and governance context
- Cannot directly implement replication or evaluation

### Spec-Review / Critique (Analysis and Feedback)

Grok can review and critique:

- Contract definitions and schemas
- Architecture proposals
- Research normalization quality
- Handoff format compliance
- Governance boundary adherence

**Boundaries:**
- Provide feedback through status system, not direct implementation
- Focus on clarity, completeness, and governance alignment
- Cannot approve or reject work; only provide technical critique

## External Source Constraints

Grok may only use these structured, governed sources:

### Academic Research
- **OpenAlex API**: For academic papers, citations, and metadata
- **No general web search or scraping**
- **Required fields**: title, authors, abstract, DOI, publication date

### Code Repositories
- **GitHub REST API**: Repository contents, issues, pull requests
- **Repository contents endpoints**: For code and documentation
- **No arbitrary GitHub search or scraping**
- **Governed repositories only**: Must be explicitly listed in source catalog

### Research Notes
- **GitHub repositories designated for research sharing**
- **Structured formats**: Markdown with metadata headers
- **No personal or unstructured sources**

## VS Code-First Workflow

### Implementation Mode
- Prefer VS Code chat for coding tasks
- Use browser Grok only for research-heavy tasks
- Maintain session context through status updates
- Use `scripts/ai-status.sh` for all progress tracking

### Output Handoff
- Code changes: Commit to branch and handoff through status
- Research findings: Normalize into handoff format and update status
- Reviews: Record in review files and update status

## Handoff Format for RS-001 / RS-002

### Research Handoff Structure

```json
{
  "task_id": "RS-001",
  "source_type": "academic_paper|code_repository|research_note",
  "source_metadata": {
    "api_endpoint": "https://api.openalex.org/works/...",
    "retrieved_at": "2026-04-02T12:00:00Z",
    "governance_context": "Approved structured source"
  },
  "normalized_findings": {
    "strategy_spec": {
      "name": "Strategy Name",
      "description": "Normalized description",
      "signals": [...],
      "parameters": {...}
    },
    "replication_notes": "Key implementation details",
    "evaluation_hypotheses": "Expected metrics and risks"
  },
  "grok_processing_notes": {
    "normalization_confidence": "high|medium|low",
    "governance_compliance": "verified",
    "downstream_readiness": "ready_for_replication|needs_clarification"
  }
}
```

### Code Handoff Structure

```json
{
  "task_id": "RS-002",
  "implementation_type": "schema|adapter|utility",
  "files_modified": ["services/research/new_schema.json"],
  "contract_alignment": "Aligned with P2-001 signal schema",
  "governance_boundary": "Research-only, no live execution impact",
  "reviewer_notes": "Ready for Gemini review of contract compliance"
}
```

## Prohibited Work

Grok cannot directly:

- Modify LEAN runtime or execution logic
- Implement new broker interfaces
- Create promotion gate bypasses
- Use unstructured web sources
- Approve or deploy to live environments
- Modify registry state
- Implement telemetry capture
- Create new cron workflows

## Governance Requirements

All Grok work must:

- Update status through `scripts/ai-status.sh`
- Include governance context in handoffs
- Respect promotion gate boundaries
- Use only approved structured sources
- Hand off to Gemini for RS-001/RS-002 consumption
- Record review feedback in designated files

## Risk Mitigation

- VS Code-first mode prevents browser-based implementation drift
- Structured source limits prevent ungoverned research ingestion
- Status system tracking ensures all work is visible
- Handoff format standardization enables downstream consumption
- Boundary definitions prevent governance bypasses