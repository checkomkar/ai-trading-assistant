# One image for any host that runs containers (a VPS, AWS ECS/App Runner, Azure Container Apps, Google Cloud Run, Fly...).
FROM python:3.10-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
# OCR for scanned PDFs (free, runs on the server itself)
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY trader ./trader
RUN pip install -e .
COPY alembic ./alembic
COPY alembic.ini ./
COPY config ./config
RUN useradd --create-home app && mkdir -p /app/data && chown -R app /app/data
USER app
# State lives only in DATABASE_URL and the /app/data volume (Fyers token, uploads); nothing else is written to disk.
VOLUME /app/data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"
# Migrations run at start-up (api/main.py), so a new database needs no extra step.
CMD ["uvicorn", "trader.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
