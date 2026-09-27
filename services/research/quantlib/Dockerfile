FROM python:3.11-slim

WORKDIR /app

COPY dependencies/locks/services-research-quantlib.txt ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

COPY services/research/quantlib/ .

ENV PYTHONUNBUFFERED=1

CMD ["python", "smoke_test.py"]
