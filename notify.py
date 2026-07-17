"""Sends email notifications about new fair listings via Resend."""
import os

import requests

RESEND_API_URL = "https://api.resend.com/emails"


def send_new_listings_email(new_listings, errors=None):
    errors = errors or []
    api_key = os.environ.get("RESEND_API_KEY")
    to_email = os.environ.get("NOTIFY_TO_EMAIL")
    from_email = os.environ.get("NOTIFY_FROM_EMAIL", "Fair Monitor <onboarding@resend.dev>")

    if not api_key or not to_email:
        raise RuntimeError(
            "RESEND_API_KEY and NOTIFY_TO_EMAIL must be set (as GitHub Actions secrets) to send email."
        )

    subject, text_body = _format_email(new_listings, errors)

    response = requests.post(
        RESEND_API_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "text": text_body,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def _format_email(new_listings, errors):
    if new_listings:
        count = len(new_listings)
        subject = f"{count} new craft fair listing{'s' if count != 1 else ''}"
    else:
        subject = "Fair monitor: scrape errors"

    lines = []

    if new_listings:
        lines.append(f"{len(new_listings)} new listing(s) found:\n")
        for listing in new_listings:
            lines.append(f"- {listing['name']}")
            if listing.get("date"):
                lines.append(f"  Date: {listing['date']}")
            if listing.get("venue"):
                lines.append(f"  Venue: {listing['venue']}")
            if listing.get("county"):
                lines.append(f"  County: {listing['county']}")
            if listing.get("source"):
                lines.append(f"  Source: {listing['source']}")
            if listing.get("url"):
                lines.append(f"  Link: {listing['url']}")
            lines.append("")

    if errors:
        lines.append("Scrape errors (a source may need its selectors updated):\n")
        for err in errors:
            lines.append(f"- {err}")
        lines.append("")

    return subject, "\n".join(lines)
