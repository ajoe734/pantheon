FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt entrypoint.py ./
RUN pip install --no-cache-dir -r requirements.txt
RUN addgroup --system pantheon \
    && adduser --system --ingroup pantheon --home /home/pantheon pantheon \
    && chown -R pantheon:pantheon /app /home/pantheon
ENV HOME=/tmp/pantheon \
    MLFLOW_HOST=127.0.0.1 \
    MLFLOW_PORT=5000 \
    MLFLOW_BACKEND_STORE_URI=sqlite:////tmp/mlflow/mlflow.db \
    MLFLOW_DEFAULT_ARTIFACT_ROOT=file:///tmp/mlflow/artifacts \
    MLFLOW_SERVER_ALLOWED_HOSTS=localhost:*,127.0.0.1:* \
    MLFLOW_SERVER_CORS_ALLOWED_ORIGINS=http://localhost:*,http://127.0.0.1:* \
    MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE=false \
    MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false
USER pantheon
ENTRYPOINT ["python", "entrypoint.py"]
