FROM python:3.12-slim

WORKDIR /workspace

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY dependencies/locks/services-consultation.txt /tmp/requirements.lock
RUN pip install --no-cache-dir -r /tmp/requirements.lock

COPY . /workspace

ENV PORT=8096
EXPOSE 8096

CMD ["python", "-m", "services.consultation.supervisor"]
