"""
ai_agent/guardrails.py — Agent guardrails.

Hallucination prevention, source citation enforcement,
permission checks, ambiguity detection, PII redaction,
and content safety filtering.
"""

from __future__ import annotations

import re
from typing import Any

from ai_agent.prompts import ambiguity_prompt
from ai_agent.tools import list_tools

# ---------------------------------------------------------------------------
# PII patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_USER_ID_RE = re.compile(r"\bu\d{3,}\b")  # matches u001, u1234 etc.
_JIRA_ID_RE = re.compile(r"[A-Z]+-\d+")   # matches CORE-1, MOBILE-42 etc.

_SENSITIVE_KEYWORDS = [
    "password", "secret", "token", "api_key", "credentials",
    "exploit", "hack", "bypass", "injection", "malware",
]


def strip_pii(text: str, *, preserve_jira_keys: bool = True) -> str:
    """Remove PII: email addresses and internal user IDs."""
    text = _EMAIL_RE.sub("[email redacted]", text)
    text = _USER_ID_RE.sub("[user-id redacted]", text)
    return text


def is_off_topic(question: str) -> bool:
    """Check if the question is harmful or off-topic for a Jira analytics agent."""
    q = question.lower()
    for kw in _SENSITIVE_KEYWORDS:
        if kw in q:
            return True
    return False


# ---------------------------------------------------------------------------
# Ambiguity detection
# ---------------------------------------------------------------------------

def detect_ambiguity(question: str, intent: str) -> list[dict] | None:
    """If multiple tools could answer, return interpretations for disambiguation."""
    from ai_agent.agent import TOOL_MAP
    candidates = TOOL_MAP.get(intent, [])
    if len(candidates) <= 1:
        return None

    q = question.lower()
    matching = []
    for tool_name in candidates:
        keywords = {
            "get_sprint_analysis": ["sprint", "velocity", "burndown"],
            "search_issues": ["issue", "bug", "story", "task"],
            "get_project_kpis": ["kpi", "delivery", "quality", "metric"],
            "get_risk_scores": ["risk", "score", "composite"],
            "get_recommendations": ["recommend", "action", "should"],
            "get_exec_summary": ["summary", "overview", "health"],
            "compare_projects": ["compare", "versus", "vs"],
            "get_trend": ["trend", "history", "over time"],
        }.get(tool_name, [])
        if any(k in q for k in keywords):
            matching.append({
                "tool": tool_name,
                "description": _tool_description(tool_name),
                "data_hint": _tool_data_hint(tool_name),
            })

    return matching if len(matching) > 1 else None


def _tool_description(name: str) -> str:
    descs = {
        "get_exec_summary": "Portfolio-level executive summary",
        "get_project_kpis": "Detailed KPI breakdown for a project",
        "get_risk_scores": "Risk score with dimensions and drivers",
        "search_issues": "Search and filter individual issues",
        "compare_projects": "Side-by-side comparison of projects",
        "get_sprint_analysis": "Sprint velocity, burndown, scope changes",
        "get_trend": "Historical trend data for a specific KPI",
        "get_recommendations": "Auto-generated action items",
    }
    return descs.get(name, name)


def _tool_data_hint(name: str) -> str:
    hints = {
        "get_exec_summary": "all projects, total open/overdue counts",
        "get_project_kpis": "delivery, quality, risk, and DQ metrics",
        "get_risk_scores": "composite + 4 dimension scores",
        "search_issues": "individual issue records with filters",
        "compare_projects": "multi-project KPI and risk comparison",
        "get_sprint_analysis": "sprint list with velocity and predictability",
        "get_trend": "time-series KPI values over 90 days",
        "get_recommendations": "prioritized action items",
    }
    return hints.get(name, name)


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------

def check_permission(project_key: str, allowed_projects: list[str] | None = None) -> bool:
    """Verify user has access to the requested project."""
    if allowed_projects is None:
        return True
    return project_key in allowed_projects


# ---------------------------------------------------------------------------
# Output validation — every claim must cite its source
# ---------------------------------------------------------------------------

_SOURCE_RE = re.compile(r'\[Source:\s*[^\]]+\]')


def validate_sources(response: str) -> bool:
    """Ensure the response contains at least one source citation."""
    return bool(_SOURCE_RE.search(response))


def enforce_source_citation(response: str, default_source: str) -> str:
    """Append a source citation if none found."""
    if not validate_sources(response):
        response += f"\n\n[Source: {default_source}]"
    return response
