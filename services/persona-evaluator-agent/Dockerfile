FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Standard library only: no third-party dependencies to lock.
COPY services/persona-evaluator-agent/persona_evaluator_agent.py /workspace/persona_evaluator_agent.py

CMD ["python", "/workspace/persona_evaluator_agent.py"]
