"""Research adapters for OpenAlex and GitHub structured source integration."""

from .github_client import GitHubClient, GitHubFileResponse, GitHubRepositoryResponse
from .openalex_client import OpenAlexClient, OpenAlexWorkResponse
from .taiwan_market_client import (
    TaiwanDisclosureRecord,
    TaiwanListingRecord,
    TaiwanMarketClient,
    TaiwanResearchDatasetRecord,
)

__all__ = [
    "OpenAlexClient",
    "OpenAlexWorkResponse",
    "GitHubClient",
    "GitHubRepositoryResponse",
    "GitHubFileResponse",
    "TaiwanMarketClient",
    "TaiwanListingRecord",
    "TaiwanDisclosureRecord",
    "TaiwanResearchDatasetRecord",
]
