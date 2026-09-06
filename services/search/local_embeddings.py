"""Local ONNX-based multilingual vector embedding engine using FastEmbed.

Ensures:
- Local offline inference only (zero external network/cloud calls).
- Manifest digest verification.
- Consistent query ("query: ") and passage ("passage: ") task prefixes.
- Fail-closed behavior on model corruption, dimension mismatch, or missing cache.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

from services.search.filters import SearchCapabilityUnavailableError

MANIFEST_PATH = Path(__file__).parent / "model-manifest.json"


class LocalEmbeddingEngine:
    """Manages local ONNX embedding inference with strict offline guarantees."""

    def __init__(
        self,
        manifest_path: Path | str | None = None,
        cache_dir: Path | str | None = None,
        local_files_only: bool = True,
    ) -> None:
        self.manifest_path = Path(manifest_path or MANIFEST_PATH)
        self.manifest = self._load_manifest()
        self.model_name = str(self.manifest.get("model_name", "intfloat/multilingual-e5-large"))
        self.dimension = int(self.manifest.get("dimension", 1024))
        self.query_prefix = str(self.manifest.get("preprocessing", {}).get("query_prefix", "query: "))
        self.passage_prefix = str(self.manifest.get("preprocessing", {}).get("passage_prefix", "passage: "))

        explicit_cache = cache_dir or os.getenv("SEARCH_EMBEDDING_CACHE_DIR", "/tmp/fastembed_cache")
        self.cache_dir = Path(explicit_cache)
        self.local_files_only = local_files_only
        self._embedder: Any = None
        self._query_cache: dict[str, list[float]] = {}

    def _load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            raise SearchCapabilityUnavailableError(
                f"Embedding model manifest not found at {self.manifest_path}"
            )
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SearchCapabilityUnavailableError(
                f"Corrupted embedding model manifest at {self.manifest_path}: {exc}"
            ) from exc

    def _find_snapshot_dir(self) -> Path | None:
        if not self.cache_dir.exists():
            return None
        candidate_prefixes = [
            f"models--{self.model_name.replace('/', '--')}-onnx",
            f"models--qdrant--{self.model_name.split('/')[-1]}-onnx",
            f"models--qdrant--multilingual-e5-large-onnx",
        ]
        for prefix in candidate_prefixes:
            target = self.cache_dir / prefix / "snapshots"
            if target.exists():
                snapshots = [s for s in target.iterdir() if s.is_dir()]
                if snapshots:
                    return sorted(snapshots)[-1]
        for child in self.cache_dir.iterdir():
            if child.is_dir() and "multilingual-e5-large" in child.name:
                snap_dir = child / "snapshots"
                if snap_dir.exists():
                    snaps = [s for s in snap_dir.iterdir() if s.is_dir()]
                    if snaps:
                        return sorted(snaps)[-1]
        return None

    def verify_integrity(self) -> bool:
        """Verify cached ONNX artifacts against manifest digests."""
        files_spec = self.manifest.get("files", {})
        if not files_spec:
            return True

        target_dir = self._find_snapshot_dir()
        if target_dir is None:
            return False

        for filename, spec in files_spec.items():
            expected_sha = spec.get("sha256")
            fpath = target_dir / filename
            if not fpath.exists():
                return False
            if expected_sha:
                real_file = fpath.resolve()
                computed = hashlib.sha256(real_file.read_bytes()).hexdigest()
                if computed.lower() != expected_sha.lower():
                    return False
        return True

    def _ensure_loaded(self) -> Any:
        if self._embedder is not None:
            return self._embedder

        try:
            from fastembed import TextEmbedding
        except ImportError as exc:
            raise SearchCapabilityUnavailableError(
                "fastembed package is not installed; semantic vector retrieval unavailable"
            ) from exc

        try:
            self._embedder = TextEmbedding(
                model_name=self.model_name,
                cache_dir=str(self.cache_dir),
                local_files_only=self.local_files_only,
            )
            return self._embedder
        except Exception as exc:
            raise SearchCapabilityUnavailableError(
                f"Failed to initialize local embedding model '{self.model_name}' from {self.cache_dir}: {exc}"
            ) from exc

    def is_ready(self) -> bool:
        try:
            self._ensure_loaded()
            return True
        except Exception:
            return False

    def embed_query(self, query: str) -> list[float]:
        """Generate normalized embedding for a retrieval query."""
        clean = str(query or "").strip()
        if not clean:
            return [0.0] * self.dimension
        if clean in self._query_cache:
            return list(self._query_cache[clean])

        embedder = self._ensure_loaded()
        text = f"{self.query_prefix}{clean}"
        try:
            gen = embedder.embed([text])
            vec = list(next(iter(gen)))
            if len(vec) != self.dimension:
                raise SearchCapabilityUnavailableError(
                    f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
                )
            result = [float(x) for x in vec]
            if len(self._query_cache) < 4096:
                self._query_cache[clean] = list(result)
            return result
        except Exception as exc:
            if isinstance(exc, SearchCapabilityUnavailableError):
                raise
            raise SearchCapabilityUnavailableError(f"Embedding generation failed: {exc}") from exc

    def embed_documents(self, texts: Sequence[str], batch_size: int = 64) -> list[list[float]]:
        """Generate normalized embeddings for document passages."""
        if not texts:
            return []
        embedder = self._ensure_loaded()
        prefixed = [f"{self.passage_prefix}{str(t or '').strip()}" for t in texts]
        try:
            gen = embedder.embed(prefixed, batch_size=batch_size)
            result = []
            for item in gen:
                vec = [float(x) for x in item]
                if len(vec) != self.dimension:
                    raise SearchCapabilityUnavailableError(
                        f"Dimension mismatch: expected {self.dimension}, got {len(vec)}"
                    )
                result.append(vec)
            return result
        except Exception as exc:
            if isinstance(exc, SearchCapabilityUnavailableError):
                raise
            raise SearchCapabilityUnavailableError(f"Batch embedding generation failed: {exc}") from exc

    def embed_text(self, text: str) -> list[float]:
        """VectorEmbeddingBackend interface compatibility."""
        return self.embed_query(text)

    def embed_batch(self, texts: Sequence[str]) -> list[list[float]]:
        """VectorEmbeddingBackend interface compatibility."""
        return self.embed_documents(texts)
