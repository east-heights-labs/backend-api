"""
OnStage Production Health Check
Hits all critical endpoints and reports status.
Designed to run as a standalone script (cron job or CI).

Exit code 0 = all healthy
Exit code 1 = one or more failures

Usage:
  python scripts/healthcheck.py
  python scripts/healthcheck.py --json   # structured output
"""

import asyncio
import httpx
import sys
import json
import argparse
from datetime import date

BASE = "https://ehl-backend-vercel.vercel.app/api/v1"
TIMEOUT = 15.0

CHECKS = [
    {
        "name": "health",
        "url": f"{BASE}/health",
        "expect_key": "status",
        "expect_value": "ok",
        "critical": True,
    },
    {
        "name": "events (Houston)",
        "url": f"{BASE}/events?lat=29.7604&lng=-95.3698&radius=10&date={date.today().isoformat()}",
        "expect_key": "count",
        "expect_min": 0,   # 0 is valid (no shows today)
        "critical": True,
    },
    {
        "name": "search/venues",
        "url": f"{BASE}/search/venues?q=house+of+blues",
        "expect_key": "count",
        "expect_min": 1,
        "critical": True,
    },
    {
        "name": "search/artists",
        "url": f"{BASE}/search/artists?q=billy+strings",
        "expect_key": "count",
        "expect_min": 0,
        "critical": False,
    },
    {
        "name": "stagetime",
        "url": f"{BASE}/stagetime/search?q=Phish",
        "expect_key": "confidence",
        "critical": False,
    },
]


async def run_check(client: httpx.AsyncClient, check: dict) -> dict:
    name = check["name"]
    try:
        resp = await client.get(check["url"], timeout=TIMEOUT)
        if resp.status_code != 200:
            return {"name": name, "ok": False, "status": resp.status_code,
                    "error": f"HTTP {resp.status_code}", "critical": check["critical"]}

        data = resp.json()

        # Validate expected key exists
        key = check.get("expect_key")
        if key and key not in data:
            return {"name": name, "ok": False, "status": 200,
                    "error": f"Missing key '{key}' in response", "critical": check["critical"]}

        # Validate minimum value
        min_val = check.get("expect_min")
        if min_val is not None and data.get(key, -1) < min_val:
            return {"name": name, "ok": False, "status": 200,
                    "error": f"'{key}' = {data.get(key)} < expected min {min_val}",
                    "critical": check["critical"]}

        # Validate exact value
        expect_val = check.get("expect_value")
        if expect_val is not None and data.get(key) != expect_val:
            return {"name": name, "ok": False, "status": 200,
                    "error": f"'{key}' = {data.get(key)!r}, expected {expect_val!r}",
                    "critical": check["critical"]}

        return {"name": name, "ok": True, "status": 200,
                "value": data.get(key), "critical": check["critical"]}

    except httpx.TimeoutException:
        return {"name": name, "ok": False, "status": None,
                "error": f"Timeout after {TIMEOUT}s", "critical": check["critical"]}
    except Exception as e:
        return {"name": name, "ok": False, "status": None,
                "error": str(e), "critical": check["critical"]}


async def main(as_json: bool = False):
    async with httpx.AsyncClient() as client:
        tasks = [run_check(client, c) for c in CHECKS]
        results = await asyncio.gather(*tasks)

    failures = [r for r in results if not r["ok"]]
    critical_failures = [r for r in failures if r["critical"]]
    all_ok = len(failures) == 0

    if as_json:
        print(json.dumps({
            "ok": all_ok,
            "critical_failures": len(critical_failures),
            "total_failures": len(failures),
            "results": results,
        }, indent=2))
    else:
        print(f"\nOnStage Health Check — {date.today().isoformat()}")
        print("=" * 50)
        for r in results:
            icon = "✅" if r["ok"] else ("🔴" if r["critical"] else "⚠️")
            val = f" ({r['value']})" if r.get("value") is not None else ""
            err = f" — {r['error']}" if r.get("error") else ""
            print(f"  {icon} {r['name']}{val}{err}")
        print()
        if all_ok:
            print("All checks passed.")
        else:
            print(f"{len(critical_failures)} critical failure(s), {len(failures) - len(critical_failures)} warning(s).")

    return 0 if all_ok else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(as_json=args.json)))
