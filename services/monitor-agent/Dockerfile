FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Standard library only: no third-party dependencies to lock.
COPY services/monitor-agent/monitor_agent.py /workspace/monitor_agent.py

CMD ["python", "/workspace/monitor_agent.py"]
