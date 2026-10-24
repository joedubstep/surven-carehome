"""Find a better address for a home whose mailbox check failed, then check that one (Joey 10/24: "If an email is
bad, enrich it, possibly. And then verify it.").

For each home not emailed yet whose address came back invalid (or unknown or catch-all), from its own website:
  1. every address the site shows (home page, contact, about, team, staff pages; mailto links and plain text)
  2. the contact person's name in the usual company patterns (first@, first.last@, flast@, ...) at the site's domain
Candidates are checked with the same mail-server question as verify_mail.py, best first: an address with the
contact's name, then a role address (admissions@, director@...), then info@-style ones. The first one the server
confirms replaces the old address in carehome.db, and its verdict goes into email_verify.json. On a server that
says yes to everything, an address printed on the home's own site counts ("published"); a guess never does.
Every change is kept in data/carehome/email_enrich.json (old address, new one, how it was found)."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
import urllib.request
from collections import Counter
from datetime import date, datetime
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import verify_mail

D = Path(__file__).resolve().parent.parent / "data" / "carehome"
LOG = D / "email_enrich.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "msn.com", "live.com",
            "comcast.net", "att.net", "sbcglobal.net", "bellsouth.net", "verizon.net", "me.com", "ymail.com"}
PAGES = ["", "contact", "contact-us", "contactus", "about", "about-us", "our-team", "team", "staff", "leadership",
         "our-staff", "meet-our-team", "careers"]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
ROLE = re.compile(r"^(admissions?|director|executivedirector|ed|administrator|admin|marketing|sales|tours?|"
                  r"move-?in|community|leasing|inquiries|enquiries|welcome)", re.I)
GENERIC = re.compile(r"^(info|contact|hello|office|mail|frontdesk|reception)", re.I)
JUNK = re.compile(r"\.(png|jpe?g|gif|svg|webp)$|example\.|sentry|wixpress|godaddy|squarespace|@2x|noreply|no-reply|"
                  r"privacy|abuse|webmaster|hr@|jobs@|careers@|billing|accounts?@|payroll", re.I)


def load() -> dict:
    try:
        return json.loads(LOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _get(url: str, timeout: int = 15) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(2_000_000).decode("utf-8", "replace")


def site_emails(site: str) -> list[str]:
    """Every address the home's site shows, in the order found."""
    found: list[str] = []
    base = site if site.startswith("http") else "https://" + site
    for page in PAGES:
        url = urljoin(base.rstrip("/") + "/", page)
        try:
            html = unescape(_get(url))
        except Exception:
            if not page:  # the home page itself does not load: try www.
                if "://www." not in base:
                    base = base.replace("://", "://www.", 1)
                    try:
                        html = unescape(_get(base))
                    except Exception:
                        return found
                else:
                    return found
            else:
                continue
        html = html.replace("[at]", "@").replace("(at)", "@").replace("[dot]", ".")
        for e in EMAIL.findall(html):
            e = e.strip(".").lower()
            if not JUNK.search(e) and e not in found:
                found.append(e)
        time.sleep(0.3)
    return found


def name_parts(contact: str) -> tuple[str, str]:
    n = re.sub(r"\b(mr|mrs|ms|dr|rn|lpn|lvn|cna|bsn|msn|np|md|jr|sr|ii|iii)\b\.?", "", (contact or "").lower())
    if "," in n:  # "Taliaferro, Kevin"
        last, first = [x.strip() for x in n.split(",", 1)]
    else:
        parts = n.split()
        first, last = (parts[0], parts[-1]) if len(parts) > 1 else (parts[0] if parts else "", "")
    first, last = re.sub(r"[^a-z]", "", first), re.sub(r"[^a-z]", "", last)
    return first, last


def guesses(first: str, last: str, domain: str) -> list[str]:
    if not first or not domain:
        return []
    out = [f"{first}@{domain}"]
    if last:
        out = [f"{first}.{last}@{domain}", f"{first[0]}{last}@{domain}", f"{first}@{domain}", f"{first}{last}@{domain}",
               f"{first}{last[0]}@{domain}", f"{first[0]}.{last}@{domain}", f"{first}_{last}@{domain}", f"{last}@{domain}"]
    return list(dict.fromkeys(out))


def rank(cands: list[str], first: str, last: str, published: set[str]) -> list[str]:
    def score(e):
        user = e.split("@")[0]
        named = bool(first and first in user) or bool(last and len(last) > 2 and last in user)
        return (0 if named and e in published else 1 if named else 2 if ROLE.match(user) else 3 if GENERIC.match(user)
                else 4, 0 if e in published else 1)
    return sorted(dict.fromkeys(cands), key=score)


