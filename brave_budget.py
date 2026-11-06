#!/usr/bin/env python3
"""Persisted Brave Search budget reservation, separate from the live board watcher.

Every metered call must be reserved and pushed to the default GitHub branch
BEFORE the key is made available to the job discovery step. Failed reservations
mean zero Brave requests; unused reservations count against the limit.
"""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LIMIT = 100
DAILY_LIMIT = 3

def reserve(previous, now, request_count=DAILY_LIMIT, monthly_limit=DEFAULT_LIMIT):
    month = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m")
    if not 0 <= request_count <= DAILY_LIMIT:
        raise ValueError("per-run Brave cap exceeded")
    if not 1 <= monthly_limit <= DEFAULT_LIMIT:
        raise ValueError("monthly Brave cap exceeded")
    current = previous if previous.get("month") == month else {}
    used = int(current.get("reserved", 0))
    if used < 0:
        raise ValueError("invalid budget ledger")
    granted = min(request_count, max(monthly_limit - used, 0))
    ledger = {"month": month, "reserved": used + granted,
              "monthly_request_cap": monthly_limit,
              "most_recent_reservation": datetime.fromtimestamp(now,timezone.utc).isoformat()}
    return ledger, granted

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", default="brave_budget.json")
    p.add_argument("--monthly-limit", type=int, default=DEFAULT_LIMIT)
    p.add_argument("--request-count", type=int, default=DAILY_LIMIT)
    p.add_argument("--output", default="brave_grant.txt")
    args = p.parse_args()
    path = Path(args.file)
    try:
        prior = json.loads(path.read_text()) if path.is_file() else {}
        ledger, granted = reserve(prior, datetime.now(timezone.utc).timestamp(),
                                  args.request_count, args.monthly_limit)
    except (ValueError, TypeError, json.JSONDecodeError) as e:
        p.error(f"refusing to call Brave: {e}")
    path.write_text(json.dumps(ledger, indent=2) + "\n")
    Path(args.output).write_text(str(granted) + "\n")
    print(f"BRAVE-BUDGET month={ledger['month']} reserved={ledger['reserved']}/{ledger['monthly_request_cap']} granted={granted}")
if __name__ == "__main__":
    main()
