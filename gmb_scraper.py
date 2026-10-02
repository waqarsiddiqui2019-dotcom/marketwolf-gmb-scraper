"""
Daily GMB (Google Maps) lead scraper for Marketwolf - niche-agnostic (started
with doctors, but --niche/--search-term let it target vets, salons, etc).

Flow (one pincode at a time, ascending, Mumbai then Navi Mumbai then Thane -
see locations.py):
  1. For the Doctors niche, every pincode is searched with each specialty in
     SPECIALTY_SEARCH_TERMS priority order (cardiologist, dermatologist, ...)
     BEFORE the generic "doctors" catch-all for that same pincode - so a run
     cut off by the daily lead target still favors specialists over generic
     listings. Other niches just use their one --search-term.
  2. Each pincode+term search scrolls until Google stops returning new
     results (not capped early) - "all doctors in this pincode" before
     moving on.
  3. Extract name, phone, address, website, GMB link, and Google's own
     per-listing category for each result.
  4. Best-effort visit each business's own website to find a public email.
  5. Skip anything whose phone number is already in the Google Sheet
     (fetched once at the start via the Apps Script doGet endpoint).
  6. POST each new lead to the Apps Script doPost endpoint, which appends a
     row, tagged with --niche as its Category.
  7. Stop once DAILY_LEAD_TARGET new leads have been sent, OR the whole
     pincode+term grid has been covered once. Progress through the grid
     (which pincode/term to resume from next time) is saved to the sheet
     itself via the Apps Script's get_scraper_cursor/set_scraper_cursor
     actions, so tomorrow's run - a brand new cloud process with no local
     state - picks up exactly where today's left off instead of re-scraping
     Mumbai pincode 400001 every single day.

Usage:
  python gmb_scraper.py                                   # doctors (default)
  python gmb_scraper.py --niche Vets --search-term "veterinary clinic"
  python gmb_scraper.py --niche Salons --search-term "salon"

Known limitations (accepted risk, discussed with the client):
  - Google Maps scraping is against Google's Terms of Service. This script will
    likely get CAPTCHA-blocked or IP-limited at some point running from a fixed
    cloud IP on a daily schedule. When that happens, the run stops early and
    prints a warning - it needs to be watched, not "fire and forget forever."
  - Google Maps doesn't expose email directly; email is only found for
    businesses whose own website publishes one, so expect partial coverage.
"""

import argparse
import asyncio
import re
import sys

import httpx
from scrapling.fetchers import AsyncStealthySession

from locations import build_priority_queries, build_queries, GENERAL_FALLBACK_TERM
from email_finder import find_email

WEBHOOK_URL = "https://script.google.com/macros/s/AKfycbwFx_9J6geeA38SMdF5QFZ4jbkZzRzKIZ-00cELpARAbhXE3k6mP4z66H_z52QEgKV8/exec"
SECRET = "marketwolf-leads-2026"

# Google's result-list cards never include the listing's real pincode (only
# the full detail page does), so without this every lead was tagged with
# whichever pincode happened to be SEARCHED - wrong whenever Google's
# radius-based "near <pincode>" search pulled in a business from a
# neighbouring pincode (discovered 2026-10-01: e.g. a lead actually in 400086
# was labelled 400001 because that's what the search grid was on). Instead,
# pull the lat/lng Google Maps already embeds in the listing's own link and
# reverse-geocode that to the true pincode via Nominatim (OSM) - free, no API
# key, but its usage policy caps public requests at 1/sec, so calls are
# serialized through _last_geocode_at.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {"User-Agent": "marketwolf-gmb-scraper/1.0 (+https://marketwolf.pro)"}
_last_geocode_at = 0.0
_geocode_lock = asyncio.Lock()


def extract_coords(gmb_link: str | None):
    """Lat/lng Google Maps embeds in its own listing links. Checked two formats
    actually seen from these links: the place-page '...!3d<lat>!4d<lng>!...'
    (what card hrefs here actually produce) and the generic map-view '/@lat,lng,zoom'."""
    if not gmb_link:
        return None
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", gmb_link)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"/@(-?\d+\.\d+),(-?\d+\.\d+)", gmb_link)
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


