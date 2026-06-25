# Sprint 4 — Sprint & Release Analytics

**Goal**: Agile KPIs, version tracking, complete trend dashboards.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 48 (0 completed, 10 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| 🔵 Planned | S4.1 | Sprint velocity engine | 8 | S3.1 |
| 🔵 Planned | S4.2 | Sprint scope change from changelog | 4 | S4.1, S3.7 |
| 🔵 Planned | S4.3 | Release KPIs | 6 | — |
| 🔵 Planned | S4.4 | Sprint burndown API endpoint | 4 | S4.1 |
| 🔵 Planned | S4.5 | Release burndown chart (frontend) | 5 | S4.3 |
| 🔵 Planned | S4.6 | Sprint velocity trend chart (frontend) | 3 | S4.1 |
| 🔵 Planned | S4.7 | 90-day trend dashboards | 8 | S3.5 |
| 🔵 Planned | S4.8 | Historical snapshot pipeline | 3 | — |
| 🔵 Planned | S4.9 | Sprint velocity KPI in FactSnapshot | 2 | S4.1 |
| 🔵 Planned | S4.10 | Sprint unit tests | 5 | S4.1 |

## Definition of Done

- [ ] Sprint velocity tracked per project with at least 3 sprints of history
- [ ] Release readiness % visible per version
- [ ] Burndown chart shows actual daily data
- [ ] Sprint scope change (added/removed mid-sprint) detected
- [ ] All sprint KPIs stored in FactSnapshot and queryable via API

## New Frontend Page

"Sprints" tab in sidebar:
- Current sprint burndown chart
- Sprint velocity bar chart (last 10 sprints)
- Sprint health indicators (scope change, carry-over rate)
- Version completion progress bars

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 4"
```

## Risks

| Risk | Mitigation |
|------|------------|
| Blocked on S3.7 (cycle time) | Implement S4.2 with partial data, cycle time optional |
| Sprint data incomplete in Jira | Fall back to customfield-based sprint detection |
