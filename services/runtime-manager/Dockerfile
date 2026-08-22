FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace

COPY services/runtime-manager/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA

EXPOSE 8081

CMD ["python", "/workspace/services/runtime-manager/main.py"]
