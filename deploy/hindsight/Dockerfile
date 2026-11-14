# syntax=docker/dockerfile:1
# Official slim omits local-onnx (full also omits it). Extend the smaller image.
FROM ghcr.io/vectorize-io/hindsight-api:1.2.1-slim@sha256:084af5a6522d67f5905f7992cdb3d910d778496e5dd2566caec7cf82ed2e7d8c
USER root
# Versions from the upstream v1.2.1 uv.lock; no torch/local-ml dependency.
RUN uv pip install --python /app/api/.venv/bin/python --no-cache \
    onnxruntime==1.31.0 transformers==5.12.1 tokenizers==0.22.2 \
    huggingface-hub==1.6.0 numpy==2.3.4
COPY services/search/model-manifest.json /opt/hindsight/embedding-manifest.json
COPY deploy/hindsight/model-manifest.json /opt/hindsight/model-manifest.json
# Keep packaging and startup verification together: runtime has no download path.
COPY <<'PY' /opt/hindsight/models.py
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path('/opt/hindsight')


def models():
    yield 'embeddings', json.loads((ROOT / 'embedding-manifest.json').read_text())
    yield 'reranker', json.loads((ROOT / 'model-manifest.json').read_text())['reranker']


def verify(package=False):
    for kind, manifest in models():
        for name, row in manifest['files'].items():
            path = ROOT / kind / name
            if package:
                import urllib.request
                url = manifest['source_url_template'].format(
                    repository=manifest['source_repository'], revision=manifest['revision'],
                    source_path=row['source_path'])
                path.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(url, timeout=600) as src, path.open('wb') as dst:
                    while chunk := src.read(1024 * 1024):
                        dst.write(chunk)
            with path.open('rb') as src:
                digest = hashlib.file_digest(src, 'sha256').hexdigest()
            if digest != row['sha256']:
                raise RuntimeError(f'model_checksum_mismatch:{kind}/{name}')


if __name__ == '__main__':
    verify(package=sys.argv[1:] == ['package'])
    if sys.argv[1:] == ['start']:
        os.execv('/app/start-all.sh', ['/app/start-all.sh'])
PY
RUN python /opt/hindsight/models.py package \
    && chmod -R a-w /opt/hindsight
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA \
    org.opencontainers.image.base.name="ghcr.io/vectorize-io/hindsight-api:1.2.1-slim" \
    org.opencontainers.image.base.digest="sha256:084af5a6522d67f5905f7992cdb3d910d778496e5dd2566caec7cf82ed2e7d8c"
ENV HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    HINDSIGHT_API_EMBEDDINGS_PROVIDER=onnx \
    HINDSIGHT_API_EMBEDDINGS_ONNX_MODEL_PATH=/opt/hindsight/embeddings/model.onnx \
    HINDSIGHT_API_EMBEDDINGS_ONNX_TOKENIZER_NAME_OR_PATH=/opt/hindsight/embeddings \
    HINDSIGHT_API_EMBEDDINGS_ONNX_DIMENSIONS=1024 \
    HINDSIGHT_API_EMBEDDINGS_ONNX_QUERY_PREFIX="query: " \
    HINDSIGHT_API_EMBEDDINGS_ONNX_PASSAGE_PREFIX="passage: " \
    HINDSIGHT_API_RERANKER_PROVIDER=onnx \
    HINDSIGHT_API_RERANKER_ONNX_MODEL_PATH=/opt/hindsight/reranker/model.onnx \
    HINDSIGHT_API_RERANKER_ONNX_TOKENIZER_NAME_OR_PATH=/opt/hindsight/reranker
USER hindsight
CMD ["python", "/opt/hindsight/models.py", "start"]
