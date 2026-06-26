"""
ai_agent/agent.py — Core agent orchestrator.

Intent classification → tool dispatch → response generation.
Supports OpenAI and direct tool-calling modes.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

from config import get_settings
from ai_agent.tools import list_tools, call_tool

logger = logging.getLogger(__name__)
settings = get_settings()

INTENTS = {
    "executive": "High-level summary, portfolio health, overall risks",
    "technical": "Sprint velocity, burndown, release status, issue details",
    "operational": "KPIs, trends, overdue items, recommendations",
    "comparison": "Side-by-side project comparison",
    "historical": "Time-series trends, historical data",
}

TOOL_MAP: dict[str, list[str]] = {
    "executive": ["get_exec_summary"],
    "technical": ["get_sprint_analysis", "search_issues"],
    "operational": ["get_project_kpis", "get_risk_scores", "get_recommendations"],
    "comparison": ["compare_projects"],
    "historical": ["get_trend"],
}


class ConversationTurn:
    def __init__(self, question: str, intent: str, tool: str | None,
                 tool_result: Any, response: str, latency_ms: float):
        self.question = question
        self.intent = intent
        self.tool = tool
        self.tool_result = tool_result
        self.response = response
        self.latency_ms = latency_ms


class AgentOrchestrator:
    """Core agent: classify intent → dispatch tool → format response."""

    def __init__(self):
        self.history: list[ConversationTurn] = []
        self._tools_meta = list_tools()

    # ------------------------------------------------------------------
    # Intent classification
    # ------------------------------------------------------------------

    def _classify_intent(self, question: str) -> str:
        q = question.lower()
        if any(w in q for w in ["compare", "versus", "vs", "difference", "both"]):
            return "comparison"
        if any(w in q for w in ["trend", "history", "over time", "last 30", "last 90"]):
            return "historical"
        if any(w in q for w in ["summary", "overview", "health", "portfolio", "executive"]):
            return "executive"
        if any(w in q for w in ["sprint", "velocity", "burndown", "release", "version"]):
            return "technical"
        if any(w in q for w in ["kpi", "risk", "overdue", "recommend", "action", "score"]):
            return "operational"
        return "executive"

    # ------------------------------------------------------------------
    # Parameter extraction (simple regex-free keyword extraction)
    # ------------------------------------------------------------------

    def _extract_project(self, question: str) -> str | None:
        q = question.upper()
        known = ["CORE", "MOBILE", "INFRA"]
        for p in known:
            if p in q:
                return p
        return None

    def _extract_parameters(self, intent: str, question: str) -> dict:
        params: dict = {}
        proj = self._extract_project(question)
        if intent == "executive":
            params["project_key"] = proj or "CORE"
        elif intent == "technical":
            params["project_key"] = proj or "CORE"
            if "sprint" in question.lower():
                params["project_key"] = proj or "CORE"
        elif intent == "operational":
            params["project_key"] = proj or "CORE"
        elif intent == "comparison":
            projs = []
            for p in ["CORE", "MOBILE", "INFRA"]:
                if p in question.upper():
                    projs.append(p)
            params["project_keys"] = ",".join(projs) if projs else "CORE,MOBILE"
        elif intent == "historical":
            params["project_key"] = proj or "CORE"
            params["days"] = 90
            if "30" in question:
                params["days"] = 30
            elif "7" in question:
                params["days"] = 7
        return params

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    def _select_tool(self, intent: str, question: str) -> str | None:
        candidates = TOOL_MAP.get(intent, [])
        if not candidates:
            return None
        if len(candidates) == 1:
            return candidates[0]
        q = question.lower()
        for tool_name in candidates:
            keyword_map = {
                "get_sprint_analysis": ["sprint", "velocity", "burndown"],
                "search_issues": ["issue", "bug", "story", "task", "search", "find"],
                "get_project_kpis": ["kpi", "delivery", "quality"],
                "get_risk_scores": ["risk", "score"],
                "get_recommendations": ["recommend", "action"],
            }
            keywords = keyword_map.get(tool_name, [])
            if any(k in q for k in keywords):
                return tool_name
        return candidates[0]

    # ------------------------------------------------------------------
    # Response generation
    # ------------------------------------------------------------------

    def _generate_response(self, question: str, intent: str,
                           tool_name: str | None, result: Any) -> str:
        if isinstance(result, dict) and "error" in result:
            return f"I encountered an error: {result['error']}"

        if tool_name == "get_exec_summary":
            projs = result.get("projects", [])
            overall = result.get("overall", {})
            lines = [f"**Executive Summary** — {result.get('generated_at', '')}"]
            lines.append(f"- {overall.get('total_open_issues', 0)} open issues, {overall.get('total_overdue', 0)} overdue")
            for p in projs[:3]:
                lines.append(f"- **{p['name']}** ({p['key']}): risk={p.get('risk_level','N/A')}, score={p.get('composite_risk','N/A')}")
            return "\n".join(lines)

        if tool_name == "get_project_kpis":
            kpis = result.get("kpis", [])
            lines = [f"**KPIs for {result['project_key']}**"]
            for k in kpis[:10]:
                trend = k.get("trend", "")
                val = k.get("current_value", "N/A")
                lines.append(f"- {k.get('name','?')}: {val} ({trend})")
            return "\n".join(lines)

        if tool_name == "get_risk_scores":
            return (
                f"**Risk Scores for {result.get('project_key')}**\n"
                f"- Composite: {result.get('composite_risk')} ({result.get('risk_level')})\n"
                f"- Delivery: {result.get('delivery_risk')}, Quality: {result.get('quality_risk')}\n"
                f"- Compliance: {result.get('compliance_risk')}, Operational: {result.get('operational_risk')}\n"
                f"- Drivers: {', '.join(result.get('risk_drivers', []))}\n"
                f"- Actions: {', '.join(result.get('recommended_actions', []))}"
            )

        if tool_name == "search_issues":
            issues = result.get("issues", [])
            lines = [f"**Issues in {result.get('project_key')}** ({len(issues)} found)"]
            for i in issues[:10]:
                lines.append(f"- {i['key']}: {i['summary']} [{i['status']}] {i.get('priority','')}")
            return "\n".join(lines)

        if tool_name == "compare_projects":
            kpis = result.get("kpis", {})
            risks = result.get("risks", {})
            lines = ["**Project Comparison**"]
            for pk in result.get("projects", []):
                r = risks.get(pk, {})
                lines.append(f"\n**{pk}**: risk={r.get('risk_level','N/A')}, score={r.get('composite_risk','N/A')}")
                pk_kpis = kpis.get(pk, {})
                for name, val in list(pk_kpis.items())[:5]:
                    lines.append(f"  - {name}: {val}")
            return "\n".join(lines)

        if tool_name == "get_sprint_analysis":
            sprints = result.get("sprints", [])
            lines = [f"**Sprint Analysis for {result.get('project_key')}** ({result.get('total', 0)} sprints)"]
            for s in sprints[:5]:
                lines.append(
                    f"- {s.get('name','?')} ({s.get('state','')}): "
                    f"{s.get('total_completed',0)}/{s.get('total_committed',0)} completed, "
                    f"velocity={s.get('velocity','N/A')}, predictability={s.get('predictability','N/A')}%"
                )
            return "\n".join(lines)

        if tool_name == "get_trend":
            points = result.get("data_points", [])
            if not points:
                return f"No trend data found for {result.get('kpi_name')} in {result.get('project_key')}."
            latest = points[-1]
            return (
                f"**Trend: {result.get('kpi_name')}** in {result.get('project_key')}\n"
                f"- Latest ({latest.get('date')}): {latest.get('current_value')} "
                f"(trend: {latest.get('trend','N/A')})\n"
                f"- {len(points)} data points over the period"
            )

        if tool_name == "get_recommendations":
            recs = result.get("recommendations", [])
            if not recs:
                return f"No recommendations for {result.get('project_key')}."
            lines = [f"**Recommendations for {result.get('project_key')}**"]
            for r in recs:
                lines.append(f"- {r}")
            return "\n".join(lines)

        return json.dumps(result, indent=2)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def ask(self, question: str) -> dict:
        start = time.monotonic()
        intent = self._classify_intent(question)
        tool_name = self._select_tool(intent, question)
        params = self._extract_parameters(intent, question)

        logger.info("agent_dispatch", intent=intent, tool=tool_name, params=params)

        result = None
        if tool_name:
            result = await call_tool(tool_name, **params)

        response = self._generate_response(question, intent, tool_name, result)
        latency = round((time.monotonic() - start) * 1000, 1)

        turn = ConversationTurn(
            question=question, intent=intent, tool=tool_name,
            tool_result=result, response=response, latency_ms=latency,
        )
        self.history.append(turn)
        if len(self.history) > settings.agent_context_size:
            self.history.pop(0)

        logger.info("agent_response", intent=intent, tool=tool_name,
                     latency_ms=latency, response_len=len(response))

        return {
            "response": response,
            "intent": intent,
            "tool_used": tool_name,
            "latency_ms": latency,
            "context_remaining": settings.agent_context_size - len(self.history),
        }

    def get_history(self) -> list[dict]:
        return [
            {
                "question": t.question,
                "intent": t.intent,
                "tool": t.tool,
                "response": t.response,
                "latency_ms": t.latency_ms,
            }
            for t in self.history
        ]
