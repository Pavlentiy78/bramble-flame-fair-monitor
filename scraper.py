#!/usr/bin/env python3
"""Scrape craft-fair listing sites and email Pavlo about new ones.

Usage:
    python scraper.py                 # scrape, email new listings, update seen.json
    python scraper.py --dry-run       # scrape and print new listings; no email, no seen.json write
    python scraper.py --dump-html DIR # fetch each source's raw HTML into DIR, then exit
                                       # (used to inspect real markup and calibrate selectors -
                                       # see README "Calibrating selectors")
"""
import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
import yaml
from bs4 import BeautifulSoup

import notify

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("scraper")

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.yaml"
SEEN_FILE = ROOT / "seen.json"

# A bare User-Agent (plus requests' default Accept: */*) was enough to get a
# 415 Unsupported Media Type from stallandcraftcollective.co.uk - its server
# (or a WAF in front of it) evidently rejects requests that don't look like a
# real browser navigation. requests never sets Content-Type on a bodyless
# GET, so that wasn't it; this sends the header set a real Chrome navigation
# sends instead.
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}


# stallandcraftcollective.co.uk started blocking with a "One moment,
# please..." interstitial recurring intermittently (both morning and,
# later, evening runs - 31 July and 2 Aug) even after moving the whole
# job off the morning slot. Its 4 county pages were previously fetched
# back-to-back within a few seconds of each other; spacing them out is
# worth trying before resorting to something more invasive (rotating
# headers, etc). Keyed by host so this doesn't slow down Stallfinder.
MIN_REQUEST_INTERVAL_SECONDS = {
    "stallandcraftcollective.co.uk": 8,
}
_last_request_at = {}


def fetch(url):
    host = urlparse(url).netloc.removeprefix("www.")
    min_interval = MIN_REQUEST_INTERVAL_SECONDS.get(host)
    if min_interval:
        last_at = _last_request_at.get(host)
        if last_at is not None:
            wait = min_interval - (time.monotonic() - last_at)
            if wait > 0:
                time.sleep(wait)
        _last_request_at[host] = time.monotonic()

    response = requests.get(url, headers=REQUEST_HEADERS, timeout=30)
    response.raise_for_status()
    return response


def _page_title(html):
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


# --- Per-site parsers -------------------------------------------------
# Each parser takes the raw page HTML and the source URL, and returns a
# list of dicts: {"name": str, "date": str, "venue": str, "url": str}.
# `name` is required; leave the rest as "" if a field isn't found.
#
# Selectors below are calibrated against real saved HTML from each site's
# Derbyshire listing page (July 2026). If a source starts returning 0
# listings, its markup has likely changed - use `python scraper.py
# --dump-html debug_html/` to grab fresh HTML and adjust. See README
# "Calibrating selectors".

def stall_and_craft_collective(html, source_url):
    # Each fair is a <div class="ev_block"> (NOT .featured_block - those are
    # marketplace products, not fairs). Inside: name in .event_heading (an
    # <a> itself), fields as <li><span class="font4">Label:</span> value</li>.
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for block in soup.select("div.ev_block"):
        heading_el = block.select_one(".event_heading")
        if not heading_el or not heading_el.get_text(strip=True):
            continue

        fields = {}
        for li in block.select("li"):
            label_el = li.select_one(".font4")
            if not label_el:
                continue
            label_text = label_el.get_text(strip=True)
            label = label_text.rstrip(":").strip().lower()
            fields[label] = li.get_text(strip=True)[len(label_text):].strip()

        href = heading_el.get("href")
        if not href:
            img_link = block.select_one("a.event_img")
            href = img_link.get("href") if img_link else None

        listings.append(
            {
                "name": heading_el.get_text(strip=True),
                "date": fields.get("date", ""),
                "venue": fields.get("venue", ""),
                "url": urljoin(source_url, href) if href else source_url,
            }
        )
    return listings


def stallfinder(html, source_url):
    # Each fair is a <div class="box_listing">. Name in "h2 a", venue/county
    # in <p class="county_contact"> as "Venue: ..." / "County: ...", dates as
    # bare "Start Date: .../End Date: ..." or "Date: ..." text (markup here
    # is a bit malformed - an unmatched </p> - so we read labelled text from
    # the whole card rather than relying on strict nesting), link in a.btn_more.
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    for card in soup.select("div.box_listing"):
        heading_link = card.select_one("h2 a")
        if not heading_link or not heading_link.get_text(strip=True):
            continue

        parts = [p for p in card.get_text(separator="|", strip=True).split("|") if p]
        fields = {}
        i = 0
        while i < len(parts) - 1:
            if parts[i].endswith(":"):
                fields[parts[i].rstrip(":").strip().lower()] = parts[i + 1]
                i += 2
            else:
                i += 1

        start_date = fields.get("start date", "")
        end_date = fields.get("end date", "")
        if start_date and end_date:
            date = f"{start_date} to {end_date}"
        else:
            date = start_date or end_date or fields.get("date", "")

        more_link = card.select_one("a.btn_more[href]")
        href = more_link.get("href") if more_link else heading_link.get("href")

        listings.append(
            {
                "name": heading_link.get_text(strip=True),
                "date": date,
                "venue": fields.get("venue", ""),
                "url": urljoin(source_url, href) if href else source_url,
            }
        )
    return listings


PARSERS = {
    "stall_and_craft_collective": stall_and_craft_collective,
    "stallfinder": stallfinder,
}


