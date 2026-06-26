# Sprint 8 — Enterprise Hardening

**Goal**: Production readiness, multi-tenant, monitoring.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 61 (61 completed, 0 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| ✅ Done | S8.1 | Multi-Jira support | 10 | S2.2 |
| ✅ Done | S8.2 | Advanced RBAC | 8 | S1.2 |
| ✅ Done | S8.3 | Audit logging middleware | 4 | — |
| ✅ Done | S8.4 | Prometheus metrics | 6 | — |
| ✅ Done | S8.5 | Admin dashboard | 8 | S8.1–S8.3 |
| ✅ Done | S8.6 | Webhook receiver | 8 | S2.1 |
| ✅ Done | S8.7 | Data retention policy | 4 | — |
| ✅ Done | S8.8 | Deployment runbook | 4 | — |
| ✅ Done | S8.9 | Load test | 6 | S8.4 |
| ✅ Done | S8.10 | Security hardening | 3 | — |
| 🔵 Planned | S8.5 | Admin dashboard | 8 | S8.1–S8.3 |
| 🔵 Planned | S8.6 | Webhook receiver | 8 | S2.1 |
| 🔵 Planned | S8.7 | Data retention policy | 4 | — |
| 🔵 Planned | S8.8 | Deployment runbook | 4 | — |
| 🔵 Planned | S8.9 | Load test | 6 | S8.4 |
| ✅ Done | S8.10 | Security hardening | 3 | — |

## Definition of Done

- [ ] Two+ Jira instances can be connected simultaneously
- [ ] RBAC enforced: viewer can see, analyst can export, admin can configure
- [ ] All API access logged to `audit_log` table with timestamp, user, endpoint, project
- [ ] Prometheus metrics available at `/metrics`
- [ ] Webhook syncs issue within 30s of Jira event
- [ ] Load test passes: p95 < 500ms, no errors
- [ ] Security headers present in all responses
- [ ] Backup/restore documented and tested

## Multi-Jira Architecture

```
JiraConnection (DB table) → JiraClient per connection
Project linked to connection via connection_id FK
Extraction isolated per connection
KPI results tagged with connection_id
User permissions scoped to connection + project
```

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 8"
alembic downgrade -2  # revert migration 004 + 005
```

## Risks

| Risk | Mitigation |
|------|------------|
| Multi-tenant isolation bugs | Dedicated integration tests with 2+ connections |
| Webhook delivery failures | Retry queue + dead-letter queue + manual resync |
| Sprint too large (61 pts) | Split: move S8.9, S8.10 to a maintenance backlog |
