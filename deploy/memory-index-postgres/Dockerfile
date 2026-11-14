FROM groonga/pgroonga:4.0.8-debian-17@sha256:15ab42f1b92749950da640b6d88f5563498a2ac06b2d0aaee1f9185afe34e376
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-17-pgvector=0.8.6-1.pgdg13+1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY deploy/memory-index-postgres/init.sql /docker-entrypoint-initdb.d/01-memory-index.sql
