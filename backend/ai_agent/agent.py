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
from ai_agent.prompts import (
    system_prompt, mode_prompt, fallback_prompt, ambiguity_prompt,
    STATIC_SUGGESTIONS, SUGGESTIONS_TEMPLATE, _render,
    detect_language, translate_labels, static_suggestions,
)
from ai_agent.guardrails import (
    strip_pii, is_off_topic, detect_ambiguity, check_permission, enforce_source_citation,
)

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
                "get_recommendations": ["recommend", "action", "should", "suggest"],
            }
            keywords = keyword_map.get(tool_name, [])
            if any(k in q for k in keywords):
                return tool_name
        return candidates[0]

    # ------------------------------------------------------------------
    # Response generation
    # ------------------------------------------------------------------

    async def _generate_response(self, question: str, intent: str,
                                 tool_name: str | None, result: Any, lang: str = "en") -> str:
        error_msg = {"en": "I encountered an error", "fr": "J'ai rencontré une erreur"}
        if isinstance(result, dict) and "error" in result:
            return f"{error_msg.get(lang, error_msg['en'])}: {result['error']}"

        # Detect report generation requests
        q = question.lower()
        is_report = any(w in q for w in ["executive report", "generate report", "full report",
                                          "structured report", "briefing", "weekly report"])
        if is_report and tool_name == "get_exec_summary":
            proj = result.get("project_key", "CORE")
            return await self.generate_executive_report(proj, lang=lang)

        # Use LLM if configured, else fallback to built-in formatters
        if settings.llm_api_key:
            return self._llm_generate(question, intent, tool_name, result, lang=lang)

        response = self._format_response(tool_name, result, lang=lang)
        return translate_labels(response, lang)

    def _llm_generate(self, question: str, intent: str,
                      tool_name: str | None, result: Any, lang: str = "en") -> str:
        mode = "executive" if intent in ("executive", "comparison") else \
               "technical" if intent in ("technical", "historical") else \
               "operational"
        source_label = f"{tool_name} for {result.get('project_key', '?')}" if tool_name else "data"
        if lang == "fr":
            source_label = f"{tool_name} pour {result.get('project_key', '?')}" if tool_name else "données"
        result_str = json.dumps(result, indent=2, default=str) if result else "No data returned."

        messages = [
            {"role": "system", "content": system_prompt(lang=lang)},
            {"role": "user", "content": mode_prompt(mode, question, result_str, source_label=source_label, lang=lang)},
        ]

        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.llm_api_key)
            resp = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                max_tokens=settings.llm_max_tokens,
                temperature=settings.llm_temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning("llm_failed", error=str(e))
            return self._format_response(tool_name, result, lang=lang)

    @staticmethod
    def _format_response(tool_name: str | None, result: Any, lang: str = "en") -> str:
        no_data_msg = {"en": "I don't have enough data to answer that.",
                       "fr": "Je n'ai pas assez de données pour répondre à cela."}
        if not result:
            return no_data_msg.get(lang, no_data_msg["en"])

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
            projects = result.get("projects", [])
            lines = ["**Project Comparison**\n"]

            if risks:
                lines.append("| Dimension | " + " | ".join(f"{p}" for p in projects) + " |")
                lines.append("|" + "|".join("---" for _ in range(len(projects) + 1)) + "|")
                dims = ["risk_level", "composite_risk", "delivery_risk", "quality_risk"]
                dim_labels = ["Risk Level", "Composite Risk", "Delivery Risk", "Quality Risk"]
                for dl, dim in zip(dim_labels, dims):
                    row = f"| **{dl}** "
                    for pk in projects:
                        r = risks.get(pk, {})
                        val = r.get(dim, "N/A")
                        if isinstance(val, (int, float)):
                            val = f"{val:.2f}"
                        arrow = ""
                        if dim != "risk_level" and isinstance(val, str):
                            try:
                                v = float(val)
                                if dim == "composite_risk":
                                    arrow = " ↓" if v > 0.6 else " ↑" if v <= 0.3 else " →"
                                else:
                                    arrow = " ↓" if v > 0.6 else " ↑" if v <= 0.3 else " →"
                            except ValueError:
                                pass
                        row += f" | {val}{arrow}"
                    lines.append(row + " |")
                lines.append("")

            all_kpi_names = sorted({k for pk in projects for k in kpis.get(pk, {})})
            if all_kpi_names:
                lines.append(f"| KPI | {' | '.join(f'{p}' for p in projects)} |")
                lines.append("|" + "|".join("---" for _ in range(len(projects) + 1)) + "|")
                for name in all_kpi_names[:15]:
                    row = f"| **{name}** "
                    for pk in projects:
                        pk_kpis = kpis.get(pk, {})
                        val = pk_kpis.get(name)
                        if val is None:
                            row += " | —"
                        else:
                            row += f" | {val:.2f}" if isinstance(val, float) else f" | {val}"
                    lines.append(row + " |")

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

        source_label = f"{tool_name} for {result.get('project_key', '?')}" if tool_name else "data"
        response = (f"[Source: {source_label}]\n" + json.dumps(result, indent=2, default=str))
        return translate_labels(response, lang)

    # ------------------------------------------------------------------
    # Language detection
    # ------------------------------------------------------------------

    def _detect_language(self, question: str) -> str:
        lang = detect_language(question)
        return lang

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def ask(self, question: str, allowed_projects: list[str] | None = None) -> dict:
        start = time.monotonic()

        lang = self._detect_language(question)
        off_topic_msg = {
            "en": "I'm a Jira analytics assistant and can only answer questions about project data, KPIs, risks, and sprints.",
            "fr": "Je suis un assistant d'analyse Jira et je peux uniquement répondre aux questions sur les données de projet, les KPI, les risques et les sprints.",
        }

        # Guard 1: content safety
        if is_off_topic(question):
            return {
                "response": off_topic_msg.get(lang, off_topic_msg["en"]),
                "intent": "rejected",
                "tool_used": None,
                "latency_ms": 0,
                "context_remaining": settings.agent_context_size - len(self.history),
            }

        intent = self._classify_intent(question)

        # Guard 2: ambiguity detection
        ambiguous = detect_ambiguity(question, intent)
        if ambiguous:
            response = ambiguity_prompt(ambiguous, lang=lang)
            latency = round((time.monotonic() - start) * 1000, 1)
            return {
                "response": response,
                "intent": intent,
                "tool_used": None,
                "latency_ms": latency,
                "context_remaining": settings.agent_context_size - len(self.history),
            }

        tool_name = self._select_tool(intent, question)
        params = self._extract_parameters(intent, question)

        # Guard 3: permission check
        proj = params.get("project_key") or params.get("project_keys", "").split(",")[0]
        no_access_msg = {
            "en": f"I'm sorry, you don't have access to project **{proj}**.",
            "fr": f"Désolé, vous n'avez pas accès au projet **{proj}**.",
        }
        if proj and allowed_projects is not None and not check_permission(proj, allowed_projects):
            return {
                "response": no_access_msg.get(lang, no_access_msg["en"]),
                "intent": intent,
                "tool_used": None,
                "latency_ms": round((time.monotonic() - start) * 1000, 1),
                "context_remaining": settings.agent_context_size - len(self.history),
            }

        logger.info("agent_dispatch", intent=intent, tool=tool_name, params=params, lang=lang)

        result = None
        if tool_name:
            result = await call_tool(tool_name, **params)

        response = await self._generate_response(question, intent, tool_name, result, lang=lang)

        # Guard 4: source citation enforcement
        default_source = f"{tool_name} for {proj}" if tool_name and proj else "data"
        if lang == "fr":
            default_source = f"{tool_name} pour {proj}" if tool_name and proj else "données"
        response = enforce_source_citation(response, default_source)

        # Guard 5: PII redaction
        response = strip_pii(response)

        latency = round((time.monotonic() - start) * 1000, 1)

        turn = ConversationTurn(
            question=question, intent=intent, tool=tool_name,
            tool_result=result, response=response, latency_ms=latency,
        )
        self.history.append(turn)
        if len(self.history) > settings.agent_context_size:
            self.history.pop(0)

        logger.info("agent_response", intent=intent, tool=tool_name,
                     latency_ms=latency, response_len=len(response), lang=lang)

        return {
            "response": response,
            "intent": intent,
            "tool_used": tool_name,
            "latency_ms": latency,
            "context_remaining": settings.agent_context_size - len(self.history),
        }

    # ------------------------------------------------------------------
    # Suggested questions
    # ------------------------------------------------------------------

    def suggest_questions(self, context: dict | None = None) -> list[str]:
        history = self.get_history()
        recent_intent = history[-1].get("intent") if history else None
        recent_tool = history[-1].get("tool") if history else None
        recent_tool_result = None
        if self.history and self.history[-1].tool_result:
            recent_tool_result = str(self.history[-1].tool_result)

        lang = "en"
        if history:
            lang = detect_language(history[-1].get("question", ""))

        # Try LLM-based suggestions if configured
        if settings.llm_api_key:
            try:
                return self._llm_suggest(history, recent_tool_result, lang=lang)
            except Exception:
                pass

        # Fallback to static context-aware suggestions
        return self._static_suggestions(recent_intent, recent_tool, context, lang=lang)

    def _llm_suggest(self, history: list[dict], recent_tool_result: str | None, lang: str = "en") -> list[str]:
        from openai import OpenAI
        from ai_agent.prompts import suggestions_prompt
        prompt = suggestions_prompt(history, recent_tool_result, lang=lang)
        client = OpenAI(api_key=settings.llm_api_key)
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": "You are a Jira analytics assistant suggesting follow-up questions."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=300,
            temperature=0.7,
        )
        content = resp.choices[0].message.content or "[]"
        import json
        suggestions = json.loads(content)
        return suggestions[:6] if isinstance(suggestions, list) else static_suggestions("default", lang=lang)

    @staticmethod
    def _static_suggestions(recent_intent: str | None, recent_tool: str | None,
                            context: dict | None, lang: str = "en") -> list[str]:
        ctx = context or {}
        context_key = "default"

        if recent_intent == "comparison":
            context_key = "comparison"
        elif recent_intent == "executive":
            context_key = "executive"
        elif recent_intent == "operational" and recent_tool == "get_risk_scores":
            context_key = "risk"
        elif ctx.get("project"):
            context_key = "project"

        return static_suggestions(context_key, project=ctx.get("project"), lang=lang)

    # ------------------------------------------------------------------
    # Executive report generation
    # ------------------------------------------------------------------

    async def generate_executive_report(self, project_key: str, lang: str = "en") -> str:
        """Generate a 3-section executive report: summary, risks, actions."""
        from ai_agent.tools import call_tool

        summary_data = await call_tool("get_exec_summary", project_key=project_key)
        risk_data = await call_tool("get_risk_scores", project_key=project_key)
        rec_data = await call_tool("get_recommendations", project_key=project_key)

        section_titles = {
            "en": ["Executive Summary", "Risk Assessment", "Recommended Actions"],
            "fr": ["Résumé Exécutif", "Évaluation des Risques", "Actions Recommandées"],
        }
        titles = section_titles.get(lang, section_titles["en"])

        # Section 1: Summary
        metrics = summary_data.get("metrics", {})
        lines = [f"# {titles[0]} — **{project_key}**\n"]
        lines.append(f"*Generated: {summary_data.get('generated_at', 'N/A')}*\n")

        total = summary_data.get("total_issues", 0)
        risk_level = summary_data.get("risk_level", "unknown")
        lines.append(f"**Portfolio Overview**: {project_key} has **{total}** tracked issues. "
                     f"Overall risk level is **{risk_level}**.\n")

        if metrics:
            lines.append("**Key Metrics**:")
            for name, val in metrics.items():
                if val is not None:
                    lines.append(f"- **{name.replace('_', ' ').title()}**: {val:.2f}" if isinstance(val, float) else f"- **{name.replace('_', ' ').title()}**: {val}")
            lines.append("")

        # Section 2: Risks
        lines.extend([f"## {titles[1]}\n"])
        if "error" not in risk_data:
            lines.append(f"- **Composite Risk**: {risk_data.get('composite_risk', 'N/A')} "
                         f"({risk_data.get('risk_level', 'N/A')})")
            for dim in ["delivery_risk", "quality_risk", "compliance_risk", "operational_risk"]:
                val = risk_data.get(dim)
                if val is not None:
                    label = dim.replace("_", " ").title()
                    lines.append(f"- **{label}**: {val:.2f}" if isinstance(val, float) else f"- **{label}**: {val}")
            drivers = risk_data.get("risk_drivers", [])
            if drivers:
                lines.append(f"\n**Top Drivers**: {', '.join(drivers[:5])}")
            lines.append("")
        else:
            lines.append("No risk data available.\n")

        # Section 3: Actions
        lines.extend([f"## {titles[2]}\n"])
        recs = rec_data.get("recommendations", [])
        if recs:
            for i, rec in enumerate(recs[:5], 1):
                priority = rec.get("priority", "medium")
                p_icon = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "🟡")
                lines.append(f"{i}. {p_icon} **{rec.get('title', 'Action')}**")
                lines.append(f"   - {rec.get('description', '')}")
                area = rec.get("impact_area", "").replace("_", " ").title()
                lines.append(f"   - Impact: {area} | Priority: **{priority.upper()}**")
            lines.append("")
        else:
            lines.append("No recommendations available.\n")

        return translate_labels("\n".join(lines), lang)

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
