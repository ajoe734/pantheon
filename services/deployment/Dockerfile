FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/deployment/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

EXPOSE 8095

CMD ["uvicorn", "services.deployment.service:app", "--host", "0.0.0.0", "--port", "8095"]