async def reverse_geocode_pincode(client: httpx.AsyncClient, lat: float, lng: float) -> str | None:
    """True postal code for a lat/lng, via Nominatim. None on any failure - caller falls back."""
    global _last_geocode_at
    async with _geocode_lock:
        wait = _last_geocode_at + 1.1 - asyncio.get_event_loop().time()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_geocode_at = asyncio.get_event_loop().time()
    try:
        resp = await client.get(
            NOMINATIM_URL,
            params={"format": "jsonv2", "lat": lat, "lon": lng, "zoom": 18, "addressdetails": 1},
            headers=NOMINATIM_HEADERS,
            timeout=10.0,
        )
        data = resp.json()
        pincode = (data.get("address") or {}).get("postcode")
        return pincode.strip() if pincode and re.match(r"^\d{6}$", pincode.strip()) else None
    except Exception:
        return None

DAILY_LEAD_TARGET = 100
# High enough that it never fires before Google's own infinite-scroll limit
# does (Maps typically stops adding new cards well before this on a single
# search) - "all doctors in this pincode", not "first 30".
MAX_RESULTS_PER_QUERY = 200
NAV_TIMEOUT_MS = 30000
SCROLL_WAIT_MS = 1800
MAX_STAGNANT_SCROLLS = 3

EXTRACT_JS = """
() => {
  const cards = Array.from(document.querySelectorAll('div[role="feed"] div[role="article"]'));
  return cards.map(card => {
    const linkEl = card.querySelector('a.hfpxzc');
    const name = linkEl ? linkEl.getAttribute('aria-label') : null;
    const gmbLink = linkEl ? linkEl.getAttribute('href') : null;
    const phoneEl = card.querySelector('.UsdlK');
    const phone = phoneEl ? phoneEl.textContent.trim() : null;
    const websiteEl = card.querySelector('a.lcr4fd');
    const website = websiteEl ? websiteEl.getAttribute('href') : null;
    const infoDivs = Array.from(card.querySelectorAll('.W4Efsd'));
    let categoryAddress = '';
    for (const div of infoDivs) {
      if (div.querySelector('.UsdlK')) continue;
      if (div.querySelector('.AJB7ye')) continue;
      const t = div.textContent.trim();
      if (t) { categoryAddress = t; break; }
    }
    return {name, gmbLink, phone, website, categoryAddress};
  });
}
"""


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    return digits if digits else None


def split_category_address(category_address: str):
    parts = [p.strip() for p in category_address.split("·") if p.strip()]
    if not parts:
        return "", ""
    category = parts[0]
    address = ", ".join(parts[1:]) if len(parts) > 1 else ""
    return category, address


async def fetch_existing_phones() -> set:
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(WEBHOOK_URL, params={"secret": SECRET}, timeout=15.0)
            data = resp.json()
            phones = data.get("phones", [])
            return {normalize_phone(p) for p in phones if normalize_phone(p)}
        except Exception as e:
            print(f"WARNING: could not fetch existing phones ({e}). Proceeding with empty dedup set.", file=sys.stderr)
            return set()


