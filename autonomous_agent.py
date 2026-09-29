"""
Autonomous Renewal Risk Agent.

Unlike risk_agent.py (a fixed reflect -> recall -> brief pipeline), here the LLM
decides its own steps. It is given tools and a goal, and it chooses what to call
and when: reflect on the account, recall similar patterns (as many times as it
likes, with sharper queries), draft outreach, and finally submit a finding.

Safety rails:
  - hard step cap, so it can't loop forever
  - tool errors are fed back to the model so it can recover
  - tools are locked to the account being assessed (no cross-account reads)
  - emails are only DRAFTED and queued for human review, never sent
  - if the tool loop fails, we fall back to the fixed pipeline

Usage:
    python autonomous_agent.py --account acct_003     # assess one account
    python autonomous_agent.py --sweep                # assess every account, fill the inbox
"""

import argparse
import json
import os
import re
from datetime import datetime

from risk_agent import (GROQ_MODEL, PLAYBOOK_BANK, REFLECT_QUERY, _account_info,
                        draft_outreach, get_full_briefing, groq, make_client)

INBOX_PATH = "data/inbox.json"
VERDICTS = ["Healthy", "Watch", "At Risk", "Critical"]
MAX_STEPS = 8

SYSTEM = """You are an autonomous Customer Success analyst assessing ONE account for renewal risk.
Work like this:
1. Call reflect_on_account to read this account's own history and trends.
2. Call recall_similar_patterns to learn how similar past accounts played out. You may call it again
   with a sharper query if the first results are vague.
3. If your verdict is At Risk or Critical, call draft_outreach_email.
4. Finish by calling submit_finding exactly once.
Never name other customers. Be specific and direct."""

TOOLS = [
    {"type": "function", "function": {
        "name": "reflect_on_account",
        "description": "Summarize this account's own history: ticket sentiment, usage, champion responsiveness, competitors.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "recall_similar_patterns",
        "description": "Search the shared playbook for how similar past accounts turned out and what worked.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The risk pattern to look for, in plain words."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "draft_outreach_email",
        "description": "Draft (not send) an outreach email to the account's champion.",
        "parameters": {"type": "object", "properties": {
            "notes": {"type": "string", "description": "Key facts about this account's history to base the email on."}},
            "required": ["notes"]}}},
    {"type": "function", "function": {
        "name": "submit_finding",
        "description": "Submit your final assessment. Call exactly once when done.",
        "parameters": {"type": "object", "properties": {
            "verdict": {"type": "string", "enum": VERDICTS},
            "reason": {"type": "string", "description": "One sentence."},
            "recommended_actions": {"type": "string", "description": "2-3 concrete numbered actions."}},
            "required": ["verdict", "reason", "recommended_actions"]}}},
]


# ---------- Tool implementations (locked to the account in `state`) ----------

def _reflect(args, state):
    return make_client().reflect(bank_id=f"account_{state['account_id']}", query=REFLECT_QUERY).text


def _recall(args, state):
    query = " ".join(str(args.get("query", "")).split()[:120])
    result = make_client().recall(bank_id=PLAYBOOK_BANK, query=query)
    texts = list(dict.fromkeys(r.text for r in result.results))[:8]
    return "\n".join(f"- {t}" for t in texts) or "No similar patterns found."


def _draft(args, state):
    state["draft"] = draft_outreach(state["account_id"], str(args.get("notes", "")), "(see notes)")
    return state["draft"]


def _submit(args, state):
    verdict = args.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    info = _account_info(state["account_id"])
    state["finding"] = {
        "account_id": state["account_id"],
        "company": info.get("company", state["account_id"]),
        "verdict": verdict,
        "reason": str(args.get("reason", "")),
        "actions": str(args.get("recommended_actions", "")),
        "draft": state["draft"],
        "steps": list(state["steps"]),
        "mode": "agent",
        "time": datetime.now().isoformat(timespec="seconds"),
    }
    return "Finding recorded."


HANDLERS = {"reflect_on_account": _reflect, "recall_similar_patterns": _recall,
            "draft_outreach_email": _draft, "submit_finding": _submit}


