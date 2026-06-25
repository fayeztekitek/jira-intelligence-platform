# Sprint Backlogs — Jira Intelligence Platform

**Cadence**: 1-week sprints | **Total**: 8 sprints | **Team**: 1-2 devs

---

## Sprint 1 — Security & Foundation
**Goal**: Seal critical security gaps, get auth working end-to-end

| Status | ID | Task | Hours | Files | Dependencies |
|--------|----|------|-------|-------|-------------|
| ✅ DONE | S1.1 | Secure CORS — restrict origins via `ALLOWED_ORIGINS` env var | 1 | `main.py`, `config.py` | — |
| ✅ DONE | S1.2 | JWT auth module — login, token validation, route protection | 6 | `api/auth.py`, `main.py`, `config.py` | S1.1 |
| ✅ DONE | S1.3 | Frontend auth — token storage, auto-re-auth, auth headers on all requests | 4 | `frontend/index.html` | S1.2 |
| ✅ DONE | S1.4 | Secure default key enforcement — warn if default in production | 1 | `config.py`, `main.py` | — |
| ✅ DONE | S1.5 | API key admin user — deterministic key from `APP_SECRET_KEY` | 2 | `api/auth.py` | S1.2 |
| ✅ DONE | S1.6 | Protect all `/api/*` routes with `require_auth` dependency | 2 | `api/routes.py` | S1.2 |
| ⌛ PENDING | S1.7 | Unit tests for auth — login, token expiry, invalid key, admin check | 4 | `tests/test_auth.py` | S1.2 |
| ⌛ PENDING | S1.8 | Integration test: auth flow end-to-end | 3 | `tests/test_api.py` | S1.7 |

**Definition of Done**:
- [ ] All endpoints return 401 without valid token
- [ ] Login with API key returns JWT
- [ ] Frontend automatically re-auths on 401
- [ ] Production CORS rejects unauthorized origins
- [ ] Auth tests pass (≥ 95% coverage on auth module)

**Already implemented in audit phase**: S1.1, S1.2, S1.3, S1.4, S1.5, S1.6

---

## Sprint 2 — Jira Connection & Data Quality
**Goal**: Reliable extraction, configurable fields, database migrations

| Status | ID | Task | Hours | Files | Dependencies |
|--------|----|------|-------|-------|-------------|
| ✅ DONE | S2.1 | Make customfield IDs configurable via env | 3 | `config.py`, `client.py`, `extractor.py`, `.env.example` | — |
| ✅ DONE | S2.2 | Alembic migration infrastructure + initial migration | 6 | `alembic.ini`, `migrations/`, `001_initial_schema.py` | — |
| ✅ DONE | S2.3 | Fix broken `get_users()` pagination | 2 | `client.py` | — |
| ✅ DONE | S2.4 | Fix `Retry-After` header non-integer crash | 1 | `client.py` | — |
| ✅ DONE | S2.5 | Remove redundant context manager in `_extract_issues_by_jql` | 1 | `extractor.py` | — |
| ✅ DONE | S2.6 | Populate `lead_time_days`, `cycle_time_days` in extraction | 2 | `extractor.py` | — |
| ⌛ PENDING | S2.7 | Auto-discover customfield IDs via `/rest/api/3/field` | 5 | New `api/fields.py`, `client.py` | — |
| ⌛ PENDING | S2.8 | OAuth2 flow implementation for Jira Cloud | 8 | `client.py`, `config.py`, `api/auth.py` | — |
| ⌛ PENDING | S2.9 | Extractor unit tests — full + incremental extraction, upsert, error handling | 8 | `tests/test_extractor.py` | S2.1-S2.6 |
| ⌛ PENDING | S2.10 | Fix `FactTransition` missing index on `changed_at` | 1 | `migrations/versions/002_add_transition_index.py` | S2.2 |

**Technical Notes**:
- **S2.7**: `GET /rest/api/3/field` returns all fields with IDs. Query and auto-map `epic`, `story_points`, `sprint` by field name patterns. Store in `FieldMapping` table.
- **S2.8**: OAuth2 flow needs: authorize URL redirect, callback handler, token refresh. Use httpx OAuth2 helpers or `atlassian-python-api` library.
- **S2.10**: Migration 002 should create `ix_fact_transition_changed_at` index to speed up time-in-status queries.

