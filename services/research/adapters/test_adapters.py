"""Smoke tests for OpenAlex and GitHub adapters.

Tests governance compliance, API integration, and normalization quality.
"""

import unittest
from datetime import datetime, timezone
from pathlib import Path
import sys

_ADAPTERS_DIR = Path(__file__).resolve().parent
if str(_ADAPTERS_DIR) not in sys.path:
    sys.path.insert(0, str(_ADAPTERS_DIR))

from openalex_client import OpenAlexClient, OpenAlexMetadata, OpenAlexWorkResponse
from coingecko_client import CoinGeckoClient
from github_client import GitHubClient, GitHubRepositoryResponse, GitHubFileResponse
from taiwan_market_client import TaiwanMarketClient


class TestOpenAlexClient(unittest.TestCase):
    """Tests for OpenAlex API adapter."""

    def setUp(self):
        """Initialize test client."""
        self.client = OpenAlexClient(email="test@example.com")

    def test_initialization(self):
        """Test client initialization."""
        self.assertEqual(self.client.email, "test@example.com")
        self.assertEqual(self.client.rate_limit_delay, 0.5)
        self.assertEqual(self.client.last_request_time, 0.0)

    def test_openalex_metadata_structure(self):
        """Test governance metadata structure."""
        metadata = OpenAlexMetadata(
            api_endpoint="https://api.openalex.org/works/W123",
            retrieved_at=datetime.now(timezone.utc).isoformat()
        )

        self.assertEqual(metadata.api_endpoint, "https://api.openalex.org/works/W123")
        self.assertEqual(metadata.governance_context, "Approved structured source")
        self.assertEqual(metadata.api_version, "2")
        self.assertEqual(metadata.data_quality_score, "high")

    def test_work_response_normalization(self):
        """Test work response normalization to internal format."""
        work = {
            "id": "W123456",
            "title": "Test Paper Title",
            "abstract": "Test abstract content",
            "publication_date": "2024-01-01",
            "doi": "https://doi.org/10.1234/test",
            "authorships": [
                {
                    "author": {
                        "display_name": "John Doe",
                        "orcid": "0000-0001-1234-5678"
                    }
                }
            ],
            "primary_location": {
                "source": {
                    "display_name": "Test Journal"
                }
            },
            "open_access": {
                "is_oa": True
            },
            "cited_by_count": 42
        }

        normalized = self.client.normalize_work(work)

        self.assertEqual(normalized.work_id, "W123456")
        self.assertEqual(normalized.title, "Test Paper Title")
        self.assertEqual(normalized.abstract, "Test abstract content")
        self.assertEqual(normalized.doi, "10.1234/test")
        self.assertEqual(len(normalized.authors), 1)
        self.assertEqual(normalized.authors[0]["name"], "John Doe")
        self.assertEqual(
            normalized.governance_metadata["governance_context"],
            "Approved structured source"
        )

    def test_work_response_missing_fields(self):
        """Test error handling for missing required fields."""
        incomplete_work = {
            "id": "W123456",
            "title": "Test Paper"
            # Missing publication_date
        }

        with self.assertRaises(ValueError) as ctx:
            self.client.normalize_work(incomplete_work)

        self.assertIn("Missing required fields", str(ctx.exception))
        self.assertIn("publication_date", str(ctx.exception))

    def test_work_to_dict_serialization(self):
        """Test serialization of normalized work to dictionary."""
        normalized_response = OpenAlexWorkResponse(
            work_id="W123",
            title="Test",
            abstract="Abstract",
            authors=[],
            publication_date="2024-01-01",
            doi="10.1234/test",
            source_metadata={"venue": "Test Journal"},
            governance_metadata={}
        )

        result = normalized_response.to_dict()

        self.assertEqual(result["work_id"], "W123")
        self.assertEqual(result["title"], "Test")
        self.assertIsInstance(result, dict)


