FROM python:3.12-slim
WORKDIR /app
COPY dependencies/locks/services-learning-dspy.txt ./requirements.lock
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir --require-hashes -r ./requirements.lock; fi
CMD ["python", "-c", "print('Deprecated research-dspy shim: canonical worker lives in services/learning/dspy')"]
