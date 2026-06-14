FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/control-plane/bff/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

# Bake the source revision so deployed images are traceable to a commit and
# drift is precisely detectable (see scripts/audit_deploy_drift.sh --precise).
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8001

CMD ["uvicorn", "main:app", "--app-dir", "/workspace/services/control-plane/bff", "--host", "0.0.0.0", "--port", "8001"]
