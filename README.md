# 🛡️ Supplier Risk Monitoring Dashboard

A production-ready MVP Streamlit application that helps companies monitor supplier risks using real-time news, weather data, and automated risk scoring.

---

## 🗂️ Project Structure

```
supplier_risk_dashboard/
├── app.py              # Main Streamlit UI
├── database.py         # SQLite database setup and operations
├── upload.py           # CSV upload, validation, storage
├── geocoding.py        # OpenStreetMap Nominatim geocoding
├── events.py           # NewsAPI + OpenWeatherMap event ingestion
├── scoring.py          # Rule-based risk scoring engine
├── mapping.py          # Plotly interactive map
├── alerts.py           # Email + Slack alert dispatching
├── requirements.txt    # Python dependencies
├── .env.example        # Environment variable template
└── .streamlit/
    └── secrets.toml    # Streamlit Cloud secrets template
```

---

## 🚀 Quick Start (Local)

### 1. Clone and set up environment

```bash
git clone <your-repo-url>
cd supplier_risk_dashboard

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure API keys

```bash
# Copy the example env file
cp .env.example .env

# Edit .env with your API keys
nano .env
```

**Required keys:**
| Key | Where to get it | Cost |
|-----|----------------|------|
| `NEWS_API_KEY` | [newsapi.org](https://newsapi.org) | Free (100 req/day) |
| `OPENWEATHER_API_KEY` | [openweathermap.org](https://openweathermap.org/api) | Free tier available |

**Optional keys:**
| Key | Purpose |
|-----|---------|
| `OPENAI_API_KEY` | AI-enhanced event parsing via GPT |
| `SLACK_WEBHOOK_URL` | Slack alert notifications |
| `SMTP_*` | Email alert notifications |

### 3. Run the app

```bash
streamlit run app.py
```

The app will open at `http://localhost:8501`

### 4. Set up the database

The SQLite database (`supplier_risk.db`) is **created automatically** on first run. No manual setup needed.

---

## 📋 Using the Dashboard

### Step 1: Upload Suppliers
- Download the sample CSV from the sidebar
- Fill it with your supplier data
- Upload via the file uploader

**Required CSV columns:**
| Column | Example |
|--------|---------|
| Supplier Name | Acme Electronics |
| Category | Electronics |
| City | Shenzhen |
| Country | China |
| Tier | 1 |

### Step 2: Fetch Events
- Click **"🔄 Fetch Latest Events"** in the sidebar
- The system pulls news and weather/disaster data
- Events are stored in the database

### Step 3: Score Suppliers
- Click **"⚡ Recalculate Risk Scores"** in the sidebar
- Risk scores are computed per supplier based on event proximity

### Step 4: Review & Alert
- Review the map, top-5 list, and full table
- Use filters to focus on high-risk suppliers
- Click **"🔔 Send Alerts"** to notify via email/Slack

---

## ⚙️ Risk Scoring Logic

| Condition | Points Added |
|-----------|-------------|
| Event in same country as supplier | +70 |
| Event in same continent/region | +40 |
| Event elsewhere | +10 |

Multiple events are aggregated. Scores are capped at 100.

**Risk Levels:**
- 🔴 **High**: Score > 50
- 🟡 **Medium**: Score 26–50
- 🟢 **Low**: Score ≤ 25

---

## 🤖 AI-Enhanced Scoring (Optional)

If an `OPENAI_API_KEY` is set, the system uses GPT-4o-mini to:
- Classify whether a news article is likely to cause supply disruption
- Extract the affected country/region
- Estimate severity (low/medium/high)

This enriches the risk scoring beyond keyword matching.

---

## 🚀 Deploy to Streamlit Cloud

1. Push your code to a **public GitHub repo** (do NOT include `.env` or `supplier_risk.db`)
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your repo and set `app.py` as the entry point
4. Add your API keys under **Settings > Secrets** using the format in `.streamlit/secrets.toml`

> **Note:** Streamlit Cloud has ephemeral storage — the SQLite database resets on each deployment. For production, replace SQLite with a hosted database (e.g., Supabase, PlanetScale).

---

## 🔔 Configuring Alerts

### Email (Gmail)
1. Enable 2FA on your Google account
2. Generate an App Password at [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
3. Set `SMTP_USER`, `SMTP_PASS`, `ALERT_EMAIL_TO` in `.env`

### Slack
1. Go to [api.slack.com/apps](https://api.slack.com/apps) → Create App
2. Enable "Incoming Webhooks"
3. Add a webhook URL for your target channel
4. Set `SLACK_WEBHOOK_URL` in `.env`

---

## 🔮 Future Enhancements

- [ ] ML predictive scoring based on historical disruption data
- [ ] Supplier relationship graph (multi-tier dependencies)
- [ ] Automated daily scheduling with Celery or APScheduler
- [ ] PostgreSQL backend for production deployments
- [ ] Supplier onboarding self-service portal
- [ ] PDF risk report generation

---

## 📦 Dependencies

- **Streamlit** — UI framework
- **Pandas** — Data manipulation
- **Plotly** — Interactive maps and charts
- **Requests** — API calls
- **pycountry / pycountry-convert** — Country/continent mapping
- **python-dotenv** — Environment variable loading
- **OpenAI** (optional) — AI event parsing
- **SQLite3** (stdlib) — Local database

---

## 🐛 Troubleshooting

**"Missing required columns"**: Ensure your CSV has exact column names (case-sensitive).

**Geocoding is slow**: Nominatim allows 1 request/second. 20 suppliers = ~20 seconds. This is by design.

**NewsAPI 426 error**: Free tier only allows recent articles. Upgrade or use a different API endpoint.

**Map shows no markers**: Run geocoding after upload. Suppliers need lat/lon to appear on the map.
