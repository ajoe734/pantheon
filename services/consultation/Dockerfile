FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY services/consultation/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

ENV PORT=8080
EXPOSE 8080

CMD ["uvicorn", "services.consultation.main:app", "--host", "0.0.0.0", "--port", "8080"]
