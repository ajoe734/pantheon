FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY dependencies/locks/services-telemetry.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

# Bake the source revision so deployed images are traceable to a commit and
# drift is precisely detectable (see scripts/audit_deploy_drift.sh --precise).
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8083

CMD ["python", "-m", "services.telemetry.main"]