class TestGitHubClient(unittest.TestCase):
    """Tests for GitHub API adapter."""

    def setUp(self):
        """Initialize test client."""
        self.client = GitHubClient(token="test-token")

    def test_initialization(self):
        """Test client initialization."""
        self.assertEqual(self.client.token, "test-token")
        self.assertEqual(self.client.rate_limit_delay, 0.1)

    def test_approved_repos(self):
        """Test approved repository list."""
        approved = self.client.APPROVED_REPOS

        self.assertIn("QuantConnect/Lean", approved)
        self.assertEqual(approved["QuantConnect/Lean"]["purpose"], "Official LEAN research and examples")

    def test_repo_approval_check(self):
        """Test repository approval validation."""
        # Approved repo
        self.assertTrue(self.client.is_repo_approved("QuantConnect", "Lean"))

        # Non-approved repo
        self.assertFalse(self.client.is_repo_approved("SomeOrg", "SomeRepo"))

    def test_repository_normalization(self):
        """Test repository response normalization."""
        repo = {
            "id": 123456,
            "name": "Lean",
            "full_name": "QuantConnect/Lean",
            "owner": {
                "login": "QuantConnect"
            },
            "description": "Test description",
            "html_url": "https://github.com/QuantConnect/Lean",
            "stargazers_count": 1000,
            "language": "C#",
            "topics": ["finance", "trading"],
            "created_at": "2020-01-01T00:00:00Z",
            "updated_at": "2024-01-01T00:00:00Z",
            "url": "https://api.github.com/repos/QuantConnect/Lean",
            "forks_count": 200,
            "open_issues_count": 50,
            "license": {
                "name": "Apache 2.0"
            }
        }

        normalized = self.client.normalize_repository(repo)

        self.assertEqual(normalized.owner, "QuantConnect")
        self.assertEqual(normalized.name, "Lean")
        self.assertEqual(normalized.full_name, "QuantConnect/Lean")
        self.assertEqual(normalized.stars, 1000)
        self.assertEqual(len(normalized.topics), 2)
        self.assertEqual(
            normalized.governance_metadata["repo_approved"],
            True
        )

    def test_file_normalization_plain_text(self):
        """Test file response normalization for plain text."""
        file_obj = {
            "path": "README.md",
            "sha": "abc123",
            "size": 1024,
            "url": "https://api.github.com/repos/QuantConnect/Lean/contents/README.md",
            "html_url": "https://github.com/QuantConnect/Lean/blob/master/README.md",
            "content": "# Test Content",
            "encoding": "utf-8"
        }

        normalized = self.client.normalize_file(file_obj, "QuantConnect/Lean")

        self.assertEqual(normalized.file_path, "README.md")
        self.assertEqual(normalized.content, "# Test Content")
        self.assertEqual(normalized.repo_full_name, "QuantConnect/Lean")

    def test_file_normalization_base64(self):
        """Test file response normalization for base64 encoded content."""
        import base64

        original_content = "Binary content here"
        encoded = base64.b64encode(original_content.encode()).decode()

        file_obj = {
            "path": "binary_file.bin",
            "sha": "def456",
            "size": 2048,
            "url": "https://api.github.com/repos/QuantConnect/Lean/contents/binary_file.bin",
            "html_url": "https://github.com/QuantConnect/Lean/blob/master/binary_file.bin",
            "content": encoded,
            "encoding": "base64"
        }

        normalized = self.client.normalize_file(file_obj, "QuantConnect/Lean")

        self.assertEqual(normalized.content, original_content)

    def test_unapproved_repo_rejection(self):
        """Test that unapproved repositories raise errors."""
        with self.assertRaises(ValueError) as ctx:
            self.client.get_repository("UnknownOrg", "UnknownRepo")

        self.assertIn("not in approved list", str(ctx.exception))

    def test_unapproved_path_rejection(self):
        """Test that paths outside approved list are rejected."""
        with self.assertRaises(ValueError) as ctx:
            self.client.get_file_content("QuantConnect", "Lean", "unapproved/path/file.py")

        self.assertIn("not in approved paths", str(ctx.exception))

    def test_repository_to_dict_serialization(self):
        """Test serialization of normalized repository to dictionary."""
        repo_response = GitHubRepositoryResponse(
            repo_id="123456",
            owner="QuantConnect",
            name="Lean",
            full_name="QuantConnect/Lean",
            description="Test",
            url="https://github.com/QuantConnect/Lean",
            stars=1000,
            language="C#",
            topics=["finance"],
            source_metadata={},
            governance_metadata={}
        )

        result = repo_response.to_dict()

        self.assertEqual(result["owner"], "QuantConnect")
        self.assertIsInstance(result, dict)


