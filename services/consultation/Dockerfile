FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/consultation/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8096
EXPOSE 8096

CMD ["sh", "-c", "uvicorn services.consultation.main:app --host 0.0.0.0 --port ${PORT:-8096}"]
