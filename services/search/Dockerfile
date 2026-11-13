FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/search/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

RUN python -c "import hashlib, pathlib, urllib.request; p=pathlib.Path('/opt/search/dict.txt.big'); p.parent.mkdir(parents=True, exist_ok=True); data=urllib.request.urlopen('https://raw.githubusercontent.com/fxsjy/jieba/67fa2e36e72f69d9134b8a1037b83fbb070b9775/extra_dict/dict.txt.big', timeout=60).read(); assert hashlib.sha256(data).hexdigest() == 'b16011275c42955ccd81fc1adecc93a59dbb7926af69d93fc95d4943d40f6aad'; p.write_bytes(data)"

RUN python -m services.search.local_embeddings package /opt/search/models

ENV SEARCH_EMBEDDING_CACHE_DIR=/opt/search/models \
    SEARCH_JIEBA_DICTIONARY=/opt/search/dict.txt.big \
    HF_HUB_OFFLINE=1

ENV PORT=8098
EXPOSE 8098

CMD ["sh", "-c", "uvicorn services.search.main:app --host 0.0.0.0 --port ${PORT:-8098}"]
