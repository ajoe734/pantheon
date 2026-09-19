FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY dependencies/locks/services-control-plane-bff.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

# Bake the source revision so deployed images are traceable to a commit and
# drift is precisely detectable (see scripts/audit_deploy_drift.sh --precise).
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA
ENV GIT_SHA=$GIT_SHA

# Bake the build timestamp so /bff/version can report exact build identity
# instead of "unknown" (see LOOP-PROD-AUTH-001).
ARG BUILD_TIME=unknown
LABEL org.opencontainers.image.created=$BUILD_TIME
ENV BUILD_TIME=$BUILD_TIME

EXPOSE 8001

CMD ["uvicorn", "services.control_plane.bff.main:app", "--host", "0.0.0.0", "--port", "8001"]
