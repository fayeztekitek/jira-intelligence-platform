# Sprint 1 — Security & Foundation

**Goal**: Seal critical security gaps, get auth working end-to-end.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 19 (6 tasks already completed during audit phase, 2 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| ✅ Completed | S1.1 | Secure CORS | 1 | — |
| ✅ Completed | S1.2 | JWT auth module | 6 | S1.1 |
| ✅ Completed | S1.3 | Frontend auth | 4 | S1.2 |
| ✅ Completed | S1.4 | Secure default key enforcement | 1 | — |
| ✅ Completed | S1.5 | API key admin user | 2 | S1.2 |
| ✅ Completed | S1.6 | Protect all API routes | 2 | S1.2 |
| ✅ Completed | S1.7 | Auth unit tests | 4 | S1.2 |
| ✅ Completed | S1.8 | Auth integration tests | 3 | S1.7 |

## Definition of Done

- [x] All endpoints return 401 without valid token
- [x] Login with API key returns JWT
- [x] Frontend automatically re-auths on 401
- [x] Production CORS rejects unauthorized origins
- [x] Auth tests pass (38 tests, all green)

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 1"
```

## Risks

| Risk | Mitigation |
|------|------------|
| Auth breaks existing endpoints | All routes already tested manually during audit |
| JWT secret hardcoded in config | `APP_SECRET_KEY` must be set in production |
