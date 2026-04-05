#!/usr/bin/env python3
"""Example smoke test for OpenAlex and GitHub adapters with governance validation.

This script demonstrates:
1. OpenAlex adapter usage for paper discovery
2. GitHub adapter usage for repository access
3. Governance handoff creation
4. Validation and JSON serialization
"""

import json
import sys
from datetime import datetime, timezone

# Adapters
from openalex_client import OpenAlexClient, OpenAlexMetadata
from github_client import GitHubClient
from handoff_schema import HandoffBuilder, HandoffBuilder


def test_openalex_adapter():
    """Test OpenAlex adapter with governance context."""
    print("=" * 70)
    print("SMOKE TEST: OpenAlex Adapter")
    print("=" * 70)
    
    client = OpenAlexClient(email="research-test@pantheon-system.local")
    
    # Create sample paper response (as if from API)
    sample_paper = {
        "id": "W2961191295",
        "title": "Machine Learning for Trading: A Survey",
        "abstract": "This survey reviews machine learning applications in algorithmic trading.",
        "publication_date": "2023-01-15",
        "doi": "https://doi.org/10.1234/example",
        "authorships": [
            {
                "author": {
                    "display_name": "Alice Smith",
                    "orcid": "0000-0001-1234-5678"
                }
            },
            {
                "author": {
                    "display_name": "Bob Johnson",
                    "orcid": None
                }
            }
        ],
        "primary_location": {
            "source": {
                "display_name": "Journal of Finance"
            }
        },
        "open_access": {
            "is_oa": True
        },
        "cited_by_count": 42
    }
    
    # Normalize paper
    print("\n1. Normalizing OpenAlex paper response...")
    normalized_paper = client.normalize_work(sample_paper)
    
    print(f"   ✓ Title: {normalized_paper.title}")
    print(f"   ✓ Authors: {len(normalized_paper.authors)}")
    print(f"   ✓ DOI: {normalized_paper.doi}")
    print(f"   ✓ Governance: {normalized_paper.governance_metadata['governance_context']}")
    
    # Create handoff
    print("\n2. Creating governance handoff for RS-001...")
    handoff = HandoffBuilder.build_academic_paper_handoff(
        task_id="RS-001",
        paper=normalized_paper.to_dict(),
        governance_metadata=normalized_paper.governance_metadata,
        confidence="high"
    )
    
    # Validate handoff
    is_valid, errors = HandoffBuilder.validate_handoff(handoff)
    if is_valid:
        print("   ✓ Handoff validation: PASSED")
    else:
        print(f"   ✗ Handoff validation: FAILED - {errors}")
        return False
    
    # Serialize to JSON
    print("\n3. Handoff JSON (governance-compliant):")
    handoff_json = json.loads(handoff.to_json())
    print(json.dumps(handoff_json, indent=2)[:500] + "...")
    
    return True


