"""
Deletes every account bank + the shared playbook bank from Hindsight so you
can re-ingest from scratch (e.g. after regenerating accounts.json).

Usage:
    python reset_banks.py

Run this BEFORE ingest.py whenever you regenerate the synthetic data.
Banks that don't exist are skipped.
"""

import json
import os

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ["HINDSIGHT_BASE_URL"].rstrip("/")
HEADERS = {}
if os.environ.get("HINDSIGHT_API_KEY"):
    HEADERS["Authorization"] = f"Bearer {os.environ['HINDSIGHT_API_KEY']}"


def main():
    with open("data/accounts.json") as f:
        accounts = json.load(f)

    bank_ids = [f"account_{a['account_id']}" for a in accounts] + ["playbook"]

    for bank_id in bank_ids:
        url = f"{BASE_URL}/v1/default/banks/{bank_id}"
        try:
            resp = requests.delete(url, headers=HEADERS, timeout=30)
            if resp.status_code == 404:
                print(f"{bank_id:<28} not found (nothing to delete)")
            else:
                resp.raise_for_status()
                print(f"{bank_id:<28} deleted")
        except Exception as e:
            print(f"{bank_id:<28} FAILED: {e}")

    # Also clear the dashboard's cached briefings
    cache = "data/briefing_cache.json"
    if os.path.exists(cache):
        os.remove(cache)
        print("data/briefing_cache.json removed")


if __name__ == "__main__":
    main()
