# Load Test — Jira Intelligence Platform

## Quick Start

```bash
# Install dependencies (optional: for plot generation)
pip install httpx rich

# Run load test
python tests/load_test.py
```

## Endpoints Tested

- `GET /api/health` — health check
- `GET /api/projects` — list projects
- `POST /api/auth/login` — authentication
- `GET /api/admin/dashboard` — admin dashboard

## Test Configuration

Edit `tests/load_test.py` to adjust:
- `CONCURRENCY` — number of concurrent workers (default: 10)
- `REQUESTS_PER_WORKER` — requests per worker (default: 20)
- `BASE_URL` — target server URL (default: http://localhost:8000)

## Results

After running, the script outputs:
- Total requests, succeeded, failed
- Requests per second
- P50, P90, P99 latency
- Per-endpoint breakdown
