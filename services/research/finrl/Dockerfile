FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install stable-baselines3
COPY . /app/services/research/finrl/
ENV PYTHONPATH=/app:/usr/local/lib/python3.11/site-packages
CMD ["python", "-m", "pytest", "services/research/finrl/smoke_test.py"]
