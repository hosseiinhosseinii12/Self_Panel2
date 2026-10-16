FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DB_PATH=/data/selfbot

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc libffi-dev libssl-dev ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY selfbot /app/selfbot
COPY app.py /app/app.py
COPY migrate.py /app/migrate.py

RUN mkdir -p /data/selfbot/logs /data/selfbot/backups /data/selfbot/sessions_backup

RUN useradd -m -u 1000 selfbot && chown -R selfbot:selfbot /app /data
USER selfbot

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:${PORT:-8080}/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "app:flask_app"]