def listing_sources(browser, place: tuple | None) -> tuple[list[str], list[str]]:
    """-> (websites from the Maps listing, emails shown on the Facebook Page's About section)."""
    if not place:
        return [], []
    from .day7 import maps
    from .day7.photo import _FB_LINK
    ctx, req = browser
    sites, mails = [], []
    page = ctx.new_page()
    try:
        m = maps.find(page, *place)
        if m.get("match") and m.get("website"):
            sites.append(m["website"])
        if m.get("match"):
            page.goto(m["website"], wait_until="domcontentloaded", timeout=30000) if m.get("website") else None
            fb = next(iter(page.evaluate(_FB_LINK)), "") if m.get("website") else ""
            if fb:
                page.goto(fb.rstrip("/") + "/about", wait_until="domcontentloaded", timeout=40000)
                page.wait_for_timeout(3000)
                mails = [e.lower() for e in EMAIL.findall(page.inner_text("body")) if not JUNK.search(e)]
    except Exception:
        pass
    finally:
        page.close()
    return sites, mails


def enrich_one(old: str, website: str, contact: str, browser=None, place: tuple | None = None) -> dict | None:
    """-> {"new", "how", "status"} or None when nothing better was found."""
    dom_old = old.split("@")[-1]
    sites = [s for s in (website, "" if dom_old in FREEMAIL else dom_old) if s]
    if browser:  # the Google Maps listing's current website and the Facebook Page's public email (day7 code)
        more, fb_mails = listing_sources(browser, place)
        sites = list(dict.fromkeys(sites + more))
        published_fb = fb_mails
    else:
        published_fb = []
    first, last = name_parts(contact)
    published: list[str] = list(published_fb)
    for s in dict.fromkeys(sites):
        published += site_emails(s)
    site_dom = (urlparse(sites[0] if sites[0].startswith("http") else "https://" + sites[0]).netloc.lower()
                .removeprefix("www.")) if sites else ""
    doms = [d for d in dict.fromkeys([site_dom, dom_old if dom_old not in FREEMAIL else ""]) if d]
    cands = [e for e in published if e != old]
    for d in doms:
        cands += guesses(first, last, d)
    # every address printed on their site, plus at most 3 name guesses (a handful of questions, not a dictionary)
    pub = [c for c in rank([c for c in cands if c in published], first, last, set(published)) if c != old]
    guess = [c for c in rank([c for c in cands if c not in published], first, last, set()) if c != old][:3]
    cands = pub[:8] + guess
    by_dom: dict[str, list[str]] = {}
    for c in cands:
        by_dom.setdefault(c.split("@")[-1], []).append(c)
    verdicts = {}
    for d, group in by_dom.items():
        verdicts.update(verify_mail.check_domain(d, group))
    for c in cands:
        v = verdicts.get(c, {})
        how = "found on their website" if c in published else "name pattern"
        if v.get("status") == "valid":
            return {"new": c, "how": how, "status": "valid", "verdict": v}
        if v.get("status") == "catch_all" and c in published:
            return {"new": c, "how": how, "status": "published", "verdict": {**v, "status": "published"}}
    return None


def run(minutes: int = 25, statuses=("invalid", "unknown", "catch_all")) -> Counter:
    con = sqlite3.connect(D / "carehome.db"); con.row_factory = sqlite3.Row
    verdicts = verify_mail.load()
    log = load()
    facts = json.loads((D / "lead_facts.json").read_text(encoding="utf-8"))
    people = {x["id"]: x for x in json.loads((D / "build" / "prospects.json").read_text(encoding="utf-8"))}
    stop_at = time.time() + minutes * 60
    n: Counter = Counter()
    today = date.today().isoformat()
    from .day7 import build as day7
    S = day7.S.start()
    browser = (S.ctx, S.req)
    for r in con.execute("SELECT id, email FROM prospects WHERE status='queued' AND email != ''"):
        old = r["email"].strip().lower()
        v = verdicts.get(old)
        if not v or v["status"] not in statuses or old in log and log[old].get("tried") == today:
            continue
        if time.time() > stop_at:
            break
        website = (facts.get(r["id"]) or {}).get("website", "") or (people.get(r["id"]) or {}).get("website", "")
        contact = (people.get(r["id"]) or {}).get("contact_name", "")
        f = facts.get(r["id"]) or {}
        place = (f.get("facility") or "", f.get("address", ""), f.get("city", "")) if f.get("address") else None
        try:
            got = enrich_one(old, website, contact, browser, place)
        except Exception:
            got = None
            n["error"] += 1
        log[old] = {"tried": today, "id": r["id"], **({"new": got["new"], "how": got["how"], "status": got["status"]}
                                                       if got else {"new": ""})}
        if got:
            con.execute("UPDATE prospects SET email=? WHERE id=? AND status='queued'", (got["new"], r["id"]))
            con.commit()
            verdicts[got["new"]] = got["verdict"]
            n["fixed " + got["status"]] += 1
        else:
            n["nothing better"] += 1
        LOG.write_text(json.dumps(log, indent=0, sort_keys=True), encoding="utf-8")
        verify_mail.save(verdicts)
    day7.close()
    print(f"{datetime.now().isoformat(timespec='seconds')} email enrich: {dict(n)}", flush=True)
    return n


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=25)
    run(ap.parse_args().minutes)
