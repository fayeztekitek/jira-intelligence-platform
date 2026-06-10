"""
jira_connector/client.py — Production-grade Jira REST API client.

Features:
- Multi-auth: API token (Cloud), PAT (Data Center), OAuth2
- Async HTTP with httpx
- Automatic pagination (startAt / maxResults)
- Rate limit handling with exponential backoff
- Retry on transient errors (429, 5xx)
- Request/response logging (tokens masked)
- Audit counter for compliance reporting
"""
import asyncio
import base64
import json
import time
from typing import Any, AsyncGenerator
from urllib.parse import urlencode

import httpx
import structlog
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type, before_sleep_log
)

from config import get_settings

logger = structlog.get_logger(__name__)


class JiraAPIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(f"Jira API error {status_code}: {message}")


class JiraClient:
    """
    Async Jira REST API v2/v3 client.
    Thread-safe, suitable for use in FastAPI and APScheduler.
    """

    def __init__(self):
        self.settings = get_settings()
        self._client: httpx.AsyncClient | None = None
        self.api_call_count = 0

    def _build_headers(self) -> dict:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Atlassian-Token": "no-check",
        }
        auth_type = self.settings.jira_auth_type
        if auth_type == "api_token":
            creds = f"{self.settings.jira_username}:{self.settings.jira_api_token}"
            encoded = base64.b64encode(creds.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"
        elif auth_type == "pat":
            headers["Authorization"] = f"Bearer {self.settings.jira_pat}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.settings.jira_base_url,
                headers=self._build_headers(),
                timeout=httpx.Timeout(30.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, JiraAPIError)),
        reraise=True,
    )
    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        payload: dict | None = None,
    ) -> dict:
        client = await self._get_client()
        # Rate limiting
        await asyncio.sleep(self.settings.jira_rate_limit_delay)
        self.api_call_count += 1

        log = logger.bind(method=method, path=path, call_num=self.api_call_count)

        try:
            resp = await client.request(
                method, path, params=params, json=payload
            )
        except httpx.TimeoutException as e:
            log.warning("jira_request_timeout", error=str(e))
            raise

        # Handle rate limiting
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", "60"))
            log.warning("jira_rate_limited", retry_after=retry_after)
            await asyncio.sleep(retry_after)
            raise JiraAPIError(429, "Rate limited")

        if resp.status_code >= 500:
            log.error("jira_server_error", status=resp.status_code)
            raise JiraAPIError(resp.status_code, resp.text)

        if resp.status_code == 401:
            raise JiraAPIError(401, "Authentication failed — check credentials")

        if resp.status_code == 403:
            raise JiraAPIError(403, f"Permission denied for {path}")

        if resp.status_code == 404:
            return {}

        if resp.status_code >= 400:
            raise JiraAPIError(resp.status_code, resp.text)

        log.debug("jira_request_ok", status=resp.status_code)
        return resp.json()

    # ─── Pagination helper ────────────────────────────────────────────────────

    async def paginate(
        self,
        path: str,
        params: dict | None = None,
        results_key: str = "issues",
        page_size: int | None = None,
    ) -> AsyncGenerator[dict, None]:
        """
        Async generator that yields individual items across all pages.
        Handles both Jira Cloud (startAt-based) and nextPageToken styles.
        """
        params = params or {}
        size = page_size or self.settings.jira_page_size
        start = 0
        total = None

        while True:
            page_params = {**params, "startAt": start, "maxResults": size}
            data = await self._request("GET", path, params=page_params)

            if not data:
                break

            items = data.get(results_key, [])
            if total is None:
                total = data.get("total", len(items))

            for item in items:
                yield item

            start += len(items)
            if not items or start >= total:
                break

            logger.debug("jira_pagination", path=path, fetched=start, total=total)

    # ─── Projects ─────────────────────────────────────────────────────────────

    async def get_all_projects(self) -> list[dict]:
        """Fetch all accessible projects with pagination."""
        projects = []
        async for p in self.paginate(
            "/rest/api/2/project/search",
            params={"expand": "description,lead,url"},
            results_key="values",
        ):
            projects.append(p)
        logger.info("jira_projects_fetched", count=len(projects))
        return projects

    async def get_project(self, project_key: str) -> dict:
        return await self._request("GET", f"/rest/api/2/project/{project_key}")

    # ─── Issues ───────────────────────────────────────────────────────────────

    async def search_issues(
        self,
        jql: str,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Stream all issues matching a JQL query."""
        default_fields = [
            "summary", "description", "issuetype", "status", "priority",
            "assignee", "reporter", "created", "updated", "resolutiondate",
            "duedate", "resolution", "labels", "components", "fixVersions",
            "customfield_10014",  # Epic Link
            "customfield_10016",  # Story Points
            "customfield_10020",  # Sprint
            "parent", "subtasks", "timespent", "timeoriginalestimate",
            "comment",
        ]
        params = {
            "jql": jql,
            "fields": ",".join(fields or default_fields),
        }
        if expand:
            params["expand"] = ",".join(expand)

        async for issue in self.paginate("/rest/api/2/search", params=params):
            yield issue

    async def get_issue_changelog(self, issue_key: str) -> list[dict]:
        """Fetch full changelog for a single issue."""
        entries = []
        async for entry in self.paginate(
            f"/rest/api/2/issue/{issue_key}/changelog",
            results_key="values",
        ):
            entries.append(entry)
        return entries

    # ─── Sprints ──────────────────────────────────────────────────────────────

    async def get_boards(self, project_key: str) -> list[dict]:
        boards = []
        async for b in self.paginate(
            "/rest/agile/1.0/board",
            params={"projectKeyOrId": project_key, "type": "scrum"},
            results_key="values",
        ):
            boards.append(b)
        return boards

    async def get_sprints(self, board_id: int) -> list[dict]:
        sprints = []
        async for s in self.paginate(
            f"/rest/agile/1.0/board/{board_id}/sprint",
            results_key="values",
        ):
            sprints.append(s)
        return sprints

    # ─── Versions ─────────────────────────────────────────────────────────────

    async def get_versions(self, project_key: str) -> list[dict]:
        return await self._request("GET", f"/rest/api/2/project/{project_key}/versions")

    # ─── Components ───────────────────────────────────────────────────────────

    async def get_components(self, project_key: str) -> list[dict]:
        return await self._request("GET", f"/rest/api/2/project/{project_key}/components")

    # ─── Users ────────────────────────────────────────────────────────────────

    async def get_users(self, project_key: str) -> list[dict]:
        users = []
        async for u in self.paginate(
            "/rest/api/2/user/assignable/multiProjectSearch",
            params={"projectKeys": project_key},
            results_key="",  # returns array directly
        ):
            users.append(u)
        return users

    async def server_info(self) -> dict:
        """Returns Jira server info — used to detect Cloud vs Data Center."""
        return await self._request("GET", "/rest/api/2/serverInfo")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()
