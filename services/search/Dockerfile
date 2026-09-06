FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/search/requirements.txt /tmp/requirements.txt
COPY services/search/requirements.lock /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

ENV PORT=8098
EXPOSE 8098

CMD ["sh", "-c", "uvicorn services.search.main:app --host 0.0.0.0 --port ${PORT:-8098}"]
