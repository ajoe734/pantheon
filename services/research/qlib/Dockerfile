FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY adapter/ ./adapter/
COPY worker.py ./worker.py
COPY examples/ ./examples/
CMD ["python", "worker.py"]
