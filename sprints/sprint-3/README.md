# Sprint 3 — KPI Engine & Real Dashboards

**Goal**: Accurate KPIs, optimized engine, real data in all charts.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 42 (6 completed, 5 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| ✅ Completed | S3.1 | KPI engine binary search optimization | 6 | — |
| ✅ Completed | S3.2 | Team KPI prev_values | 3 | S3.1 |
| ✅ Completed | S3.3 | Fix snapshot bugs_resolved | 1 | — |
| ✅ Completed | S3.4 | Fix snapshot purge all tables | 1 | — |
| ✅ Completed | S3.5 | Frontend real chart data | 10 | S3.1 |
| ✅ Completed | S3.6 | Fix N+1 executive summary query | 3 | — |
| ✅ Completed | S3.7 | cycle_time_days from changelog | 5 | S2.6 |
| ✅ Completed | S3.8 | first_response_date from comments | 4 | — |
| ✅ Completed | S3.9 | KPI edge case tests | 4 | S3.1 |
| ✅ Completed | S3.10 | Frontend loading & error UI | 3 | S3.5 |
| 🔵 Planned | S3.11 | Period selector chart history | 2 | S3.5 |

## Definition of Done

- [ ] All 9 delivery KPI cards display real data
- [ ] Created vs Resolved chart shows actual historical trend
- [ ] Bug trend + priority donut show real data
- [ ] Risk trend chart shows actual history
- [ ] Workload distribution shows real assignee counts
- [ ] KPI engine handles 10k+ issues in < 2s
- [ ] All edge case tests pass

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 3"
```

## Risks

| Risk | Mitigation |
|------|------------|
| KPI perf regression | Binary search already deployed; benchmark before/after |
| Chart data mismatch | Compare frontend values to raw SQL queries |
