# One image for every service: web app, workflow worker, Temporal dev server, Demo HRMS.
FROM node:20-bookworm-slim AS screens
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright PAYROLL_DATA_DIR=/data
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
# Temporal CLI (its dev server keeps workflow state in a SQLite file on a volume)
RUN curl -sSf https://temporal.download/cli.sh | sh -s -- --dir /usr/local \
    && ln -sf /usr/local/bin/temporal /usr/bin/temporal && temporal --version
WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt && playwright install --with-deps chromium
COPY backend/ backend/
COPY mock_hrms/ mock_hrms/
COPY data/ data/
COPY answer_sheets/ answer_sheets/
COPY integrations/ integrations/
COPY --from=screens /app/frontend/dist frontend/dist
WORKDIR /app/backend
EXPOSE 8080
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "8", "--timeout", "180"]
