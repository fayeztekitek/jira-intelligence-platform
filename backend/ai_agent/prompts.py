"""
ai_agent/prompts.py — Prompt templates for the AI agent.

Uses Jinja2 for template rendering with structured variables.
Templates are organized by agent mode (executive, technical, operational).
Supports English and French languages.
"""

from __future__ import annotations

import re

from jinja2 import Environment, BaseLoader

_ENV = Environment(loader=BaseLoader(), autoescape=False, trim_blocks=True, lstrip_blocks=True)

# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

FRENCH_STOP_WORDS = {
    "le", "la", "les", "des", "pour", "quel", "quels", "quelle", "quelles",
    "comment", "pourquoi", "quand", "où", "combien", "est-ce", "ce", "cet",
    "cette", "ces", "mon", "ton", "son", "ma", "ta", "sa", "mes", "tes",
    "ses", "nos", "vos", "leurs", "je", "tu", "il", "elle", "on", "nous",
    "vous", "ils", "elles", "me", "te", "se", "nous", "vous", "lui",
    "leur", "eux", "elles", "du", "de", "d'", "au", "aux", "un", "une",
    "et", "ou", "donc", "ni", "car", "mais", "que", "qui", "dont", "dans",
    "sur", "avec", "sans", "sous", "entre", "par", "vers", "chez", "pendant",
    "depuis", "jusque", "selon", "suivant", "concernant", "sauf", "voici",
    "voilà", "alors", "ensuite", "enfin", "pourtant", "cependant", "donc",
    "ainsi", "aussi", "également", "surtout", "notamment", "peut-être",
    "presque", "vraiment", "très", "plus", "moins", "autant", "tant",
    "assez", "trop", "peu", "un peu", "beaucoup", "combien", "qu'est-ce",
    "quels sont", "quelles sont", "quel est", "quelle est", "montre-moi",
    "affiche", "donne-moi", "dis-moi", "explique",
}

FRENCH_PROJECT_KEYWORDS = ["projet", "risque", "vélocité", "sprint", "kpi", "livraison"]


def detect_language(text: str) -> str:
    """Detect if text is French or English. Returns 'fr' or 'en'."""
    if not text:
        return "en"
    lower = text.lower()
    words = re.findall(r"[a-zéèêëàâîïôûùç'']+", lower)
    stop_count = sum(1 for w in words if w in FRENCH_STOP_WORDS)
    project_count = sum(1 for w in words if w in FRENCH_PROJECT_KEYWORDS)
    if stop_count >= 2 or project_count >= 1:
        return "fr"
    return "en"


# ---------------------------------------------------------------------------
# French label mapping for tool output
# ---------------------------------------------------------------------------

FRENCH_LABELS: dict[str, str] = {
    "Executive Summary": "Résumé Exécutif",
    "Project": "Projet",
    "Risk": "Risque",
    "Score": "Score",
    "Delivery": "Livraison",
    "Quality": "Qualité",
    "Compliance": "Conformité",
    "Operational": "Opérationnel",
    "Risk Drivers": "Facteurs de Risque",
    "Recommended Actions": "Actions Recommandées",
    "Trend": "Tendance",
    "Open Issues": "Problèmes Ouverts",
    "Overdue": "En Retard",
    "Velocity": "Vélocité",
    "Predictability": "Prévisibilité",
    "Sprint": "Sprint",
    "Completed": "Terminé",
    "Committed": "Planifié",
    "Burndown": "Avancement",
    "Scope Change": "Changement de Périmètre",
    "Release": "Version",
    "Version": "Version",
    "Status": "Statut",
    "Priority": "Priorité",
    "Assignee": "Assigné",
    "Component": "Composant",
    "Category": "Catégorie",
    "Value": "Valeur",
    "Current": "Actuel",
    "Previous": "Précédent",
    "Change": "Variation",
    "Period": "Période",
    "Source": "Source",
    "kpi": "indicateur",
    "risk": "risque",
    "project": "projet",
    "issue": "ticket",
    "bugs": "bugs",
    "story": "histoire",
    "task": "tâche",
    "month": "mois",
    "week": "semaine",
    "day": "jour",
    "score": "score",
    "level": "niveau",
    "high": "élevé",
    "medium": "moyen",
    "low": "faible",
}


def translate_labels(text: str, lang: str) -> str:
    """Replace English labels with French equivalents if lang=='fr'."""
    if lang != "fr":
        return text
    result = text
    for en, fr in FRENCH_LABELS.items():
        result = result.replace(en, fr)
    return result


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a Jira Intelligence Analyst — an AI assistant for {{project_name}}.
You help users understand project health, risks, delivery performance, and team productivity.

## Capabilities
- Answer questions about KPIs, risks, sprints, releases, and issues
- Compare projects side-by-side
- Show historical trends and patterns
- Generate actionable recommendations