**Definition of Done**:
- [ ] Extraction works against any Jira instance (no hardcoded field IDs)
- [ ] `alembic upgrade head` creates full schema
- [ ] OAuth2 flow works (if implemented)
- [ ] All extractor tests pass
- [ ] Field discovery endpoint returns mapped fields

---

## Sprint 3 — KPI Engine & Real Dashboards
**Goal**: Accurate KPIs, optimized engine, real data in all charts

| Status | ID | Task | Hours | Files | Dependencies |
|--------|----|------|-------|-------|-------------|
| ✅ DONE | S3.1 | KPI engine binary search optimization | 6 | `calculator.py` | — |
| ✅ DONE | S3.2 | Team KPI prev_values — all team KPIs now compute deltas | 3 | `calculator.py` | S3.1 |
| ✅ DONE | S3.3 | Fix snapshot `bugs_resolved` (was hardcoded 0) | 1 | `snapshot_writer.py` | — |
| ✅ DONE | S3.4 | Fix snapshot purge to clean all tables (KPIResult + FactSnapshot + RiskScore) | 1 | `snapshot_writer.py` | — |
| ✅ DONE | S3.5 | Frontend: Replace all hardcoded chart data with real API calls | 10 | `frontend/index.html` | S3.1 |
| ✅ DONE | S3.6 | Fix N+1 query in `executive_summary` — batch load risk scores | 3 | `api/routes.py` | — |
| ⌛ PENDING | S3.7 | Add `cycle_time_days` computation from changelog (first "In Progress" → "Done") | 5 | `extractor.py`, `client.py` | S2.6 |
| ⌛ PENDING | S3.8 | Add `first_response_date` from comments | 4 | `extractor.py` | — |
| ⌛ PENDING | S3.9 | KPI edge case tests — empty project, all resolved, single issue, all periods | 4 | `tests/test_kpi_calculator.py` | S3.1 |
| ⌛ PENDING | S3.10 | Frontend loading states and error UI for all pages | 3 | `frontend/index.html` | S3.5 |
| ⌛ PENDING | S3.11 | Period selector affects chart history range | 2 | `frontend/index.html` | S3.5 |

**Technical Notes**:
- **S3.7**: Extract first transition where `field="status"` and `to_string in ("In Progress", ...)`. Use `FactTransition` table. Compute `cycle_time_days = resolved_date - first_in_progress_date`.
- **S3.8**: `first_response_date` = created date of first comment (by someone other than reporter). Already in `comment` field data.
- **S3.11**: Pass `period` parameter to history API calls, adjust `days` accordingly.

**Definition of Done**:
- [ ] All 9 delivery KPI cards display real data
- [ ] Created vs Resolved chart shows actual historical trend (not mock)
- [ ] Bug trend + priority donut show real data
- [ ] Risk trend chart shows actual history
- [ ] Workload distribution shows real assignee counts
- [ ] KPI engine handles 10k+ issues in < 2s
- [ ] All edge case tests pass

---

## Sprint 4 — Sprint & Release Analytics
**Goal**: Agile KPIs, version tracking, complete trend dashboards

| ID | Task | Hours | Files | Dependencies |
|----|------|-------|-------|-------------|
| S4.1 | Sprint velocity engine — commitment, completed, carry-over, predictability | 8 | New `kpi_engine/sprint.py` | S3.1 |
| S4.2 | Sprint scope change from changelog (issues added mid-sprint) | 4 | `calculator.py` | S4.1, S3.7 |
| S4.3 | Release KPIs — fix version completion, scope increase, delayed issues | 6 | `calculator.py` | — |
| S4.4 | Sprint burndown API endpoint — daily remaining points per sprint | 4 | `api/routes.py`, `storage/repositories.py` | S4.1 |
| S4.5 | Release burndown chart — frontend version tracking view | 5 | `frontend/index.html` (new release page) | S4.3 |
| S4.6 | Sprint velocity trend chart — last 10 sprints | 3 | `frontend/index.html` | S4.1 |
| S4.7 | 90-day trend dashboards for all 6 KPI families | 8 | `frontend/index.html` | S3.5 |
| S4.8 | Historical snapshot pipeline — daily snapshots with complete data | 3 | `jobs.py`, `snapshot_writer.py` | — |
| S4.9 | Add `sprint_velocity` KPI to `FactSnapshot` | 2 | `calculator.py`, `snapshot_writer.py` | S4.1 |
| S4.10 | Sprint unit tests — velocity, predictability, scope change | 5 | `tests/test_sprint_kpis.py` | S4.1 |

