FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/policy-learning

COPY services/policy-learning/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8100
EXPOSE 8100

CMD ["sh", "-c", "uvicorn main:app --app-dir services/policy-learning --host 0.0.0.0 --port ${PORT:-8100}"]
