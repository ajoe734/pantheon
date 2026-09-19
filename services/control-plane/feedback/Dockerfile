FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/control-plane/feedback

COPY dependencies/locks/services-control-plane-feedback.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

EXPOSE 8085

CMD ["uvicorn", "main:app", "--app-dir", "/workspace/services/control-plane/feedback", "--host", "0.0.0.0", "--port", "8085"]
