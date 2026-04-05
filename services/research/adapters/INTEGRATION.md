# Research Adapter Integration Guide

## Overview

The OpenAlex and GitHub adapters implement the `AUD-GROK-002` spike task, providing governed interfaces for structured research ingestion while maintaining governance compliance and source validation.

## Adapters

### OpenAlex Client (`openalex_client.py`)

Provides access to academic papers via the OpenAlex API with governance tracking.

**Key Features:**
- Rate limiting (0.5s delay between requests)
- Governance metadata preservation
- Paper metadata normalization to internal schemas
- Error handling for API failures and validation

**Usage:**
```python
from services.research.adapters import OpenAlexClient

client = OpenAlexClient(email="your-email@example.com")

# Search for papers
papers = client.search_and_normalize(
    title="machine learning finance",
    limit=5
)

# Normalize individual paper
normalized = client.normalize_work(work_response)
```

**API Endpoints:**
- `GET /works` - Search for papers
- `GET /works/{id}` - Get specific paper

**Rate Limits:**
- Free tier: 10,000 requests/day
- Polite requests: Include email parameter for higher rates

### GitHub REST API Client (`github_client.py`)

Provides structured access to approved repositories via the GitHub REST API with approval enforcement.

**Key Features:**
- Approved repository whitelist enforcement
- Path-based access control within repositories
- Rate limiting and pagination
- Governance metadata preservation
- Base64 content decoding

**Approved Repositories:**
- `QuantConnect/Lean` (paths: Research/, Algorithm.Python/, documentation/)

**Usage:**
```python
from services.research.adapters import GitHubClient

client = GitHubClient(token="gh_xxxx")

# Get repository metadata
repo = client.get_repository("QuantConnect", "Lean")
normalized_repo = client.normalize_repository(repo)

# Get file content from approved path
file = client.get_file_content("QuantConnect", "Lean", "Research/README.md")
normalized_file = client.normalize_file(file, "QuantConnect/Lean")
```

**API Endpoints:**
- `GET /repos/{owner}/{repo}` - Repository metadata
- `GET /repos/{owner}/{repo}/contents/{path}` - File contents
- `GET /repos/{owner}/{repo}/issues` - Repository issues
- `GET /repos/{owner}/{repo}/pulls` - Pull requests

**Rate Limits:**
- Unauthenticated: 60 requests/hour
- Authenticated: 5,000 requests/hour

## Governance Schema (`handoff_schema.py`)

Defines the structure for handing off research findings to downstream consumers (RS-001, RS-002).

**Handoff Structure:**
```json
{
  "task_id": "RS-001",
  "source_type": "academic_paper|code_repository|research_note",
  "source_metadata": {
    "api_endpoint": "https://api.openalex.org/works/...",
    "retrieved_at": "2026-04-05T12:00:00Z",
    "governance_context": "Approved structured source"
  },
  "normalized_findings": {
    "strategy_spec": {
      "name": "Strategy Name",
      "description": "Normalized description",
      "signals": [],
      "parameters": {}
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

**Building Handoffs:**
```python
from services.research.adapters.handoff_schema import HandoffBuilder

# Build from academic paper
handoff = HandoffBuilder.build_academic_paper_handoff(
    task_id="RS-001",
    paper=normalized_paper,
    governance_metadata=paper.governance_metadata,
    confidence="high"
)

# Validate before handing off
is_valid, errors = HandoffBuilder.validate_handoff(handoff)
if not is_valid:
    print(f"Validation errors: {errors}")
else:
    handoff_json = handoff.to_json()
```

## Smoke Tests

Run the comprehensive test suite to verify adapter functionality and governance compliance:

```bash
cd services/research/adapters
python3 -m unittest discover -s . -p "test_*.py" -v
```

**Test Coverage:**
- Adapter initialization and configuration
- API request handling and error cases
- Response normalization and validation
- Governance metadata preservation
- Repository approval enforcement
- File content decoding
- Serialization to JSON

All 16 tests should pass, confirming:
- ✅ OpenAlex adapter governance compliance
- ✅ GitHub adapter approval enforcement
- ✅ Metadata preservation in normalized responses
- ✅ Error handling for malformed data

## Integration Steps (for RS-001)

1. **Create adapter instances** in RS-001 implementation
2. **Configure approved sources** in GitHub approved repos list
3. **Search and normalize** research materials
4. **Build governance handoffs** using HandoffBuilder
5. **Validate handoffs** before downstream consumption
6. **Update status** with handoff artifacts

## Governance Requirements Met

This implementation satisfies the `AUD-GROK-002` spike audit findings:

- ✅ **OpenAlex Client Adapter**: Implemented with rate limiting and governance metadata
- ✅ **GitHub API Client Adapter**: Implemented with approval enforcement
- ✅ **StrategySpec Normalizer**: Governance schema with validation
- ✅ **Smoke Tests**: Comprehensive test coverage (16 tests, 100% passing)
- ✅ **Error Handling**: HTTPError and validation exception handling
- ✅ **Rate Limiting**: Respects API rate limits and delays between requests
- ✅ **Governance Context**: All responses include governance metadata and source tracking

## Dependencies

No external Python packages required beyond standard library:
- `json` - Response parsing
- `urllib` - HTTP requests
- `base64` - File content decoding
- `dataclasses` - Schema definitions
- `datetime` - Timestamp tracking

This maintains the research service isolation principle and avoids dependency conflicts.

## Next Steps

After AUD-GROK-002 completion:

1. **RS-001**: Build research ingestion workflow using these adapters
2. **RS-002**: Normalize discovered material into StrategySpec format
3. **RS-003**: Run first-pass replication gate before registry admission
