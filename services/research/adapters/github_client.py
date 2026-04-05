"""GitHub REST API client adapter for structured repository discovery and code research.

This module provides a governed interface to the GitHub API with:
- Rate limiting and error handling
- Repository content retrieval for approved repos only
- Metadata preservation for governance
- Source validation and tracking
"""

import json
import os
from base64 import b64decode
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass
class GitHubMetadata:
    """Governance metadata for GitHub API responses."""
    api_endpoint: str
    retrieved_at: str
    governance_context: str = "Approved structured source"
    api_version: str = "2022-11-28"
    repo_approved: bool = True


@dataclass
class GitHubRepositoryResponse:
    """Normalized GitHub repository response."""
    repo_id: str
    owner: str
    name: str
    full_name: str
    description: Optional[str]
    url: str
    stars: int
    language: Optional[str]
    topics: list[str]
    source_metadata: dict[str, Any]
    governance_metadata: dict[str, Any]
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


@dataclass
class GitHubFileResponse:
    """Normalized GitHub file response."""
    file_path: str
    repo_full_name: str
    content: str
    size: int
    sha: str
    url: str
    governance_metadata: dict[str, Any]
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class GitHubClient:
    """Client for GitHub REST API with governance tracking."""
    
    BASE_URL = "https://api.github.com"
    DEFAULT_RATE_LIMIT_DELAY = 0.1
    
    # Approved repositories for research access
    APPROVED_REPOS = {
        "QuantConnect/Lean": {
            "approved_paths": ["Research/", "Algorithm.Python/", "documentation/"],
            "purpose": "Official LEAN research and examples"
        },
    }
    
    def __init__(self, token: Optional[str] = None, rate_limit_delay: float = DEFAULT_RATE_LIMIT_DELAY):
        """Initialize GitHub client.
        
        Args:
            token: GitHub API token (defaults to GH_TOKEN env var)
            rate_limit_delay: Delay between requests in seconds
        """
        self.token = token or os.environ.get("GH_TOKEN", "")
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0.0
    
    def _make_request(self, endpoint: str, method: str = "GET") -> dict[str, Any]:
        """Make an API request with rate limiting and error handling.
        
        Args:
            endpoint: API endpoint path (e.g., 'repos/owner/repo')
            method: HTTP method (GET, POST, etc.)
            
        Returns:
            Response JSON as dictionary
            
        Raises:
            HTTPError: If API request fails
            ValueError: If response cannot be parsed as JSON
        """
        import time
        
        # Rate limiting
        time_since_last = time.time() - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
        
        url = f"{self.BASE_URL}/{endpoint}"
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        
        if self.token:
            headers["Authorization"] = f"token {self.token}"
        
        request = Request(url, headers=headers, method=method)
        
        try:
            self.last_request_time = time.time()
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            return data
        except HTTPError as e:
            raise HTTPError(
                url, e.code, f"GitHub API error: {e.reason}",
                e.hdrs, e.fp
            ) from e
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON response from GitHub API: {e}") from e
    
    def is_repo_approved(self, owner: str, repo: str) -> bool:
        """Check if a repository is in the approved list.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            True if repository is approved for access
        """
        full_name = f"{owner}/{repo}"
        return full_name in self.APPROVED_REPOS
    
    def get_repository(self, owner: str, repo: str) -> dict[str, Any]:
        """Get repository metadata.
        
        Args:
            owner: Repository owner
            repo: Repository name
            
        Returns:
            Full repository object
            
        Raises:
            ValueError: If repository not approved
        """
        if not self.is_repo_approved(owner, repo):
            raise ValueError(
                f"Repository {owner}/{repo} not in approved list. "
                f"Approved repos: {list(self.APPROVED_REPOS.keys())}"
            )
        
        return self._make_request(f"repos/{owner}/{repo}")
    
    def get_file_content(self, owner: str, repo: str, path: str) -> dict[str, Any]:
        """Get file content from repository.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path within repository
            
        Returns:
            File object with content
            
        Raises:
            ValueError: If repository not approved or path not in approved list
        """
        if not self.is_repo_approved(owner, repo):
            raise ValueError(
                f"Repository {owner}/{repo} not in approved list"
            )
        
        # Check path approval
        approved_paths = self.APPROVED_REPOS[f"{owner}/{repo}"]["approved_paths"]
        if not any(path.startswith(ap) for ap in approved_paths):
            raise ValueError(
                f"Path {path} not in approved paths for {owner}/{repo}: {approved_paths}"
            )
        
        return self._make_request(f"repos/{owner}/{repo}/contents/{path}")
    
    def normalize_repository(self, repo: dict[str, Any]) -> GitHubRepositoryResponse:
        """Normalize GitHub repository response to governed format.
        
        Args:
            repo: GitHub repository object
            
        Returns:
            Normalized repository response with governance metadata
            
        Raises:
            ValueError: If required fields are missing
        """
        # Validate required fields
        required_fields = ["id", "owner", "name", "full_name", "html_url"]
        missing = [f for f in required_fields if not repo.get(f)]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        
        # Build metadata
        metadata = GitHubMetadata(
            api_endpoint=repo.get("url", ""),
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            repo_approved=self.is_repo_approved(
                repo["owner"]["login"], repo["name"]
            )
        )
        
        return GitHubRepositoryResponse(
            repo_id=str(repo["id"]),
            owner=repo["owner"]["login"],
            name=repo["name"],
            full_name=repo["full_name"],
            description=repo.get("description"),
            url=repo["html_url"],
            stars=repo.get("stargazers_count", 0),
            language=repo.get("language"),
            topics=repo.get("topics", []),
            source_metadata={
                "created_at": repo.get("created_at"),
                "updated_at": repo.get("updated_at"),
                "forks_count": repo.get("forks_count"),
                "open_issues_count": repo.get("open_issues_count"),
                "license": repo.get("license", {}).get("name"),
            },
            governance_metadata=asdict(metadata)
        )
    
    def normalize_file(
        self,
        file_obj: dict[str, Any],
        repo_full_name: str
    ) -> GitHubFileResponse:
        """Normalize GitHub file response to governed format.
        
        Args:
            file_obj: GitHub file object
            repo_full_name: Full repository name (owner/repo)
            
        Returns:
            Normalized file response with governance metadata
            
        Raises:
            ValueError: If required fields are missing
        """
        # Validate required fields
        required_fields = ["path", "sha", "size", "url", "content"]
        missing = [f for f in required_fields if f not in file_obj]
        if missing:
            raise ValueError(f"Missing required fields: {missing}")
        
        # Decode content if it's base64 encoded
        content = file_obj["content"]
        if file_obj.get("encoding") == "base64":
            try:
                content = b64decode(content).decode("utf-8")
            except Exception as e:
                raise ValueError(f"Failed to decode base64 content: {e}") from e
        
        # Build metadata
        metadata = GitHubMetadata(
            api_endpoint=file_obj["url"],
            retrieved_at=datetime.now(timezone.utc).isoformat()
        )
        
        return GitHubFileResponse(
            file_path=file_obj["path"],
            repo_full_name=repo_full_name,
            content=content,
            size=file_obj["size"],
            sha=file_obj["sha"],
            url=file_obj["html_url"],
            governance_metadata=asdict(metadata)
        )
    
    def search_approved_repos(self, query: str) -> list[GitHubRepositoryResponse]:
        """Search approved repositories by name or description.
        
        Note: This performs local filtering only, not GitHub search.
        
        Args:
            query: Search query
            
        Returns:
            List of matching approved repositories
        """
        matching = []
        for repo_name in self.APPROVED_REPOS:
            if query.lower() in repo_name.lower():
                owner, repo = repo_name.split("/")
                try:
                    repo_obj = self.get_repository(owner, repo)
                    normalized = self.normalize_repository(repo_obj)
                    matching.append(normalized)
                except (HTTPError, ValueError):
                    continue
        
        return matching
