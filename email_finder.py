import re
import httpx

EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Skip social platforms / directories - not the business's own site, no point crawling
SKIP_DOMAINS = (
    "instagram.com", "facebook.com", "youtube.com", "twitter.com", "x.com",
    "linkedin.com", "wa.me", "whatsapp.com", "justdial.com", "practo.com",
    "parivahan.gov.in", "goo.gl", "g.page",
)

# Common junk matches to filter out (image filenames misparsed as emails, etc.)
JUNK_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def _clean_candidates(text):
    found = []
    for m in EMAIL_RE.findall(text):
        low = m.lower()
        if low.endswith(JUNK_SUFFIXES):
            continue
        if "example.com" in low or "sentry.io" in low or "wixpress.com" in low:
            continue
        found.append(m)
    return found


async def find_email(website_url: str, client: httpx.AsyncClient) -> str | None:
    """Best-effort: fetch the homepage (and /contact-us if homepage has no email),
    return the first plausible email address found, or None."""
    if not website_url:
        return None
    if any(d in website_url for d in SKIP_DOMAINS):
        return None

    pages_to_try = [website_url]
    base = website_url.rstrip("/")
    pages_to_try += [f"{base}/contact", f"{base}/contact-us", f"{base}/about"]

    for url in pages_to_try:
        try:
            resp = await client.get(url, timeout=8.0, follow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text

            # mailto: links first - most reliable signal
            mailto_matches = re.findall(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', html)
            candidates = _clean_candidates(" ".join(mailto_matches)) or _clean_candidates(html)

            if candidates:
                return candidates[0]
        except Exception:
            continue

    return None
