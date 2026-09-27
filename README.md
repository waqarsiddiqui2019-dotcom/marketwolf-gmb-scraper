# Marketwolf GMB Scraper

Daily Google Maps lead scraper for Marketwolf, covering Mumbai / Navi Mumbai
/ Thane pincode-by-pincode (see `locations.py`). Doctors is the current
niche (specialists prioritized before generic listings); `--niche` /
`--search-term` support other verticals (vets, salons, etc).

Leads are posted to a Google Sheet via an Apps Script webhook, which the
Marketwolf CRM (crm.marketwolf.pro) polls every few minutes to create leads
automatically.

Run manually:
```
pip install -r requirements.txt
playwright install --with-deps chromium
python gmb_scraper.py
```

See `CLOUD_PROMPT.md` for the daily automated-run instructions used by the
scheduled cloud routine.
