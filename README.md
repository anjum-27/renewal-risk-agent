# Renewal Risk Agent

An AI agent that watches a customer relationship over time and flags churn risk **before it's obvious**, using [Hindsight](https://hindsight.vectorize.io/) as its memory layer.

No single support ticket or QBR note tells you a customer is about to leave. The signal only appears when you compare this month to last month: usage sliding, tickets getting curter, a champion who used to reply the same day going quiet. Remove the memory and there is no product left.

The agent also learns **across accounts**. When it assesses a customer, it recalls how similar past accounts played out (churned vs. saved) and recommends what actually worked, without naming those other customers.


---

## What it does

For any account, the agent:

1. **Reflects** on the account's own history (Hindsight `reflect`) to produce a trend summary: ticket sentiment, usage, champion responsiveness, competitor mentions, and inflection points.
2. **Recalls** similar patterns from a shared playbook of other accounts' outcomes (Hindsight `recall`).
3. **Briefs** the CS manager (LLM via Groq): a risk verdict (Healthy / Watch / At Risk / Critical) plus 2-3 concrete actions grounded in what worked before.

## The demo: memory you can watch

`demo_progression.py` reveals one account's history month by month and re-briefs after each month. Sample run on a synthetic account that later recovers:

| Month | Verdict | What the agent picks up on |
|---|---|---|
| 1-2 | Healthy | Only early data; stable, positive |
| 3 | At Risk | Usage falling, tickets turning terse, flagged before the champion goes silent |
| 4 | At Risk | Champion unresponsive, usage down ~55% |
| 5 | At Risk | Usage rebounds, agent stays cautious |
| 6 | Healthy | Recovery confirmed; actions cite what worked for similar past accounts |

Same agent, same account. The only thing that changes is what it remembers.

---
## Features

- Long-term customer memory with Hindsight
- Account-level renewal risk assessment
- Historical churn-pattern comparison
- Usage and engagement tracking
- Evidence-backed risk signals
- Recommended customer-success actions
- Account health dashboard
- Healthy / Watch / At Risk / Critical states

## How Hindsight memory is used

There are two kinds of memory, and both are central.

**1. Per-account memory** (bank `account_<id>`)
Every event (support ticket, usage snapshot, QBR note, champion outreach) is retained with its real timestamp. Hindsight's `reflect` then reasons over the whole timeline to surface trends and inflection points. This is what makes month-over-month change visible.

**2. Shared playbook memory** (bank `playbook`)
When an account reaches an outcome (churned, or saved by an intervention), an anonymized summary of what happened is retained here. When another account looks risky, `recall` finds matching patterns, so recommendations come from real precedent.

Design choices worth noting:

- **Isolation by bank.** Each customer's raw history lives in its own bank. Only anonymized outcome summaries are shared, so no customer-specific details cross accounts.
- **Anonymized playbook.** Outcome summaries contain no company names, and the briefing prompt forbids naming other accounts.
- **Async ingestion.** `ingest.py` uses `retain_batch(..., retain_async=True)` so Hindsight's own worker handles extraction and provider rate limits. `check_status.py` reports when it's done.
- **Fresh client per call.** `risk_agent.py` creates a new Hindsight client per call to avoid event-loop reuse errors in the long-running Flask server.

## Architecture

```
generate_synthetic_data.py  ->  data/accounts.json  (6 accounts x 6 months)
          |
ingest.py  ->  Hindsight
          |      account_<id> banks   (full event history per account)
          |      playbook bank        (anonymized outcomes of churned/saved accounts)
          |
risk_agent.py:  reflect(account bank) + recall(playbook) + Groq LLM  ->  briefing
          |
   +------+---------------------------+
   |                                  |
demo_progression.py               app.py (Flask dashboard, localhost:5000)
month-by-month CLI demo           account list + cached briefings
```

## Project files

