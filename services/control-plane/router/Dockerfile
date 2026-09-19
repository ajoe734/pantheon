FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY dependencies/locks/services-control-plane-router.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