async def get_scraper_cursor(niche: str) -> int:
    """Resume position (an index into this niche's flat pincode+term grid),
    persisted in the sheet so a fresh daily cloud run picks up where
    yesterday's left off instead of restarting from Mumbai 400001 every time."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(WEBHOOK_URL, params={"secret": SECRET, "action": "get_scraper_cursor", "niche": niche}, timeout=15.0)
            data = resp.json()
            return int(data.get("cursor", 0))
        except Exception as e:
            print(f"WARNING: could not fetch scraper cursor ({e}). Starting from 0.", file=sys.stderr)
            return 0


async def set_scraper_cursor(client: httpx.AsyncClient, niche: str, cursor: int):
    try:
        await client.post(WEBHOOK_URL, json={
            "secret": SECRET, "action": "set_scraper_cursor", "niche": niche, "cursor": cursor,
        }, timeout=15.0, follow_redirects=False)
    except Exception as e:
        print(f"  ! could not save scraper cursor: {e}", file=sys.stderr)


async def sort_sheet_by_location():
    async with httpx.AsyncClient() as client:
        try:
            await client.post(WEBHOOK_URL, json={"secret": SECRET, "action": "sort"}, timeout=15.0, follow_redirects=False)
            print("Sheet sorted by location.")
        except Exception as e:
            print(f"WARNING: could not sort sheet ({e})", file=sys.stderr)


async def post_lead(client: httpx.AsyncClient, lead: dict):
    payload = {"secret": SECRET, **lead}
    try:
        resp = await client.post(WEBHOOK_URL, json=payload, timeout=15.0, follow_redirects=False)
        # Apps Script returns a 302 to a googleusercontent echo URL - that alone
        # confirms the POST reached the script and it ran without throwing.
        if resp.status_code not in (200, 302):
            print(f"  ! Unexpected status {resp.status_code} posting {lead.get('name')}")
    except Exception as e:
        print(f"  ! Failed to post lead {lead.get('name')}: {e}")


# Google Maps list cards stopped showing phone numbers (2026-10-02) - the
# number is only on each place's own page. Fallback: open the place page and
# read the phone button. Only used for cards whose Google category matches
# --category-keywords (if given), to keep page loads down.
DETAIL_PHONE_JS = """() => {
  const b = document.querySelector('button[data-item-id^="phone:tel:"]');
  return b ? b.getAttribute('data-item-id').replace('phone:tel:', '') : null;
}"""
CATEGORY_KEYWORDS: list = []
_detail_seen_links: set = set()


async def fetch_phone_from_detail(session, gmb_link):
    if not gmb_link or gmb_link in _detail_seen_links:
        return None
    _detail_seen_links.add(gmb_link)
    captured = {"phone": None}

    async def act(page):
        try:
            await page.wait_for_selector("h1", timeout=10000)
            await page.wait_for_timeout(1200)
            captured["phone"] = await page.evaluate(DETAIL_PHONE_JS)
        except Exception:
            pass

    try:
        await session.fetch(gmb_link, page_action=act, timeout=30000)
    except Exception as e:
        print(f"  ! detail page failed: {e}")
    await asyncio.sleep(2.0)
    return captured["phone"]


def category_matches(card_category: str) -> bool:
    if not CATEGORY_KEYWORDS:
        return True
    c = (card_category or "").lower()
    return any(k in c for k in CATEGORY_KEYWORDS)


async def scrape_query(session, city, pincode, term, query, niche, seen_phones_global, http_client):
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}"
    print(f"\n[{city} / {pincode} / {term}] {url}")

    # All the real browser automation (scroll-until-stagnant, then extract)
    # happens here via Scrapling's page_action, which hands us the same
    # Playwright `page` object this used to drive directly - so the actual
    # scrolling/extraction logic is unchanged from the raw-Playwright version.
    # Results are captured via this closure since page_action itself has no
    # return path back to the caller.
    captured = {"raw_results": None, "blocked": False}

    async def automate(page):
        try:
            await page.wait_for_selector('div[role="feed"]', timeout=10000)
        except Exception as e:
            print(f"  ! Could not load results (possibly blocked): {e}")
            return

        content = await page.content()
        if "unusual traffic" in content.lower() or "recaptcha" in content.lower():
            print("  !!! BLOCKED: Google is showing a CAPTCHA / unusual-traffic wall. Stopping this run. !!!")
            captured["blocked"] = True
            return

        prev_count = 0
        stagnant = 0
        while stagnant < MAX_STAGNANT_SCROLLS:
            cards = await page.query_selector_all('div[role="feed"] div[role="article"]')
            count = len(cards)
            if count >= MAX_RESULTS_PER_QUERY:
                break
            stagnant = stagnant + 1 if count == prev_count else 0
            prev_count = count
            await page.evaluate("""
                () => { const f = document.querySelector('div[role="feed"]'); if (f) f.scrollTop = f.scrollHeight; }
            """)
            await page.wait_for_timeout(SCROLL_WAIT_MS)

        captured["raw_results"] = await page.evaluate(EXTRACT_JS)

    try:
        await session.fetch(url, page_action=automate, timeout=NAV_TIMEOUT_MS)
    except Exception as e:
        print(f"  ! Could not load results (possibly blocked): {e}")
        return 0

    if captured["blocked"]:
        raise RuntimeError("BLOCKED_BY_GOOGLE")
    if not captured["raw_results"]:
        return 0

    raw_results = captured["raw_results"][:MAX_RESULTS_PER_QUERY]

    new_count = 0
    query_seen_gmb = set()

    for r in raw_results:
        gmb_link = r.get("gmbLink")
        if gmb_link in query_seen_gmb:
            continue  # duplicate within this same scroll (Google Maps re-renders sometimes)
        query_seen_gmb.add(gmb_link)

        phone_norm = normalize_phone(r.get("phone"))
        if not phone_norm:
            card_cat, _ = split_category_address(r.get("categoryAddress") or "")
            if category_matches(card_cat):
                detail_phone = await fetch_phone_from_detail(session, gmb_link)
                if detail_phone:
                    r["phone"] = detail_phone
                    phone_norm = normalize_phone(detail_phone)
        if not phone_norm:
            continue  # no phone = not usable as a lead
        if phone_norm in seen_phones_global:
            continue  # already in the sheet from a previous run, or seen earlier this run

        # "specialty" here is Google's own per-listing business type (e.g.
        # "General practitioner", "Dermatologist") - distinct from both
        # "niche" (Doctors/Vets/Salons) and the search term that found this
        # result, since Google's classification of the actual business is
        # more reliable than whichever specialty term happened to surface it.
        specialty, address = split_category_address(r.get("categoryAddress") or "")
        website = r.get("website")
        email = await find_email(website, http_client) if website else None

        coords = extract_coords(gmb_link)
        real_pincode = await reverse_geocode_pincode(http_client, *coords) if coords else None
        location = f"{city} - {real_pincode}" if real_pincode else f"{city} - {pincode}"
        if real_pincode and real_pincode != pincode:
            print(f"  (real pincode {real_pincode}, not searched {pincode})")

        lead = {
            "location": location,
            "name": r.get("name") or "",
            "phone": r.get("phone") or "",
            "email": email or "",
            "address": address or specialty,
            "website": website or "",
            "gmbLink": gmb_link or "",
            "category": niche,
            "specialty": specialty,
        }

        await post_lead(http_client, lead)
        seen_phones_global.add(phone_norm)
        new_count += 1
        print(f"  + {lead['name']} | {lead['phone']} | email={'yes' if email else 'no'}")

    print(f"  -> {len(raw_results)} found, {new_count} new leads sent")
    return new_count


def parse_args():
    parser = argparse.ArgumentParser(description="GMB lead scraper (niche-agnostic).")
    parser.add_argument("--niche", default="Doctors",
                         help='Category tag stored on each lead, e.g. "Vets", "Salons" (default: Doctors)')
    parser.add_argument("--search-term", default="doctors",
                         help='Google Maps search phrase for non-Doctors niches, e.g. "veterinary clinic", "salon" (ignored for Doctors, which uses the specialty-priority list instead)')
    parser.add_argument("--daily-target", type=int, default=DAILY_LEAD_TARGET,
                         help=f"Stop once this many new leads have been sent (default: {DAILY_LEAD_TARGET})")
    parser.add_argument("--category-keywords", default="",
                         help="Comma-separated, case-insensitive; only open detail pages (for phone) of cards whose Google category contains one of these")
    parser.add_argument("--start-index", type=int, default=None,
                         help="Grid index to resume from, overriding the remote (Sheet-persisted) cursor. "
                              "Use when the remote cursor isn't persisting reliably - the local "
                              "cursor_<niche>.txt file (written after every step) gives the value to pass here.")
    return parser.parse_args()


def build_grid(args):
    """Flat, ordered (city, pincode, term, query) list for this niche - the
    full priority-ordered grid for Doctors, or a flat single-term grid for
    any other niche."""
    if args.niche == "Doctors":
        return list(build_priority_queries())
    # --search-term may be comma-separated ("interior designer,interior decorator"):
    # the grid interleaves the terms per pincode so every area gets all of them
    # before moving on, instead of finishing one term across the whole city first.
    terms = [t.strip() for t in args.search_term.split(",") if t.strip()]
    per_term = [[(city, pincode, t, query) for city, pincode, query in build_queries(t)] for t in terms]
    return [row for group in zip(*per_term) for row in group]


async def main():
    args = parse_args()
    CATEGORY_KEYWORDS[:] = [k.strip().lower() for k in args.category_keywords.split(",") if k.strip()]
    print(f"Niche: {args.niche} | Daily target: {args.daily_target} leads")

    grid = build_grid(args)
    if args.start_index is not None:
        cursor = args.start_index
        print(f"Using --start-index override ({cursor}) instead of the remote cursor.")
    else:
        cursor = await get_scraper_cursor(args.niche)
    cursor = cursor % len(grid)
    print(f"Grid has {len(grid)} pincode+term combinations. Resuming from index {cursor}.")

    niche_slug = re.sub(r"[^a-zA-Z0-9]+", "_", args.niche).strip("_").lower()
    local_cursor_file = f"cursor_{niche_slug}.txt"

    print("Fetching already-recorded phone numbers from the sheet for dedup...")
    seen_phones = await fetch_existing_phones()
    print(f"Loaded {len(seen_phones)} existing phone numbers.")

    total_new = 0
    blocked = False
    steps_taken = 0

    async with httpx.AsyncClient() as http_client:
        # StealthyFetcher/AsyncStealthySession (patchright under the hood) in
        # place of raw Playwright - same automation (page_action still gets
        # the real Playwright `page`), but with realistic fingerprints, canvas
        # noise, WebRTC/CDP-leak patches, etc. to reduce the chance of Google's
        # CAPTCHA/"unusual traffic" wall in the first place. max_pages=1 since
        # queries are still run one at a time, matching the old single-page
        # behaviour the resume cursor / politeness delay assume.
        async with AsyncStealthySession(
            headless=True,
            max_pages=1,
            block_webrtc=True,
            useragent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            locale="en-IN",
            extra_flags=["--disable-gpu", "--disable-dev-shm-usage"],
        ) as session:
            # Bounded by len(grid) so a niche that's fully out of new
            # leads (everything already scraped) can't spin forever -
            # one full lap without hitting the target just ends the run.
            for _ in range(len(grid)):
                if total_new >= args.daily_target:
                    break

                city, pincode, term, query = grid[cursor]
                try:
                    new_count = await scrape_query(session, city, pincode, term, query, args.niche, seen_phones, http_client)
                    total_new += new_count
                except RuntimeError as e:
                    if str(e) == "BLOCKED_BY_GOOGLE":
                        print("\nStopping entire run - Google has blocked this session.")
                        blocked = True
                        break
                    raise

                cursor = (cursor + 1) % len(grid)
                steps_taken += 1
                await set_scraper_cursor(http_client, args.niche, cursor)
                try:
                    with open(local_cursor_file, "w") as f:
                        f.write(str(cursor))
                except Exception as e:
                    print(f"  ! could not write local cursor file: {e}", file=sys.stderr)
                await asyncio.sleep(2.5)  # be a little polite between queries

    if total_new > 0:
        await sort_sheet_by_location()

    status = "BLOCKED by Google" if blocked else ("daily target reached" if total_new >= args.daily_target else "full grid lap completed")
    print(f"\n=== DONE ({status}). {total_new} new leads added across {steps_taken} pincode+term searches. Next run resumes at grid index {cursor}. ===")


if __name__ == "__main__":
    asyncio.run(main())
