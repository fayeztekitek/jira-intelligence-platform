# Sprint 7 — AI Agent v2 & Chat UI

**Goal**: Interactive chat, French support, auto-recommendations.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 42 (0 completed, 9 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| 🔵 Planned | S7.1 | Chat interface UI | 8 | S6.7 |
| 🔵 Planned | S7.2 | Suggested queries | 3 | S7.1 |
| 🔵 Planned | S7.3 | French language support | 6 | S6.5 |
| 🔵 Planned | S7.4 | Recommendations engine | 5 | S6.4 |
| 🔵 Planned | S7.5 | Compare projects tool (UI) | 4 | S6.4 |
| 🔵 Planned | S7.6 | Executive report generator | 5 | S7.4 |
| 🔵 Planned | S7.7 | Question history sidebar | 3 | S7.1 |
| 🔵 Planned | S7.8 | Token usage tracking | 2 | S6.7 |
| 🔵 Planned | S7.9 | Rate limiting on AI endpoint | 3 | — |

## Example Questions Supported

| Question | Response |
|----------|----------|
| "Compare CORE and MOBILE" | Structured diff table |
| "What changed during the last sprint?" | Sprint scope change report |
| "Which components generate the most bugs?" | Component quality ranking |
| "Give me an executive summary for INFRA" | 3-paragraph report |
| "Quels sont les risques principaux ce mois-ci ?" | French response |
| "What should management focus on this week?" | Ranked recommendations |

## Definition of Done

- [ ] Chat UI renders messages, shows typing indicator, supports markdown tables
- [ ] French questions answered in French
- [ ] "Compare project A and B" returns diff table with trend arrows
- [ ] "What should management focus on?" returns 3+ actionable recommendations
- [ ] Question history persists across sessions
- [ ] AI endpoint rate-limited to 20 req/min/user

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 7"
```

## Risks

| Risk | Mitigation |
|------|------------|
| French translation quality | Use LLM bilingual capability, not translation layer |
| Chat UI complexity | Start with minimal UI, iterate on UX later |
