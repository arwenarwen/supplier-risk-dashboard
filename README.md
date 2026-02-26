# SupplierGuard

**Real-time supply chain risk intelligence. Upload your suppliers. Get live risk scores.**

SupplierGuard monitors 50+ global news sources, scores every supplier 0–100 based on proximity to live disruptions, and tells you which suppliers are at risk — and why — before the shipment is late.

---

## What it does

- Ingests your supplier list via CSV upload (name, city, country, tier)
- Geocodes every supplier to exact coordinates using OpenStreetMap
- Fetches live news, weather events, and geopolitical disruptions every refresh
- Scores each supplier using a transparent formula: `25 × distance_weight × severity_weight × recency_weight`
- Only events from the last **21 days** affect scores — no stale data
- Surfaces the top 5 scoring events per supplier with full breakdowns
- Recommends alternative suppliers when risk is high
- Posts alerts to Slack and Teams when suppliers cross risk thresholds

---

## Project structure

```
supplier_risk_dashboard/
├── app.py              # Main Streamlit UI — pages, layout, drill-downs
├── database.py         # SQLite setup, event storage, purge logic
├── upload.py           # CSV upload, validation, column normalisation
├── geocoding.py        # Nominatim geocoding with persistent cache
├── events.py           # Live news ingestion — 20 parallel Google News queries
├── scoring.py          # Risk scoring engine — distance, severity, recency weights
├── mapping.py          # Plotly interactive map with risk-coloured markers
├── alerts.py           # Slack Block Kit + Teams Adaptive Card alerts
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable reference
└── .streamlit/
    └── secrets.toml    # Streamlit Cloud secrets template
```

---

## Quick start

**1. Clone and install**

```bash
git clone https://github.com/your-org/supplier-risk-dashboard.git
cd supplier-risk-dashboard
pip install -r requirements.txt
```

**2. Configure environment**

Copy `.env.example` to `.env` and fill in the values you need:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `SLACK_WEBHOOK_URL` | Optional | Incoming webhook for Slack alerts |
| `SLACK_BOT_TOKEN` | Optional | Bot token for slash commands (`xoxb-…`) |
| `SLACK_SIGNING_SECRET` | Optional | Verifies slash command requests |
| `TEAMS_WEBHOOK_URL` | Optional | Incoming webhook for Teams alerts |
| `SMTP_HOST` | Optional | SMTP server for email alerts |
| `SMTP_PORT` | Optional | SMTP port (default `587`) |
| `SMTP_USER` | Optional | SMTP username / sender address |
| `SMTP_PASS` | Optional | SMTP password |
| `ALERT_EMAIL_TO` | Optional | Recipient address for email alerts |

The app runs without any environment variables — alerts are silently skipped if credentials are missing.

**3. Run locally**

```bash
streamlit run app.py
```

Open [http://localhost:8501](http://localhost:8501).

---

## CSV format

Upload a CSV with the following columns. Column names are case-insensitive and extra columns are ignored.

| Column | Required | Example |
|---|---|---|
| `supplier_name` | Yes | `Apex Electronics Co.` |
| `city` | Yes | `Dhaka` |
| `country` | Yes | `Bangladesh` |
| `tier` | No | `Tier 1` |
| `category` | No | `Electronics` |
| `contact_email` | No | `orders@apex.com` |

A sample file is available at `sample_suppliers.csv` in the repo root.

---

## How scoring works

Every matched event scores up to **25 points**:

```
event_score = 25 × distance_weight × severity_weight × recency_weight
```

| Weight | Range | Logic |
|---|---|---|
| `distance_weight` | 0.0 – 1.0 | 1.0 = same city · 0.84 = same country + high signal · 0.6 = same country · 0.15 = regional |
| `severity_weight` | 0.4 – 1.0 | Classified from article title and body: high / medium / low signal |
| `recency_weight` | 0.0 – 1.0 | Linear decay from 1.0 (today) to 0.0 (21 days ago). Zero beyond 21 days. |

The **top 5 event scores are summed** to produce the final risk score (max 100). Events ranked 6+ are displayed for context but do not affect the score.

**Risk thresholds:**

| Score | Level |
|---|---|
| 60 – 100 | 🔴 High |
| 26 – 59 | 🟡 Medium |
| 0 – 25 | 🟢 Low |

---

## News sources

On every fetch, the app runs **20 parallel Google News RSS queries** targeting the supplier countries in your uploaded CSV plus a set of always-on global risk queries:

- Red Sea / Houthi shipping disruption
- Iran sanctions and military activity
- Ukraine war and Black Sea routes
- Taiwan Strait tensions
- US-China trade and tariff changes
- Semiconductor shortage and export controls
- Global container shipping and port strikes
- Typhoon, earthquake, and flood events

Plus country-specific queries for Bangladesh, Vietnam, Cambodia, India, Pakistan, Nigeria, South Korea, Indonesia, Philippines, Mexico, Brazil, Egypt, Turkey, Morocco, Sri Lanka, Saudi Arabia, UAE, Israel, and Japan when those countries appear in your supplier list.

Only articles published within the last **21 days** are stored. Older articles are purged on every fetch.

---

## Deploying to Streamlit Cloud

1. Push the repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io) and connect the repo
3. Set `app.py` as the entry point
4. Add your secrets under **Settings → Secrets** using the format in `.streamlit/secrets.toml`
5. Deploy — the app auto-restarts on every push to `main`

