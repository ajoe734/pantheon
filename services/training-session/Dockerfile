FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/training-session

COPY dependencies/locks/services-training-session.txt ./requirements.lock
RUN pip install --no-cache-dir --require-hashes -r ./requirements.lock

COPY . /workspace

ENV PORT=8099
EXPOSE 8099

CMD ["sh", "-c", "uvicorn main:app --app-dir services/training-session --host 0.0.0.0 --port ${PORT:-8099}"]
