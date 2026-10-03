FROM python:3.12-slim
WORKDIR /app
COPY dependencies/locks/services-learning-imitation.txt ./requirements.lock
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then pip install --no-cache-dir --require-hashes -r ./requirements.lock; fi
COPY services/research/imitation/ .
CMD ["python", "smoke_test.py"]
