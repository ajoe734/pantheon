"""Research adapters for governed structured-source integration."""

from .coingecko_client import CoinGeckoAssetRecord, CoinGeckoClient
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
    "CoinGeckoClient",
    "CoinGeckoAssetRecord",
    "GitHubClient",
    "GitHubRepositoryResponse",
    "GitHubFileResponse",
    "TaiwanMarketClient",
    "TaiwanListingRecord",
    "TaiwanDisclosureRecord",
    "TaiwanResearchDatasetRecord",
]
