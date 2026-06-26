# Sprint 6 — AI Agent v1

**Goal**: Foundation AI agent with RAG and tool calling.

**Cadence**: 1 week (Mon–Fri)

**Total Points**: 46 (46 completed, 0 pending)

## Stories

| Status | ID | Title | Hours | Dependencies |
|--------|----|-------|-------|-------------|
| ✅ Done | S6.1 | pgvector setup | 4 | S2.2 |
| ✅ Done | S6.2 | Embedding pipeline | 6 | S6.1 |
| ✅ Done | S6.3 | Agent orchestrator | 10 | S6.4 |
| ✅ Done | S6.4 | Tool registry (8 tools) | 8 | S5.1, S4.1 |
| ✅ Done | S6.5 | Prompt templates | 4 | S6.3 |
| ✅ Done | S6.6 | Guardrails | 5 | S6.3 |
| ✅ Done | S6.7 | AI chat API endpoint | 3 | S6.3 |
| ✅ Done | S6.8 | AI agent unit tests | 6 | S6.3–S6.6 |

## Definition of Done

- [ ] `POST /api/ai/ask` returns structured answers with source citations
- [ ] "What are the most risky projects?" returns real risk scores
- [ ] "Explain why the backlog increased" returns trend data
- [ ] Agent refuses with "No data" when tool returns empty
- [ ] Agent asks for clarification on ambiguous questions
- [ ] All 8 tools work correctly

## Rollback

```bash
git revert HEAD~..HEAD --no-commit
git commit -m "rollback: sprint 6"
alembic downgrade -1  # if pgvector was added
```

## Risks

| Risk | Mitigation |
|------|------------|
| pgvector not available on host | Fallback to Python cosine similarity (scikit-learn) |
| LLM API cost | Use local Ollama for dev, set token limits in prod |
| Agent hallucination | Strict tool-only responses with source attribution; guardrails reject unknowns |
