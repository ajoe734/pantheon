FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

COPY services/paper_fleet_reconciler/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

# Bake the source revision so deployed images are traceable to a commit and
# drift is precisely detectable (see scripts/audit_deploy_drift.sh --precise).
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8011

CMD ["python", "services/paper_fleet_reconciler/paper_fleet_reconciler.py"]
