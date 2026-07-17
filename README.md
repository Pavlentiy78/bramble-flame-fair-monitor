# Bramble & Flame — Fair Monitor

Checks a couple of craft-fair listing sites daily and emails Pavlo when a new
fair shows up in Derbyshire / the East Midlands, so stalls don't get missed.

## How it works

```
GitHub Actions (cron, daily 07:00 UTC)
        │
        ▼
  scraper.py  ──reads──▶  sources.yaml (URLs + which parser to use)
        │
        ▼
  seen.json  (committed to the repo — the "database" of fairs already notified about)
        │
        ▼
  diff: new listings = scraped - seen
        │
        ▼
  if new listings (or scrape errors) → email via Resend
```

No server — GitHub Actions runs `scraper.py` on schedule and shuts down.

## Setup

1. **Create a free [Resend](https://resend.com) account** and generate an API key.
   Resend lets you send from `onboarding@resend.dev` without verifying your
   own domain, which is enough for this volume (a handful of emails a month).

2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `RESEND_API_KEY` — the API key from Resend
   - `NOTIFY_TO_EMAIL` — where alerts should go (e.g. Pavlo's email)
   - `NOTIFY_FROM_EMAIL` *(optional)* — defaults to `Fair Monitor <onboarding@resend.dev>`

3. That's it — the workflow (`.github/workflows/check-fairs.yml`) runs daily
   automatically. You can also trigger it manually from the Actions tab
   ("Run workflow").

## Calibrating selectors

`scraper.py`'s two parser functions (`stall_and_craft_collective` and
`stallfinder`) were written without the ability to fetch the live pages from
the environment that built them, so their CSS selectors are a best-effort
structural guess, not verified against real markup. They almost certainly
need a correction pass against the real HTML before this is reliable.

To calibrate them:

1. In the Actions tab, run "Check for new craft fairs" manually with the
   **dump_html** input checked. This fetches each source's raw HTML (from a
   normal GitHub-hosted runner, which has full internet access) and uploads
   it as a workflow artifact called `source-html-dump`, instead of
   scraping/emailing.
2. Download the artifact, open the HTML files, and find the real repeating
   element for each listing (view source, or `Ctrl+F` for one event's name).
3. Update the `.select(...)` calls in the matching parser function in
   `scraper.py` to match.
4. Run `python scraper.py --dry-run` locally (or trigger the workflow
   normally) to confirm listings are now parsed correctly, then commit.

You can also run this locally any time a source's HTML changes:

```
python scraper.py --dump-html debug_html/
```

## Local testing

```
pip install -r requirements.txt
python scraper.py --dry-run
```

`--dry-run` scrapes and prints any new listings to stdout without sending
email or writing to `seen.json` — safe to run repeatedly while iterating on
selectors.

## Adding a new source

1. Write a parser function in `scraper.py` that takes `(html, source_url)`
   and returns a list of `{"name", "date", "venue", "url"}` dicts.
2. Register it in the `PARSERS` dict.
3. Add an entry to `sources.yaml` pointing at the new URL and parser name.

No other code changes needed — `scraper.py`, `seen.json`, and the workflow
are all source-agnostic.

## Known limitations

- **Sites can change their HTML at any time**, which breaks the selectors —
  this needs occasional maintenance, not a one-off build. If a source starts
  returning 0 listings, the scraper logs a warning and includes it in the
  next notification email so it doesn't fail silently.
- **Only catches *new* listings, not changed ones.** Some fair sites list
  events months ahead and quietly update details (date/fee changes) rather
  than only adding new listings — v1 doesn't detect edits to a listing it's
  already seen. Worth revisiting if it turns out to matter.
- **Email only.** No Telegram bot in v1 — easy to add later if wanted.
- **Facebook group source** ("Craft Fairs and Stallholders Derbyshire") is
  deliberately left out of v1 — harder to scrape reliably, worth revisiting
  once the two web sources are proven out.

## Optional next step

Once this is working reliably, new fairs could be appended directly into the
existing Bramble & Flame fair tracker's storage instead of living in a
separate `seen.json` — worth deciding once the scraper itself is solid.
