FROM python:3.12-slim
WORKDIR /app
COPY services/broker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY services/broker/ .
ARG GIT_SHA=unknown
LABEL org.opencontainers.image.revision=$GIT_SHA
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8102"]
