"""
Generates synthetic customer-account histories for the Renewal Risk Agent.

Each account gets 6 months of events:
  - support_ticket  : a realistic ticket exchange
  - usage_snapshot  : monthly active users, trending up or down
  - qbr_note        : quarterly business review notes (months 3 and 6)
  - stakeholder_ping: whether the named champion responded to outreach (months 2, 4, 6)

Accounts follow one of three trajectories so the agent has real patterns to learn from:
  - "healthy"       : stable/improving the whole time
  - "churned"       : degrades steadily and the deal is lost
  - "at_risk_saved" : degrades, then CS intervenes and it recovers

HOW THE TEXT IS MADE
The *facts* for every month (sentiment, usage, champion responsiveness, competitor
mention) are fixed by the trajectory logic below, so the story is deterministic.
The *wording* is written by an LLM (Groq) so tickets, outreach logs and QBR notes
read like real CRM records instead of six template sentences. One LLM call per
account. If a call fails or returns bad JSON, that account falls back to the
plain templates, so generation never breaks.

Usage:
    python generate_synthetic_data.py             # LLM-written text (needs GROQ_API_KEY)
    python generate_synthetic_data.py --no-llm    # templates only, no API calls

Run this FIRST, before ingest.py. After regenerating, run reset_banks.py before
re-ingesting so old data doesn't linger in Hindsight.

NOTE: demo_progression.py finds each month's events by searching the event text
for "month N)", so the "(month N)" marker added below must stay in the content.
"""

import argparse
import json
import os
import random
import re
import time

from faker import Faker

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

SEED = 7
random.seed(SEED)
Faker.seed(SEED)  # stable champion names across regenerations
fake = Faker()

GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")

# Fictional customers: (company, industry). Company names are invented.
COMPANIES = [
    ("Northgate Freight Lines", "logistics"),
    ("Brightpath Care Network", "healthcare services"),
    ("Cedarline Mutual", "insurance"),
    ("Harborview Capital Partners", "financial services"),
    ("Kestrel Retail Group", "retail"),
    ("Ridgeway Precision Manufacturing", "manufacturing"),
    ("Solano Grid Services", "energy"),
    ("Tallgrass Broadband", "telecommunications"),
    ("Ironbridge Builders", "construction"),
    ("Meridian Talent Solutions", "staffing"),
    ("Pinecrest Advisory", "professional services"),
    ("Bluewater Hospitality Group", "hospitality"),
]

CHAMPION_TITLES = [
    "VP of Operations",
    "Director of Analytics",
    "Head of Revenue Operations",
    "Senior Operations Manager",
]

COMPETITORS = ["Gainsight", "Vitally", "ChurnZero", "Totango"]

FEATURES = ["the reporting dashboard", "SSO login", "the API integration", "bulk export", "the mobile app"]

SENTIMENT_GUIDE = {
    "positive": "warm and engaged; asks thoughtful follow-up questions, thanks the agent",
    "neutral": "businesslike and routine; neither happy nor upset",
    "negative": "frustrated and terse; curt one-line replies, no follow-up questions, "
                "impatient about the impact on their team's work",
}

# ---------- Template fallback (also used with --no-llm) ----------

TICKET_TEMPLATES = {
    "positive": [
        "Quick question about {feature}, resolved in one reply, team seemed happy.",
        "Asked for a walkthrough of {feature}. Very engaged, asked good follow-up questions.",
    ],
    "neutral": [
        "Routine bug report about {feature}. Standard back and forth, resolved normally.",
        "Asked about billing details for {feature}. Straightforward exchange.",
    ],
    "negative": [
        "Frustrated ticket about {feature} not working as expected. Terse, one-line replies.",
        "Escalation: {feature} broke a workflow. Tone noticeably curt, no follow-up questions asked.",
    ],
}


def fallback_ticket(m):
    return random.choice(TICKET_TEMPLATES[m["sentiment"]]).format(feature=m["feature"])


def fallback_ping(m, champion):
    if m["champion_responsive"]:
        return f"Outreach to {champion}: replied within a day, engaged and positive."
    return f"Outreach to {champion}: two follow-ups this week went unanswered. Previously always replied same-day."


def fallback_qbr(m):
    if m["competitor"]:
        return f"Stakeholders mentioned evaluating {m['competitor_name']} as an alternative."
    return "No competitor mentions. Discussion focused on expanding usage to other teams."


# ---------- Month-by-month facts (this is the story; the LLM only writes words) ----------

