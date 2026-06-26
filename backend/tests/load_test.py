"""
load_test.py — Basic load test for the Jira Intelligence Platform API.

Usage:
    python tests/load_test.py
"""

import asyncio
import statistics
import time
import httpx

BASE_URL = "http://localhost:8000"
CONCURRENCY = 10
REQUESTS_PER_WORKER = 20

ENDPOINTS = [
    ("GET", "/api/health", None),
    ("GET", "/api/projects", None),
]

async def worker(worker_id: int, results: list):
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=10) as client:
        for i in range(REQUESTS_PER_WORKER):
            for method, path, _ in ENDPOINTS:
                start = time.monotonic()
                try:
                    resp = await client.request(method, path)
                    lat = (time.monotonic() - start) * 1000
                    results.append({
                        "worker": worker_id,
                        "method": method,
                        "path": path,
                        "status": resp.status_code,
                        "latency_ms": round(lat, 1),
                        "success": resp.status_code < 500,
                    })
                except Exception as e:
                    results.append({
                        "worker": worker_id,
                        "method": method,
                        "path": path,
                        "status": 0,
                        "latency_ms": 0,
                        "success": False,
                        "error": str(e),
                    })


async def main():
    print(f"Load test: {CONCURRENCY} workers x {REQUESTS_PER_WORKER} requests x {len(ENDPOINTS)} endpoints")
    print(f"Target: {BASE_URL}")
    print()

    all_results = []
    workers = [worker(i, all_results) for i in range(CONCURRENCY)]
    start = time.monotonic()
    await asyncio.gather(*workers)
    elapsed = time.monotonic() - start

    succeeded = [r for r in all_results if r["success"]]
    failed = [r for r in all_results if not r["success"]]
    latencies = sorted(r["latency_ms"] for r in succeeded)

    print(f"Completed in {elapsed:.2f}s")
    print(f"Total requests: {len(all_results)}")
    print(f"Succeeded: {len(succeeded)}")
    print(f"Failed: {len(failed)}")
    print(f"Requests/sec: {len(all_results) / elapsed:.1f}")
    print()

    if latencies:
        print(f"Latency (ms):")
        print(f"  P50 : {statistics.median(latencies):.1f}")
        print(f"  P90 : {latencies[int(len(latencies) * 0.9)]:.1f}")
        print(f"  P99 : {latencies[int(len(latencies) * 0.99)]:.1f}")
        print(f"  Max : {max(latencies):.1f}")
    print()

    # Per-endpoint breakdown
    from collections import defaultdict
    by_path = defaultdict(list)
    for r in succeeded:
        by_path[r["path"]].append(r["latency_ms"])

    print("Per-endpoint:")
    for path, lats in sorted(by_path.items()):
        lats.sort()
        print(f"  {path}: count={len(lats)} p50={statistics.median(lats):.1f}ms p90={lats[int(len(lats)*0.9)]:.1f}ms")

    if failed:
        print(f"\nFailed requests:")
        for r in failed[:10]:
            err = r.get("error", f"HTTP {r['status']}")
            print(f"  {r['method']} {r['path']}: {err}")


if __name__ == "__main__":
    asyncio.run(main())
