FROM python:3.11-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace:/workspace/services/control-plane/feedback

COPY services/control-plane/feedback/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY . /workspace

EXPOSE 8085

CMD ["uvicorn", "main:app", "--app-dir", "/workspace/services/control-plane/feedback", "--host", "0.0.0.0", "--port", "8085"]
