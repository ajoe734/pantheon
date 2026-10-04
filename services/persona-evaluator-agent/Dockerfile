FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir "psycopg[binary]==3.3.6"
COPY services/__init__.py /workspace/services/__init__.py
COPY services/foundation /workspace/services/foundation
COPY services/rankings /workspace/services/rankings
COPY services/persona-evaluator-agent/persona_evaluator_agent.py /workspace/persona_evaluator_agent.py

CMD ["python", "/workspace/persona_evaluator_agent.py"]
