FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY dependencies/locks/services-feedback.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

EXPOSE 8085

CMD ["uvicorn", "services.feedback.main:app", "--host", "0.0.0.0", "--port", "8085"]
