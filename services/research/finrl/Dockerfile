FROM python:3.12-slim
WORKDIR /app
COPY dependencies/locks/services-research-finrl.txt ./requirements.lock
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then apt-get update && apt-get install -y build-essential && pip install --no-cache-dir --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r ./requirements.lock && rm -rf /var/lib/apt/lists/*; fi
RUN addgroup --system pantheon \
    && adduser --system --ingroup pantheon --home /home/pantheon pantheon
COPY --chown=pantheon:pantheon services/research/finrl/ /app/services/research/finrl/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app:/usr/local/lib/python3.12/site-packages
ENV HOME=/tmp/pantheon
WORKDIR /app/services/research/finrl
USER pantheon
CMD ["python", "smoke_test.py"]
