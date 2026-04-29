FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/training-session

COPY services/training-session/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8099
EXPOSE 8099

CMD ["sh", "-c", "uvicorn main:app --app-dir services/training-session --host 0.0.0.0 --port ${PORT:-8099}"]
