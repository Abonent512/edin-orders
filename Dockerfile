# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/ ./
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/proxy_ca; fi; \
    npm ci --no-audit --no-fund && npm run build && rm -rf node_modules

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data STATIC_DIR=/app/static APK_FILE=/app/releases/edin-android-demo.apk
WORKDIR /app
COPY backend/requirements.txt ./requirements.txt
COPY backend/app ./app
COPY releases/edin-android-demo.apk ./releases/edin-android-demo.apk
COPY --from=frontend /build/dist ./static
RUN --mount=type=secret,id=proxy_ca \
    if [ -f /run/secrets/proxy_ca ]; then export PIP_CERT=/run/secrets/proxy_ca; fi; \
    pip install --no-cache-dir -r requirements.txt && \
    useradd --uid 10001 --create-home edin && mkdir -p /app/data /backups && chown -R edin:edin /app /backups
USER edin
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=12s --start-period=40s --retries=3 CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/api/v1/health',timeout=10)"
CMD ["python", "-m", "app.runner"]
