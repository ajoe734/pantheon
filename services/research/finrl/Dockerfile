FROM python:3.12-slim
WORKDIR /app
COPY [d]ependencies/locks/services-research-finrl.txt* [r]equirements.txt* /tmp/requirements/
ARG PANTHEON_INSTALL_UPSTREAM_DEPS=true
RUN if [ "$PANTHEON_INSTALL_UPSTREAM_DEPS" = "true" ]; then if [ ! -f /tmp/requirements/services-research-finrl.txt ]; then echo "Canonical lock /tmp/requirements/services-research-finrl.txt required when PANTHEON_INSTALL_UPSTREAM_DEPS=true" >&2; exit 1; fi; apt-get update && apt-get install -y build-essential && pip install --no-cache-dir --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r /tmp/requirements/services-research-finrl.txt && rm -rf /var/lib/apt/lists/*; fi
RUN addgroup --system pantheon \
    && adduser --system --ingroup pantheon --home /home/pantheon pantheon
COPY --chown=pantheon:pantheon [s]ervices/research/finrl/ /app/services/research/finrl/
COPY --chown=pantheon:pantheon [e]ngine/ /app/services/research/finrl/engine/
COPY --chown=pantheon:pantheon [e]xamples/ /app/services/research/finrl/examples/
COPY --chown=pantheon:pantheon [a]dapter.py [c]onfig.py [s]moke_test.py [t]wse_stock_env.py [w]orker.py [p]roduction_drl_run.py [r]egistry_admission_packet.py [a]ctivation_smoke.py [t]est_*.py [__]init__.py /app/services/research/finrl/
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app:/usr/local/lib/python3.12/site-packages
ENV HOME=/tmp/pantheon
WORKDIR /app/services/research/finrl
USER pantheon
CMD ["python", "smoke_test.py"]
