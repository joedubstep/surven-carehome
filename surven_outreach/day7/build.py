"""Day 7 of the v3 cold sequence, built per home on top of the campaign engine (Joey 10/24: "build on top of the one
we already have built").

  prep(r, p)   the slow part, done the night before (carehome day7-prep) or on the spot if it was missed: the free Ad
               Library check (adlib.py) and, for homes with no ads, the best photo on their own website (photo.py).
               Kept small in data/carehome/day7.json, one record per home.
  build(r, p)  at send time: the email text and html with the picture inside it. Homes running ads (only when the
               ads are tied to them, adlib.py) get the agent's draft of this very email on a phone (phone.py);
               every other home gets one finished free ad made from its photo and colors (free_ad, the locked
               template from 10/24). Pictures are made in memory, a second or two each, so nothing heavy is stored.
Wording is Joey's, locked 10/23-24 (phone.py). Footer: name, company, site and "Reply STOP to opt out." (no mailing
address, Joey 10/24)."""
from __future__ import annotations

import html as H
import io
import json
import re
import tempfile
from datetime import date
from pathlib import Path

from . import phone, photo

ROOT = Path(__file__).resolve().parents[2]
D = ROOT / "data" / "carehome"
STATE = D / "day7.json"
FACTS = D / "lead_facts.json"
PROSPECTS = D / "build" / "prospects.json"
UA = photo.UA
SITE = "https://surven.ai"
FOOTER = "Joey Gargiulo, Surven Occupancy · surven.ai\nReply STOP to opt out."
TITLE_NAMES = {"lvn", "rn", "lpn", "cna", "bsn", "msn", "np", "md", "dr", "mr", "mrs", "ms", "owner", "admin",
               "administrator", "manager", "director"}
_cache: dict = {}


# 10/28: Meta refuses the Ad Library to GitHub's machines, so Joey's PC does the checks (meta_followup.py) and keeps
# them in its own file, pushed to the state branch. The cloud reads both; the newer check wins.
PC_STATE = D / "day7_pc.json"
OUT = STATE  # Joey's PC sets this to PC_STATE


