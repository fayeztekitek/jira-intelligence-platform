# Jira Intelligence Platform

Enterprise Jira analytics with historical snapshots, KPI engine, risk scoring, and executive dashboards — built for Product Governance, Risk, Audit, and COMEX reporting.

---

## Quick Start (5 minutes, mock data)

```bash
# 1. Clone / unzip the project
cd jira_platform/backend

# 2. Install dependencies
pip install -r requirements.txt

# 3. Seed mock data (3 projects, 540 issues, all KPIs calculated)
DATABASE_URL="sqlite+aiosqlite:///./jira_test.db" python -m tests.seed_mock_data

# 4. Start the server
DATABASE_URL="sqlite+aiosqlite:///./jira_test.db" python main.py

# 5. Open the dashboard
open http://localhost:8000
# OR open frontend/index.html directly in your browser
```

---

## Architecture

```
┌─────────────────────────────────────────────┐
│              Jira REST API / Mock            │
├──────────────────────────────────────────────┤
│  Jira Connector  │  Extractor  │  Scheduler  │
├──────────────────────────────────────────────┤
│  SQLite (dev) / PostgreSQL (production)      │
│  Star Schema: Facts + Dims + Audit tables    │
├──────────────────────────────────────────────┤
│  KPI Engine (7 categories × 10 periods)      │
│  Risk Scorer (4 dimensions, weighted avg)    │
├──────────────────────────────────────────────┤
│  FastAPI REST API                            │
├──────────────────────────────────────────────┤
│  HTML/ECharts Dashboard                      │
└──────────────────────────────────────────────┘
```

---

## Project Structure

```
jira_platform/
├── backend/
│   ├── main.py                    # FastAPI app entry point
│   ├── config.py                  # Settings (env vars)
│   ├── requirements.txt
│   ├── storage/
│   │   ├── models.py              # SQLAlchemy star schema
│   │   └── database.py            # Async engine + session
│   ├── jira_connector/
│   │   └── client.py              # Jira REST API client
│   ├── ingestion/
│   │   ├── extractor.py           # Full/incremental extraction
│   │   └── snapshot_writer.py     # KPI/risk persistence
│   ├── kpi_engine/
│   │   └── calculator.py          # All KPI calculations
│   ├── risk_engine/
│   │   └── scorer.py              # 4-dimension risk model
│   ├── scheduler/
│   │   └── jobs.py                # APScheduler jobs
│   ├── api/
│   │   └── routes.py              # All FastAPI endpoints
│   └── tests/
│       ├── seed_mock_data.py      # Mock data generator
│       ├── test_kpi_calculator.py # KPI unit tests
│       └── test_risk_scorer.py    # Risk unit tests
└── frontend/
    └── index.html                 # Single-file dashboard
```

---

## KPI Catalogue

### Delivery (10 KPIs)
| KPI | Formula | Risk Threshold |
|-----|---------|---------------|
| Issues Created | COUNT(created IN period) | — |
| Issues Resolved | COUNT(resolved IN period) | — |
| Resolution Rate | resolved / created × 100 | <80% = risk |
| Avg Resolution Days | MEAN(resolved - created) | >30d = critical |
| Median Resolution Days | MEDIAN(resolved - created) | — |
| Throughput | resolved / period_days | — |
| Backlog Size | COUNT(open as of period end) | >500 = critical |
| WIP | COUNT(In Progress) | >50 = high |
| Overdue Issues | COUNT(open AND due_date < today) | >20 = critical |
| Aging Issues >30d | COUNT(open AND age > 30) | >50 = critical |

### Quality (7 KPIs)
| KPI | Formula | Risk Threshold |
|-----|---------|---------------|
| Bugs Created | COUNT(type=Bug AND created IN period) | >30 = critical |
| Bug Resolution Rate | bugs_resolved / bugs_created × 100 | <70% = risk |
| Reopened Count | COUNT(times_reopened > 0) | >15 = critical |
| Reopen Rate | reopened / total × 100 | >10% = critical |
| Critical Bugs Open | COUNT(open bugs, priority=Critical/Blocker) | >10 = critical |
| High Bugs Open | COUNT(open bugs, priority=High) | >25 = high |
| Repeat Reopens | COUNT(times_reopened >= 2) | >5 = critical |

### Risk & Control (5 KPIs)
- Unassigned Open, No Fix Version, Stuck >14d, Stale >7d, Critical Open

