# Grok OSS Alignment Audit

Scope:

- `RS-000`
- `RS-001`
- `RS-002`
- `RS-003`

Questions to answer:

1. Are the source assumptions correctly limited to structured/governed sources?
2. Does the current work split respect Grok's coding-assist versus research boundaries?
3. Which research adapters must exist before downstream tasks should continue?
4. Are any current task descriptions still too conceptual?

Output sections:

- Summary
- Findings
- Source and ingestion constraints
- Adapter requirements
- Recommended task/status corrections

## Summary

RS-000 (completed by Grok) correctly defines structured source constraints using OpenAlex API and GitHub REST API, aligning with OSS integration requirements. However, RS-001 through RS-003 remain conceptual and require explicit upstream integration steps including dependency addition, adapter implementation, and smoke testing before they can be considered complete.

## Findings

### RS-000: Valid as Local Spec with Upstream Pointers
- **Status**: Valid but only local wrapper/contract
- **Evidence**: Defines OpenAlex API and GitHub REST API as approved sources, includes governance context requirements
- **Gap**: No version pinning, dependency addition, or smoke test yet
- **Recommendation**: Keep as completed spec, but add follow-up integration tasks

### RS-001: Missing Upstream Integration Steps
- **Status**: Missing upstream integration step
- **Evidence**: ROADMAP requires structured APIs, but no actual adapter code or dependency exists
- **Gap**: No integration.md, adapter/, smoke_test.md for OpenAlex/GitHub APIs
- **Recommendation**: Create new spike task for API client integration

### RS-002: Conceptual Normalization Task
- **Status**: Valid but only local wrapper/contract
- **Evidence**: Defines StrategySpec output format, but no real adapter to transform API responses
- **Gap**: Normalization logic not implemented
- **Recommendation**: Depends on RS-001 adapter completion

### RS-003: Conceptual Gate Task
- **Status**: Valid but only local wrapper/contract
- **Evidence**: Defines replication gate concept, but no implementation
- **Gap**: No actual gate code or integration with research frameworks
- **Recommendation**: Depends on RS-002 and upstream research framework integration

## Source and Ingestion Constraints

Current source assumptions are sound:
- OpenAlex API for academic papers (structured, governed)
- GitHub REST API for repositories (structured, governed)
- No general web scraping allowed

However, constraints need enforcement through actual adapter code, not just documentation.

## Adapter Requirements

Before downstream tasks continue, these adapters must exist:

1. **OpenAlex Client Adapter**
   - Python client for OpenAlex API
   - Response normalization to internal schema
   - Rate limiting and error handling
   - Governance metadata preservation

2. **GitHub API Client Adapter**
   - REST API client for repository contents
   - Structured search within approved repos
   - Content extraction and metadata handling
   - Authentication and rate limiting

3. **StrategySpec Normalizer**
   - Transform API responses into StrategySpec JSON
   - Validation against schema
   - Confidence scoring for normalization quality

4. **Replication Gate Adapter**
   - Interface to research frameworks (Qlib, etc.)
   - First-pass execution and validation
   - Pass/fail criteria implementation

## Recommended Task/Status Corrections

1. **Mark RS-000 as complete** with note that it's spec-only, integration follows
2. **Create AUD-GROK-002**: "Implement OpenAlex and GitHub API client adapters with smoke tests"
3. **Block RS-001 on AUD-GROK-002 completion**
4. **Block RS-002 on RS-001 completion**
5. **Block RS-003 on RS-002 and upstream research framework integration**

This ensures research tasks don't proceed as conceptual work but require real upstream integration evidence.