## Constraints
- Only answer based on data returned by your tools — never guess or infer
- If data is missing, say "I don't have enough data to answer that" and suggest what data could help
- Always cite your sources using inline brackets: `[Source: KPI name for project X]`
- Keep responses concise and actionable
- If the question is ambiguous, list the possible interpretations and ask for clarification

## Data Sources
- KPI Result: delivery, quality, risk, data-quality metrics
- Risk Score: composite + dimension risk scores with drivers
- Fact Issue: individual issue details
- Sprint Analysis: velocity, burndown, scope changes
- Executive Summary: portfolio-level health
- Trend Data: time-series KPI values
"""

# ---------------------------------------------------------------------------
# Mode templates
# ---------------------------------------------------------------------------

EXECUTIVE_TEMPLATE = """You are answering in **executive mode** — be concise, high-level, and recommendation-focused.

## User Question
{{question}}

## Tool Result
{{tool_result}}

## Instructions
1. Start with a one-sentence bottom-line answer
2. List 2-3 key findings in bullet points
3. End with 1 actionable recommendation
4. Use inline source citations: `[Source: {{source_label}}]`
5. Highlight risk levels and trends that need attention
"""

TECHNICAL_TEMPLATE = """You are answering in **technical mode** — be detailed, data-focused, and precise.

## User Question
{{question}}

## Tool Result
{{tool_result}}

## Instructions
1. Provide specific numbers, percentages, and comparisons
2. Include relevant context (time periods, thresholds, baselines)
3. List data points in structured format
4. Use inline source citations: `[Source: {{source_label}}]`
5. Mention trends and deltas where relevant
"""

OPERATIONAL_TEMPLATE = """You are answering in **operational mode** — be action-oriented and task-focused.

## User Question
{{question}}

## Tool Result
{{tool_result}}

## Instructions
1. Focus on actionable items and next steps
2. Highlight overdue items, blockers, and risks that need immediate attention
3. Group recommendations by priority (high/medium/low)
4. Use inline source citations: `[Source: {{source_label}}]`
5. Keep language direct and prescriptive
"""

# ---------------------------------------------------------------------------
# Ambiguity and fallback prompts
# ---------------------------------------------------------------------------

AMBIGUITY_TEMPLATE = """I found multiple ways to interpret your question:

{% for interpretation in interpretations %}
{{ loop.index }}. **{{ interpretation.description }}**
   - Relevant data: {{ interpretation.data_hint }}
{% endfor %}

Could you clarify which one you are interested in?
"""

# ---------------------------------------------------------------------------
# Suggestions prompt
# ---------------------------------------------------------------------------

SUGGESTIONS_TEMPLATE = """Suggest 4-6 follow-up questions the user could ask based on this context.

## Recent conversation
{% for turn in history %}
- User: {{turn.question}}
- Assistant: {{turn.response[:200]}}...
{% endfor %}

## Current context
{% if recent_tool_result %}
Last data retrieved: {{recent_tool_result[:300]}}
{% endif %}

## Instructions
- Suggest diverse questions covering risks, KPIs, trends, comparisons, and recommendations
- If a project was mentioned, include that project key in relevant suggestions
- Each suggestion should be a complete, specific question (not generic)
- Return as a JSON array of strings only, no other text
"""

# ---------------------------------------------------------------------------
# Static fallback suggestions (used when no LLM key or history available)
# ---------------------------------------------------------------------------

STATIC_SUGGESTIONS: dict[str, list[str]] = {
    "default": [
        "What are the most risky projects?",
        "Show me the executive summary for CORE",
        "What changed during the last sprint?",
        "Which components generate the most bugs?",
        "Compare CORE and MOBILE",
        "What should management focus on this week?",
    ],
    "project": [
        "What are the top risks for {project}?",
        "Show me the KPIs for {project}",
        "What changed during the last sprint in {project}?",
        "Give me an executive summary for {project}",
        "Compare {project} with CORE",
        "What should management focus on in {project}?",
    ],
    "comparison": [
        "What are the key differences in risk between these projects?",
        "Which project has better delivery velocity?",
        "Compare the quality metrics",
        "What trends differentiate these projects?",
        "Show the sprint performance comparison",
        "Which project needs more management attention?",
    ],
    "risk": [
        "What is driving the risk score?",
        "Show me the risk trend over the last 30 days",
        "What actions are recommended to reduce risk?",
        "Which projects have improving or degrading risk?",
        "Compare risk dimensions across all projects",
    ],
    "executive": [
        "What are the portfolio-wide risks?",
        "How does overall project health look?",
        "What trends need immediate attention?",
        "Show me the executive summary for all projects",
        "What is the overall delivery performance?",
    ],
}

# ---------------------------------------------------------------------------
# French templates
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_FR = """Vous êtes un Analyste Jira Intelligence — un assistant IA pour {{project_name}}.
Vous aidez les utilisateurs à comprendre la santé du projet, les risques, la performance de livraison et la productivité des équipes.