### Data Quality (8 KPIs)
- Missing: assignee, priority, component, fix version, epic, due date
- Closed without resolution
- DQ Score: 100 - AVG(pct_missing per field)

### Governance (4 KPIs)
- % Done, Story Points Delivered, New Epics %, Issue Type Distribution

### Team (4 KPIs)
- Max Assignee Load, Workload Imbalance Ratio, Active Contributors, Unassigned %

---

## Risk Scoring Model

```
Composite Risk = (
  delivery_risk  × 0.30 +
  quality_risk   × 0.35 +
  compliance_risk × 0.20 +
  operational_risk × 0.15
)

Risk Level: Low (<25) | Medium (25-50) | High (50-75) | Critical (>75)

Each dimension is scored 0-100 based on KPI thresholds.
Each score is adjusted by a trend multiplier:
  Improving → × 0.85
  Stable    → × 1.00
  Degrading → × 1.20
```

Weights are configurable in `risk_engine/scorer.py` (`DEFAULT_WEIGHTS`).

---

## Connecting to Real Jira (Phase 6)

### 1. Copy and fill `.env`

```bash
cp .env.example .env
```

```env
JIRA_BASE_URL=https://your-company.atlassian.net
JIRA_AUTH_TYPE=api_token
JIRA_USERNAME=your-email@company.com
JIRA_API_TOKEN=your_token_here
DATABASE_URL=postgresql://user:pass@localhost:5432/jira_intelligence
```

### 2. Generate your Jira API token

- Go to https://id.atlassian.com/manage-profile/security/api-tokens
- Create token → copy to `JIRA_API_TOKEN`

### 3. For Jira Data Center (PAT)

```env
JIRA_AUTH_TYPE=pat
JIRA_PAT=your_personal_access_token
```

### 4. Run first full extraction

```bash
python -c "
import asyncio
from jira_connector.client import JiraClient
from ingestion.extractor import JiraExtractor

async def run():
    async with JiraClient() as client:
        info = await client.server_info()
        print('Connected to:', info.get('serverTitle'))
        extractor = JiraExtractor(client)
        await extractor.run_full_extraction()

asyncio.run(run())
"
```

### 5. Validate connection

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/projects
curl http://localhost:8000/api/executive/summary
```

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | Health check |
| GET | /api/projects | List all projects |
| GET | /api/projects/{key}/kpis | KPIs for a project |
| GET | /api/projects/{key}/risk | Risk score |
| GET | /api/projects/{key}/issues | Paginated issues |
| GET | /api/executive/summary | Portfolio executive view |
| GET | /api/kpis/history | KPI trend over time |
| GET | /api/sync/status | Extraction run history |
| POST | /api/sync/trigger | Trigger manual sync |
| GET | /api/export/csv | Export issues to CSV |
| GET | /api/docs | Swagger UI |

---

## Running Tests

```bash
cd backend
pytest tests/test_kpi_calculator.py tests/test_risk_scorer.py -v
# Expected: 38 passed
```

---

## Scheduler Jobs

| Job | Schedule | Description |
|-----|----------|-------------|
| incremental_extraction | Daily 02:00 UTC | Sync issues updated in last 25h |
| kpi_calculation | Daily 03:00 UTC | Recalculate all KPIs |
| full_sync | Sunday 01:00 UTC | Full re-sync of all issues |
| snapshot_maintenance | Friday 04:00 UTC | Purge old snapshots |

Disable scheduler: set `SCHEDULER_ENABLED=false` in `.env`.

---

## Production Deployment

```bash
# PostgreSQL
DATABASE_URL=postgresql://user:pass@host/db

# Run with gunicorn
pip install gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000

# Or Docker (docker-compose.yml included)
docker-compose up -d
```

---

## Audit & Compliance

Every extraction run is recorded in `extraction_run` table with:
- `run_id`: unique UUID
- `triggered_by`: scheduler | api | manual
- `issues_extracted`, `issues_updated`, `transitions_extracted`
- `jira_api_calls`: exact API call count
- `duration_seconds`, `error_count`, `error_details`

Every KPI result in `kpi_result` stores:
- `formula`: exact calculation formula
- `interpretation`: business meaning
- `calculation_date`: when it was computed
- `period_label`: which time window

This provides full data lineage and traceability for internal audit.