# ---------- The agent loop ----------

def run_agent(account_id, log=print):
    info = _account_info(account_id)
    state = {"account_id": account_id, "steps": [], "draft": None, "finding": None}
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": f"Assess account {account_id} ({info.get('company', account_id)}) for renewal risk."},
    ]
    for _ in range(MAX_STEPS):
        msg = groq.chat.completions.create(model=GROQ_MODEL, messages=messages, tools=TOOLS).choices[0].message
        if not msg.tool_calls:
            break
        messages.append({
            "role": "assistant", "content": msg.content or "",
            "tool_calls": [{"id": c.id, "type": "function",
                            "function": {"name": c.function.name, "arguments": c.function.arguments}}
                           for c in msg.tool_calls],
        })
        for call in msg.tool_calls:
            name = call.function.name
            state["steps"].append(name)
            try:
                result = HANDLERS[name](json.loads(call.function.arguments or "{}"), state)
            except Exception as e:  # feed the error back so the model can recover
                result = f"tool error: {e}"
            log(f"    {account_id}: {name}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": str(result)[:6000]})
        if state["finding"]:
            break
    return state["finding"]


def pipeline_fallback(account_id):
    """Fixed reflect -> recall -> brief pipeline, used if the tool loop fails."""
    r = get_full_briefing(account_id)
    lines = [l.replace("**", "") for l in r["briefing"].splitlines() if l.strip()]
    m = re.search(r"(Critical|At Risk|Watch|Healthy)", r["briefing"], re.I)
    verdict = next((v for v in VERDICTS if m and v.lower() == m.group(1).lower()), "Watch")
    reason = re.sub(r"^.*?(Critical|At Risk|Watch|Healthy)[\s:–—-]*", "", lines[0], flags=re.I) if lines else ""
    steps = ["reflect", "recall", "briefing"]
    draft = None
    if verdict in ("At Risk", "Critical"):
        draft = draft_outreach(account_id, r["reflection"], r["briefing"])
        steps.append("draft")
    return {"account_id": account_id, "company": _account_info(account_id).get("company", account_id),
            "verdict": verdict, "reason": reason, "actions": "\n".join(lines[1:]), "draft": draft,
            "steps": steps, "mode": "pipeline", "time": datetime.now().isoformat(timespec="seconds")}


# ---------- Inbox + sweep ----------

def load_inbox():
    if os.path.exists(INBOX_PATH):
        with open(INBOX_PATH) as f:
            return json.load(f)
    return {}


def save_inbox(inbox):
    os.makedirs(os.path.dirname(INBOX_PATH), exist_ok=True)
    with open(INBOX_PATH, "w") as f:
        json.dump(inbox, f, indent=2)


def assess(account_id, log=print):
    """Agent first; fixed pipeline if the agent fails; error record if both fail."""
    finding = None
    try:
        finding = run_agent(account_id, log)
    except Exception as e:
        log(f"    {account_id}: agent failed ({e}), using fallback pipeline")
    try:
        finding = finding or pipeline_fallback(account_id)
    except Exception as e:
        finding = {"account_id": account_id, "company": _account_info(account_id).get("company", account_id),
                   "verdict": "Error", "reason": str(e), "actions": "", "draft": None, "steps": [],
                   "mode": "error", "time": datetime.now().isoformat(timespec="seconds")}
    inbox = load_inbox()
    inbox[account_id] = finding
    save_inbox(inbox)
    return finding


def sweep(progress=None, log=print):
    with open("data/accounts.json") as f:
        ids = [a["account_id"] for a in json.load(f)]
    for i, account_id in enumerate(ids):
        if progress:
            progress(i, len(ids), account_id)
        assess(account_id, log)
    if progress:
        progress(len(ids), len(ids), "")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", help="assess one account, e.g. acct_003")
    parser.add_argument("--sweep", action="store_true", help="assess every account")
    args = parser.parse_args()
    if args.sweep:
        sweep()
        print("Done. Findings saved to", INBOX_PATH)
    elif args.account:
        print(json.dumps(assess(args.account), indent=2))
    else:
        parser.error("use --account <id> or --sweep")