def plan_months(trajectory):
    plan = []
    for month in range(1, 7):
        if trajectory == "healthy":
            sentiment = random.choice(["positive", "neutral", "positive"])
            usage = 40 + month * 3 + random.randint(-2, 2)
            responsive = True
            competitor = False

        elif trajectory == "churned":
            # steadily gets worse, never recovers
            sentiment = "positive" if month <= 2 else ("neutral" if month <= 3 else "negative")
            usage = max(5, 45 - month * 6)
            responsive = month <= 3
            competitor = month >= 3  # first QBR after decline is month 3

        else:  # at_risk_saved
            # worse through month 4, CS intervenes, recovers by month 6
            if month <= 4:
                sentiment = "positive" if month <= 1 else ("neutral" if month == 2 else "negative")
                usage = max(10, 45 - month * 7)
                responsive = month <= 2
                competitor = month == 3  # QBR month, so the mention is actually recorded
            else:
                sentiment = "neutral" if month == 5 else "positive"
                usage = 25 + (month - 4) * 8
                responsive = True
                competitor = False

        need_qbr = month % 3 == 0
        plan.append({
            "month": month,
            "date": f"2025-{month:02d}-15",
            "sentiment": sentiment,
            "usage": usage,
            "champion_responsive": responsive,
            "competitor": competitor,
            "competitor_name": random.choice(COMPETITORS) if competitor and need_qbr else None,
            "feature": random.choice(FEATURES),
            "need_ping": month % 2 == 0,
            "need_qbr": need_qbr,
        })
    return plan


# ---------- LLM text generation ----------

_groq = None


def get_groq():
    global _groq
    if _groq is None:
        from openai import OpenAI
        _groq = OpenAI(
            base_url="https://api.groq.com/openai/v1",
            api_key=os.environ["GROQ_API_KEY"],
        )
    return _groq


def build_prompt(company, industry, champion, title, months):
    lines = []
    for m in months:
        parts = [f'- month {m["month"]}: ticket about {m["feature"]}; customer tone = {SENTIMENT_GUIDE[m["sentiment"]]}']
        if m["need_ping"]:
            if m["champion_responsive"]:
                parts.append("ping = CS outreach to the champion; they replied within a day, engaged and positive")
            else:
                parts.append("ping = CS outreach to the champion; two follow-ups went unanswered, "
                             "which is unusual because they previously always replied same-day")
        if m["need_qbr"]:
            if m["competitor"]:
                parts.append(f'qbr = QBR notes; stakeholders mention evaluating {m["competitor_name"]} as an alternative')
            else:
                parts.append("qbr = QBR notes; no competitor mentions, discussion about expanding usage to other teams")
        lines.append("; ".join(parts))

    return f"""You are generating realistic CRM records for a B2B customer-analytics SaaS platform.
Customer: {company}, a {industry} company. Main contact (champion): {champion}, {title}.

Write the records requested for each month below.

{chr(10).join(lines)}

Rules:
- "ticket": 2-4 sentences. Include the customer's message and a brief note on how support responded.
  Add concrete detail: what they were trying to do, an error message or symptom, a ticket reference like SUP-4821.
- Show tone through wording and behavior only. Never write a sentiment label or the words
  "positive", "negative", "sentiment", "churn", or "risk".
- "ping": 1-2 sentence CS outreach log entry that names the champion. For unanswered outreach, say plainly that
  two follow-ups went unanswered and that they normally reply same-day; do not imply how long it has lasted.
- "qbr": 2-3 sentences naming attendee roles and what was discussed.
- Each record must stand alone. Do not refer to other months, trends, or earlier events,
  and never write the word "month" or a month number.
- Use null for any ping/qbr that was not requested for that month.
- Vary phrasing and length between records. Do not reuse the same ticket wording.

Return ONLY valid JSON, no markdown:
{{"months": [{{"month": 1, "ticket": "...", "ping": null, "qbr": null}}, ...]}}"""


def llm_json(prompt, retries=3):
    for attempt in range(1, retries + 1):
        try:
            resp = get_groq().chat.completions.create(
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": "You output only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.9,
            )
            text = resp.choices[0].message.content.strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
            return json.loads(text)
        except Exception as e:
            print(f"    LLM attempt {attempt}/{retries} failed: {e}")
            time.sleep(10 * attempt)
    return None