def _read(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def load() -> dict:
    s = _read(STATE)
    for k, v in _read(PC_STATE).items():
        if v.get("checked", "") >= (s.get(k) or {}).get("checked", ""):
            s[k] = v
    return s


def save(s: dict) -> None:
    if OUT == STATE:
        STATE.write_text(json.dumps(s, indent=1), encoding="utf-8")
        return
    cloud = _read(STATE)  # the PC file keeps only what the cloud's file does not already have
    OUT.write_text(json.dumps({k: v for k, v in s.items() if cloud.get(k) != v}, indent=1), encoding="utf-8")


def ready(rec: dict) -> bool:
    """A record the day 7 email can be built from: the photo is picked, or they run ads (no photo needed). The Ad
    Library checks made for reply texts (email_leads.check_meta, "v" 2) have no photo yet, so day 7 still picks one."""
    return "v" not in rec or "photo" in rec or (rec.get("ads") == "yes" and bool(rec.get("start")))


def _facts(pid: str) -> dict:
    if "facts" not in _cache:
        try:
            _cache["facts"] = json.loads(FACTS.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache["facts"] = {}
    return _cache["facts"].get(pid) or {}


def _last_name(pid: str) -> str:
    if "people" not in _cache:
        try:
            _cache["people"] = {x.get("id"): x.get("contact_name") or "" for x in
                                json.loads(PROSPECTS.read_text(encoding="utf-8"))}
        except (OSError, ValueError):
            _cache["people"] = {}
    n = (_cache["people"].get(pid) or "").strip()
    if "," in n:  # "Taliaferro, Kevin"
        return n.split(",")[0].strip()
    parts = n.split()
    return parts[-1] if len(parts) > 1 else ""


def first_name(r) -> str:
    n = (r["first_name"] or "").strip()
    return "" if n.rstrip(".").lower() in TITLE_NAMES else n


def home_name(p: dict) -> str:
    """The name the earlier emails used ("Morningstar Of Pasadena"), or "your home" when the license name looked
    like a company (those emails said "your home" too). License lists put "The" last ("Plaza, The", "Bridgewater At
    Waterman Village (the)"); here it goes back in front."""
    n = p.get("home")
    if not n:
        m = re.search(r"open(?: rooms)? at (.+?) right now\?", p.get("emails", {}).get("1", {}).get("body", ""))
        n = m.group(1) if m else "your home"
    m = re.match(r"^(.+?),?\s*\(?the\)?$", n, re.I) if re.search(r"(,\s*the|\(the\)|\s+the)$", n, re.I) else None
    if m:
        n = "The " + m.group(1).strip()
    m = re.match(r"^The - .+ (\S+)$", n)  # "The - A Senior Living Community Canopy" = "The Canopy"
    if m:
        n = "The " + m.group(1)
    return n.replace(" @ ", " At ")


GENERIC = re.compile(r"\s+(?:assisted living(?: facility| residence| community| center)?|(?:and|&) memory care(?: community)?|"
                     r"retirement communit(?:y|ies)|senior living(?: community)?|(?:and|&)? ?retirement cottages|"
                     r"(?:and|&)? ?special care (?:center|house))(?=\s|$)", re.I)


def ad_name(home: str) -> str:
    """The name on the ad's top band and corner. A long name loses its generic words so it stays readable
    ("Oakbridge Terrace Assisted Living Residence At Buckingham's Choice" -> "Oakbridge Terrace At Buckingham's
    Choice"). The email keeps the full name."""
    if len(home) <= 30:
        return home
    short = re.sub(r"\s{2,}", " ", GENERIC.sub("", home)).strip(" ,&-")
    return short if len(short.split()) >= 2 else home


STREET = re.compile(r"^.*\b(?:road|rd|drive|dr|street|avenue|ave|blvd|boulevard|lane|ln|highway|hwy|pike|way|circle|"
                    r"place|parkway|pkwy)\b\.?\s+(?=\S)", re.I)


def clean_city(city: str) -> str:
    """Fix what the state lists hand us: a street glued to the city when the address had no comma ("Foulk Road
    Wilmington"), a cut-off name ("Washington Court Hou") and "Mcminnville"."""
    c = STREET.sub("", (city or "").replace("200e", "").strip())
    c = re.sub(r"\bCourt Hou$", "Court House", c)
    return re.sub(r"\bMc([a-z])", lambda m: "Mc" + m.group(1).upper(), c)


def _care(f: dict) -> str:
    return "ASSISTED LIVING | MEMORY CARE" if re.search(r"memory|dementia|alzheimer", f.get("care", ""), re.I) \
        else "ASSISTED LIVING"


def _clean_license(name: str) -> str:
    n = re.sub(r",?\s*(llc|inc|l\.l\.c\.|corp|corporation|ltd|lp)\.?$", "", (name or "").strip(), flags=re.I)
    return n.title() if n.isupper() else n


class Session:
    """One browser for a whole run (Ad Library checks and both pictures)."""

    def __init__(self):
        self.pw = None

    def start(self):
        if self.pw:
            return self
        from playwright.sync_api import sync_playwright
        self._cm = sync_playwright()
        self.pw = self._cm.start()
        self.browser = self.pw.chromium.launch()
        self.ctx = self.browser.new_context(viewport={"width": 1080, "height": 1080}, user_agent=UA)
        self.req = self.pw.request.new_context(user_agent=UA, ignore_https_errors=True)
        self.phone_ctx = self.browser.new_context(viewport={"width": 390, "height": 900}, device_scale_factor=2)
        self._adlib = None
        return self

    def adlib(self):
        if self._adlib is None:
            from .adlib import AdLib
            self._adlib = AdLib(self.pw)
        return self._adlib

    def close(self):
        if not self.pw:
            return
        try:
            if self._adlib is not None:
                self._adlib.close()
            self.req.dispose()
            self.browser.close()
        finally:
            self._cm.__exit__(None, None, None)
            self.pw = None


S = Session()


def close() -> None:
    S.close()


def prep(r, p: dict, today: date | None = None) -> dict:
    """Ad Library check + photo pick for one home; saved to day7.json and returned."""
    from .adlib import RateLimited, check_home
    f = _facts(r["id"])
    home = home_name(p)
    search = home if home != "your home" else _clean_license(f.get("facility") or p.get("facility", ""))
    city, st = f.get("city", ""), f.get("state", "")
    al = S.start().adlib()
    if al.rate_limited:  # stays limited for this run; the email waits for a run Meta answers
        raise RateLimited("Ad Library rate limited earlier in this run")
    try:  # 10/28: their website lets check_home open the Facebook Page the site links to (same check as reply texts)
        res = check_home(al, search, city, st, website=f.get("website", ""))
    except RateLimited:
        al.rate_limited = True  # every later home in this run would be refused the same way
        raise
    rec = {"checked": (today or date.today()).isoformat(), "ads": res.get("running_ads", ""),
           "count": res.get("active_ads", ""), "start": res.get("earliest_start", ""),
           "page": res.get("page_name", ""), "page_id": res.get("page_id", ""), "match": res.get("match", ""),
           "how": res.get("how", ""), "search": search}
    if not (rec["ads"] == "yes" and rec["start"]):
        rec.update(pick_photo(r, p, rec))
    s = load()
    s[r["id"]] = rec
    save(s)
    return rec


def pick_photo(r, p: dict, rec: dict) -> dict:
    """A real photo of this home: its Google Maps listing (same address), its Facebook Page, its website (photo.py)."""
    f = _facts(r["id"])
    home = home_name(p)
    name = home if home != "your home" else _clean_license(f.get("facility") or p.get("facility", ""))
    S.start()
    got = photo.pick(f.get("website", ""), r["email"], browser=(S.ctx, S.req),
                     place=(name, f.get("address", ""), f.get("city", "")), fb=rec.get("page_id", ""), home=name)
    got.pop("fb", None)
    return got


def _jpeg(png_path: Path, quality: int = 82) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.open(png_path).convert("RGB").save(buf, "JPEG", quality=quality, optimize=True, progressive=True)
    return buf.getvalue()


def render_ad(r, p: dict, rec: dict) -> bytes:
    from .free_ad.render import render_one
    f = _facts(r["id"])
    home = home_name(p)
    city, st = clean_city(f.get("city", "")), f.get("state", "")
    # "your home" rows: the email does not name the home, so the ad shows its Facebook Page name or no name at all
    # (10/24 volume test: license names like "LIVE HEAL CONNECT RESIDENCES AT SPARTANBURG LLC (LHC ...)").
    shown = ad_name(home) if home != "your home" else ad_name(rec.get("page") or "")
    a = {"name": shown, "city": city, "state": st, "care": "assisted living",
         "band": shown.upper() if home != "your home" else f"ASSISTED LIVING · {city.upper()}, {st}".strip(" ,·"),
         "headline": f"Senior Living in {city}, Designed for Living Well" if city else "Senior Living Designed for Living Well",
         "line": _care(f), "cta": "Book a private tour today.", "cta_style": "text", "shade": "corner",
         "site": rec.get("site", ""), "photo": rec.get("photo") or str(photo.STOCK)}
    S.start()
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "ad.png"
        try:
            render_one(S.ctx, S.req, a, out)
        except Exception:
            if a["photo"] == str(photo.STOCK):
                raise
            a["photo"] = str(photo.STOCK)  # their photo would not load: the stock photo keeps the gift on time
            render_one(S.ctx, S.req, a, out)
        return _jpeg(out)


def render_phone(draft: str, code: str, who: str) -> bytes:
    """Joey's real iPhone screenshot with only the words changed (shot.py, 10/24: the drawn phone "looks so fake")."""
    from . import shot
    return shot.render(draft, code, who)


def _paras(text: str) -> str:
    return "".join(f'<p style="margin:0 0 14px">{H.escape(t).replace(chr(10), "<br>")}</p>'
                   for t in text.split("\n\n") if t.strip())


def _html(before: str, cid: str, alt: str, width: int, after: str) -> str:
    foot = (f'<p style="margin:22px 0 0;font-size:12px;line-height:1.5;color:#8a8a8a">Joey Gargiulo, Surven Occupancy '
            f'&middot; <a href="{SITE}" style="color:#8a8a8a">surven.ai</a><br>Reply STOP to opt out.</p>')
    img = (f'<p style="margin:0 0 14px"><img src="cid:{cid}" alt="{H.escape(alt)}" width="{width}" '
           f'style="display:block;width:100%;max-width:{width}px;height:auto;border:0"></p>')
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#222">'
            + _paras(before) + img + _paras(after) + foot + "</div>")


def build(r, p: dict) -> dict:
    """-> {"body": plain text, "html": html, "images": [(cid, bytes, subtype)], "kind": "ads" | "free_ad"}"""
    rec = load().get(r["id"]) or prep(r, p)
    if not ready(rec):  # only the Ad Library was checked (a reply text or the list check): pick the photo now
        rec = {**rec, **pick_photo(r, p, rec)}
        s = load()
        s[r["id"]] = rec
        save(s)
    first, home = first_name(r), home_name(p)
    if rec.get("ads") == "yes" and rec.get("start"):
        body = phone.ads_email(first, home, date.fromisoformat(rec["start"]))
        code, who = phone.code_for(r["id"]), phone.short_name(first, _last_name(r["id"]), home)
        img = render_phone(phone.draft_text(code, who, body), code, who)
        before, after = body.split("It wrote this email.")
        before += "It wrote this email."
        return {"body": body + "\n\n" + FOOTER, "kind": "ads", "images": [("day7", img, "png")],
                "html": _html(before, "day7", "The agent's draft of this email", 360, after.strip())}
    top, bottom = phone.noads_email(first, home)
    img = render_ad(r, p, rec)
    return {"body": f"{top}\n\n{bottom}\n\n{FOOTER}", "kind": "free_ad", "images": [("day7", img, "jpeg")],
            "html": _html(top, "day7", f"A free Facebook ad for {home}", 520, bottom)}
