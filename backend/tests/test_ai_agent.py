"""
Tests for ai_agent module: tools, guardrails, orchestrator, prompts.

Tools require DB access (integration-level), so this test covers the
unit-testable layers: guardrails, orchestrator logic, and prompt rendering.
Tool integration is covered via the existing test_api_integration.py suite.
"""
import pytest
from ai_agent.guardrails import (
    strip_pii, is_off_topic, detect_ambiguity, check_permission, validate_sources,
    enforce_source_citation,
)
from ai_agent.agent import AgentOrchestrator
from ai_agent.prompts import system_prompt, mode_prompt, fallback_prompt, ambiguity_prompt


# ===================================================================
# Guardrails
# ===================================================================

class TestGuardrailsPII:
    def test_strips_email(self):
        assert strip_pii("user@example.com") == "[email redacted]"

    def test_strips_user_id(self):
        assert strip_pii("assigned to u001") == "assigned to [user-id redacted]"

    def test_preserves_jira_key(self):
        result = strip_pii("CORE-42 is critical", preserve_jira_keys=True)
        assert "CORE-42" in result

    def test_no_pii_passthrough(self):
        text = "No sensitive data here"
        assert strip_pii(text) == text


class TestGuardrailsOffTopic:
    def test_rejects_password(self):
        assert is_off_topic("what is the admin password") is True

    def test_rejects_exploit(self):
        assert is_off_topic("how to exploit the system") is True

    def test_allows_kpi_question(self):
        assert is_off_topic("what is the delivery kpi for CORE") is False

    def test_allows_sprint_question(self):
        assert is_off_topic("sprint velocity trend") is False


class TestGuardrailsPermission:
    def test_allows_authorized(self):
        assert check_permission("CORE", ["CORE", "MOBILE"]) is True

    def test_blocks_unauthorized(self):
        assert check_permission("INFRA", ["CORE", "MOBILE"]) is False

    def test_allows_when_no_restrictions(self):
        assert check_permission("ANYTHING", None) is True


class TestGuardrailsSourceCitation:
    def test_validate_sources_found(self):
        assert validate_sources("Data shows risk [Source: get_risk_scores for CORE]") is True

    def test_validate_sources_missing(self):
        assert validate_sources("Data shows risk without citation") is False

    def test_enforce_appends_if_missing(self):
        result = enforce_source_citation("Some claim", "test_tool")
        assert "[Source: test_tool]" in result

    def test_enforce_does_not_duplicate(self):
        text = "Claim [Source: test_tool] here"
        result = enforce_source_citation(text, "test_tool")
        assert result.count("[Source:") == 1


class TestGuardrailsAmbiguity:
    def test_no_ambiguity_single_tool(self):
        result = detect_ambiguity("what is the sprint velocity", "technical")
        assert result is None or len(result) == 0


# ===================================================================
# Orchestrator
# ===================================================================

class TestOrchestratorIntent:
    def setup_method(self):
        self.agent = AgentOrchestrator()

    def test_executive_intent(self):
        intent = self.agent._classify_intent("give me the executive summary")
        assert intent == "executive"

    def test_comparison_intent(self):
        intent = self.agent._classify_intent("compare CORE and MOBILE")
        assert intent == "comparison"

    def test_historical_intent(self):
        intent = self.agent._classify_intent("trend of issues over last 90 days")
        assert intent == "historical"

    def test_technical_intent(self):
        intent = self.agent._classify_intent("sprint velocity for last sprint")
        assert intent == "technical"

    def test_operational_intent(self):
        intent = self.agent._classify_intent("what are the risk scores")
        assert intent == "operational"

    def test_unknown_falls_to_executive(self):
        intent = self.agent._classify_intent("hello")
        assert intent == "executive"


class TestOrchestratorProjectExtraction:
    def setup_method(self):
        self.agent = AgentOrchestrator()

    def test_extract_core(self):
        assert self.agent._extract_project("how is CORE doing") == "CORE"

    def test_extract_mobile(self):
        assert self.agent._extract_project("MOBILE sprint status") == "MOBILE"

    def test_no_project(self):
        assert self.agent._extract_project("what are my kpis") is None


