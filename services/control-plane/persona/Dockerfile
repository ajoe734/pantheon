FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/control-plane/persona/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

EXPOSE 8002

CMD ["uvicorn", "main:app", "--app-dir", "/workspace/services/control-plane/persona", "--host", "0.0.0.0", "--port", "8002"]
