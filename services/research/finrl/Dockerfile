FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt ./requirements.txt
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then apt-get update && apt-get install -y build-essential && pip install --no-cache-dir -r requirements.txt && rm -rf /var/lib/apt/lists/*; fi
RUN addgroup --system pantheon \
    && adduser --system --ingroup pantheon --home /home/pantheon pantheon
COPY --chown=pantheon:pantheon . /app/services/research/finrl/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app:/usr/local/lib/python3.11/site-packages
ENV HOME=/tmp/pantheon
WORKDIR /app/services/research/finrl
USER pantheon
CMD ["python", "smoke_test.py"]
