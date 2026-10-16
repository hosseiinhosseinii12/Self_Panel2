FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_NO_CACHE_DIR=1 \\
    DB_PATH=/data/selfbot

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \\
        gcc libffi-dev libssl-dev ca-certificates curl \\
        unzip wget gosu \\
    && rm -rf /var/lib/apt/lists/*

RUN XRAY_VERSION="1.8.24" && \\
    ARCH=$(dpkg --print-architecture) && \\
    case "$ARCH" in \\
        amd64) XRAY_ARCH="64" ;; \\
        arm64) XRAY_ARCH="arm64-v8a" ;; \\
        *) XRAY_ARCH="64" ;; \\
    esac && \\
    wget -q "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip" \\
        -O /tmp/xray.zip && \\
    unzip -q /tmp/xray.zip -d /usr/local/bin/ && \\
    chmod +x /usr/local/bin/xray && \\
    rm /tmp/xray.zip && \\
    /usr/local/bin/xray version

COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

COPY selfbot /app/selfbot
COPY app.py /app/app.py
COPY migrate.py /app/migrate.py
COPY xray.json /app/xray.json
COPY xray-configs.json /app/xray-configs.json
COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

RUN mkdir -p /data/selfbot/logs /data/selfbot/backups /data/selfbot/sessions_backup

RUN useradd -m -u 1000 selfbot && chown -R selfbot:selfbot /app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=3 \\
    CMD curl -fsS http://127.0.0.1:${PORT:-8080}/health || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["sh", "-c", "xray run -c /app/xray.json & exec gunicorn --bind 0.0.0.0:8080 --workers 1 --threads 4 --timeout 120 app:flask_app"]