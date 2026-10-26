"""Free check: is a US care home's Facebook Page running Meta ads right now?

Uses the public Meta Ad Library website (no login, no API key) through headless
Chromium (Playwright). For each home it:
  1. types the home name into the Ad Library search box and reads the advertiser
     (Page) suggestions Meta returns,
  2. scores them by name similarity + senior-living category,
  3. opens each likely Page's own Ad Library view (view_all_page_id) and checks
     the Page's city/state shown in the search box against the home's city/state,
  4. on the matched Page, reads the "~N results" count and every
     "Started running on" date (active ads only, US, all ad types).
If no Page matches, it falls back to an exact-phrase keyword search and labels
the result "keyword" (other advertisers may be counted).

Usage:
  python tools/adlib_check.py "The Retreat at Conyers|Conyers|GA" "Thrive Assisted Living||AK"
  python tools/adlib_check.py --csv homes.csv --out results.csv   (columns: name,city,state)
  python tools/adlib_check.py --test                               (the 5 test homes)
Flags: --stealth (playwright-stealth), --headed, --pause 4 (seconds between homes),
       --max-scrolls 15 (pages of 30 ads to load when looking for the earliest date).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import difflib
import json
import re
import sys
import time
from urllib.parse import quote, unquote_plus

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
BASE = "https://www.facebook.com/ads/library/"
COMMON = "active_status=active&ad_type=all&country=US&is_targeted_country=false&media_type=all"

TEST_HOMES = [
    ("Parkside Assisted Living & Memory Care at Conyers", "Conyers", "GA"),
    ("The Retreat at Conyers", "Conyers", "GA"),
    ("Thrive Assisted Living", "", "AK"),
    ("Inspired Living at Alpharetta", "Alpharetta", "GA"),
    ("Maple Springs of Anchorage", "Anchorage", "AK"),
]

STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV", "new hampshire": "NH",
    "new jersey": "NJ", "new mexico": "NM", "new york": "NY", "north carolina": "NC",
    "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
    "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN",
    "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY", "district of columbia": "DC",
}
SENIOR_CAT = re.compile(r"retire|assisted|senior|nursing|memory|elder|home health|"
                        r"personal care|caregiver|hospice|adult day", re.I)
GENERIC = {"the", "at", "of", "and", "llc", "inc", "assisted", "living", "memory", "care",
           "senior", "community", "residence", "residences", "home", "homes", "center"}


def state_code(s: str) -> str:
    s = (s or "").strip()
    return s.upper() if len(s) == 2 else STATES.get(s.lower(), s.upper())


def norm(s: str) -> str:
    s = s.lower().replace("&", " and ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s)).strip()


def name_score(home: str, page: str) -> float:
    a, b = norm(home), norm(page)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    ta = set(a.split()) - GENERIC
    tb = set(b.split()) - GENERIC
    distinct = len(ta & tb) / len(ta | tb) if ta else 0.0  # overlap of distinctive words
    return round(0.5 * ratio + 0.5 * distinct, 3)


def location_verdict(text: str, city: str, st: str) -> str:
    """'city' / 'state' (confirmed), 'other' (names a different state), '' (unknown)."""
    n = f" {norm(text)} "
    if city and f" {norm(city)} " in n:
        return "city"
    full = {v: k for k, v in STATES.items()}.get(st, "")
    if st and (re.search(rf"\b{st}\b", text) or (full and f" {full} " in n)):
        return "state"
    for name_, code in STATES.items():
        if st and code != st and (f" {name_} " in n or re.search(rf",\s*{code}\b", text)):
            return "other"
    return ""


def query_variants(name: str, city: str) -> list[str]:
    out = [name]
    if "&" in name:
        out.append(name.replace("&", "and"))
    m = re.match(r"(.+?)\s+(?:at|of|in)\s+.+$", name, re.I)
    if m and len(m.group(1).split()) >= 2:
        out.append(m.group(1))  # "Parkside Assisted Living & Memory Care at Conyers" -> before "at"
    core = [w for w in norm(name).split() if w not in GENERIC]
    if core:
        short = " ".join(core)
        if city and norm(city) not in short:
            short += " " + city
        out.append(short)
    seen, uniq = set(), []
    for q in out:
        if q.lower() not in seen:
            seen.add(q.lower())
            uniq.append(q)
    return uniq


def parse_count(text: str) -> int | None:
    m = re.search(r"~?\s*([\d.,]+)\s*([KkMm])?\s+results?\b", text)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    mult = {"k": 1_000, "m": 1_000_000}.get((m.group(2) or "").lower(), 1)
    return int(n * mult)


AD_TEXT_KEYS = re.compile(r'"(?:link_url|caption|title|link_description|text)":"((?:[^"\\]|\\.){2,400})"')


def _unescape(s: str) -> str:
    try:
        return json.loads(f'"{s}"')
    except ValueError:
        return s


def ad_records(blob: str) -> dict[str, dict]:
    """ad_archive_id -> {start, page_id, words} from page HTML or GraphQL JSON. "words" is the ad's own text: its
    body, headline, link and link caption (Joey 10/24: the ad itself is "the golden thing to look at")."""
    recs = {}
    for chunk in blob.split('"ad_archive_id":"')[1:]:
        m_id = re.match(r"(\d+)", chunk)
        m_sd = re.search(r'"start_date":(\d+)', chunk)
        m_pid = re.search(r'"page_id":"(\d+)"', chunk)
        if m_id and m_sd:
            words = " | ".join(_unescape(w) for w in AD_TEXT_KEYS.findall(chunk[:20000]))
            recs[m_id.group(1)] = {"start": int(m_sd.group(1)),
                                   "page_id": m_pid.group(1) if m_pid else None, "words": words[:3000]}
    return recs


def ads_name_home(words: str, name: str, city: str) -> str:
    """Do the Page's own ads name this home? -> "city", "name" or "". The city counts when it is in the ad text or
    link; the name counts when most of its distinctive words are there (so "St. John" ads for a church in another
    state do not count for "St John Of God Residence" in Los Angeles)."""
    w = norm(words)
    if city and norm(city) and f" {norm(city)} " in f" {w} ":
        return "city"
    toks = [t for t in norm(name).split() if t not in GENERIC and len(t) > 2]
    if toks and sum(t in w.split() for t in toks) >= max(1, (len(toks) * 2 + 2) // 3):
        return "name"
    compact = norm(name).replace(" ", "")
    return "name" if len(compact) >= 8 and compact in w.replace(" ", "") else ""


def site_domain(url: str) -> str:
    """"https://www.fitelivingcentre.com/about" -> "fitelivingcentre.com"."""
    host = re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/")[0].split("?")[0]
    return host[4:] if host.startswith("www.") else host


def ads_link_site(words: str, website: str) -> bool:
    """10/26: the Page's ads send people to the home's own website (the ad's link or the domain under its picture)."""
    dom = site_domain(website)
    return bool(dom) and dom in (words or "").lower()


AD_LINK = re.compile(r"https?://(?:www\.)?([a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,})", re.I)


def ad_sites(words: str) -> set[str]:
    """The websites the Page's ads send people to (Facebook's own lead forms and Messenger left out)."""
    return {d.lower() for d in AD_LINK.findall(words or "")
            if not re.search(r"facebook|fb\.|instagram|messenger|wa\.me|whatsapp", d, re.I)}


FB_LINK = re.compile(r"""(?:https?:)?(?:\\?/){2}(?:www\.|m\.|web\.)?facebook\.com(?:\\?/)[^"'\s<>|]+""", re.I)
FB_SKIP = re.compile(r"facebook\.com/(?:sharer|share|plugins|dialog|tr\b|groups|events|watch|login|policies|help|"
                     r"privacy|legal|business|ads|hashtag|photo|story\.php|permalink|l\.php|2008|v\d|search|marketplace|"
                     r"reel|video)", re.I)


def facebook_links(html: str) -> list[str]:
    """Links to a Facebook Page in a web page or an email, most-linked first ("facebook.com/TheFiteLivingCentre")."""
    out = []
    for u in FB_LINK.findall(html or ""):
        u = u.replace("\\/", "/").split("#")[0].split("&")[0]
        u = re.sub(r"^(?:https?:)?//(?:m\.|web\.)?", "https://", u).replace("https://facebook.com", "https://www.facebook.com")
        u = re.sub(r"[).,;:*]+$", "", u).rstrip("/")
        if "?" in u and "profile.php?id=" not in u:
            u = u.split("?")[0]
        if not FB_SKIP.search(u) and u.count("/") >= 3 and u.split("/")[3]:
            out.append(u)
    return sorted(dict.fromkeys(out), key=lambda x: -out.count(x))


def site_facebook(al: "AdLib", website: str) -> list[str]:
    """The Facebook Page links on the home's own website, opened in the browser (JavaScript sites, 403s)."""
    if not website:
        return []
    pg = al.ctx.new_page()
    try:
        pg.goto(website if "://" in website else "https://" + website, wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(2500)
        return facebook_links(pg.content())
    except Exception:
        return []
    finally:
        pg.close()


PAGE_ID = (r'"associated_page_id":"(\d+)"', r'"delegate_page_id":"(\d+)"', r'"pageID":"(\d+)"', r'fb://page/\?id=(\d+)',
           r'fb://page/(\d+)', r'"page_id":"(\d+)"')


def page_of_link(al: "AdLib", link: str) -> dict | None:
    """The Ad Library id of the Page behind a facebook.com link, read from the public Page (no login):
    {"page_id", "name"}. 10/26: a Page's own Ad Library id is its "associated_page_id"; the profile id in the same
    page (fb://profile/...) opens an empty Ad Library view."""
    pg = al.ctx.new_page()
    try:
        pg.goto(link, wait_until="domcontentloaded", timeout=40000)
        for wait in (3000, 4000):  # a slow Page fills in its ids a few seconds after it opens
            pg.wait_for_timeout(wait)
            html, title = pg.content(), pg.title()
            m = next((m for m in (re.search(pat, html) for pat in PAGE_ID) if m), None)
            if m:
                break
    except Exception:
        return None
    finally:
        pg.close()
    if not m:
        return None
    name = re.split(r"\s+\|\s+", title or "")[0].strip()
    return {"page_id": m.group(1), "name": "" if name.lower() in ("facebook", "log in or sign up") else name}


def epoch_to_date(ts: int) -> dt.date:
    # Ad Library start dates are midnight US Pacific; shifting -7h lands on the right day
    # in both PDT and PST.
    return dt.datetime.fromtimestamp(ts - 7 * 3600, dt.timezone.utc).date()


def dom_dates(text: str) -> list[dt.date]:
    out = []
    for s in re.findall(r"Started running on ([A-Z][a-z]{2} \d{1,2}, \d{4})", text):
        try:
            out.append(dt.datetime.strptime(s, "%b %d, %Y").date())
        except ValueError:
            pass
    return out


class RateLimited(RuntimeError):
    """Meta refused to answer. The home was NOT checked, so it must not get the "no ads running" email."""


class AdLib:
    def __init__(self, pw, stealth=False, headed=False, max_scrolls=15):
        args = ["--disable-blink-features=AutomationControlled"]
        if headed:
            self.browser = pw.chromium.launch(headless=False, args=args)
        elif stealth:
            # full Chromium in new headless mode (closer to real Chrome than headless shell)
            self.browser = pw.chromium.launch(headless=False, args=args + ["--headless=new"])
        else:
            self.browser = pw.chromium.launch(headless=True, args=args)
        self.ctx = self.browser.new_context(user_agent=UA, locale="en-US",
                                            timezone_id="America/New_York",
                                            viewport={"width": 1400, "height": 900})
        if stealth:
            from playwright_stealth import Stealth
            Stealth().apply_stealth_sync(self.ctx)
        self.page = self.ctx.new_page()
        self.max_scrolls = max_scrolls
        self.typeahead: dict[str, list] = {}
        self.filter_pages: list[dict] = []  # advertisers behind the current search's results
        self.blobs: list[str] = []
        self.rate_limited = False  # 10/24: Meta answered "Rate limit exceeded"; nothing it says now can be trusted
        self.page.on("response", self._on_response)

    def close(self):
        self.browser.close()

    def _on_response(self, r):
        if "/api/graphql" not in r.url:
            return
        try:
            pd = r.request.post_data or ""
            body = r.text()
        except Exception:
            return
        if "1675004" in body or "Rate limit exceeded" in body:
            self.rate_limited = True
            return
        if "TypeaheadSuggestion" in pd:
            m = re.search(r"(?:^|&)variables=([^&]*)", pd)
            try:
                qs = json.loads(unquote_plus(m.group(1))).get("queryString", "") if m else ""
                pr = json.loads(body.splitlines()[0])["data"]["ad_library_main"][
                    "typeahead_suggestions"]["page_results"]
            except Exception:
                return
            self.typeahead[qs] = pr or []
        elif "FilterContextProvider" in pd:
            try:
                self.filter_pages = json.loads(body.splitlines()[0])["data"]["ad_library_main"][
                    "dynamic_filter_options"]["pages"] or []
            except Exception:
                pass
        elif '"ad_archive_id"' in body:
            self.blobs.append(body)

    def _blocked(self) -> bool:
        url = self.page.url
        if "/login" in url or "checkpoint" in url:
            return True
        try:
            t = self.page.inner_text("body", timeout=3000)
        except Exception:
            return False
        return bool(re.search(r"log in to continue|you must log in|temporarily blocked|"
                              r"confirm you're human|security check", t, re.I))

    def _wait_results(self, timeout=20000) -> str:
        try:
            self.page.wait_for_function(
                "() => !!document.body && /\\d\\s*[KM]?\\s+results?\\b|No ads match|didn't find any/i"
                ".test(document.body.innerText)",
                timeout=timeout)
        except PWTimeout:
            pass
        self.page.wait_for_timeout(800)
        return self.page.inner_text("body")

    def goto(self, url: str) -> str:
        self.blobs.clear()
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        if self._blocked():
            raise RuntimeError("BLOCKED: Facebook showed a login wall or block page")
        return self._wait_results()

    def suggest_pages(self, query: str) -> list[dict]:
        """Type into the Ad Library search box; return Page suggestions Meta offers."""
        box = self.page.get_by_placeholder("Search by keyword or advertiser").first
        box.click()
        self.page.keyboard.press("Control+A")
        self.page.keyboard.press("Backspace")
        # 10/24: typing letter by letter sent one suggestion request per letter (about 20 a search) and got this
        # machine rate limited. Paste all but the last letter, then type it: 1 or 2 requests.
        box.fill(query[:-1])
        box.type(query[-1:], delay=25)
        deadline = time.time() + 8
        while time.time() < deadline and query not in self.typeahead and not self.rate_limited:
            self.page.wait_for_timeout(250)
        if self.rate_limited:
            raise RateLimited("Meta rate limited the Ad Library search")
        return self.typeahead.get(query, [])

    def keyword_count(self, name: str) -> tuple[int, list[dict]]:
        """Exact-phrase keyword search. Returns (ad count, advertisers [{key, display_name}])."""
        url = f"{BASE}?{COMMON}&q={quote(chr(34) + name + chr(34))}&search_type=keyword_exact_phrase"
        self.filter_pages = []
        text = self.goto(url)
        if re.search(r"No ads match|didn't find any", text, re.I):
            return 0, []
        deadline = time.time() + 4
        while time.time() < deadline and not self.filter_pages:
            self.page.wait_for_timeout(250)
        return parse_count(text) or 0, list(self.filter_pages)

    def page_about(self) -> str:
        """Click the Page's About tab in the Ad Library; return its description line."""
        try:
            self.page.get_by_text("About", exact=True).first.click(timeout=5000)
            self.page.wait_for_function(
                "() => /Page transparency/.test(document.body.innerText)", timeout=8000)
            t = self.page.inner_text("body")
        except Exception:
            return ""
        m = re.search(r"Page transparency\s*(.*?)\s*Pages and accounts", t, re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""

    def page_view(self, page_id: str) -> dict:
        """Active-ad count + first batch of ads + About description for one Page."""
        url = f"{BASE}?{COMMON}&search_type=page&view_all_page_id={page_id}"
        text = self.goto(url)
        no_ads = bool(re.search(r"No ads match|didn't find any", text, re.I))
        count = 0 if no_ads else (parse_count(text) or 0)
        recs = ad_records(self.page.content())
        desc = self.page_about()
        return self._summary(page_id, count, recs, desc, text)

    def load_all(self, page_id: str, view: dict) -> dict:
        """Reload the Page's ad list and scroll until every active ad is loaded (or cap)."""
        url = f"{BASE}?{COMMON}&search_type=page&view_all_page_id={page_id}"
        self.goto(url)
        recs = dict(view["_recs"])
        recs.update(ad_records(self.page.content()))
        count = view["count"]
        scrolls = 0
        while count and len(recs) < count and scrolls < self.max_scrolls:
            before = len(recs)
            self.page.mouse.wheel(0, 20000)
            self.page.wait_for_timeout(1800)
            for b in self.blobs:
                recs.update(ad_records(b))
            scrolls += 1
            if len(recs) == before:
                self.page.wait_for_timeout(1500)
                for b in self.blobs:
                    recs.update(ad_records(b))
                if len(recs) == before:
                    break
        return self._summary(page_id, count, recs, view["about"], self.page.inner_text("body"))

    @staticmethod
    def _summary(page_id, count, recs, desc, text) -> dict:
        recs = {k: v for k, v in recs.items() if v["page_id"] in (None, page_id)}
        dates = [epoch_to_date(v["start"]) for v in recs.values()]
        if not dates and count:
            dates = dom_dates(text)
        words = " | ".join(v.get("words", "") for v in recs.values()) or (text if count else "")
        return {"about": desc, "count": count, "loaded": len(recs), "_recs": recs, "words": words,
                "earliest": min(dates).isoformat() if dates else "",
                "complete": (not count) or len(recs) >= count}


def _own_pages(al: AdLib, name: str, website: str, fb_links) -> list[dict]:
    """10/26: the home's own Facebook Page, from a facebook.com link in their reply or on their website. Strongest
    proof a Page is theirs, and it finds Pages the name search misses (Fite Living Center's Page is "The Fite Living
    Centre"). Each one must open in the Ad Library as a real Page (it shows ads, an About line or its name)."""
    links = [(u, "their reply") for u in fb_links or []] + [(u, "their website") for u in site_facebook(al, website)]
    own, seen = [], set()
    for link, via in links[:4]:
        p = page_of_link(al, link)
        if not p or p["page_id"] in seen:
            continue
        seen.add(p["page_id"])
        view = al.page_view(p["page_id"])
        body = norm(al.page.inner_text("body"))
        words = [w for w in norm(p["name"]).split() if w not in GENERIC and len(w) > 2]
        if view["count"] or view["about"] or (words and all(w in body.split() for w in words)):
            own.append({**p, "via": via, "view": view, "name": p["name"] or name})
        if len(own) >= 2:
            break
    return own


def _ties(view: dict, verdict: str, name: str, city: str, st: str, website: str, via: str = "") -> list[str]:
    """Why a Page counts as this home's, in words for Joey's text. Empty = nothing ties it to them."""
    ties = [f"Page linked on {via}"] if via else []
    if verdict == "city":
        ties.append(f"Page says {city}")
    words = view.get("words", "")
    named = ads_name_home(words, name, city) if view.get("count") else ""
    # 10/26: the name alone is not enough when the ads place the Page in another state, or send people to a
    # website that is not the home's ("City View La" in Los Angeles vs "City View" apartments in Lansing, MI).
    sites = ad_sites(words)
    if named == "name" and (location_verdict(words, city, st) == "other"
                            or (website and sites and not ads_link_site(words, website))):
        named = ""
    if named:
        ties.append(f"ads name {city}" if named == "city" else "ads name the home")
    if view.get("count") and ads_link_site(view.get("words", ""), website):
        ties.append(f"ads link to {site_domain(website)}")
    return ties


def check_home(al: AdLib, name: str, city: str, state: str, website: str = "", fb_links=()) -> dict:
    """website / fb_links (10/26): the home's own site and any Facebook link they wrote. Both are optional; with
    them the check also opens the Page their site links to and counts ads that link to their site."""
    t0 = time.time()
    st = state_code(state)
    res = {"home": name, "city": city, "state": st, "running_ads": "", "active_ads": "",
           "earliest_start": "", "match": "", "page_name": "", "page_id": "",
           "page_about": "", "keyword_hits": "", "rejected": "", "note": "", "ads_name_home": "", "how": ""}
    own = _own_pages(al, name, website, fb_links) if (website or fb_links) else []
    # Load an exact-phrase keyword search first: enables the search box, and its count is
    # a cross-check (ads from ANY advertiser whose text contains the home's name).
    kw_count, kw_pages = al.keyword_count(name)
    res["keyword_hits"] = kw_count
    cands: dict[str, dict] = {}
    # Source 1: advertisers whose active ads contain the exact name (catches small Pages
    # that Meta's typeahead does not suggest).
    for p in kw_pages:
        cands[p["key"]] = {"page_id": p["key"], "name": p["display_name"], "category": "",
                           "likes": 0, "score": name_score(name, p["display_name"])}
    # Source 2: the Page suggestions Meta shows while typing in the search box.
    for q in query_variants(name, city):
        for p in al.suggest_pages(q):
            if p.get("page_is_deleted"):
                continue
            score = name_score(name, p["name"])
            if SENIOR_CAT.search(p.get("category") or ""):
                score += 0.05
            if p["page_id"] not in cands or cands[p["page_id"]]["score"] < score:
                cands[p["page_id"]] = {**p, "score": round(score, 3)}
        if any(c["score"] >= 0.8 for c in cands.values()):
            break
    ranked = sorted(cands.values(), key=lambda c: (-c["score"], -(c.get("likes") or 0)))
    own_ids = {o["page_id"] for o in own}
    ranked = [c for c in ranked if c["score"] >= 0.55 and c["page_id"] not in own_ids][:3]

    chosen, chosen_view, fallback, rejected, verdict = None, None, None, [], ""
    for o in own:  # their own Page running ads settles it
        if o["view"]["count"]:
            chosen, chosen_view = o, o["view"]
            verdict = location_verdict(o["name"] + " | " + o["view"]["about"], city, st)
            res["match"] = f"their own Page (linked on {o['via']})"
            break
    for c in (ranked if not chosen else []):
        view = al.page_view(c["page_id"])
        verdict = location_verdict(c["name"] + " | " + view["about"], city, st)
        if verdict == "city" or (verdict == "state" and not city):
            chosen, chosen_view = c, view
            res["match"] = f"Page name + {verdict} found in Page name/About"
            break
        if verdict == "other":
            rejected.append(f"{c['name']} ({view['about'][:60]})")
            continue
        if fallback is None:
            fallback = (c, view, verdict)
    if not chosen and fallback:
        chosen, chosen_view, verdict = fallback
        res["match"] = ("Page name + state only" if verdict == "state"
                        else "Page name only (About gives no location)")
    res["rejected"] = "; ".join(rejected)

    if chosen:
        if chosen_view["count"] and not chosen_view["complete"]:
            chosen_view = al.load_all(chosen["page_id"], chosen_view)
        n = chosen_view["count"]
        res.update(running_ads="yes" if n else "no", active_ads=n,
                   earliest_start=chosen_view["earliest"], page_name=chosen["name"],
                   page_id=chosen["page_id"], page_about=chosen_view["about"][:120])
        if n and not chosen_view["complete"]:
            res["note"] = f"earliest date from {chosen_view['loaded']} of {n} ads loaded"
        # 10/24: a Page found by name alone counts only when something ties it to the home: its About names the
        # home's city, its ads name the city or the home, (10/26) its ads link to the home's website, or the home's
        # own site or reply links the Page. Otherwise it is "unsure" and the home gets the no-ads email (we never
        # claim ads we cannot tie to them).
        named = ads_name_home(chosen_view.get("words", ""), name, city) if n else ""
        res["ads_name_home"] = named
        ties = _ties(chosen_view, verdict, name, city, st, website, chosen.get("via", ""))
        res["how"] = ", ".join(ties)
        if n and not ties:
            res["running_ads"] = "unsure"
    else:
        res.update(running_ads="unsure" if kw_count else "no", active_ads=kw_count,
                   match="no matching Page found; keyword count only (may include other advertisers)")
    if own and res["running_ads"] != "yes":
        # 10/26: their own Page is known and runs no ads, and no other Page with ads ties to them, so "no". A
        # same-name Page with untied ads is someone else's.
        o = own[0]
        res.update(running_ads="no", active_ads=0, earliest_start="", page_name=o["name"], page_id=o["page_id"],
                   page_about=o["view"]["about"][:120], note="", how=f"Page linked on {o['via']}",
                   match=f"their own Page (linked on {o['via']}) has no active ads")
    if al.rate_limited:
        raise RateLimited("Meta rate limited the Ad Library during this check")
    res["secs"] = round(time.time() - t0, 1)
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("homes", nargs="*", help='"Name|City|State" (city may be blank)')
    ap.add_argument("--csv", help="input CSV with columns name,city,state")
    ap.add_argument("--out", help="write results CSV here")
    ap.add_argument("--test", action="store_true", help="run the 5 built-in test homes")
    ap.add_argument("--stealth", action="store_true", help="playwright-stealth + --headless=new")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--pause", type=float, default=4.0, help="seconds between homes (default 4)")
    ap.add_argument("--max-scrolls", type=int, default=15)
    a = ap.parse_args()

    homes = list(TEST_HOMES) if a.test else []
    for h in a.homes:
        parts = (h.split("|") + ["", ""])[:3]
        homes.append(tuple(p.strip() for p in parts))
    if a.csv:
        with open(a.csv, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                row = {k.lower().strip(): (v or "").strip() for k, v in row.items()}
                homes.append((row.get("name", ""), row.get("city", ""), row.get("state", "")))
    if not homes:
        ap.error("give homes, --csv, or --test")

    results = []
    with sync_playwright() as pw:
        al = AdLib(pw, stealth=a.stealth, headed=a.headed, max_scrolls=a.max_scrolls)
        try:
            for i, (name, city, state) in enumerate(homes):
                if i:
                    time.sleep(a.pause)
                try:
                    r = check_home(al, name, city, state)
                except RuntimeError as e:
                    if "BLOCKED" in str(e) and not a.stealth:
                        print("Blocked; retrying with stealth mode...", file=sys.stderr)
                        al.close()
                        a.stealth = True
                        al = AdLib(pw, stealth=True, headed=a.headed, max_scrolls=a.max_scrolls)
                        r = check_home(al, name, city, state)
                    else:
                        raise
                except Exception as e:  # one bad home should not stop the batch
                    r = {"home": name, "city": city, "state": state, "running_ads": "error",
                         "active_ads": "", "earliest_start": "", "match": "", "page_name": "",
                         "page_id": "", "page_about": "", "keyword_hits": "", "rejected": "",
                         "note": f"{type(e).__name__}: {str(e)[:150]}", "secs": ""}
                results.append(r)
                print(json.dumps(r), flush=True)
        finally:
            al.close()

    print()
    print(f"{'home':50} {'ads':5} {'count':>5}  {'earliest':10}  {'secs':>5}  matched Page / how")
    for r in results:
        print(f"{r['home'][:50]:50} {r['running_ads']:5} {str(r['active_ads']):>5}  "
              f"{r['earliest_start'] or '-':10}  {str(r['secs']):>5}  "
              f"{r['page_name'] or '-'} [{r['page_id'] or '-'}] / {r['match']} {r['note']}".rstrip())
    if a.out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(results[0].keys()))
            w.writeheader()
            w.writerows(results)


if __name__ == "__main__":
    main()
