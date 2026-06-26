"""
api/webhooks.py — Webhook dispatch logic.

Dispatches events to all configured webhooks that match the event type and project.
"""

import hashlib
import hmac
import json
import structlog
import httpx

logger = structlog.get_logger(__name__)

EVENT_TYPES = {
    "issue.created",
    "issue.updated",
    "issue.deleted",
    "sync.completed",
    "kpi.updated",
    "risk.changed",
}


async def dispatch_event(event_type: str, payload: dict, project_key: str | None = None) -> list[dict]:
    """
    Send event to all matching active webhooks.
    Returns list of delivery results.
    """
    from sqlalchemy import select
    from storage.database import get_db
    from storage.models import WebhookConfig, WebhookEvent

    results: list[dict] = []

    async with get_db() as db:
        query = select(WebhookConfig).where(
            WebhookConfig.is_active == True,
            WebhookConfig.events.contains(event_type),
        )
        if project_key:
            query = query.where(
                (WebhookConfig.project_key == project_key) | (WebhookConfig.project_key.is_(None))
            )
        webhooks = (await db.execute(query)).scalars().all()

    for wh in webhooks:
        try:
            body = json.dumps(payload, default=str).encode()
            headers = {
                "Content-Type": "application/json",
                "X-Webhook-Event": event_type,
            }
            if wh.secret:
                signature = hmac.new(
                    wh.secret.encode(), body, hashlib.sha256
                ).hexdigest()
                headers["X-Webhook-Signature"] = f"sha256={signature}"

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    wh.url, content=body, headers=headers
                )

            success = 200 <= resp.status_code < 300

            async with get_db() as db:
                event_log = WebhookEvent(
                    webhook_id=wh.id,
                    event_type=event_type,
                    payload=json.dumps(payload, default=str),
                    status_code=resp.status_code,
                    response_body=resp.text[:2000],
                    success=success,
                )
                db.add(event_log)
                await db.commit()

            results.append({
                "webhook_id": wh.id,
                "name": wh.name,
                "url": wh.url,
                "status_code": resp.status_code,
                "success": success,
            })

            logger.info(
                "webhook_delivered",
                webhook_id=wh.id, event_type=event_type,
                status=resp.status_code, success=success,
            )

        except Exception as e:
            logger.error("webhook_failed", webhook_id=wh.id, event_type=event_type, error=str(e))
            results.append({
                "webhook_id": wh.id,
                "name": wh.name,
                "url": wh.url,
                "error": str(e),
                "success": False,
            })

    return results