def write_texts(company, industry, champion, title, months, use_llm):
    """Returns {month: {"ticket":..., "ping":..., "qbr":...}} with fallbacks filled in."""
    generated = {}
    if use_llm:
        result = llm_json(build_prompt(company, industry, champion, title, months))
        if isinstance(result, dict) and isinstance(result.get("months"), list):
            for item in result["months"]:
                if isinstance(item, dict) and isinstance(item.get("month"), int):
                    generated[item["month"]] = item
        else:
            print("    falling back to templates for this account")

    def clean(value):
        return value.strip() if isinstance(value, str) and value.strip() else None

    texts = {}
    for m in months:
        g = generated.get(m["month"], {})
        texts[m["month"]] = {
            "ticket": clean(g.get("ticket")) or fallback_ticket(m),
            "ping": (clean(g.get("ping")) or fallback_ping(m, champion)) if m["need_ping"] else None,
            "qbr": (clean(g.get("qbr")) or fallback_qbr(m)) if m["need_qbr"] else None,
        }
    return texts


# ---------- Account assembly ----------

def build_account(idx, trajectory, company, industry, use_llm):
    account_id = f"acct_{idx:03d}"
    champion = fake.name()
    title = random.choice(CHAMPION_TITLES)

    months = plan_months(trajectory)
    print(f"  writing {account_id} ({company}, {trajectory})...")
    texts = write_texts(company, industry, champion, title, months, use_llm)

    events = []
    for m in months:
        n, date, t = m["month"], m["date"], texts[m["month"]]

        events.append({
            "type": "support_ticket",
            "date": date,
            "content": f"[{company}] Support ticket (month {n}): {t['ticket']}",
            "metadata": {"sentiment": m["sentiment"], "account_id": account_id},
        })

        events.append({
            "type": "usage_snapshot",
            "date": date,
            "content": f"[{company}] Monthly usage (month {n}): {m['usage']} active users logged in this month.",
            "metadata": {"usage": m["usage"], "account_id": account_id},
        })

        if t["ping"]:
            events.append({
                "type": "stakeholder_ping",
                "date": date,
                "content": f"[{company}] CS outreach log (month {n}): {t['ping']}",
                "metadata": {"champion_responsive": m["champion_responsive"], "account_id": account_id},
            })

        if t["qbr"]:
            events.append({
                "type": "qbr_note",
                "date": date,
                "content": f"[{company}] QBR (month {n}): {t['qbr']}",
                "metadata": {"competitor_mentioned": m["competitor"], "account_id": account_id},
            })

    outcome = {
        "healthy": "active",
        "churned": "churned",
        "at_risk_saved": "renewed_after_intervention",
    }[trajectory]

    # Playbook summaries are anonymized on purpose: they are shared across accounts,
    # so they must not name the customer.
    intervention_summary = None
    if trajectory == "at_risk_saved":
        intervention_summary = (
            f"Outcome: a mid-size {industry} account showed churn signals (usage drop, quiet champion, "
            f"competitor mention) around months 3-4. CS ran a proactive check-in, offered a "
            f"quick-win training session on {random.choice(FEATURES)}, and usage + engagement "
            f"recovered by month 6. Account renewed."
        )
    elif trajectory == "churned":
        intervention_summary = (
            f"Outcome: a mid-size {industry} account showed churn signals (usage drop, quiet champion, "
            f"competitor mention) starting around months 3-4. No proactive outreach was made. "
            f"Account churned at renewal."
        )

    return {
        "account_id": account_id,
        "company": company,
        "industry": industry,
        "champion": champion,
        "trajectory": trajectory,
        "outcome": outcome,
        "events": events,
        "intervention_summary": intervention_summary,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true",
                        help="use the plain templates instead of LLM-written text (no API calls)")
    args = parser.parse_args()

    use_llm = not args.no_llm
    if use_llm and not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY not set, falling back to templates (use --no-llm to silence this).")
        use_llm = False

    # 2 healthy, 2 churned (historical), 2 at-risk-saved (historical)
    plan = ["healthy", "healthy", "churned", "churned", "at_risk_saved", "at_risk_saved"]
    picks = random.sample(COMPANIES, len(plan))

    accounts = []
    for i, (trajectory, (company, industry)) in enumerate(zip(plan, picks), start=1):
        accounts.append(build_account(i, trajectory, company, industry, use_llm))

    os.makedirs("data", exist_ok=True)
    with open("data/accounts.json", "w") as f:
        json.dump(accounts, f, indent=2)

    print(f"\nGenerated {len(accounts)} synthetic accounts -> data/accounts.json "
          f"({'LLM-written text' if use_llm else 'template text'})")
    for a in accounts:
        print(f"  {a['account_id']} ({a['company']}, {a['industry']}): "
              f"trajectory={a['trajectory']} outcome={a['outcome']}")


if __name__ == "__main__":
    main()
