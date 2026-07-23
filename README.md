# Bramble & Flame — Fair Monitor

Checks a couple of craft-fair listing sites daily and emails Pavlo when a new
fair shows up in Derbyshire / the East Midlands, so stalls don't get missed.

## How it works

```
cron-job.org (daily HTTP POST, ~07:00 UTC)
        │  triggers workflow_dispatch via the GitHub REST API
        ▼
GitHub Actions ── scraper.py ──reads──▶ sources.yaml (URLs + which parser to use)
        │
        ▼
  seen.json  (committed to the repo — the "database" of fairs already notified about)
        │
        ▼
  diff: new listings = scraped - seen
        │
        ▼
  email via Resend, every run — new listings, "nothing new today", or
  flagged sources/errors, whichever applies (see notify.py)
```

No server — GitHub Actions runs `scraper.py` on trigger and shuts down.

The workflow has **no native GitHub `schedule:` trigger**. It was tried first
and proved unreliable in practice: multi-hour delays, and some days it never
fired at all, even after moving the cron off the exact hour (GitHub's own
docs note exact-hour schedules are the most congested slot, but that wasn't
enough here). **cron-job.org** (a free external cron service) now calls the
workflow's `workflow_dispatch` REST endpoint daily instead — reliable because
it's entirely outside GitHub's own scheduler. A Claude Code Remote Routine
also checks in shortly after (07:25 UTC) as a backup, triggering a run only
if cron-job.org's didn't land that day.

## Setup

1. **Create a free [Resend](https://resend.com) account** and generate an API key.
   Resend lets you send from `onboarding@resend.dev` without verifying your
   own domain, which is enough for this volume (a handful of emails a month).

2. **Add repo secrets** (Settings → Secrets and variables → Actions):
   - `RESEND_API_KEY` — the API key from Resend
   - `NOTIFY_TO_EMAIL` — where alerts should go (e.g. Pavlo's email)
   - `NOTIFY_FROM_EMAIL` *(optional)* — defaults to `Fair Monitor <onboarding@resend.dev>`

3. **Set up the daily trigger via [cron-job.org](https://cron-job.org)** (free):
   - Generate a GitHub fine-grained personal access token scoped to just this
     repo, with **Actions: Read and write** permission (nothing else needed).
   - Create a cronjob there:
     - URL: `https://api.github.com/repos/Pavlentiy78/bramble-flame-fair-monitor/actions/workflows/check-fairs.yml/dispatches`
     - Method: `POST`
     - Headers: `Authorization: Bearer <token>`, `Accept: application/vnd.github+json`,
       `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`
     - Body: `{"ref": "claude/fair-monitor-scraper-lhs0a3"}`
     - Schedule: daily, time zone **UTC** (not your local time zone — cron-job.org
       defaults to whatever you picked at signup, so double check)
   - A successful trigger returns `204 No Content`; you'll see the run appear
     in the repo's Actions tab within a few seconds.

4. You can also trigger a run manually any time from the Actions tab
   ("Run workflow").

## Calibrating selectors

`scraper.py`'s two parser functions (`stall_and_craft_collective` and
`stallfinder`) are calibrated against real saved HTML from each site's
Derbyshire listing page (captured July 2026) and were verified to correctly
extract every listing on that page (6/6 and 20/20 respectively).

Sites change their markup over time, though, so if a source ever starts
returning 0 listings (the scraper logs a warning and reports it by email),
recalibrate:

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
