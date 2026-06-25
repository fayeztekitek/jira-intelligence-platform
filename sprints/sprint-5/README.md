# Sprint 5 — Risk Engine v2 & Export

**Goal**: Enhanced risk scoring, multi-format export.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 46 (0 completed, 10 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| 🔵 Planned | S5.1 | Expand risk KPIs | 5 | — |
| 🔵 Planned | S5.2 | Multi-period risk scoring | 4 | S3.1 |
| 🔵 Planned | S5.3 | Configurable risk weights | 2 | — |
| 🔵 Planned | S5.4 | Risk trend API | 3 | S5.2 |
| 🔵 Planned | S5.5 | Streaming CSV export | 4 | — |
| 🔵 Planned | S5.6 | Excel export | 6 | — |
| 🔵 Planned | S5.7 | PDF executive report | 8 | S5.1 |
| 🔵 Planned | S5.8 | Export button in frontend | 3 | S5.5–S5.7 |
| 🔵 Planned | S5.9 | API integration tests | 8 | — |
| 🔵 Planned | S5.10 | Risk health trend chart | 3 | S5.4 |

## Definition of Done

- [ ] Risk scored for 1w, 1m, 3m windows
- [ ] CSV exports 100k+ rows in < 5s (streaming)
- [ ] Excel with 5+ sheets, formatted headers
- [ ] PDF executive report with risk matrix, KPI cards, and alert list
- [ ] All 15 API endpoints tested with > 80% coverage

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 5"
```

## Risks

| Risk | Mitigation |
|------|------------|
| PDF library installation (weasyprint) | Requires OS deps: libpango, libcairo. Dockerize if needed |
| Export performance large datasets | Streaming for CSV, chunked writes for Excel |
