"""Research adapters for OpenAlex and GitHub structured source integration."""

from .github_client import GitHubClient, GitHubFileResponse, GitHubRepositoryResponse
from .openalex_client import OpenAlexClient, OpenAlexWorkResponse

__all__ = [
    "OpenAlexClient",
    "OpenAlexWorkResponse",
    "GitHubClient",
    "GitHubRepositoryResponse",
    "GitHubFileResponse",
]
