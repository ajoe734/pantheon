FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY adapter/ ./adapter/
COPY config.py worker.py smoke_test.py ./
COPY examples/ ./examples/
CMD ["python", "worker.py"]