**New Frontend Page**: "Sprints" tab in sidebar with:
- Current sprint burndown chart
- Sprint velocity bar chart (last 10 sprints)
- Sprint health indicators (scope change, carry-over rate)
- Version completion progress bars

**Definition of Done**:
- [ ] Sprint velocity tracked per project with at least 3 sprints of history
- [ ] Release readiness % visible per version
- [ ] Burndown chart shows actual daily data
- [ ] Sprint scope change (added/removed mid-sprint) detected
- [ ] All sprint KPIs stored in `FactSnapshot` and queryable via API

---

## Sprint 5 — Risk Engine v2 & Export
**Goal**: Enhanced risk scoring, multi-format export

| ID | Task | Hours | Files | Dependencies |
|----|------|-------|-------|-------------|
| S5.1 | Expand risk KPIs — blocked critical, aging critical, SLA-close indicators | 5 | `calculator.py` (add to _risk_control) | — |
| S5.2 | Multi-period risk scoring — compute for 1w, 1m, 3m (not just 1m) | 4 | `scorer.py`, `config.py` | S3.1 |
| S5.3 | Configurable risk weights from settings env var | 2 | `config.py`, `scorer.py` | — |
| S5.4 | Risk trend API — history of composite risk + dimensions | 3 | `api/routes.py` | S5.2 |
| S5.5 | Streaming CSV export with configurable row limit | 4 | `api/routes.py` | — |
| S5.6 | Excel export — `.xlsx` with separate sheets per KPI category | 6 | New `api/export.py` (openpyxl) | — |
| S5.7 | PDF executive report — A4 format with risk matrix, KPI summary, alerts | 8 | New `api/export.py` (reportlab/weasyprint) | S5.1 |
| S5.8 | Export button in frontend for all formats | 3 | `frontend/index.html` | S5.5-S5.7 |
| S5.9 | API integration tests — all endpoints with seeded mock data | 8 | `tests/test_api.py` | — |
| S5.10 | Risk health trend chart in Executive page | 3 | `frontend/index.html` | S5.4 |

**Technical Notes**:
- **S5.7**: For PDF, use `weasyprint` with HTML template rendering. Generate executive summary HTML with inline CSS, convert to PDF.
- **S5.6**: Excel sheets: Delivery KPIs, Quality KPIs, Risk KPIs, DQ KPIs, Team KPIs, Issues.

**Definition of Done**:
- [ ] Risk scored for 1w, 1m, 3m windows
- [ ] CSV exports 100k+ rows in < 5s (streaming)
- [ ] Excel with 5+ sheets, formatted headers
- [ ] PDF executive report with risk matrix, KPI cards, and alert list
- [ ] All 15 API endpoints tested with > 80% coverage

---

## Sprint 6 — AI Agent v1
**Goal**: Foundation AI agent with RAG and tool calling

| ID | Task | Hours | Files | Dependencies |
|----|------|-------|-------|-------------|
| S6.1 | pgvector setup — PostgreSQL vector extension + embeddings column | 4 | `migrations/versions/003_pgvector.py`, `config.py` | S2.2 |
| S6.2 | Embedding pipeline — generate + store embeddings for KPI definitions, issue summaries, project metadata | 6 | `ai_agent/rag_index.py` | S6.1 |
| S6.3 | Agent orchestrator — intent classification, tool dispatch, response generation | 10 | `ai_agent/agent.py` | S6.4 |
| S6.4 | Tool registry — 8 tools: `get_project_kpis`, `get_risk_scores`, `search_issues`, `get_exec_summary`, `compare_projects`, `get_sprint_analysis`, `get_trend`, `get_recommendations` | 8 | `ai_agent/tools.py` | S5.1 (for risk), S4.1 (for sprint) |
| S6.5 | Prompt templates — system prompt, mode templates (executive/technical/operational), ambiguity handling | 4 | `ai_agent/prompts.py` | S6.3 |
| S6.6 | Guardrails — hallucination prevention, source citation, permission check, ambiguity detection | 5 | `ai_agent/guardrails.py` | S6.3 |
| S6.7 | AI chat API endpoint — `POST /api/ai/ask` | 3 | `api/routes.py` | S6.3 |
| S6.8 | AI agent unit tests — all 8 tools, guardrails, prompt rendering | 6 | `tests/test_ai_agent.py` | S6.3-S6.6 |

