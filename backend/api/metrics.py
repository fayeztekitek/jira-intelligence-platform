"""
metrics.py — Prometheus metrics for the API.
"""

import time
from prometheus_client import Counter, Histogram, generate_latest, REGISTRY

REQUEST_COUNT = Counter(
    "api_request_count",
    "Total API requests",
    ["method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "api_request_latency_seconds",
    "API request latency in seconds",
    ["method", "path"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ERROR_COUNT = Counter(
    "api_error_count",
    "Total API errors (4xx/5xx)",
    ["method", "path", "status"],
)


def observe_request(method: str, path: str, status_code: int, latency: float):
    REQUEST_COUNT.labels(method=method, path=path, status=str(status_code)).inc()
    REQUEST_LATENCY.labels(method=method, path=path).observe(latency)
    if 400 <= status_code < 600:
        ERROR_COUNT.labels(method=method, path=path, status=str(status_code)).inc()


async def metrics_endpoint():
    from fastapi.responses import Response
    return Response(
        content=generate_latest(REGISTRY),
        media_type="text/plain; version=0.0.4",
    )
