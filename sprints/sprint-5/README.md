# Sprint 5 — Risk Engine v2 & Export

**Goal**: Enhanced risk scoring, multi-format export

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 46 (0 completed, 10 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| ✅ Done | S5.1 | Expand risk KPIs — blocked critical, aging critical, SLA-close indicators | 5 | — |
| ✅ Done | S5.2 | Multi-period risk scoring — compute for 1w, 1m, 3m (not just 1m) | 4 | S3.1 |
| ✅ Done | S5.3 | Configurable risk weights from settings env var | 2 | — |
| ✅ Done | S5.4 | Risk trend API — history of composite risk + dimensions | 3 | S5.2 |
| ✅ Done | S5.5 | Streaming CSV export with configurable row limit | 4 | — |
| 🔵 Planned | S5.6 | Excel export — `.xlsx` with separate sheets per KPI category | 6 | — |
| 🔵 Planned | S5.7 | PDF executive report — A4 format with risk matrix, KPI summary, alerts | 8 | S5.1 |
| 🔵 Planned | S5.8 | Export button in frontend for all formats | 3 | S5.5-S5.7 |
| 🔵 Planned | S5.9 | API integration tests — all endpoints with seeded mock data | 8 | — |
| 🔵 Planned | S5.10 | Risk health trend chart in Executive page | 3 | S5.4 |

## Definition of Done

- [ ] Risk scored for 1w, 1m, 3m windows
- [ ] CSV exports 100k+ rows in < 5s (streaming)
- [ ] Excel with 5+ sheets, formatted headers
- [ ] PDF executive report with risk matrix, KPI cards, and alert list
- [ ] All 15 API endpoints tested with > 80% coverage

## Technical Notes

- **S5.7**: For PDF, use `weasyprint` with HTML template rendering. Generate executive summary HTML with inline CSS, convert to PDF.
- **S5.6**: Excel sheets: Delivery KPIs, Quality KPIs, Risk KPIs, DQ KPIs, Team KPIs, Issues.

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 5"
```

## Risks

| Risk | Mitigation |
|------|------------|
| openpyxl / weasyprint not installed | Add to `requirements.txt` early in sprint |
| Large CSV export times out | Streaming response + configurable `max_rows` default 10k |
| Risk scoring multi-period changes score values | Keep backward-compatible 1m as default, add periods as opt-in params |
