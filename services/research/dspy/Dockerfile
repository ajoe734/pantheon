FROM python:3.11-slim
WORKDIR /app
COPY services/learning/dspy/requirements.txt .
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir -r requirements.txt; fi
CMD ["python", "-c", "print('Deprecated research-dspy shim: canonical worker lives in services/learning/dspy')"]
