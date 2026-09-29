"""
Checks the status of the async retain_batch operations submitted by ingest.py.

Hindsight exposes a REST endpoint per bank listing its background operations
and their status (pending / processing / completed / failed). This just
calls that endpoint for every account bank + the shared playbook bank and
prints a summary, so you know when it's actually safe to run risk_agent.py
or open the dashboard instead of guessing.

Usage:
    python check_status.py
    python check_status.py --watch      (re-checks every 15s until all done)
"""

import argparse
import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ["HINDSIGHT_BASE_URL"].rstrip("/")
HEADERS = {}
if os.environ.get("HINDSIGHT_API_KEY"):
    HEADERS["Authorization"] = f"Bearer {os.environ['HINDSIGHT_API_KEY']}"


def bank_ids_from_data():
    with open("data/accounts.json") as f:
        accounts = json.load(f)
    ids = [f"account_{a['account_id']}" for a in accounts]
    ids.append("playbook")
    return ids


def check_bank(bank_id):
    url = f"{BASE_URL}/v1/default/banks/{bank_id}/operations"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        ops = resp.json().get("operations", [])
    except Exception as e:
        return [(bank_id, "?", f"could not check: {e}")]

    if not ops:
        return [(bank_id, "-", "no operations recorded (may have used sync retain)")]
    return [(bank_id, op.get("status", "?"), op.get("error_message") or "") for op in ops]


def run_once():
    all_rows = []
    for bank_id in bank_ids_from_data():
        all_rows.extend(check_bank(bank_id))

    pending = [r for r in all_rows if r[1] in ("pending", "processing")]
    print(f"{'BANK':<28} {'STATUS':<12} NOTE")
    for bank_id, status, note in all_rows:
        print(f"{bank_id:<28} {status:<12} {note}")

    return len(pending) == 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch", action="store_true", help="keep checking every 15s until everything's done")
    args = parser.parse_args()

    if not args.watch:
        run_once()
        return

    while True:
        done = run_once()
        if done:
            print("\nAll operations completed. Safe to run risk_agent.py or the dashboard now.")
            break
        print("\n...still processing, checking again in 15s (Ctrl+C to stop watching)\n")
        time.sleep(15)


if __name__ == "__main__":
    main()
