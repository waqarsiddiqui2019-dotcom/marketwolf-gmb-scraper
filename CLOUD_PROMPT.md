# Marketwolf GMB Scraper - Daily Cloud Run

This is a self-contained daily automation run. Follow it exactly, start to
finish, autonomously. No confirmation needed from the user - this repo IS
the confirmation.

## What this does

Scrapes Google Maps for doctor leads across Mumbai, Navi Mumbai, and Thane
(pincode by pincode, specialists prioritized before generic "doctor"
listings - see `locations.py`), and sends each new lead to a Google Sheet
via an Apps Script webhook. That sheet is separately synced into
crm.marketwolf.pro every few minutes by the CRM's own cron job - nothing
else needs to happen here.

Progress through the full pincode+specialty grid is saved in the sheet
itself (a "ScraperState" tab, managed by the Apps Script), so every day's
run automatically continues from where the previous run left off. Nothing
in this repo needs to track that - just run the script.

## Steps

1. Install dependencies:
   ```
   pip install -r requirements.txt
   playwright install --with-deps chromium
   ```

2. Run the scraper with its defaults (Doctors niche, stops automatically
   after 120 new leads or one full lap of the grid, whichever comes first):
   ```
   python gmb_scraper.py
   ```

3. Read the script's own final summary line (starts with `=== DONE`) and
   report it back as your result for this run - how many new leads were
   added, whether it hit the daily target or got blocked by Google, and the
   grid index it will resume from tomorrow. No other action is needed.

## If Google blocks the run (CAPTCHA / "unusual traffic")

The script detects this itself, stops immediately, and prints a warning.
That's expected occasionally, not a bug - just report it plainly (how many
leads were added before the block, if any) and end the run. Do not retry
with a different approach, different user agent, proxy, etc - respect the
block for this run; tomorrow's scheduled run will simply try again.

## Explicitly out of scope for this routine

- Do NOT set up WhatsApp messaging, drip campaigns, or any outbound contact
  to the leads. That is a deliberately separate, not-yet-built piece of the
  project - leave it alone.
- Do NOT modify locations.py, gmb_scraper.py, email_finder.py, or the Apps
  Script/webhook. If the run fails because of an actual code bug (not a
  Google block), report the error clearly instead of attempting a fix.
- Do NOT change the Google Sheet's structure, headers, or the CRM's cron
  sync - this routine's only job is running the scraper script.
