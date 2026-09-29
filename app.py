"""
Local web dashboard for the Renewal Risk Agent — the judge-facing demo.

Runs a small Flask server on your machine (NOT hosted anywhere else - it needs
to reach your local Hindsight container and your Groq key, so it only works
on the same computer that's running Docker).

Usage:
    python app.py
Then open: http://localhost:5000

Requires ingest.py to have been run already (accounts must exist in Hindsight).
"""

import json
import os
import threading

from flask import Flask, jsonify, request, send_from_directory

from autonomous_agent import load_inbox, sweep
from risk_agent import draft_outreach, get_full_briefing, record_outcome

app = Flask(__name__, static_folder="static")

# Briefings cost thousands of LLM tokens each, so we save them to disk.
# First click on an account generates it; every click after that is instant
# and free. Add ?refresh=1 to the URL to force a fresh one.
CACHE_PATH = "data/briefing_cache.json"


def load_cache():
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH) as f:
            return json.load(f)
    return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/accounts")
def list_accounts():
    with open("data/accounts.json") as f:
        accounts = json.load(f)
    # Only send what the UI needs - not the full event history (keeps payload small)
    summary = [
        {
            "account_id": a["account_id"],
            "company": a["company"],
            "trajectory": a["trajectory"],
            "outcome": a["outcome"],
        }
        for a in accounts
    ]
    return jsonify(summary)


@app.route("/api/brief/<account_id>")
def brief(account_id):
    cache = load_cache()
    if account_id in cache and not request.args.get("refresh"):
        return jsonify(cache[account_id])
    try:
        result = get_full_briefing(account_id)
        cache[account_id] = result
        save_cache(cache)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


SWEEP = {"running": False, "done": 0, "total": 0, "current": ""}


def _progress(done, total, current):
    SWEEP.update(done=done, total=total, current=current)


def _run_sweep():
    try:
        sweep(progress=_progress)
    finally:
        SWEEP["running"] = False


@app.route("/api/sweep", methods=["POST"])
def start_sweep():
    if not SWEEP["running"]:
        SWEEP["running"] = True
        threading.Thread(target=_run_sweep, daemon=True).start()
    return jsonify(SWEEP)


@app.route("/api/inbox")
def inbox():
    return jsonify({**SWEEP, "findings": load_inbox()})


@app.route("/api/draft/<account_id>")
def draft(account_id):
    try:
        r = load_cache().get(account_id) or get_full_briefing(account_id)
        return jsonify({"draft": draft_outreach(account_id, r["reflection"], r["briefing"])})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/outcome/<account_id>", methods=["POST"])
def outcome(account_id):
    data = request.get_json(silent=True) or {}
    action = (data.get("action") or "").strip()
    result = data.get("result")
    if not action or result not in ("renewed", "churned", "open"):
        return jsonify({"error": "Describe what was done and pick a result."}), 400
    try:
        record_outcome(account_id, action, result)
        cache = load_cache()
        cache.pop(account_id, None)  # next briefing must include the new memory
        save_cache(cache)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("\nDashboard running at: http://localhost:5000\n")
    app.run(debug=True, port=5000)