## Capacités
- Répondre aux questions sur les KPI, risques, sprints, versions et tickets
- Comparer des projets côte à côte
- Afficher les tendances historiques et les modèles
- Générer des recommandations actionnables

## Contraintes
- Répondez uniquement sur la base des données retournées par vos outils — ne devinez jamais
- Si des données sont manquantes, dites "Je n'ai pas assez de données pour répondre à cela" et suggérez quelles données pourraient aider
- Citez toujours vos sources entre crochets : `[Source : nom KPI pour projet X]`
- Gardez les réponses concises et actionnables
- Si la question est ambiguë, listez les interprétations possibles et demandez une clarification

## Sources de Données
- Résultat KPI : métriques de livraison, qualité, risque, qualité des données
- Score de Risque : scores composites + dimensions avec facteurs de risque
- Ticket : détails individuels des tickets
- Analyse Sprint : vélocité, avancement, changements de périmètre
- Résumé Exécutif : santé au niveau du portefeuille
- Données de Tendance : valeurs KPI chronologiques
"""

EXECUTIVE_TEMPLATE_FR = """Vous répondez en **mode exécutif** — soyez concis, haut niveau, et axé sur les recommandations.

## Question de l'Utilisateur
{{question}}

## Résultat de l'Outil
{{tool_result}}

## Instructions
1. Commencez par une réponse en une phrase
2. Listez 2-3 constatations clés en points
3. Terminez par 1 recommandation actionnable
4. Utilisez des citations de sources : `[Source : {{source_label}}]`
5. Mettez en évidence les niveaux de risque et les tendances qui nécessitent une attention
"""

TECHNICAL_TEMPLATE_FR = """Vous répondez en **mode technique** — soyez détaillé, axé sur les données et précis.

## Question de l'Utilisateur
{{question}}

## Résultat de l'Outil
{{tool_result}}

## Instructions
1. Fournissez des chiffres spécifiques, des pourcentages et des comparaisons
2. Incluez le contexte pertinent (périodes, seuils, références)
3. Listez les points de données dans un format structuré
4. Utilisez des citations de sources : `[Source : {{source_label}}]`
5. Mentionnez les tendances et les écarts le cas échéant
"""

OPERATIONAL_TEMPLATE_FR = """Vous répondez en **mode opérationnel** — soyez orienté action et axé sur les tâches.

## Question de l'Utilisateur
{{question}}

## Résultat de l'Outil
{{tool_result}}

## Instructions
1. Concentrez-vous sur les éléments actionnables et les prochaines étapes
2. Mettez en évidence les éléments en retard, les blocages et les risques nécessitant une attention immédiate
3. Regroupez les recommandations par priorité (haute/moyenne/basse)
4. Utilisez des citations de sources : `[Source : {{source_label}}]`
5. Gardez un langage direct et prescriptif
"""

AMBIGUITY_TEMPLATE_FR = """J'ai trouvé plusieurs façons d'interpréter votre question :

{% for interpretation in interpretations %}
{{ loop.index }}. **{{ interpretation.description }}**
   - Données pertinentes : {{ interpretation.data_hint }}
{% endfor %}

Pourriez-vous clarifier celle qui vous intéresse ?
"""

FALLBACK_TEMPLATE_FR = """Je n'ai pas assez de données pour répondre à cette question sur **{{project_key}}**.

**Ce que je peux vous dire :**
{% if available_data %}
{% for item in available_data %}
- Je peux répondre aux questions sur {{item}}
{% endfor %}
{% endif %}

**Suggestions :**
- Essayez de reformuler votre question
- Demandez des KPI, risques ou sprints spécifiques
- Comparez des projets en mentionnant deux clés de projet
"""

SUGGESTIONS_TEMPLATE_FR = """Suggérez 4 à 6 questions de suivi que l'utilisateur pourrait poser en fonction de ce contexte.

## Conversation récente
{% for turn in history %}
- Utilisateur : {{turn.question}}
- Assistant : {{turn.response[:200]}}...
{% endfor %}

## Contexte actuel
{% if recent_tool_result %}
Dernières données récupérées : {{recent_tool_result[:300]}}
{% endif %}