class TestGovernanceCompliance(unittest.TestCase):
    """Tests for governance compliance in adapters."""

    def test_openalex_metadata_preservation(self):
        """Test that OpenAlex responses preserve governance metadata."""
        client = OpenAlexClient()

        work = {
            "id": "W123",
            "title": "Test",
            "abstract": "Abstract",
            "publication_date": "2024-01-01",
        }

        normalized = client.normalize_work(work)

        # Check governance metadata exists and is preserved
        self.assertIsNotNone(normalized.governance_metadata)
        self.assertEqual(
            normalized.governance_metadata["governance_context"],
            "Approved structured source"
        )
        self.assertIn("retrieved_at", normalized.governance_metadata)
        self.assertIn("api_endpoint", normalized.governance_metadata)

    def test_github_metadata_preservation(self):
        """Test that GitHub responses preserve governance metadata."""
        client = GitHubClient(token="test")

        repo = {
            "id": 123,
            "name": "Lean",
            "full_name": "QuantConnect/Lean",
            "owner": {"login": "QuantConnect"},
            "description": "Test",
            "html_url": "https://github.com/QuantConnect/Lean",
            "url": "https://api.github.com/repos/QuantConnect/Lean",
        }

        normalized = client.normalize_repository(repo)

        # Check governance metadata exists and is preserved
        self.assertIsNotNone(normalized.governance_metadata)
        self.assertEqual(
            normalized.governance_metadata["governance_context"],
            "Approved structured source"
        )
        self.assertTrue(normalized.governance_metadata["repo_approved"])
        self.assertIn("retrieved_at", normalized.governance_metadata)


