FROM python:3.11-slim
WORKDIR /app
COPY services/learning/imitation/requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
COPY services/research/imitation/ .
CMD ["python", "smoke_test.py"]
