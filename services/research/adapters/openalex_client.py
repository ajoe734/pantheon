"""OpenAlex API client adapter for structured research paper discovery and ingestion.

This module provides a governed interface to the OpenAlex API with:
- Rate limiting and error handling
- Metadata preservation for governance
- Response normalization to internal schemas
- Source validation and tracking
"""

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class OpenAlexMetadata:
    """Governance metadata for OpenAlex API responses."""
    api_endpoint: str
    retrieved_at: str
    governance_context: str = "Approved structured source"
    api_version: str = "2"
    data_quality_score: str = "high"


@dataclass
class OpenAlexWorkResponse:
    """Normalized OpenAlex work (paper) response."""
    work_id: str
    title: str
    abstract: Optional[str]
    authors: list[dict[str, Any]]
    publication_date: str
    doi: Optional[str]
    source_metadata: dict[str, Any]
    governance_metadata: dict[str, Any]
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class OpenAlexClient:
    """Client for OpenAlex API with governance tracking."""
    
    BASE_URL = "https://api.openalex.org"
    DEFAULT_RATE_LIMIT_DELAY = 0.5  # seconds
    
    def __init__(self, email: Optional[str] = None, rate_limit_delay: float = DEFAULT_RATE_LIMIT_DELAY):
        """Initialize OpenAlex client.
        
        Args:
            email: Optional email for API polite requests
            rate_limit_delay: Delay between requests in seconds
        """
        self.email = email or "research-bot@pantheon-system.local"
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0.0
    
    def _make_request(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Make an API request with rate limiting and error handling.
        
        Args:
            endpoint: API endpoint path (e.g., 'works')
            params: Query parameters
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            HTTPError: If API request fails
            ValueError: If response cannot be parsed as JSON
        """
        # Rate limiting
        time_since_last = time.time() - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
        
        # Build request
        params["mailto"] = self.email
        url = f"{self.BASE_URL}/{endpoint}?{urlencode(params)}"
        
        request = Request(url, headers={
            "User-Agent": "PantheonGrokResearch/1.0 (research-bot@pantheon-system.local)"
        })
        
        try:
            self.last_request_time = time.time()
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            return data
        except HTTPError as e:
            raise HTTPError(
                url, e.code, f"OpenAlex API error: {e.reason}",
                e.hdrs, e.fp
            ) from e
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response from OpenAlex API: {e}") from e
    
    def search_works(
        self,
        title: Optional[str] = None,
        author: Optional[str] = None,
        doi: Optional[str] = None,
        per_page: int = 10,
        page: int = 1
    ) -> dict[str, Any]:
        """Search for academic works.
        
        Args:
            title: Paper title to search
            author: Author name to search
            doi: DOI to search
            per_page: Results per page (1-200)
            page: Page number for pagination
            
        Returns:
            API response with results and pagination info
        """
        params = {
            "per-page": min(max(per_page, 1), 200),
            "page": max(page, 1)
        }
        
        # Build search query
        query_parts = []
        if title:
            query_parts.append(f'title.search:"{title}"')
        if author:
            query_parts.append(f'author.name:"{author}"')
        if doi:
            query_parts.append(f'doi:"{doi}"')
        
        if query_parts:
            params["filter"] = ",".join(query_parts)
        
        return self._make_request("works", params)
    
    def get_work(self, work_id: str) -> dict[str, Any]:
        """Get a specific work by ID.
        
        Args:
            work_id: OpenAlex work ID (e.g., 'W123456' or full URL)
            
        Returns:
            Full work object
        """
        # Normalize work_id (remove URL prefix if present)
        if work_id.startswith("http"):
            work_id = work_id.split("/")[-1]
        if not work_id.startswith("W"):
            work_id = f"W{work_id}"
        
        return self._make_request(f"works/{work_id}", {})
    
    def normalize_work(self, work: dict[str, Any]) -> OpenAlexWorkResponse:
        """Normalize OpenAlex work response to governed format.
        
        Args:
            work: OpenAlex work object
            
        Returns:
            Normalized work response with governance metadata
            
        Raises:
            ValueError: If required fields are missing
        """
        # Validate required fields
        required_fields = ["id", "title", "publication_date"]
        missing = [f for f in required_fields if not work.get(f)]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        
        # Extract authors
        authors = []
        for author_info in work.get("authorships", []):
            if author_info.get("author"):
                authors.append({
                    "name": author_info["author"].get("display_name"),
                    "orcid": author_info["author"].get("orcid"),
                })
        
        # Extract DOI
        doi = None
        if work.get("doi"):
            doi = work["doi"].replace("https://doi.org/", "")
        
        # Build metadata
        metadata = OpenAlexMetadata(
            api_endpoint=work.get("id", ""),
            retrieved_at=datetime.now(timezone.utc).isoformat()
        )
        
        return OpenAlexWorkResponse(
            work_id=work["id"],
            title=work["title"],
            abstract=work.get("abstract"),
            authors=authors,
            publication_date=work.get("publication_date", ""),
            doi=doi,
            source_metadata={
                "venue": work.get("primary_location", {}).get("source", {}).get("display_name"),
                "open_access": work.get("open_access", {}).get("is_oa"),
                "citation_count": work.get("cited_by_count"),
            },
            governance_metadata=asdict(metadata)
        )
    
    def search_and_normalize(
        self,
        title: Optional[str] = None,
        author: Optional[str] = None,
        doi: Optional[str] = None,
        limit: int = 5
    ) -> list[OpenAlexWorkResponse]:
        """Search for works and normalize results.
        
        Args:
            title: Paper title
            author: Author name
            doi: DOI
            limit: Maximum number of results to return
            
        Returns:
            List of normalized work responses
        """
        results = self.search_works(
            title=title,
            author=author,
            doi=doi,
            per_page=min(limit, 200)
        )
        
        normalized = []
        for work in results.get("results", []):
            try:
                normalized_work = self.normalize_work(work)
                normalized.append(normalized_work)
                if len(normalized) >= limit:
                    break
            except ValueError as e:
                # Skip works with missing required fields
                continue
        
        return normalized
