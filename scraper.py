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
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

import notify

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("scraper")

ROOT = Path(__file__).resolve().parent
SOURCES_FILE = ROOT / "sources.yaml"
SEEN_FILE = ROOT / "seen.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch(url):
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return response.text


# --- Per-site parsers -------------------------------------------------
# Each parser takes the raw page HTML and the source URL, and returns a
# list of dicts: {"name": str, "date": str, "venue": str, "url": str}.
# `name` is required; leave the rest as "" if a field isn't found.
#
# These two were written without being able to fetch the live pages from
# the dev sandbox that built them (no outbound web access there). They are
# a best-effort structural guess, not verified against real markup. Run
# `python scraper.py --dump-html debug_html/` from an environment with
# normal internet access (e.g. trigger the GitHub Actions workflow manually
# with "dump_html" checked) and adjust the selectors below against the
# saved HTML. See README "Calibrating selectors".

def stall_and_craft_collective(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    cards = soup.select(".event-card, .event-list-item, article, li.event")
    for card in cards:
        name_el = card.select_one("h2, h3, .event-title, .title")
        if not name_el or not name_el.get_text(strip=True):
            continue
        date_el = card.select_one(".event-date, .date, time")
        venue_el = card.select_one(".event-venue, .venue, .location")
        link_el = card.find("a", href=True)
        listings.append(
            {
                "name": name_el.get_text(strip=True),
                "date": date_el.get_text(strip=True) if date_el else "",
                "venue": venue_el.get_text(strip=True) if venue_el else "",
                "url": urljoin(source_url, link_el["href"]) if link_el else source_url,
            }
        )
    return listings


def stallfinder(html, source_url):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    cards = soup.select(".searchResult, .search-result, .event-listing, .listing, li.event")
    for card in cards:
        name_el = card.select_one("h2, h3, .event-title, .title, a")
        if not name_el or not name_el.get_text(strip=True):
            continue
        date_el = card.select_one(".event-date, .date, time")
        venue_el = card.select_one(".event-venue, .venue, .location")
        link_el = card.find("a", href=True)
        listings.append(
            {
                "name": name_el.get_text(strip=True),
                "date": date_el.get_text(strip=True) if date_el else "",
                "venue": venue_el.get_text(strip=True) if venue_el else "",
                "url": urljoin(source_url, link_el["href"]) if link_el else source_url,
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


def scrape_source(source):
    """Fetch + parse one source. Returns (listings, error_message_or_None)."""
    parser = PARSERS.get(source["parser"])
    if parser is None:
        return [], f"{source['name']}: unknown parser '{source['parser']}'"

    try:
        html = fetch(source["url"])
    except requests.RequestException as exc:
        return [], f"{source['name']}: fetch failed ({exc})"

    try:
        raw_listings = parser(html, source["url"])
    except Exception as exc:  # a broken selector shouldn't kill the whole run
        return [], f"{source['name']}: parse failed ({exc})"

    listings = []
    for item in raw_listings:
        if not item.get("name"):
            continue
        item["source"] = source["name"]
        item["county"] = source.get("county", "")
        listings.append(item)

    if not listings:
        log.warning("%s: parsed 0 listings - selectors may need updating", source["name"])

    return listings, None


def dump_html(out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for source in load_sources():
        slug = source["name"].lower().replace(" ", "_").replace("/", "_")
        try:
            html = fetch(source["url"])
        except requests.RequestException as exc:
            log.error("%s: fetch failed (%s)", source["name"], exc)
            continue
        path = out_dir / f"{slug}.html"
        path.write_text(html, encoding="utf-8")
        log.info("saved %s (%d bytes)", path, len(html))


def run(dry_run=False):
    sources = load_sources()
    seen = load_seen()
    new_listings = []
    errors = []

    for source in sources:
        log.info("checking %s", source["name"])
        listings, error = scrape_source(source)
        if error:
            log.error(error)
            errors.append(error)
            continue

        for listing in listings:
            listing_id = make_id(listing["name"], listing.get("date", ""), listing.get("venue", ""))
            if listing_id not in seen:
                new_listings.append(listing)
                seen[listing_id] = {**listing, "id": listing_id}

    log.info("found %d new listing(s), %d error(s)", len(new_listings), len(errors))

    if dry_run:
        for listing in new_listings:
            print(json.dumps(listing, indent=2))
        return 1 if errors else 0

    if new_listings or errors:
        try:
            notify.send_new_listings_email(new_listings, errors)
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
