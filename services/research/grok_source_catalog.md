# Grok Source Catalog

## Overview

This catalog defines the approved structured sources that Grok may use for research ingestion. All sources must be governed APIs with predictable schemas and stable endpoints. No general web scraping or unstructured search is permitted.

## Academic Research Sources

### OpenAlex API
- **Purpose**: Academic paper discovery and metadata retrieval
- **Endpoint**: `https://api.openalex.org/`
- **Allowed Operations**:
  - Works search and retrieval
  - Author and institution metadata
  - Citation networks
  - Abstract and full-text links (when available)
- **Required Fields**: title, authors, abstract, DOI, publication_date
- **Governance**: Must include API response metadata in handoffs
- **Rate Limits**: Respect OpenAlex API limits (10k requests/day for free tier)

### Usage Guidelines
- Search for papers using structured queries (title, author, DOI)
- Extract key findings and methodology sections
- Normalize into StrategySpec format
- Include full citation metadata

## Code Repository Sources

### GitHub REST API
- **Purpose**: Code repository analysis and research code discovery
- **Endpoints**:
  - `https://api.github.com/repos/{owner}/{repo}`
  - `https://api.github.com/repos/{owner}/{repo}/contents/{path}`
  - `https://api.github.com/repos/{owner}/{repo}/issues`
  - `https://api.github.com/repos/{owner}/{repo}/pulls`
- **Allowed Repositories**: Only explicitly listed governed repositories
- **Operations**:
  - Repository contents retrieval
  - Issue and PR analysis
  - Code search within approved repos
- **Authentication**: Use GitHub tokens if required (no personal tokens)

### Approved Research Repositories

#### QuantConnect Research
- **Owner**: QuantConnect
- **Repository**: Lean
- **Purpose**: Official LEAN research and examples
- **Allowed Paths**: Research/, Algorithm.Python/, documentation
- **Notes**: Focus on research notebooks and example algorithms

#### Academic Finance Repositories
- **Owner**: Various academic institutions
- **Repository**: Must be explicitly approved per task
- **Purpose**: Peer-reviewed research implementations
- **Requirements**: Must have DOI or academic affiliation

#### Open Source Finance Libraries
- **Owner**: Various
- **Repository**: Qlib, FinRL, RLlib, etc.
- **Purpose**: Framework research and implementation patterns
- **Notes**: Limited to documented APIs and examples

## Research Notes Sources

### Governed GitHub Repositories
- **Purpose**: Structured research sharing within approved communities
- **Format Requirements**:
  - Markdown files with YAML frontmatter
  - Required metadata: title, author, date, tags, doi (if applicable)
  - Structured sections: Abstract, Methodology, Results, Code
- **Allowed Repositories**: Must be listed in this catalog
- **Access**: Public repositories only

### Approved Research Note Repositories

#### QuantConnect Research Notes
- **Owner**: QuantConnect
- **Repository**: research-notes (hypothetical)
- **Purpose**: Internal research sharing
- **Format**: Standardized Markdown with metadata

## Source Validation Rules

### Pre-Ingest Checks
1. Verify source is in approved catalog
2. Check API availability and rate limits
3. Validate response schema matches expectations
4. Ensure source provides required metadata fields

### Post-Ingest Processing
1. Extract and preserve source metadata
2. Normalize content into governed formats
3. Include governance context in handoffs
4. Record processing confidence levels

### Error Handling
- If source unavailable: Log blocker and wait for resolution
- If schema changed: Update catalog and reprocess
- If rate limited: Implement backoff and retry logic

## Governance Context Requirements

All ingested material must include:

```json
{
  "source_type": "openalex|github_api|research_notes",
  "source_url": "https://api.openalex.org/works/W123456",
  "retrieved_at": "2026-04-02T12:00:00Z",
  "governance_status": "approved_catalog_source",
  "metadata_preservation": {
    "original_schema": "OpenAlex v2",
    "required_fields_present": true,
    "data_quality_score": "high|medium|low"
  }
}
```

## Maintenance

### Catalog Updates
- New sources require explicit approval through status system
- Schema changes must be documented and tested
- Rate limit changes must be monitored

### Deprecation
- Deprecated sources marked as such
- Migration path provided for dependent tasks
- Historical data preserved with deprecation metadata

## Security and Compliance

- No personal API keys or credentials
- All requests logged for audit purposes
- Source data treated as research input only
- No execution of retrieved code without governance review