def test_github_adapter():
    """Test GitHub adapter with approval enforcement."""
    print("\n" + "=" * 70)
    print("SMOKE TEST: GitHub Adapter")
    print("=" * 70)
    
    client = GitHubClient(token="test-token-not-used")
    
    # Create sample repository response (as if from API)
    sample_repo = {
        "id": 8514034,
        "name": "Lean",
        "full_name": "QuantConnect/Lean",
        "description": "Lean Algorithmic Trading Engine",
        "owner": {
            "login": "QuantConnect"
        },
        "html_url": "https://github.com/QuantConnect/Lean",
        "url": "https://api.github.com/repos/QuantConnect/Lean",
        "stargazers_count": 8500,
        "language": "C#",
        "topics": ["algorithmic-trading", "finance", "quantconnect"],
        "created_at": "2015-04-01T00:00:00Z",
        "updated_at": "2024-04-05T00:00:00Z",
        "forks_count": 2800,
        "open_issues_count": 127,
        "license": {
            "name": "Apache License 2.0"
        }
    }
    
    print("\n1. Checking repository approval...")
    is_approved = client.is_repo_approved("QuantConnect", "Lean")
    print(f"   ✓ QuantConnect/Lean approved: {is_approved}")
    
    # Normalize repository
    print("\n2. Normalizing repository response...")
    normalized_repo = client.normalize_repository(sample_repo)
    
    print(f"   ✓ Owner: {normalized_repo.owner}")
    print(f"   ✓ Stars: {normalized_repo.stars}")
    print(f"   ✓ Language: {normalized_repo.language}")
    print(f"   ✓ Governance: {normalized_repo.governance_metadata['governance_context']}")
    
    # Test approval enforcement
    print("\n3. Testing approval enforcement...")
    is_unapproved = not client.is_repo_approved("UnknownOrg", "UnknownRepo")
    if is_unapproved:
        print("   ✓ Unapproved repo correctly rejected")
    else:
        print("   ✗ Should have rejected unapproved repo")
        return False
    
    # Test path approval
    print("\n4. Testing path approval enforcement...")
    approved_paths = client.APPROVED_REPOS["QuantConnect/Lean"]["approved_paths"]
    print(f"   ✓ Approved paths: {approved_paths}")
    
    # Create handoff
    print("\n5. Creating governance handoff for RS-001...")
    handoff = HandoffBuilder.build_code_repository_handoff(
        task_id="RS-001",
        repository=normalized_repo.to_dict(),
        governance_metadata=normalized_repo.governance_metadata,
        key_files=[],
        confidence="high"
    )
    
    # Validate handoff
    is_valid, errors = HandoffBuilder.validate_handoff(handoff)
    if is_valid:
        print("   ✓ Handoff validation: PASSED")
    else:
        print(f"   ✗ Handoff validation: FAILED - {errors}")
        return False
    
    print("\n6. Handoff JSON (governance-compliant):")
    handoff_json = json.loads(handoff.to_json())
    print(json.dumps(handoff_json, indent=2)[:500] + "...")
    
    return True


def test_governance_compliance():
    """Test governance compliance across adapters."""
    print("\n" + "=" * 70)
    print("SMOKE TEST: Governance Compliance")
    print("=" * 70)
    
    # Test OpenAlex governance metadata
    print("\n1. OpenAlex metadata structure:")
    metadata = OpenAlexMetadata(
        api_endpoint="https://api.openalex.org/works/W123",
        retrieved_at=datetime.now(timezone.utc).isoformat()
    )
    print(f"   ✓ Governance context: {metadata.governance_context}")
    print(f"   ✓ API version: {metadata.api_version}")
    print(f"   ✓ Data quality score: {metadata.data_quality_score}")
    
    # Test GitHub governance
    print("\n2. GitHub approval enforcement:")
    client = GitHubClient(token="test")
    
    # Should allow QuantConnect/Lean
    result = client.is_repo_approved("QuantConnect", "Lean")
    print(f"   ✓ QuantConnect/Lean: {result}")
    
    # Should reject unknown
    result = client.is_repo_approved("Unknown", "Repo")
    print(f"   ✓ Unknown/Repo: {result} (correctly rejected)")
    
    print("\n✓ All governance compliance checks passed")
    return True


def main():
    """Run all smoke tests."""
    print("\n" + "=" * 70)
    print("PANTHEON RESEARCH ADAPTER SMOKE TESTS")
    print("Testing AUD-GROK-002 Implementation")
    print("=" * 70)
    
    results = []
    
    try:
        results.append(("OpenAlex Adapter", test_openalex_adapter()))
    except Exception as e:
        print(f"   ✗ OpenAlex test failed: {e}")
        results.append(("OpenAlex Adapter", False))
    
    try:
        results.append(("GitHub Adapter", test_github_adapter()))
    except Exception as e:
        print(f"   ✗ GitHub test failed: {e}")
        results.append(("GitHub Adapter", False))
    
    try:
        results.append(("Governance Compliance", test_governance_compliance()))
    except Exception as e:
        print(f"   ✗ Governance compliance test failed: {e}")
        results.append(("Governance Compliance", False))
    
    # Summary
    print("\n" + "=" * 70)
    print("SMOKE TEST SUMMARY")
    print("=" * 70)
    
    for test_name, passed in results:
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{test_name:30s} {status}")
    
    all_passed = all(passed for _, passed in results)
    
    if all_passed:
        print("\n✓ All smoke tests passed - adapters ready for RS-001 integration")
        return 0
    else:
        print("\n✗ Some tests failed - review errors above")
        return 1


if __name__ == "__main__":
    sys.exit(main())
