FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY . .

CMD ["python", "worker.py"]
