"""
Ingests generate_synthetic_data.py output into Hindsight.

THIS VERSION uses retain_batch(..., retain_async=True) instead of calling
retain() once per event. Why this is better on a free-tier LLM provider:

  - One API call per account (not one per event) - far fewer round trips.
  - retain_async=True hands the batch to Hindsight's own background worker,
    which has its own built-in quota-aware retry/defer logic (this is what
    the "Fact extraction deferred by provider quota... will retry at <time>"
    message you may have seen is for). We don't need to reimplement that
    ourselves with manual sleeps and retries.
  - Our script returns almost immediately per account; the actual extraction
    happens in the background over the next several minutes.

Two kinds of memory get built here:

1. PER-ACCOUNT MEMORY (bank_id = f"account_{account_id}")
   Every event for that account (tickets, usage, QBR notes, stakeholder pings)
   is retained with its real date, so Hindsight can reason about trends over
   time for that one customer relationship.

2. SHARED "PLAYBOOK" MEMORY (bank_id = "playbook")
   Accounts that have already reached an outcome (churned, or saved by an
   intervention) get a summary retained here too, so OTHER accounts' risk
   assessments can learn from what happened.

Run this AFTER generate_synthetic_data.py.

IMPORTANT: because this is async, ingestion keeps running in the background
after this script finishes. Give it a few minutes before running risk_agent.py
or the dashboard - see check_status.py to watch progress instead of guessing.
"""

import argparse
import json
import os

from dotenv import load_dotenv
from hindsight_client import Hindsight

load_dotenv()

client = Hindsight(
    base_url=os.environ["HINDSIGHT_BASE_URL"],
    api_key=os.environ.get("HINDSIGHT_API_KEY") or None,
)

PLAYBOOK_BANK = "playbook"


def stringify_metadata(metadata):
    """Hindsight's metadata field requires string values - cast numbers/bools/None."""
    return {k: str(v) for k, v in metadata.items()}


def ingest_account(account):
    bank_id = f"account_{account['account_id']}"

    items = [
        {
            "content": event["content"],
            "context": event["type"],
            "timestamp": event["date"],
            "metadata": stringify_metadata(event["metadata"]),
        }
        for event in account["events"]
    ]

    result = client.retain_batch(bank_id=bank_id, items=items, retain_async=True)
    op_id = getattr(result, "operation_id", None) or getattr(result, "id", None)
    print(f"  submitted {len(items)} events as one async batch -> bank '{bank_id}' (operation: {op_id})")

    if account["intervention_summary"]:
        pb_result = client.retain_batch(
            bank_id=PLAYBOOK_BANK,
            items=[{
                "content": account["intervention_summary"],
                "context": "account_outcome",
                "metadata": stringify_metadata({
                    "account_id": account["account_id"],
                    "outcome": account["outcome"],
                    "trajectory": account["trajectory"],
                }),
            }],
            retain_async=True,
        )
        pb_op_id = getattr(pb_result, "operation_id", None) or getattr(pb_result, "id", None)
        print(f"  submitted outcome summary -> shared '{PLAYBOOK_BANK}' bank (operation: {pb_op_id})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip", default="",
                        help="comma-separated account_ids to leave out (e.g. the account you'll use in demo_progression.py)")
    args = parser.parse_args()
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    with open("data/accounts.json") as f:
        accounts = json.load(f)

    for account in accounts:
        if account["account_id"] in skip:
            print(f"Skipping {account['account_id']} ({account['company']}) - leave for demo_progression.py")
            continue
        print(f"Submitting {account['account_id']} ({account['company']}, {account['trajectory']})...")
        ingest_account(account)

    print(
        "\nAll batches submitted. Processing now happens in Hindsight's background worker - "
        "this can take a few minutes, longer if it hits rate limits (it'll retry on its own).\n"
        "Run 'python check_status.py' to watch progress, or just wait ~5 minutes and try "
        "'python risk_agent.py --account <id>'."
    )


if __name__ == "__main__":
    main()
