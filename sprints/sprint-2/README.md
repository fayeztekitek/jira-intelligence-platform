# Sprint 2 — Jira Connection & Data Quality

**Goal**: Reliable extraction, configurable fields, database migrations.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 38 (6 completed, 4 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| ✅ Completed | S2.1 | Configurable customfield IDs | 3 | — |
| ✅ Completed | S2.2 | Alembic migration infrastructure | 6 | — |
| ✅ Completed | S2.3 | Fix get_users pagination | 2 | — |
| ✅ Completed | S2.4 | Fix Retry-After crash | 1 | — |
| ✅ Completed | S2.5 | Remove redundant context manager | 1 | — |
| ✅ Completed | S2.6 | Populate lead_time_days, cycle_time_days | 2 | — |
| ✅ Completed | S2.7 | Auto-discover customfield IDs | 5 | — |
| ⏭️ Deferred | S2.8 | OAuth2 flow implementation | 8 | — |
| ✅ Completed | S2.9 | Extractor unit tests | 8 | S2.1–S2.6 |
| ✅ Completed | S2.10 | FactTransition index | 1 | S2.2 |

## Definition of Done

- [ ] Extraction works against any Jira instance (no hardcoded field IDs)
- [ ] `alembic upgrade head` creates full schema
- [x] OAuth2 flow deferred — Jira Data Center v9.12.23 uses PAT/API tokens
- [ ] All extractor tests pass
- [ ] Field discovery endpoint returns mapped fields

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 2"
alembic downgrade -1
```

## Risks

| Risk | Mitigation |
|------|------------|
| Customfield IDs differ per Jira | Auto-discovery via field API (S2.7), fallback to env vars |
| OAuth2 complexity high | Deferable to later sprint if blocked |
