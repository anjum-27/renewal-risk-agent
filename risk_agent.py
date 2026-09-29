"""
Renewal Risk Agent — the core demo.

For a given account, this:
  1. Uses Hindsight's `reflect` to synthesize this account's own history into
     a trend summary (sentiment, usage, stakeholder engagement, competitors).
  2. Uses `recall` against the shared "playbook" bank to find similar
     historical patterns from OTHER accounts (churned or saved).
  3. Hands both to an LLM (via Groq) to produce a final, readable risk
     briefing with a concrete recommended action — citing the similar past
     account by pattern, not by name (no cross-tenant leakage).

Usage:
    python risk_agent.py --account acct_005
    python risk_agent.py --account acct_005 --months 1,3,5   (demo progression mode)
"""

import argparse
import os

from dotenv import load_dotenv
from hindsight_client import Hindsight
from openai import OpenAI

load_dotenv()

def make_client():
    """A fresh Hindsight client per call rather than one shared instance.

    Why: the hindsight_client library manages its own aiohttp session tied to
    the event loop it was created in. In a short-lived CLI script that's fine
    (one event loop, one call, then exit). But in a long-running server like
    our Flask app, each request runs in a fresh event loop, and reusing one
    shared client across requests causes "Timeout context manager should be
    used inside a task" - a leftover connection from a previous, now-closed
    event loop being reused in a new one. Creating a new client per call
    avoids this entirely, at the cost of a tiny bit of extra connection setup.
    """
    return Hindsight(
        base_url=os.environ["HINDSIGHT_BASE_URL"],
        api_key=os.environ.get("HINDSIGHT_API_KEY") or None,
    )


groq = OpenAI(
    base_url="https://api.groq.com/openai/v1",
    api_key=os.environ["GROQ_API_KEY"],
)

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

PLAYBOOK_BANK = "playbook"

REFLECT_QUERY = (
    "Summarize how this account's health has trended over time: support ticket "
    "sentiment, usage/login numbers, whether the champion is still responsive, "
    "and any competitor mentions. Note any inflection points where things got "
    "noticeably worse or better."
)


def account_reflection(account_bank):
    hindsight = make_client()
    result = hindsight.reflect(bank_id=account_bank, query=REFLECT_QUERY)
    return result.text


def similar_playbook_patterns(reflection_summary):
    # Hindsight's recall query has a hard cap (500 tokens on Cloud). The full
    # reflect summary can run longer than that, so we truncate to a short,
    # safe query instead of passing the whole paragraph back in.
    short_summary = " ".join(reflection_summary.split()[:120])
    hindsight = make_client()
    result = hindsight.recall(
        bank_id=PLAYBOOK_BANK,
        query=f"Accounts with a similar risk pattern: {short_summary}",
    )
    return [r.text for r in result.results]


def generate_briefing(account_id, reflection_summary, playbook_matches):
    playbook_block = "\n".join(f"- {m}" for m in playbook_matches) or "(no similar historical patterns found yet)"

    prompt = f"""You are a Customer Success renewal-risk assistant. Based on the data below,
write a short briefing (under 200 words) for a CS manager about account {account_id}.

ACCOUNT HEALTH SUMMARY (from this account's own history):
{reflection_summary}

SIMILAR PATTERNS FROM OTHER ACCOUNTS (what happened before, what worked):
{playbook_block}

Write:
1. A one-line risk verdict (Healthy / Watch / At Risk / Critical) with a one-sentence reason.
2. 2-3 concrete recommended actions, grounded in what the similar historical patterns show worked.
Never name other accounts or companies; refer to them only as 'a similar past account'.
Be direct and specific. No filler, no disclaimers.
"""

    response = groq.chat.completions.create(
        model=GROQ_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content


STORY_DATE = "2025-07-01"  # just after the 6 months of synthetic history


def _account_info(account_id):
    import json
    with open("data/accounts.json") as f:
        for a in json.load(f):
            if a["account_id"] == account_id:
                return a
    return {}


def draft_outreach(account_id, reflection, briefing):
    """Drafts a champion outreach email grounded in this account's memory."""
    info = _account_info(account_id)
    champion = info.get("champion", "the main contact")
    company = info.get("company", account_id)
    prompt = f"""Write a short outreach email (under 120 words) from a Customer Success manager to {champion} at {company}.

ACCOUNT HISTORY:
{reflection}

INTERNAL BRIEFING (for your context only):
{briefing}

Rules: warm and specific to what actually happened for this customer; one clear ask (a short call);
never mention risk scores, churn, internal analysis, or other customers.
Output a subject line, then the body."""
    response = groq.chat.completions.create(model=GROQ_MODEL, messages=[{"role": "user", "content": prompt}])
    return response.choices[0].message.content


def record_outcome(account_id, action, result):
    """Closes the learning loop: stores what CS did and how it turned out.

    - Always retained in the account's own bank.
    - If the account renewed or churned, an anonymized entry also goes into the
      shared playbook so other accounts' briefings can learn from it.
    """
    info = _account_info(account_id)
    company = info.get("company", account_id)
    hindsight = make_client()

    hindsight.retain(
        bank_id=f"account_{account_id}",
        content=f"[{company}] CS action logged: {action}. Result so far: {result}.",
        context="cs_action",
        timestamp=STORY_DATE,
        metadata={"account_id": account_id, "result": result},
    )

    if result in ("renewed", "churned"):
        safe = action.replace(company, "the customer").replace(info.get("champion", "\0"), "the champion")
        hindsight.retain(
            bank_id=PLAYBOOK_BANK,
            content=f"Outcome: a mid-size {info.get('industry', 'B2B')} account with churn signals. "
                    f"CS action: {safe}. Result: account {result}.",
            context="account_outcome",
            metadata={"account_id": account_id, "outcome": result},
        )


def get_full_briefing(account_id):
    """Returns a dict with all three pieces - used by both the CLI and the web UI."""
    bank_id = f"account_{account_id}"

    reflection = account_reflection(bank_id)
    matches = similar_playbook_patterns(reflection)
    briefing = generate_briefing(account_id, reflection, matches)

    return {
        "account_id": account_id,
        "reflection": reflection,
        "playbook_matches": matches,
        "briefing": briefing,
    }


def brief_account(account_id):
    """CLI-friendly wrapper: prints progress and returns just the final briefing text."""
    print(f"\n=== Briefing for {account_id} ===")
    result = get_full_briefing(account_id)

    print("\n[Hindsight reflect — this account's trend]")
    print(result["reflection"])

    print(f"\n[Hindsight recall — {len(result['playbook_matches'])} similar historical pattern(s) found in playbook]")

    print("\n[Final briefing]")
    print(result["briefing"])
    return result["briefing"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", required=True, help="account_id, e.g. acct_005")
    args = parser.parse_args()
    brief_account(args.account)