class TestOrchestratorToolSelection:
    def setup_method(self):
        self.agent = AgentOrchestrator()

    def test_select_get_exec_summary(self):
        tool = self.agent._select_tool("executive", "show me summary")
        assert tool == "get_exec_summary"

    def test_select_get_risk_scores(self):
        tool = self.agent._select_tool("operational", "risk score for project")
        assert tool == "get_risk_scores"

    def test_select_get_sprint_analysis(self):
        tool = self.agent._select_tool("technical", "sprint burndown")
        assert tool == "get_sprint_analysis"

    def test_select_compare_projects(self):
        tool = self.agent._select_tool("comparison", "compare projects")
        assert tool == "compare_projects"

    def test_select_get_trend(self):
        tool = self.agent._select_tool("historical", "trend over time")
        assert tool == "get_trend"

    def test_select_get_recommendations(self):
        tool = self.agent._select_tool("operational", "what should I do")
        assert tool == "get_recommendations"

    def test_select_search_issues(self):
        tool = self.agent._select_tool("technical", "find bug issues")
        assert tool == "search_issues"


# ===================================================================
# Format response (no-LLM fallback)
# ===================================================================

class TestFormatResponse:
    def setup_method(self):
        self.agent = AgentOrchestrator()

    def test_exec_summary_format(self):
        result = {
            "generated_at": "2026-06-26",
            "projects": [
                {"name": "Core", "key": "CORE", "risk_level": "low", "composite_risk": 25.0},
                {"name": "Mobile", "key": "MOBILE", "risk_level": "medium", "composite_risk": 50.0},
            ],
            "overall": {"total_open_issues": 100, "total_overdue": 5},
        }
        resp = self.agent._format_response("get_exec_summary", result)
        assert "Executive Summary" in resp
        assert "CORE" in resp
        assert "MOBILE" in resp

    def test_risk_scores_format(self):
        result = {
            "project_key": "CORE", "composite_risk": 45.0, "risk_level": "medium",
            "delivery_risk": 30.0, "quality_risk": 50.0, "compliance_risk": 20.0,
            "operational_risk": 25.0, "risk_drivers": ["driver1"], "recommended_actions": ["action1"],
        }
        resp = self.agent._format_response("get_risk_scores", result)
        assert "Risk Scores" in resp
        assert "CORE" in resp
        assert "Composite: 45.0" in resp

    def test_error_result(self):
        resp = self.agent._format_response("any_tool", {"error": "something broke"})
        assert "error" in resp

    def test_empty_result(self):
        resp = self.agent._format_response("any_tool", None)
        assert "don't have enough data" in resp


# ===================================================================
# Prompt rendering
# ===================================================================

class TestPrompts:
    def test_system_prompt_renders(self):
        result = system_prompt(project_name="TestProj")
        assert "TestProj" in result
        assert "Jira Intelligence Analyst" in result

    def test_mode_prompt_executive(self):
        result = mode_prompt("executive", "How is CORE?", "KPI data", source_label="kpis")
        assert "executive mode" in result
        assert "How is CORE?" in result

    def test_mode_prompt_technical(self):
        result = mode_prompt("technical", "Show numbers", "data", source_label="test")
        assert "technical mode" in result

    def test_mode_prompt_operational(self):
        result = mode_prompt("operational", "What to do", "data", source_label="test")
        assert "operational mode" in result

    def test_fallback_prompt_renders(self):
        result = fallback_prompt("CORE", ["KPIs", "risks"])
        assert "CORE" in result
        assert "KPIs" in result
        assert "risks" in result

    def test_ambiguity_prompt(self):
        interpretations = [
            {"description": "Check risk scores", "data_hint": "risk data"},
            {"description": "Check KPIs", "data_hint": "KPI data"},
        ]
        result = ambiguity_prompt(interpretations)
        assert "multiple ways" in result
        assert "Check risk scores" in result
        assert "Check KPIs" in result