class TestTaiwanMarketClient(unittest.TestCase):
    """Tests for Taiwan market structured-source adapters."""

    def setUp(self):
        self.client = TaiwanMarketClient()

    def test_twse_listing_normalization(self):
        normalized = self.client.normalize_twse_listing(
            {
                "Code": "2330",
                "Name": "台積電",
                "ISIN": "TW0002330008",
                "Industry": "半導體業",
                "ListingDate": "1994-09-05",
            }
        )
        self.assertEqual(normalized.symbol, "2330")
        self.assertEqual(normalized.venue, "TWSE")
        self.assertEqual(normalized.governance_metadata["source_class"], "official_reference")

    def test_tpex_listing_normalization(self):
        normalized = self.client.normalize_tpex_listing(
            {
                "SecuritiesCompanyCode": "6488",
                "CompanyName": "環球晶",
                "ISIN": "TW0006488003",
                "Industry": "電子工業",
                "ListingDate": "2011-10-13",
            }
        )
        self.assertEqual(normalized.symbol, "6488")
        self.assertEqual(normalized.venue, "TPEx")

    def test_mops_disclosure_normalization(self):
        normalized = self.client.normalize_mops_disclosure(
            {
                "company_id": "2330",
                "company_name": "台積電",
                "filing_code": "monthly_revenue",
                "announce_date": "2026-04-10",
                "fiscal_period": "2026-03",
            }
        )
        self.assertEqual(normalized.symbol, "2330")
        self.assertEqual(normalized.filing_code, "monthly_revenue")
        self.assertEqual(normalized.governance_metadata["source_key"], "mops")

    def test_mops_route_inventory_covers_recommended_official_sources(self):
        inventory = self.client.mops_route_inventory(
            index_js_text='__vite__mapDeps.viteFileDeps = ["assets/t05st02.js","assets/t05st03.js","assets/custom_new.js"]'
        )
        by_id = {route.route_id: route for route in inventory}

        for route_id in ("t05st02", "t05st03", "t163sb01", "t05st10_ifrs", "stapap1", "query6_1"):
            self.assertIn(route_id, by_id)
        self.assertEqual(by_id["t05st03"].source_type, "market")
        self.assertEqual(by_id["t163sb01"].category, "financials")
        self.assertEqual(by_id["custom_new"].category, "discovered_from_spa_bundle")
        self.assertFalse(by_id["custom_new"].allow_fetch)

    def test_mops_payload_rows_and_disclosures_normalize_homepage_table(self):
        payload = {
            "code": 200,
            "message": "查詢成功",
            "result": {
                "titles": [{"main": "發言日期"}, {"main": "發言時間"}, {"main": "公司代號"}, {"main": "公司名稱"}, {"main": "主旨"}],
                "data": [["115/06/08", "14:07:19", "2428", "興勤", "本公司115年05月份自結合併營業收入公告"]],
            },
            "datetime": "115/06/08 23:11:35",
        }

        rows = self.client.mops_result_rows(payload)
        self.assertEqual(rows[0]["公司代號"], "2428")
        disclosures = self.client.normalize_mops_disclosures_from_payload("t05st02", payload)
        self.assertEqual(disclosures[0].symbol, "2428")
        self.assertEqual(disclosures[0].filing_code, "t05st02")
        self.assertEqual(disclosures[0].source_metadata["route_title_zh"], "當日重大訊息")

    def test_tej_trial_table_inventory_maps_research_categories(self):
        payload = {
            "tables": [
                {"tableName": "AIND", "cName": "上市(櫃)基本資料", "groupName": "公司營運面資料", "dataRange": "近五年"},
                {"tableName": "TATINST1", "cName": "三大法人買賣超", "groupName": "公司交易面資料", "dataRange": "近五年"},
                {"tableName": "TAIM1A", "cName": "IFRS以合併為主簡表(累計)-全產業", "groupName": "公司財務資料"},
            ]
        }

        specs = self.client.tej_trial_table_inventory_from_payload(payload)
        by_code = {spec.table_code: spec for spec in specs}
        self.assertEqual(by_code["AIND"].dataset_code, "TRAIL/AIND")
        self.assertEqual(by_code["TATINST1"].source_category, "institutional_flow")
        self.assertEqual(by_code["TAIM1A"].source_category, "financials")

    def test_tej_dataset_normalization_keeps_vendor_boundary(self):
        normalized = self.client.normalize_tej_dataset(
            {
                "coid": "2330",
                "mdate": "2026-04-24",
                "pe_ratio": 18.2,
                "foreign_holding_pct": 72.4,
            },
            dataset_code="TWN/APRCD1",
        )
        self.assertEqual(normalized.dataset_code, "TWN/APRCD1")
        self.assertEqual(normalized.values["pe_ratio"], 18.2)
        self.assertIn("does not replace official disclosure truth", normalized.governance_metadata["governance_context"])


class TestCoinGeckoClient(unittest.TestCase):
    """Tests for CoinGecko reference adapters."""

    def setUp(self):
        self.client = CoinGeckoClient()

    def test_asset_normalization_keeps_reference_boundary(self):
        normalized = self.client.normalize_asset(
            {
                "id": "bitcoin",
                "symbol": "btc",
                "name": "Bitcoin",
                "market_cap_rank": 1,
                "categories": ["Smart Contract Platform", "Layer 1"],
            }
        )
        self.assertEqual(normalized.coingecko_id, "bitcoin")
        self.assertEqual(normalized.symbol, "BTC")
        self.assertEqual(normalized.market_cap_rank, 1)
        self.assertIn("does not replace Kraken execution truth", normalized.governance_metadata["governance_context"])


if __name__ == "__main__":
    unittest.main()