def make_id(name, date, venue):
    key = f"{name}|{date}|{venue}".strip().lower()
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def load_sources():
    with open(SOURCES_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["sources"]


def load_seen():
    if not SEEN_FILE.exists():
        return {}
    with open(SEEN_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(seen, f, indent=2, sort_keys=True)
        f.write("\n")


def _next_page_url(url, page_size):
    """Given a '.../<offset>/' listing URL, return the URL for the next page.

    Stallfinder paginates by putting a numeric result-offset as the last URL
    segment (.../derbyshire/0/, .../derbyshire/20/, .../derbyshire/40/, ...
    confirmed from the "Next"/page-number links on a real results page).
    Returns None if the URL doesn't end in a number (nothing to paginate).
    """
    match = re.match(r"^(.*/)(\d+)(/?)$", url)
    if not match:
        return None
    prefix, offset, trailing_slash = match.groups()
    return f"{prefix}{int(offset) + page_size}{trailing_slash}"


def _fetch_and_parse_page(source, url, parser):
    """Fetch + parse a single page. Returns (listings, error, response_or_None)."""
    try:
        response = fetch(url)
    except requests.RequestException as exc:
        return [], f"{source['name']}: fetch failed ({exc})", None

    html = response.text

    try:
        raw_listings = parser(html, source["url"])
    except Exception as exc:  # a broken selector shouldn't kill the whole run
        return [], f"{source['name']}: parse failed ({exc})", response

    listings = []
    for item in raw_listings:
        if not item.get("name"):
            continue
        item["source"] = source["name"]
        item["county"] = source.get("county", "")
        listings.append(item)

    return listings, None, response


def scrape_source(source):
    """Fetch + parse a source, following pagination if configured.

    Set `paginate: true` (and optionally `page_size`, default 20) on a
    source in sources.yaml to follow Stallfinder-style numeric-offset
    pagination until a page comes back with 0 listings. Returns
    (listings, error_message_or_None, warning_message_or_None).
    """
    parser = PARSERS.get(source["parser"])
    if parser is None:
        return [], f"{source['name']}: unknown parser '{source['parser']}'", None

    paginate = source.get("paginate", False)
    page_size = source.get("page_size", 20)
    max_pages = 20  # safety cap so a pagination bug can't loop forever

    all_listings = []
    last_response = None
    last_html = ""
    url = source["url"]

    for page_num in range(max_pages):
        listings, error, response = _fetch_and_parse_page(source, url, parser)
        if error:
            if page_num == 0:
                return [], error, None
            break  # a later page failing (e.g. past the last real page) just ends pagination

        last_response, last_html = response, response.text
        if not listings:
            break

        all_listings.extend(listings)

        if not paginate:
            break

        next_url = _next_page_url(url, page_size)
        if not next_url:
            break
        url = next_url

    warning = None
    if not all_listings:
        # Selectors matching 0 elements and the server not sending the page
        # we expect (WAF/anti-bot interstitial, redirect, rate limiting) look
        # identical from the "0 listings" count alone. Surface what was
        # actually received - both in the log and in the daily email - so a
        # recurrence is diagnosable without digging through Actions logs or
        # requiring another manual HTML dump.
        warning = (
            f"{source['name']}: parsed 0 listings - status={last_response.status_code if last_response else None} "
            f"final_url={last_response.url if last_response else url} "
            f"bytes={len(last_response.content) if last_response else 0} "
            f"title={_page_title(last_html)!r}"
        )
        log.warning(warning)

    return all_listings, None, warning


def dump_html(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in load_sources():
        slug = source["name"].lower().replace(" ", "_").replace("/", "_")
        try:
            response = fetch(source["url"])
        except requests.RequestException as exc:
            log.error("%s: fetch failed (%s)", source["name"], exc)
            continue
        html = response.text
        path = out_dir / f"{slug}.html"
        path.write_text(html, encoding="utf-8")
        log.info(
            "saved %s (%d bytes, status=%s, final_url=%s)",
            path,
            len(html),
            response.status_code,
            response.url,
        )


def run(dry_run=False):
    sources = load_sources()
    seen = load_seen()
    new_listings = []
    errors = []
    warnings = []

    for source in sources:
        log.info("checking %s", source["name"])
        listings, error, warning = scrape_source(source)
        if error:
            log.error(error)
            errors.append(error)
            continue
        if warning:
            warnings.append(warning)

        for listing in listings:
            listing_id = make_id(listing["name"], listing.get("date", ""), listing.get("venue", ""))
            if listing_id not in seen:
                new_listings.append(listing)
                seen[listing_id] = {**listing, "id": listing_id}

    log.info(
        "found %d new listing(s), %d error(s), %d source(s) with 0 listings",
        len(new_listings),
        len(errors),
        len(warnings),
    )

    if dry_run:
        for listing in new_listings:
            print(json.dumps(listing, indent=2))
        for warning in warnings:
            print(f"WARNING: {warning}")
        return 1 if errors else 0

    # Sent every run, not just when there's something new - a source silently
    # going empty (WAF interstitial, markup change) needs to be visible
    # without digging through Actions logs. See warnings above.
    try:
        notify.send_daily_email(new_listings, errors, warnings)
    except Exception as exc:
        log.error("failed to send notification email: %s", exc)

    save_seen(seen)

    return 1 if errors and len(errors) == len(sources) else 0


def main():
    parser = argparse.ArgumentParser(description="Scrape craft fair listing sites for new fairs.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scrape and print new listings; don't email or update seen.json.",
    )
    parser.add_argument(
        "--dump-html",
        metavar="DIR",
        help="Fetch each source's raw HTML into DIR for selector calibration, then exit.",
    )
    args = parser.parse_args()

    if args.dump_html:
        dump_html(args.dump_html)
        return 0

    return run(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
