"""Day 7 of the v3 cold sequence (Joey 10/23-24, locked), built per home from fixed templates: no AI call per home.
  ads_email()    the running-ads email, with "since early/mid/late <Month>" from the Ad Library start date
  noads_email()  the no-ads email (the free ad image goes under its first paragraph)
  draft_text()   the email as the agent's "Follow-up draft (email)" text to Joey, the phone picture's first bubble
  Phone          renders the phone picture (phone.html) for many homes with one browser, about 0.2 s each
Joey 10/24: "this process is something that will be done hundreds of times so it has to be fast"."""
from __future__ import annotations

import hashlib
import html
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SUBJECT = "Re: Do you have an open room?"


def since(start: date, today: date | None = None) -> str:
    """2026-08-20 -> "late August"; a start in another year adds it: 2025-02-18 -> "mid February 2025"."""
    today = today or date.today()
    third = "early" if start.day <= 10 else "mid" if start.day <= 20 else "late"
    return f"{third} {start:%B}" + (f" {start.year}" if start.year != today.year else "")


def _hi(first: str) -> str:
    return f"Hi {first}," if first else "Hi,"


def ads_email(first: str, home: str, start: date, today: date | None = None) -> str:
    return (f"{_hi(first)} I saw {home} has Facebook ads running since {since(start, today)}.\n\n"
            "If follow-up takes forever, I built a free tool for senior living that gets families to tour by itself "
            "even if you’re busy caring for a resident.\n\n"
            "And yes, it sounds human.\n\nIt wrote this email.\n\n"
            "Reply “yes” and I’ll let you give it a try.\n\nJoey")


def noads_email(first: str, home: str) -> tuple[str, str]:
    """-> (text above the ad image, text below it)."""
    return (f"{_hi(first)} I checked {home} on the Facebook Ad Library and saw there were no ads running. "
            "So I made you one for free to use.",
            # value line above the ask, Joey 10/24 (his words)
            "Helping senior living communities get exclusive, private pay move-ins without the placement fee is the "
            "only thing I do.\n\nReply “yes” and I’ll make you 2 more.\n\nJoey")


def short_name(first: str, last: str, home: str) -> str:
    """The agent's way of naming a lead: "Linda R"; a home with no contact name goes by the home."""
    if first:
        return f"{first} {last[:1].upper()}".strip()
    return home


def code_for(pid: str) -> str:
    return f"E{10 + int(hashlib.sha1(pid.encode()).hexdigest(), 16) % 90}"


def draft_text(code: str, who: str, body: str) -> str:
    return f'Follow-up draft (email): {code}. {who}.\n"Subject: {SUBJECT}\n\n{body}"\n\nReply "Y{code}" to send'


class Phone:
    """with Phone() as ph: ph.render(path, draft, code, who)"""

    def __enter__(self):
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch()
        self._page = self._browser.new_page(viewport={"width": 390, "height": 900}, device_scale_factor=2)
        self._tpl = (HERE / "phone.html").read_text(encoding="utf-8")
        return self

    def render(self, out: Path, draft: str, code: str, who: str) -> Path:
        page = self._tpl.replace("{{DRAFT}}", html.escape(draft)).replace("{{REPLY}}", html.escape(f"Y{code}")) \
                        .replace("{{SENT}}", html.escape(f"Sent to {who}."))
        self._page.set_content(page, wait_until="load")
        self._page.locator("#phone").screenshot(path=str(out))
        return out

    def __exit__(self, *a):
        self._browser.close()
        self._pw.stop()