## Instructions
- Suggérez des questions diverses couvrant les risques, KPI, tendances, comparaisons et recommandations
- Si un projet a été mentionné, incluez cette clé de projet dans les suggestions pertinentes
- Chaque suggestion doit être une question complète et spécifique (pas générique)
- Retournez uniquement un tableau JSON de chaînes, sans autre texte
"""

STATIC_SUGGESTIONS_FR: dict[str, list[str]] = {
    "default": [
        "Quels sont les projets les plus risqués ?",
        "Montrez-moi le résumé exécutif pour CORE",
        "Qu'est-ce qui a changé pendant le dernier sprint ?",
        "Quels composants génèrent le plus de bugs ?",
        "Comparez CORE et MOBILE",
        "Sur quoi la direction devrait-elle se concentrer cette semaine ?",
    ],
    "project": [
        "Quels sont les principaux risques pour {project} ?",
        "Montrez-moi les KPI pour {project}",
        "Qu'est-ce qui a changé pendant le dernier sprint dans {project} ?",
        "Donnez-moi un résumé exécutif pour {project}",
        "Comparez {project} avec CORE",
        "Sur quoi la direction devrait-elle se concentrer dans {project} ?",
    ],
    "comparison": [
        "Quelles sont les principales différences de risque entre ces projets ?",
        "Quel projet a une meilleure vélocité de livraison ?",
        "Comparez les métriques de qualité",
        "Quelles tendances différencient ces projets ?",
        "Montrez la comparaison des performances sprint",
        "Quel projet nécessite plus d'attention de la direction ?",
    ],
    "risk": [
        "Qu'est-ce qui motive le score de risque ?",
        "Montrez-moi la tendance des risques sur les 30 derniers jours",
        "Quelles actions sont recommandées pour réduire le risque ?",
        "Quels projets ont un risque qui s'améliore ou se dégrade ?",
        "Comparez les dimensions de risque pour tous les projets",
    ],
    "executive": [
        "Quels sont les risques à l'échelle du portefeuille ?",
        "Comment se porte la santé globale du projet ?",
        "Quelles tendances nécessitent une attention immédiate ?",
        "Montrez-moi le résumé exécutif pour tous les projets",
        "Quelle est la performance globale de livraison ?",
    ],
}

# ---------------------------------------------------------------------------
# Render helper
# ---------------------------------------------------------------------------

def _render(template_str: str, **kwargs) -> str:
    return _ENV.from_string(template_str).render(**kwargs)

# ---------------------------------------------------------------------------
# Prompt helpers with language support
# ---------------------------------------------------------------------------

FR_MODE_TEMPLATES = {
    "executive": EXECUTIVE_TEMPLATE_FR,
    "technical": TECHNICAL_TEMPLATE_FR,
    "operational": OPERATIONAL_TEMPLATE_FR,
}


def system_prompt(project_name: str = "your project", lang: str = "en") -> str:
    if lang == "fr":
        return _render(SYSTEM_PROMPT_FR, project_name=project_name)
    return _render(SYSTEM_PROMPT, project_name=project_name)


def mode_prompt(mode: str, question: str, tool_result: str, source_label: str = "", lang: str = "en") -> str:
    templates = FR_MODE_TEMPLATES if lang == "fr" else {
        "executive": EXECUTIVE_TEMPLATE,
        "technical": TECHNICAL_TEMPLATE,
        "operational": OPERATIONAL_TEMPLATE,
    }
    t = templates.get(mode, EXECUTIVE_TEMPLATE if lang != "fr" else EXECUTIVE_TEMPLATE_FR)
    return _render(t, question=question, tool_result=tool_result, source_label=source_label)


def ambiguity_prompt(interpretations: list[dict], lang: str = "en") -> str:
    t = AMBIGUITY_TEMPLATE_FR if lang == "fr" else AMBIGUITY_TEMPLATE
    return _render(t, interpretations=interpretations)


def fallback_prompt(project_key: str, available_data: list[str] | None = None, lang: str = "en") -> str:
    t = FALLBACK_TEMPLATE_FR if lang == "fr" else FALLBACK_TEMPLATE
    return _render(t, project_key=project_key, available_data=available_data or [])


def suggestions_prompt(history: list[dict], recent_tool_result: str | None = None, lang: str = "en") -> str:
    t = SUGGESTIONS_TEMPLATE_FR if lang == "fr" else SUGGESTIONS_TEMPLATE
    return _render(t, history=history, recent_tool_result=recent_tool_result)


def static_suggestions(context_key: str = "default", project: str | None = None, lang: str = "en") -> list[str]:
    pool = STATIC_SUGGESTIONS_FR if lang == "fr" else STATIC_SUGGESTIONS
    base = pool.get(context_key, pool["default"])
    if project and context_key in ("project", "default"):
        return [s.replace("{project}", project) for s in base]
    return list(base)


FALLBACK_TEMPLATE = """I don't have enough data to answer that question about **{{project_key}}**.

**What I can tell you:**
{% if available_data %}
{% for item in available_data %}
- I can answer questions about {{item}}
{% endfor %}
{% endif %}

**Suggestions:**
- Try rephrasing your question
- Ask about specific KPIs, risks, or sprints
- Compare projects by mentioning two project keys
"""


