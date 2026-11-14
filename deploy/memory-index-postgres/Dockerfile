FROM groonga/pgroonga:4.0.8-debian-17@sha256:15ab42f1b92749950da640b6d88f5563498a2ac06b2d0aaee1f9185afe34e376
# PGDG rotates old versions out of its live index. The official signed archive
# retains the exact upstream recipe version (including amd64 and arm64).
RUN echo 'deb [signed-by=/usr/local/share/keyrings/postgres.gpg.asc] https://apt-archive.postgresql.org/pub/repos/apt trixie-pgdg-archive main' > /etc/apt/sources.list.d/pgdg-archive.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    postgresql-17-pgvector=0.8.6-1.pgdg13+1 \
    && apt-get clean && rm -rf /var/lib/apt/lists/*
COPY deploy/memory-index-postgres/init.sql /docker-entrypoint-initdb.d/01-memory-index.sql
# Explicitly invoke the pinned upstream Bash entrypoint for Stage0 inspection.
# Keep its native init, argument forwarding, privilege drop and final exec.
ENTRYPOINT ["bash", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["postgres"]
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA \
    org.opencontainers.image.source="https://github.com/ajoe734/pantheon" \
    org.opencontainers.image.base.name="groonga/pgroonga:4.0.8-debian-17" \
    org.opencontainers.image.base.digest="sha256:15ab42f1b92749950da640b6d88f5563498a2ac06b2d0aaee1f9185afe34e376"
