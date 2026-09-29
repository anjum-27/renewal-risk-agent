"""
THE DEMO SCRIPT — run this in front of judges.

Picks ONE at-risk account and ingests its history progressively, month by
month, running a fresh risk briefing after each month. This is what makes
memory visibly the star: the same account, the same agent, but the briefing
gets sharper and more specific as more months are retained.

Run generate_synthetic_data.py once first. Then:

    python demo_progression.py --account acct_005

Watch the output for month 1 (nothing to go on), month 4 (risk signals
appearing), and month 6 (either recovered, if it's an at_risk_saved account,
or the agent flags it should have escalated earlier).
"""

import argparse
import json
import os
import time

from dotenv import load_dotenv
from hindsight_client import Hindsight

from ingest import stringify_metadata
from risk_agent import brief_account, PLAYBOOK_BANK

load_dotenv()

hindsight = Hindsight(
    base_url=os.environ["HINDSIGHT_BASE_URL"],
    api_key=os.environ.get("HINDSIGHT_API_KEY") or None,
)


def load_account(account_id):
    with open("data/accounts.json") as f:
        accounts = json.load(f)
    for a in accounts:
        if a["account_id"] == account_id:
            return a
    raise ValueError(f"Unknown account_id: {account_id}")


def retain_month(account, bank_id, month_idx):
    """Retains one month's events synchronously (not batched/async) since this
    is a live, manually-paced demo - we want the agent to actually know about
    the new month before the briefing runs immediately after. If you hit a
    rate limit here, just wait ~60s and press enter again; the events for
    this month that already succeeded won't be duplicated by re-running once
    (Hindsight will just add them again, which is harmless for a demo)."""
    month_events = [e for e in account["events"] if e["content"].find(f"month {month_idx})") != -1]
    for event in month_events:
        try:
            hindsight.retain(
                bank_id=bank_id,
                content=event["content"],
                context=event["type"],
                timestamp=event["date"],
                metadata=stringify_metadata(event["metadata"]),
            )
        except Exception as e:
            if "rate_limit" in str(e) or "429" in str(e):
                print("  rate-limited - waiting 60s before continuing (this is a good moment to keep talking to the judges)...")
                time.sleep(60)
                hindsight.retain(
                    bank_id=bank_id,
                    content=event["content"],
                    context=event["type"],
                    timestamp=event["date"],
                    metadata=stringify_metadata(event["metadata"]),
                )
            else:
                raise
    return len(month_events)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True)
    parser.add_argument("--months", default="1,2,3,4,5,6",
                         help="comma-separated months to reveal progressively (default: all 6)")
    args = parser.parse_args()

    account = load_account(args.account)
    bank_id = f"account_{account['account_id']}"
    months = [int(m) for m in args.months.split(",")]

    print(f"Demo account: {account['company']} ({account['account_id']}), trajectory={account['trajectory']}")
    print("NOTE: for a real demo, seed the shared playbook bank with OTHER accounts' outcomes first")
    print("      (run ingest.py on the other 5 synthetic accounts) so recall has patterns to match against.\n")

    for month in months:
        n = retain_month(account, bank_id, month)
        print(f"\n{'=' * 60}\nMONTH {month} — retained {n} new event(s) for {account['account_id']}\n{'=' * 60}")
        brief_account(account["account_id"])
        input("\n[press enter to advance to next month]")


if __name__ == "__main__":
    main()
