FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y build-essential && rm -rf /var/lib/apt/lists/*
COPY services/research/finrl/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app:/usr/local/lib/python3.11/site-packages
WORKDIR /app/services/research/finrl
CMD ["python", "smoke_test.py"]
