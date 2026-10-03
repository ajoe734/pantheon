FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/workspace:/workspace/services/policy-learning

COPY dependencies/locks/services-policy-learning.txt ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r ./requirements.lock

COPY . /workspace

ENV PORT=8100
EXPOSE 8100

CMD ["sh", "-c", "uvicorn main:app --app-dir services/policy-learning --host 0.0.0.0 --port ${PORT:-8100}"]
