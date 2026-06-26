# Deployment Runbook — Jira Intelligence Platform

## Prerequisites

- Python 3.12+
- PostgreSQL 15+ with pgvector extension (optional, falls back to SQLite)
- Node.js 20+ (for frontend builds)

## Quick Start (Development)

```bash
# Backend
cd backend
pip install -r requirements.txt
cp .env.example .env  # configure Jira credentials
alembic upgrade head
uvicorn main:app --reload --port 8000

# Frontend (AI Chat)
cd frontend/ai-chat
npm install
npm run dev  # proxies /api to localhost:8000
```

## Production Deployment

### 1. Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `APP_SECRET_KEY` | Yes | — | JWT signing key (32+ chars) |
| `APP_ENV` | No | development | `development`, `staging`, `production` |
| `DATABASE_URL` | No | sqlite+aiosqlite:///data/jira_intel.db | Database connection string |
| `JIRA_BASE_URL` | Yes* | — | Default Jira instance URL |
| `JIRA_AUTH_TYPE` | No | api_token | `api_token` or `pat` |
| `JIRA_USERNAME` | Yes* | — | Jira account email |
| `JIRA_API_TOKEN` | Yes* | — | Jira API token |
| `SCHEDULER_ENABLED` | No | true | Enable APScheduler jobs |
| `LOG_LEVEL` | No | INFO | Logging level |
| `SNAPSHOT_RETENTION_DAYS` | No | 365 | Snapshot retention period |
| `ENABLE_PGVECTOR` | No | false | Enable pgvector for embeddings |
| `AI_RATE_LIMIT` | No | 20 | AI API rate limit (tokens/min) |

*Required for default Jira instance; multi-Jira instances can be configured via the admin API.

### 2. Database Setup

```bash
# SQLite (default, no setup needed)
alembic upgrade head

# PostgreSQL with pgvector
createdb jira_intel
psql jira_intel -c "CREATE EXTENSION vector;"
DATABASE_URL=postgresql+asyncpg://user:pass@localhost/jira_intel alembic upgrade head
```

### 3. Build & Run

```bash
# Build frontend
cd frontend/ai-chat
npm ci && npm run build

# Start backend (serves built frontend at /ai/)
cd backend
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

# Recommended: run behind nginx with reverse proxy
```

### 4. nginx Configuration

```nginx
server {
    listen 443 ssl;
    server_name jira-intel.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    client_max_body_size 1m;
}
```

### 5. Health Checks

- `GET /api/health` — basic health check
- `GET /api/metrics` — Prometheus metrics
- `GET /api/admin/dashboard` — admin stats (requires auth)

### 6. Backup & Restore

```bash
# SQLite
cp data/jira_intel.db backups/$(date +%Y%m%d).db

# PostgreSQL
pg_dump -Fc jira_intel > backups/$(date +%Y%m%d).dump
pg_restore -d jira_intel backups/latest.dump
```

### 7. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
| 401 on login | Wrong API key | Check `APP_SECRET_KEY` env var |
| AI agent fails | No LLM API key | Set `llm_api_key` or use fallback mode |
| Scheduler not running | `SCHEDULER_ENABLED=false` | Set to `true` |
| Embedding pipeline stuck | pgvector not enabled | Set `ENABLE_PGVECTOR=true` or use SQLite fallback |