---

## Slack & Teams integration

### Slack

Set `SLACK_WEBHOOK_URL` to receive formatted Block Kit alert cards when suppliers cross the High risk threshold.

To enable the `/supplierguard [name]` slash command, also set `SLACK_BOT_TOKEN` and `SLACK_SIGNING_SECRET`, then run `slack_bot.py` as a separate service alongside the Streamlit app.

### Teams

Set `TEAMS_WEBHOOK_URL` to receive Adaptive Card alerts in any Teams channel. Create the webhook under **Channel Settings → Connectors → Incoming Webhook**.

---

## File-by-file reference

### `app.py`
Main Streamlit application. Handles page routing, sidebar controls, CSV upload flow, the interactive map, supplier drill-down panels, score explanation boxes, predictions, and the manual alert dispatch button.

### `database.py`
SQLite database layer. Creates the `events` and `suppliers` tables on first run. Exposes `insert_event`, `get_events_for_supplier`, `purge_old_events`, and `clear_events`. Database file is stored at `supplier_risk.db` in the working directory.

### `upload.py`
CSV parsing and validation. Normalises column names, checks for required fields, deduplicates rows, and writes validated suppliers to the database. Returns a clean DataFrame for the rest of the app.

### `geocoding.py`
Wraps the Nominatim API with a persistent JSON cache (`geocode_cache.json`). Converts city + country strings to latitude/longitude. Falls back gracefully when a location cannot be resolved.

### `events.py`
News ingestion engine. `refresh_all_events` orchestrates the full fetch cycle: purges old events, runs 20 parallel Google News RSS queries via `_get_live_baseline_articles`, parses publication dates using a robust multi-format parser (ISO 8601, GDELT `YYYYMMDDHHmmss`, RFC 2822), enforces the 21-day cutoff at storage time, and stores passing articles to SQLite.

### `scoring.py`
Risk scoring engine. `score_supplier` computes the weighted score for a given supplier against all stored events. `get_score_breakdown` returns the per-event detail used in the drill-down UI. `classify_signal` classifies article titles as high / medium / low signal using keyword matching. `_parse_published` handles all known date formats from RSS feeds.

### `mapping.py`
Builds the Plotly scatter map. Colours markers by risk level, sizes them by score, and renders supplier name and score in hover tooltips.

### `alerts.py`
Alert dispatcher. Builds Slack Block Kit payloads (`build_slack_alert_blocks`, `build_slack_digest_blocks`, `build_slack_query_blocks`) and Teams Adaptive Card payloads (`build_teams_alert_card`, `build_teams_digest_card`). `dispatch_alerts` iterates over High-risk suppliers and sends to all configured channels.

---

## Requirements

```
streamlit
pandas
plotly
requests
feedparser
geopy
python-dotenv
```

Full list with pinned versions in `requirements.txt`.

Python 3.9+ required.

---

## Licence

MIT
