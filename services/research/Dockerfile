FROM python:3.11-slim

WORKDIR /app

# 安裝系統依賴 (部分 AI 框架需要編譯)
RUN apt-get update && apt-get install -y git build-essential && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 啟動研究 Worker
ENTRYPOINT ["python", "main.py"]