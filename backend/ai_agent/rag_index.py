"""
ai_agent/rag_index.py — Embedding pipeline.

Generates and stores embeddings for project metadata, issue summaries,
and KPI definitions using sentence-transformers (all-MiniLM-L6-v2, 384-dim).

Embeddings are stored as JSON float arrays in the `embedding` Text column
of each respective table (dim_project, fact_issue, kpi_result).

Usage:
    from ai_agent.rag_index import EmbeddingPipeline
    pipeline = EmbeddingPipeline()
    await pipeline.run_incremental()   # only un-embedded records
    await pipeline.run_full()          # all records
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, null, or_
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from storage.database import get_db
from storage.models import DimProject, FactIssue, KPIResult

logger = logging.getLogger(__name__)
settings = get_settings()


# ---------------------------------------------------------------------------
# Lazy-loaded model singleton (sentence-transformers)
# ---------------------------------------------------------------------------
_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        name = settings.embedding_model
        logger.info("loading_embedding_model", model=name)
        _model = SentenceTransformer(name)
    return _model


def _encode(texts: list[str]) -> list[list[float]]:
    """Encode a batch of texts into 384-dim float vectors."""
    model = _get_model()
    embeddings = model.encode(texts, batch_size=settings.embedding_batch_size, show_progress_bar=False)
    return [e.tolist() for e in embeddings]


def _text_to_json(v: list[float]) -> str:
    return json.dumps(v, ensure_ascii=False)


def _extract_text(row: Any) -> str:
    texts = []
    if hasattr(row, "name") and row.name:
        texts.append(row.name)
    if hasattr(row, "description") and row.description:
        texts.append(row.description)
    if hasattr(row, "jira_key") and row.jira_key:
        texts.insert(0, row.jira_key)
    if hasattr(row, "summary") and row.summary:
        texts.append(row.summary)
    if hasattr(row, "kpi_name") and row.kpi_name:
        texts.append(row.kpi_name)
    if hasattr(row, "formula") and row.formula:
        texts.append(row.formula)
    if hasattr(row, "interpretation") and row.interpretation:
        texts.append(row.interpretation)
    return " | ".join(t for t in texts if t)


# ---------------------------------------------------------------------------
# Batch embedder per table
# ---------------------------------------------------------------------------

async def _embed_projects(session: AsyncSession, *, incremental: bool = True) -> int:
    q = select(DimProject)
    if incremental:
        q = q.where(DimProject.embedding.is_(None))
    rows = (await session.execute(q)).scalars().all()
    if not rows:
        return 0
    texts = [_extract_text(r) for r in rows]
    vectors = _encode(texts)
    for row, vec in zip(rows, vectors):
        row.embedding = _text_to_json(vec)
    await session.commit()
    logger.info("embedded_projects", count=len(rows), incremental=incremental)
    return len(rows)


async def _embed_issues(session: AsyncSession, *, incremental: bool = True) -> int:
    q = select(FactIssue)
    if incremental:
        q = q.where(FactIssue.embedding.is_(None))
    rows = (await session.execute(q)).scalars().all()
    if not rows:
        return 0
    texts = [_extract_text(r) for r in rows]
    vectors = _encode(texts)
    for row, vec in zip(rows, vectors):
        row.embedding = _text_to_json(vec)
    await session.commit()
    logger.info("embedded_issues", count=len(rows), incremental=incremental)
    return len(rows)


async def _embed_kpis(session: AsyncSession, *, incremental: bool = True) -> int:
    q = select(KPIResult)
    if incremental:
        q = q.where(KPIResult.embedding.is_(None))
    rows = (await session.execute(q)).scalars().all()
    if not rows:
        return 0
    texts = [_extract_text(r) for r in rows]
    vectors = _encode(texts)
    for row, vec in zip(rows, vectors):
        row.embedding = _text_to_json(vec)
    await session.commit()
    logger.info("embedded_kpis", count=len(rows), incremental=incremental)
    return len(rows)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class EmbeddingPipeline:
    """Embedding pipeline for RAG indexing."""

    @staticmethod
    async def run_incremental() -> dict[str, int]:
        """Embed only records where `embedding` is NULL."""
        counts = {}
        async with get_db() as session:
            counts["projects"] = await _embed_projects(session, incremental=True)
        async with get_db() as session:
            counts["issues"] = await _embed_issues(session, incremental=True)
        async with get_db() as session:
            counts["kpis"] = await _embed_kpis(session, incremental=True)
        total = sum(counts.values())
        logger.info("embedding_pipeline_done", mode="incremental", counts=counts, total=total)
        return counts

    @staticmethod
    async def run_full() -> dict[str, int]:
        """Re-embed all records."""
        counts = {}
        async with get_db() as session:
            counts["projects"] = await _embed_projects(session, incremental=False)
        async with get_db() as session:
            counts["issues"] = await _embed_issues(session, incremental=False)
        async with get_db() as session:
            counts["kpis"] = await _embed_kpis(session, incremental=False)
        total = sum(counts.values())
        logger.info("embedding_pipeline_done", mode="full", counts=counts, total=total)
        return counts