| File | Purpose |
|---|---|
| `generate_synthetic_data.py` | Creates 6 synthetic accounts with healthy, churned, and at-risk-then-saved trajectories |
| `ingest.py` | Loads events into per-account banks and outcomes into the playbook. `--skip` leaves accounts out |
| `check_status.py` | Shows the status of Hindsight's background ingestion (`--watch` to poll) |
| `reset_banks.py` | Deletes all banks and the briefing cache so you can re-ingest cleanly |
| `risk_agent.py` | Core agent: reflect + recall + LLM briefing (CLI and library) |
| `demo_progression.py` | Progressive month-by-month demo |
| `app.py` | Local web dashboard with briefing cache |

---

## Setup

### 1. Get Hindsight running

Pick one:

- **Hindsight Cloud:** sign up at https://ui.hindsight.vectorize.io, then copy your base URL and API key.
- **Docker (local):**
  ```
  export OPENAI_API_KEY=your-key
  docker run --rm -it --pull always -p 8888:8888 -p 9999:9999 \
    -e HINDSIGHT_API_LLM_API_KEY=$OPENAI_API_KEY \
    -e HINDSIGHT_API_LLM_MODEL=o3-mini \
    -v $HOME/.hindsight-docker:/home/hindsight/.pg0 \
    ghcr.io/vectorize-io/hindsight:latest
  ```
  API at `http://localhost:8888`, UI at `http://localhost:9999`.

### 2. Get a Groq API key

Free tier at https://groq.com. The default model is `openai/gpt-oss-120b`.

### 3. Install dependencies

```
python -m venv venv
venv\Scripts\activate          # Windows  (macOS/Linux: source venv/bin/activate)
pip install -r requirements.txt
```

### 4. Configure environment

```
copy .env.example .env         # Windows  (macOS/Linux: cp .env.example .env)
```

Edit `.env`:

```
HINDSIGHT_BASE_URL=...         # e.g. https://api.hindsight.vectorize.io or http://localhost:8888
HINDSIGHT_API_KEY=...          # Cloud only; leave blank for local Docker
GROQ_API_KEY=...
GROQ_MODEL=openai/gpt-oss-120b # optional
```

Never commit `.env`.

---

## Run it

### Prepare the data (once)

```
python generate_synthetic_data.py
python ingest.py --skip acct_005
python check_status.py --watch
```

`--skip acct_005` keeps the demo account out of Hindsight so it starts with **no memory**. The demo then fills its bank month by month. The other five accounts (including the two churned and one saved) populate the playbook.

Wait for `All operations completed` before continuing.

### Run the progression demo

```
python demo_progression.py --account acct_005
```

Press Enter to advance each month.

### One-shot briefing

```
python risk_agent.py --account acct_001
```

(Use an account that was ingested in full.)

### Web dashboard

```
python app.py
```

Open http://localhost:5000. Briefings are cached in `data/briefing_cache.json`; add `?refresh=1` to `/api/brief/<account_id>` to regenerate one. The dashboard runs locally because it needs your Hindsight connection and Groq key.

### Starting over

```
python reset_banks.py
python generate_synthetic_data.py
python ingest.py --skip acct_005
```

**Always run `reset_banks.py` before re-ingesting.** Ingestion adds to existing memory rather than replacing it, so old data will otherwise leak into new runs. Company names are randomly generated each time you run `generate_synthetic_data.py`, so regenerate and re-ingest together.

---

## Known limitations

- **Synthetic data.** Accounts are generated from templates. The pipeline works on any event stream, but real ticket and CRM exports would be the next step.
- **LLM summaries can be imperfect.** `reflect` occasionally misreads details (for example, stretching a single missed follow-up into a longer silence). The overall pattern is reliable; specific phrasings should be checked.
- **Ingestion is asynchronous.** Give it a few minutes after `ingest.py` and use `check_status.py` instead of guessing.
- **Local-only dashboard.** No auth or hosting; it is a demo UI.
- **Rate limits.** On Groq's free tier the demo may hit limits; `demo_progression.py` retries once after 60 seconds.

## Next steps

- Import real support-ticket and usage exports instead of synthetic data.
- Rank all accounts by risk on the dashboard and show verdict trends over time.
- Close the loop: record which recommended actions were taken and their outcomes back into the playbook.
- Add automated tests for the ingest and briefing flow.

## Tech stack

Python, [Hindsight](https://github.com/vectorize-io/hindsight) (memory), Groq (LLM via OpenAI-compatible API), Flask, Faker.
