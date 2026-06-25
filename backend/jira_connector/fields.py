"""
jira_connector/fields.py — Jira customfield auto-discovery.

Queries /rest/api/3/field to discover customfield IDs by name pattern.
Respects explicit env var overrides when set.
"""

from __future__ import annotations

import structlog

from config import get_settings

logger = structlog.get_logger(__name__)

# Known field name patterns: (display_name_substring, logical_key)
KNOWN_FIELDS: list[tuple[str, str]] = [
    ("Epic Link", "epic_link"),
    ("Story Points", "story_points"),
    ("Sprint", "sprint"),
]

# Env var names that override auto-discovery
ENV_VAR_MAP: dict[str, str] = {
    "epic_link": "jira_field_epic_link",
    "story_points": "jira_field_story_points",
    "sprint": "jira_field_sprint",
}


class FieldDiscoverer:
    """
    Discovers customfield IDs from the Jira instance.
    Env var overrides take precedence over auto-discovery.
    Results are cached per JiraClient instance.
    """

    def __init__(self, client) -> None:
        self._client = client
        self._cache: dict[str, str] | None = None

    async def get_field_map(self) -> dict[str, str]:
        """
        Return a dict mapping logical keys to field IDs:
            {"epic_link": "customfield_10014", "story_points": "customfield_10016", ...}
        """
        if self._cache is not None:
            return self._cache

        settings = get_settings()
        result: dict[str, str] = {}

        # Start with env var overrides (highest priority)
        for logical_key, env_name in ENV_VAR_MAP.items():
            env_value = getattr(settings, env_name, None)
            if env_value:
                result[logical_key] = env_value

        # Auto-discover any keys not already set via env vars
        missing_keys = [k for k, _ in KNOWN_FIELDS if k not in result]
        if missing_keys and settings.jira_base_url and settings.jira_base_url != "https://your-company.atlassian.net":
            await self._discover(result, missing_keys)

        # Fill remaining missing keys with defaults
        defaults = {
            "epic_link": "customfield_10014",
            "story_points": "customfield_10016",
            "sprint": "customfield_10020",
        }
        for logical_key in [k for _, k in KNOWN_FIELDS]:
            if logical_key not in result:
                result[logical_key] = defaults.get(logical_key, "")

        self._cache = result
        logger.info("field_map_resolved", fields=result)
        return result

    async def _discover(self, result: dict[str, str], logical_keys: list[str]) -> None:
        """Query Jira API for all fields and match by name pattern."""
        try:
            fields = await self._client.get_fields()
        except Exception as exc:
            logger.warning("field_discovery_failed", error=str(exc))
            return

        # Build lookup: logical_key → field_id by matching name
        name_to_key = {name: key for name, key in KNOWN_FIELDS}

        for field in fields:
            field_name: str = field.get("name", "")
            field_id: str = field.get("id", "")

            # Check if this field name matches any known pattern
            for display_name, logical_key in KNOWN_FIELDS:
                if logical_key in result:
                    continue  # already set by env var
                if display_name.lower() in field_name.lower():
                    result[logical_key] = field_id
                    logger.info("field_discovered", logical=logical_key, id=field_id, name=field_name)
                    break


def get_epic_link_field() -> str:
    """Shortcut: returns the epic link field ID (env override or default)."""
    return getattr(get_settings(), "jira_field_epic_link", "customfield_10014")


def get_story_points_field() -> str:
    """Shortcut: returns the story points field ID (env override or default)."""
    return getattr(get_settings(), "jira_field_story_points", "customfield_10016")


def get_sprint_field() -> str:
    """Shortcut: returns the sprint field ID (env override or default)."""
    return getattr(get_settings(), "jira_field_sprint", "customfield_10020")