**Technical Notes**:
- Use `sentence-transformers` or OpenAI embeddings for vector generation
- Store embeddings in pgvector column on `kpi_result`, `fact_issue`, `dim_project`
- Use function-calling with structured JSON: `{"tool": "get_project_kpis", "params": {...}}`
- For MVP, use OpenAI API or local LLM via Ollama (configurable)

**Definition of Done**:
- [ ] `POST /api/ai/ask` returns structured answers with source citations
- [ ] "What are the most risky projects?" returns real risk scores
- [ ] "Explain why the backlog increased" returns trend data
- [ ] Agent refuses with "No data" when tool returns empty
- [ ] Agent asks for clarification on ambiguous questions
- [ ] All 8 tools work correctly

---

## Sprint 7 — AI Agent v2 & Chat UI
**Goal**: Interactive chat, French support, auto-recommendations

| ID | Task | Hours | Files | Dependencies |
|----|------|-------|-------|-------------|
| S7.1 | Chat interface UI — message bubbles, typing indicator, suggested queries | 8 | New `frontend/src/ai/ChatInterface.jsx`, `frontend/src/ai/MessageBubble.jsx` | S6.7 |
| S7.2 | Suggested queries — dynamic question chips based on current context | 3 | `ai_agent/prompts.py`, `frontend/src/ai/SuggestedQueries.jsx` | S7.1 |
| S7.3 | French language support — detect language, bilingual prompts, translate answers | 6 | `ai_agent/prompts.py`, `ai_agent/agent.py` | S6.5 |
| S7.4 | Recommendations engine — auto-generated 3-5 recommendations per project | 5 | `ai_agent/agent.py`, `ai_agent/tools.py` | S6.4 |
| S7.5 | Compare projects tool — side-by-side KPI diff table | 4 | `ai_agent/tools.py`, `frontend/src/ai/` | S6.4 |
| S7.6 | Executive report generator — structured report: executive summary, risks, actions | 5 | `ai_agent/agent.py` | S7.4 |
| S7.7 | Question history sidebar — recent questions, re-ask, pin favorites | 3 | `frontend/src/ai/` (localStorage) | S7.1 |
| S7.8 | Token usage tracking + cost estimation | 2 | `ai_agent/agent.py` | S6.7 |
| S7.9 | Rate limiting on AI endpoint (per user) | 3 | `api/routes.py`, `cache/redis_cache.py` | — |

**Example Questions Supported**:
- "Compare CORE and MOBILE" → structured diff table
- "What changed during the last sprint?" → sprint scope change
- "Which components generate the most bugs?" → component quality ranking
- "Give me an executive summary for INFRA" → 3-paragraph report
- "Quels sont les risques principaux ce mois-ci ?" → French response
- "What should management focus on this week?" → ranked recommendations

**Definition of Done**:
- [ ] Chat UI renders messages, shows typing indicator, supports markdown tables
- [ ] French questions answered in French
- [ ] "Compare project A and B" returns diff table with trend arrows
- [ ] "What should management focus on?" returns 3+ actionable recommendations
- [ ] Question history persists across sessions
- [ ] AI endpoint rate-limited to 20 req/min/user

---

## Sprint 8 — Enterprise Hardening
**Goal**: Production readiness, multi-tenant, monitoring

