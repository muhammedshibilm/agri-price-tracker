# Agri Price Tracker — Data Pipeline

## About the repo name
You get to pick this — I can't know it for you. Two options:

1. **Create a brand-new GitHub repo** and name it whatever you like
   (e.g. `agri-price-tracker`, `kerala-mandi-prices`, `farmgate-predictor`).
   Set it to **Private** in the creation screen if you don't want the data
   or predictions visible to anyone but you.
2. **Already have a repo?** Just drop these files into it at the same
   relative paths shown below.

## What's in this folder
```
agri-price-tracker/
├── fetch_data.py                        # main pipeline script
├── requirements.txt
├── data/
│   ├── data.json                        # generated output for your Flutter app
│   └── history/                         # per-commodity CSV price history (auto-created)
└── .github/workflows/daily-update.yml   # runs fetch_data.py once a day
```

## Before this will work
Open `fetch_data.py` and replace `fetch_price_from_source()` — right now it
deliberately raises an error instead of returning fake data, so you don't
accidentally ship garbage predictions. Plug in whatever real price source
you're using (official API, licensed provider, or a scrape you've confirmed
is allowed by that site's Terms of Service).

## Steps to go live
1. Push this folder to your repo (private, if you want the data hidden).
2. On GitHub: **Settings → Actions → General → Workflow permissions** →
   set to "Read and write permissions" (needed for the daily commit step).
3. Test it manually first: **Actions tab → Daily Price Update → Run workflow**.
4. Check `data/data.json` got updated and committed.
5. From then on it runs automatically every day at 01:00 UTC (edit the
   cron line in the workflow file to change the time).

## If you add an API key later
Never hardcode it. Go to **Settings → Secrets and variables → Actions →
New repository secret**, then reference it in the workflow as
`${{ secrets.YOUR_SECRET_NAME }}` (see the commented example in
`daily-update.yml`).