| ID | Task | Hours | Files | Dependencies |
|----|------|-------|-------|-------------|
| S8.1 | Multi-Jira support — connection profiles, credential store, project isolation | 10 | New `providers/`, `storage/models.py` (add `JiraConnection`), `config.py` | S2.2 |
| S8.2 | Advanced RBAC — per-project permissions, viewer/analyst/admin roles in DB | 8 | `storage/models.py` (add `User`, `Role`, `ProjectPermission`), `api/auth.py`, migration 004 | S1.2 |
| S8.3 | Audit logging middleware — all data access logged to DB | 4 | New `api/middleware.py`, `storage/models.py` (add `AuditLog`) | — |
| S8.4 | Prometheus metrics — request count, latency, error rate, API call count, sync duration | 6 | `main.py` (add `prometheus_client`), `scheduler/jobs.py` | — |
| S8.5 | Admin dashboard — manage connections, view audit logs, configure settings | 8 | New `frontend/src/pages/AdminPage.jsx`, `api/admin.py` | S8.1-S8.3 |
| S8.6 | Webhook receiver — Jira event listener, incremental sync on issue update | 8 | New `api/webhooks.py`, `ingestion/webhook_processor.py` | S2.1 |
| S8.7 | Data retention policy — configurable retention per table, archive to cold storage | 4 | `config.py`, `jobs.py`, `storage/repositories.py` | — |
| S8.8 | Deployment runbook — Docker Compose prod, env vars checklist, backup/restore, monitoring setup | 4 | `docs/DEPLOYMENT.md`, `docs/MONITORING.md` | — |
| S8.9 | Load test — 50k+ issues, 50 projects, 20 concurrent users | 6 | `tests/load/locustfile.py` | S8.4 |
| S8.10 | Security hardening — HTTPS redirect, HSTS headers, Content-Security-Policy, X-Frame-Options | 3 | `main.py`, nginx config | — |

**Multi-Jira Architecture**:
```
JiraConnection (DB table) → JiraClient is instantiated per connection
Project is linked to a connection via connection_id FK
Extraction runs per connection isolated
KPI results tagged with connection_id
User permissions scoped to connection + project
```

**Definition of Done**:
- [ ] Two+ Jira instances can be connected simultaneously
- [ ] RBAC enforced: viewer can see, analyst can export, admin can configure
- [ ] All API access logged to `audit_log` table with timestamp, user, endpoint, project
- [ ] Prometheus metrics available at `/metrics`
- [ ] Webhook syncs issue within 30s of Jira event
- [ ] Load test passes: p95 < 500ms, no errors
- [ ] Security headers present in all responses
- [ ] Backup/restore documented and tested

---

## Rollback Plan

### Per-Sprint Rollback
```bash
# If sprint N causes issues:
git revert HEAD~N..HEAD --no-commit
git commit -m "rollback: sprint N - $(reason)"
```

### Database Rollback
```bash
alembic downgrade -1   # revert last migration
alembic downgrade <base>  # revert to specific version
```

### Feature Flag Rollback
```python
# config.py
enable_ai_agent: bool = False  # Flip to disable AI feature without code revert
```

### Data Recovery
```bash
# Automated daily DB dump
pg_dump -U jirauser jira_intelligence > /backups/jira_intel_$(date +%Y%m%d).sql
# Restore
pg_restore -U jirauser -d jira_intelligence /backups/jira_intel_YYYYMMDD.sql
```

---

## Risk Register

| Risk | Probability | Impact | Mitigation |
|------|-----------|--------|------------|
| Jira API rate limit during full sync | Medium | High | Implement token bucket, adaptive delay, respect Retry-After |
| Jira customfield IDs differ per instance | High | High | Auto-discovery via field API (S2.7), configurable via env |
| LLM cost explosion in Sprint 6-7 | Medium | Medium | Token tracking, rate limiting, caching common queries |
| pgvector installation complexity | Medium | Medium | Fallback to cosine similarity via Python (no DB extension) |
| Frontend migration to React takes longer than expected | High | Medium | Keep single-page HTML as fallback, migrate incrementally |
| Multi-tenant isolation bugs | Low | Critical | Thorough testing with 2+ Jira connections, data leakage tests |
| Webhook delivery failures | Medium | Medium | Retry queue, dead-letter queue, manual re-sync option |

---

## Velocity Tracking

| Sprint | Planned Points | Actual Points | Velocity | Notes |
|--------|---------------|---------------|----------|-------|
| 1 | 19 | — | — | Includes 6 already done |
| 2 | 38 | — | — | Includes 6 already done |
| 3 | 42 | — | — | Includes 6 already done |
| 4 | 48 | — | — | New development |
| 5 | 46 | — | — | New + tests |
| 6 | 46 | — | — | AI layer new |
| 7 | 42 | — | — | AI + frontend |
| 8 | 61 | — | — | Hardening largest |

Adjust velocity expectation to **25-35 points/sprint** for single developer.
