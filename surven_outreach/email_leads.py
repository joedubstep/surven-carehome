"""Cold email repliers as callable leads (2026-10-06).

When an owner replies to a cold email, the scan (carehome.py) queues an alert. Before that alert is texted to Joey, this
module makes the replier a lead in the SAME machine as the form leads:
  - a GoHighLevel contact in Surven HQ, tagged "email-reply" (+ "cold email sept 2026"), with the facility phone,
    the website, and a fixed call code number in the "Email Reply Code" field: E1, E2, E3... The reply listener
    (surven-reply-listener, lib/account.js emailLeads) reads that field, so Joey texts "E3" and the usual call bridge,
    round check, transcript and agent wake run exactly as for any lead.
  - an opportunity in the "Email Pipeline" (stage Replied), one per contact.
  - a note with what they wrote.
Every step is safe to run twice: the contact is upserted by email, a code is assigned once and kept, an opportunity is
made only when the contact has none in this pipeline.

Phone and website are never guessed:
  - phone, in this order: a number they wrote in the reply itself ("call my cell 916-600-0734"), then a phone already
    on the contact (Joey may have fixed it by hand), then the facility's own number from its state license list,
    matched by license id (data/carehome/lead_facts.json, built by build/lead_facts.py from build/license_phones.py).
    Nothing found means "not found", never a nearby number.
  - website (10/26, Joey: "beef up the system that grabs the website"; Fite Living Center wrote
    www.fitelivingcentre.com in its signature from a Gmail address and the text said "not found"), in this order:
    a website written in their reply or signature, the state list's website, the replier's business email domain,
    the website on the home's Google Maps listing (same address). Each must open. The email domain also has to name
    the facility, its city or its phone (the others are tied to the home by the reply, the license or the address).
    Free mail (gmail...) gives no website.
"""
import json
import re
import subprocess
import urllib.request
from datetime import datetime
from pathlib import Path

from . import secrets_store

LOCATION = "EeadFhXhDOJ1DWE4ArhK"  # Surven HQ
TAGS = ["cold email sept 2026", "email-reply", "🏢 business (not a person)", "assisted living"]
CODE_FIELD = "6pUnUwaxqRfQW3q0g2AR"  # contact field "Email Reply Code"
PIPELINE = "D26Fys9N44CiadqYjkm7"    # "Email Pipeline"
STAGE_REPLIED = "2d72401c-7e3b-47ad-9444-a38ea1a91e3e"
D = Path(__file__).resolve().parent.parent / "data" / "carehome"
FACTS = D / "lead_facts.json"
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "msn.com", "live.com",
            "comcast.net", "att.net", "sbcglobal.net", "bellsouth.net", "verizon.net", "me.com", "ymail.com", "cox.net",
            "charter.net", "earthlink.net", "protonmail.com", "mail.com", "gmx.com", "frontier.com", "windstream.net"}
GENERIC = {"assisted", "living", "care", "home", "homes", "senior", "seniors", "residence", "residential", "manor",
           "village", "house", "place", "center", "community", "memory", "the", "and", "llc", "inc", "facility",
           "retirement", "elderly", "board", "group", "health", "healthcare", "services", "personal"}


def _ghl(method, path, body=None, version="2021-07-28"):
    args = ["curl", "-s", "-X", method, "https://services.leadconnectorhq.com" + path,
            "-H", "Authorization: Bearer " + secrets_store.get("GHL_PIT"), "-H", "Version: " + version,
            "-H", "Content-Type: application/json"]
    if body is not None:
        args += ["--data-binary", "@-"]
    r = subprocess.run(args, input=json.dumps(body) if body is not None else None, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=40)
    try:
        return json.loads(r.stdout or "{}")
    except ValueError:
        return {"_raw": (r.stdout or "")[:200]}


def _facts(pid: str) -> dict:
    try:
        return json.load(open(FACTS, encoding="utf-8")).get(pid) or {}
    except (OSError, ValueError):
        return {}


PHONE_IN_TEXT = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?([2-9]\d{2})\)?[\s.-]?(\d{3})[\s.-]?(\d{4})(?!\d)")


def phone_in_reply(said: str) -> str:
    """A US number written in the reply itself, as +1XXXXXXXXXX, or "". Only the newest text, so a quoted signature
    of ours never counts (new_text already cut the quoted part)."""
    m = PHONE_IN_TEXT.search(said or "")
    return f"+1{m.group(1)}{m.group(2)}{m.group(3)}" if m else ""


def pretty_phone(e164: str) -> str:
    d = re.sub(r"\D", "", e164 or "")[-10:]
    return f"({d[:3]}) {d[3:6]}-{d[6:]}" if len(d) == 10 else ""


def _page_text(url: str) -> str:
    """The page's html; a site that refuses a plain download (403, bad certificate, JavaScript) opens in the browser."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.read(400_000).decode("utf-8", "ignore")
    except Exception:
        pass
    from .day7 import build as day7
    pg = day7.S.start().ctx.new_page()
    try:
        pg.goto(url, wait_until="domcontentloaded", timeout=30000)
        pg.wait_for_timeout(2000)
        return pg.content()
    finally:
        pg.close()


def site_names_facility(url: str, facility: str, city: str, phone: str) -> bool:
    """The page at url names this facility (a distinctive word of its name), its city, or its phone number."""
    try:
        html = _page_text(url).lower()
    except Exception:
        return False
    words = [w for w in re.findall(r"[a-z]{4,}", (facility or "").lower()) if w not in GENERIC]
    digits = re.sub(r"\D", "", phone or "")[-10:]
    return (any(w in html for w in words) or (city and city.lower() in html)
            or (len(digits) == 10 and digits in re.sub(r"\D", "", html)))


def site_loads(url: str) -> str:
    """The address that opens: url, or its http:// form when the https one fails (Beach Terrace's certificate is
    broken). "" when neither opens."""
    for u in dict.fromkeys([url, re.sub(r"^https://", "http://", url)]):
        try:
            if len(_page_text(u)) > 500:
                return u
        except Exception:
            pass
    return ""


EMAIL_IN_TEXT = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
URL_IN_TEXT = re.compile(r"(?:https?://|www\.)[^\s<>\"'*()\[\],|]+|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
                         r"\.(?:com|org|net|us|care|info|biz|co|health|homes|home|life|living|senior|community)\b"
                         r"(?:/[^\s<>\"'*()\[\],|]*)?", re.I)
# Links in a reply that are not the home's own site: social pages, directories, mail and meeting tools, and us.
NOT_THEIR_SITE = re.compile(r"facebook|instagram|linkedin|twitter|\bx\.com|youtube|youtu\.be|tiktok|google|goo\.gl|"
                            r"yelp|caring\.com|aplaceformom|seniorly|senioradvisor|bit\.ly|zoom\.us|calendly|surven|"
                            r"improvmx|microsoft|outlook|office\.com|gmail|yahoo|apple\.com|icloud|mailchimp|"
                            r"constantcontact|docusign|hipaa|adobe|dropbox", re.I)


def sites_in_text(said: str) -> list:
    """Websites written in the reply or its signature, as https://host ("*www.fitelivingcentre.com*" ->
    https://www.fitelivingcentre.com). Email addresses are not websites."""
    out = []
    for u in URL_IN_TEXT.findall(EMAIL_IN_TEXT.sub(" ", said or "")):
        host = re.sub(r"^https?://", "", u, flags=re.I).split("/")[0].split("?")[0].rstrip(".:;").lower()
        if "." in host and not NOT_THEIR_SITE.search(host) and host.split(".")[-1].isalpha():
            out.append(f"https://{host}")
    return list(dict.fromkeys(out))


def maps_website(pid: str, facility: str) -> str:
    """The website on the home's Google Maps listing, when the listing is at the license address (day7/maps.py)."""
    f = _facts(pid)
    if not f.get("address"):
        return ""
    from .day7 import build as day7, maps
    pg = day7.S.start().ctx.new_page()
    try:
        got = maps.find(pg, day7._clean_license(facility or f.get("facility", "")), f["address"], f.get("city", ""))
    except Exception:
        return ""
    finally:
        pg.close()
    return got.get("website", "") if got.get("match") else ""


def find_website(pid: str, replier_email: str, facility: str, said: str = "") -> dict:
    """-> {"url", "from", "facebook"}: the home's own website, where it was found, and a Facebook Page link when that
    is all a source gave (a Maps listing or state list whose "website" is their Facebook Page)."""
    f = _facts(pid)
    fac, city, phone = facility or f.get("facility", ""), f.get("city", ""), f.get("phone", "")
    from .day7.adlib import facebook_links
    fb = next(iter(facebook_links(said or "")), "")
    for url in sites_in_text(said):
        if opened := site_loads(url):
            return {"url": opened, "from": "their reply", "facebook": fb}
    if f.get("website"):
        url = f["website"] if "://" in f["website"] else "https://" + f["website"]
        if "facebook.com" in url.lower():
            fb = fb or url
        elif opened := site_loads(url):
            return {"url": opened, "from": "state license", "facebook": fb}
    dom = (replier_email or "").split("@")[-1].lower()
    if dom and dom not in FREEMAIL:
        for url in (f"https://{dom}", f"https://www.{dom}"):
            if site_names_facility(url, fac, city, phone):
                return {"url": url, "from": "their email address", "facebook": fb}
    try:
        url = maps_website(pid, fac)
    except Exception:
        url = ""
    if url and "facebook.com" in url.lower():
        return {"url": "", "from": "", "facebook": fb or url}
    if url and (opened := site_loads(url)):
        return {"url": opened, "from": "Google Maps", "facebook": fb}
    return {"url": "", "from": "", "facebook": fb}


def _code_of(contact: dict) -> int:
    v = next((f.get("value") for f in contact.get("customFields") or [] if f.get("id") == CODE_FIELD), None)
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (TypeError, ValueError):
        return 0


# Codes given out by this process. GoHighLevel's search can lag a contact made seconds ago, so two replies in one run
# must not both see the same highest code. Across runs (30+ minutes apart) the search has long caught up.
_GIVEN: dict = {}


def _taken_codes() -> set:
    j = _ghl("POST", "/contacts/search", {"locationId": LOCATION, "pageLimit": 100,
                                          "filters": [{"field": "tags", "operator": "contains", "value": "email-reply"}]})
    if "contacts" not in j:
        raise RuntimeError(f"code search failed: {json.dumps(j)[:200]}")
    return {n for n in ({_code_of(c) for c in j["contacts"]} | set(_GIVEN.values())) if n}


def prepare(item: dict) -> dict:
    """Make the replier a callable lead. Returns {"code": "E3", "phone": "+1...", "website": "...", "contact_id": ...}.
    Raises on any GoHighLevel failure so the caller retries next run; every step is safe to repeat."""
    email = (item.get("from_email") or item.get("email") or "").strip().lower()
    if not email:
        raise ValueError("no replier email")
    facts = _facts(item["id"])
    site = ({"url": item["website"], "from": "", "facebook": ""} if "website" in item
            else find_website(item["id"], email, item.get("facility", ""), item.get("said", "")))
    website = site["url"]
    name = (item.get("from_name") or "").strip()
    first, _, last = name.partition(" ")
    body = {"locationId": LOCATION, "email": email, "companyName": item.get("facility", ""),
            "source": "cold email sept 2026", "tags": TAGS}
    if first:
        body.update({"firstName": first, "lastName": last})
    if website:
        body["website"] = website
    up = _ghl("POST", "/contacts/upsert", body)
    contact = up.get("contact") or {}
    cid = contact.get("id")
    if not cid:
        raise RuntimeError(f"contact upsert failed: {json.dumps(up)[:200]}")
    contact = (_ghl("GET", f"/contacts/{cid}").get("contact") or contact)
    _ghl("POST", f"/contacts/{cid}/tags", {"tags": TAGS})
    # The phone to call: what they wrote, else what is already saved, else the state license list's facility phone.
    phone = phone_in_reply(item.get("said", "")) or contact.get("phone") or facts.get("phone", "")
    phone_from = phone_source(phone, item.get("said", ""), contact.get("phone", ""), facts)
    if phone and phone != contact.get("phone"):
        put = _ghl("PUT", f"/contacts/{cid}", {"phone": phone})
        if not (put.get("contact") or put.get("succeded")):
            raise RuntimeError(f"phone save failed: {json.dumps(put)[:200]}")
    # The code: the one already on the contact (it never changes), else the next free number.
    n = _code_of(contact) or _GIVEN.get(email) or (max(_taken_codes() or {0}) + 1)
    _GIVEN[email] = n
    if _code_of(contact) != n:
        put = _ghl("PUT", f"/contacts/{cid}", {"customFields": [{"id": CODE_FIELD, "field_value": n}]})
        if not (put.get("contact") or put.get("succeded")):
            raise RuntimeError(f"code save failed: {json.dumps(put)[:200]}")
    # One opportunity per contact in the Email Pipeline. A later reply never moves its stage back.
    opps = _ghl("GET", f"/opportunities/search?location_id={LOCATION}&contact_id={cid}&pipeline_id={PIPELINE}")
    if "opportunities" not in opps:
        raise RuntimeError(f"opportunity search failed: {json.dumps(opps)[:200]}")
    if not opps["opportunities"]:
        made = _ghl("POST", "/opportunities/", {"pipelineId": PIPELINE, "locationId": LOCATION, "pipelineStageId": STAGE_REPLIED,
                                                 "status": "open", "contactId": cid,
                                                 "name": f"{item.get('facility', '')} ({name or email})"[:120]})
        if not (made.get("opportunity") or {}).get("id"):
            raise RuntimeError(f"opportunity create failed: {json.dumps(made)[:200]}")
    if not item.get("noted"):
        when = item.get("at", datetime.now().isoformat(timespec="minutes"))[:16].replace("T", " ")
        _ghl("POST", f"/contacts/{cid}/notes", {"body": f"Cold email reply {when} ({item.get('facility', '')}, code E{n}): {item.get('said', '')[:1500]}"})
    return {"code": f"E{n}", "phone": phone, "phone_from": phone_from, "website": website,
            "website_from": site["from"], "facebook": site["facebook"], "contact_id": cid}


def phone_source(phone: str, said: str, saved: str, facts: dict) -> str:
    """Where the number came from, for the line under it in Joey's text (10/20, Joey: "where you got the phone
    number from... (from blah blah blah)"). A state license number is the home's main line, not a personal cell."""
    if not phone:
        return ""
    if phone == phone_in_reply(said):
        return "their reply"
    if phone == facts.get("phone"):
        return "state license"
    return "GoHighLevel" if phone == saved else ""


def short_name(name: str, fallback: str) -> str:
    parts = (name or "").split()
    if not parts:
        return fallback
    return f"{parts[0]} {parts[-1][0]}" if len(parts) > 1 else parts[0]


TZ_NAMES = {"America/New_York": "Eastern", "America/Detroit": "Eastern", "America/Chicago": "Central",
            "America/Denver": "Mountain", "America/Boise": "Mountain", "America/Phoenix": "Arizona",
            "America/Los_Angeles": "Pacific", "America/Anchorage": "Alaska", "Pacific/Honolulu": "Hawaii"}
DIRECTIONS = {"N", "S", "E", "W", "NE", "NW", "SE", "SW"}


def nice_address(a: str, state: str = "") -> str:
    """State lists often shout ("1352 WELLBROOK CIR, CONYERS, GA 30012"). Only an all-caps address is changed."""
    a = re.sub(r"\s+,", ",", (a or "").strip())
    if not a or a != a.upper():
        return a
    def word(w):
        core = w.strip(",.")
        if core in DIRECTIONS or (state and core == state) or core.isdigit():
            return w
        if re.fullmatch(r"\d+(ST|ND|RD|TH)", core):
            return w.lower()
        return w.capitalize()
    return " ".join(word(w) for w in a.split())


def community_lines(f: dict) -> list:
    """10/20, Joey: "more information on their community... what kind of care they provide, how many beds there are,
    where they're located, and their time zone". Only what the state license list said; a missing fact is left out."""
    out = []
    if f.get("care"):
        out.append(f"Care: {f['care']}")
    if f.get("beds"):
        beds = f"Beds: {f['beds']}" if f.get("beds_word", "beds") == "beds" else f"Units: {f['beds']}"
        if f.get("open_beds") is not None and f.get("census_date"):
            beds += f" ({f['open_beds']} open at the {f['census_date']} state visit)"
        out.append(beds)
    where = nice_address(f.get("address", ""), f.get("state", "")) or ", ".join(x for x in (f.get("city"), f.get("state")) if x)
    if where:
        out.append(f"Where: {where}")
    if f.get("tz"):  # 10/20, Joey: "just say time: Eastern. I don't need the exact time."
        out.append(f"Time: {TZ_NAMES.get(f['tz'], f['tz'])}")
    return out


LIBRARY = "https://www.facebook.com/ads/library/?active_status=active&ad_type=all&country=US"
META_LATER = "Meta ads: still checking, I'll text it"


def check_meta(pid: str, facts: dict, ld: dict) -> dict | None:
    """A fresh Ad Library check for one home (day7/adlib.py check_home with their website and Facebook link), saved
    to day7.json for day 7 too. None when the home is not in the queue. Raises when Meta could not be checked
    (meta_line then says "still checking" and the next run, on a new machine, tries again)."""
    from datetime import date
    from .day7 import build as day7
    from .day7.adlib import check_home
    queue = {x["id"]: x for x in json.load(open(FACTS.parent / "queue.json", encoding="utf-8"))}
    p = queue.get(pid)
    if not p:
        return None
    home = day7.home_name(p)
    name = home if home != "your home" else day7._clean_license(facts.get("facility") or p.get("facility", ""))
    args = (name, facts.get("city", ""), facts.get("state", ""))
    kw = {"website": ld.get("website", ""), "fb_links": [ld["facebook"]] if ld.get("facebook") else []}
    res = check_home(day7.S.start().adlib(), *args, **kw)
    rec = {"v": 2, "checked": date.today().isoformat(), "ads": res.get("running_ads", ""),
           "count": res.get("active_ads", ""), "start": res.get("earliest_start", ""),
           "page": res.get("page_name", ""), "page_id": res.get("page_id", ""),
           "match": res.get("match", ""), "how": res.get("how", ""), "search": name}
    saved = day7.load()
    saved[pid] = {**(saved.get(pid) or {}), **rec}
    day7.save(saved)
    return rec


def meta_line(pid: str, facts: dict, lead: dict | None = None) -> list:
    """10/25, Joey: "another line that includes Meta Ads, yes or no. If it's a yes, then I would like a link to their
    corresponding Facebook Ad Library". 10/26, Joey: "double, triple, quadruple check the Meta ads claim": a reply
    always gets a fresh check that also opens the Facebook Page their website (or reply) links to and counts ads
    that link to their website (check_meta), saved for day 7 too. The line under it says how the Page was tied to
    them. Only ads tied to the home count as yes. 10/28, Joey: "We must always check": when Meta cannot be checked,
    the line says so and send_alert_texts keeps retrying and texts the answer when it has it (META_LATER)."""
    from datetime import date
    from urllib.parse import quote
    try:
        from .day7 import build as day7, phone
        old = day7.load().get(pid)
        rec = old if old and old.get("v") == 2 and old.get("checked") == date.today().isoformat() else None
        rec = rec or check_meta(pid, facts, lead or {})
        if rec is None:
            return []
    except Exception as ex:
        print(f"meta check {pid} failed: {ex!r}"[:400], flush=True)
        return [META_LATER]
    how = rec.get("how", "")
    if rec.get("ads") == "yes":
        since = f", since {phone.since(date.fromisoformat(rec['start']))}" if rec.get("start") else ""
        link = f"{LIBRARY}&view_all_page_id={rec['page_id']}" if rec.get("page_id") else ""
        return [f"Meta ads: yes{since}"] + ([f"(how we know: {how})"] if how else []) + ([link] if link else [])
    if rec.get("ads") == "unsure":
        q = quote('"' + (rec.get("search") or rec.get("page") or facts.get("facility", "")) + '"')
        return ["Meta ads: not sure (ads match the name, not tied to them)",
                f"{LIBRARY}&q={q}&search_type=keyword_exact_phrase"]
    page = rec.get("page") or "no name"
    if rec.get("page_id") and how:
        return ["Meta ads: no", f"(their Page, {page}, has 0 ads. How we know it's theirs: {how})"]
    if rec.get("page_id"):
        return ["Meta ads: none found", f"(closest Page by name, {page}, has 0 ads, but nothing ties it to them)"]
    return ["Meta ads: none found (no Facebook Page found for them)"]


def replied_to_line(item: dict) -> list:
    """10/28, Joey: "I need to see what email they are replying to, like what stage they are in."
    -> ['Replied to: Day 3 email (2 of 4), sent Oct 28: "I just emailed you."'] or []."""
    t = item.get("replied_to") or {}
    if not t.get("step"):
        return []
    from datetime import date
    from . import carehome as ch
    try:
        p = next((x for x in json.load(open(FACTS.parent / "queue.json", encoding="utf-8")) if x["id"] == item.get("id")), {})
    except (OSError, ValueError):
        p = {}
    offs = ch.offsets(p)
    step = int(t["step"])
    day = f"Day {offs[step] + 1} email" if step in offs else f"email {step}"
    sent = date.fromisoformat(t["sent"]).strftime("%b %d").replace(" 0", " ") if t.get("sent") else ""
    line = f"Replied to: {day} ({step} of {max(offs)})" + (f", sent {sent}" if sent else "")
    return [line + (f': "{t["line"]}"' if t.get("line") else "")]


def alert_text(item: dict, lead: dict | None) -> str:
    """Joey's text, in the agent's own shape ("Call now: 20. Iivory W ... Reply "20" to call.")."""
    who = short_name(item.get("from_name", ""), item.get("first_name") or "Owner")
    code = (lead or {}).get("code", "")
    head = "Email reply again" if item.get("again") else "Email reply"
    said = (item.get("said") or "").strip()
    if len(said) > 700:
        said = said[:700].rsplit(" ", 1)[0] + "..."
    phone = pretty_phone((lead or {}).get("phone", "")) or "not found"
    site = re.sub(r"^https?://(www\.)?", "", (lead or {}).get("website", "")).rstrip("/") or "not found"
    lines = [f"{head}: {code + '. ' if code else ''}{who}, {item.get('facility', '')}"] + replied_to_line(item) + [f"\"{said}\""]
    facts = _facts(item.get("id", ""))
    lines += community_lines(facts)
    lines += meta_line(item.get("id", ""), facts, lead)
    lines.append(f"Phone: {phone}")
    ld = lead or {}
    src = ld.get("phone_from") or phone_source(ld.get("phone", ""), item.get("said", ""), "", facts)
    if src:
        lines.append(f"(from {src})")
    lines.append(f"Site: {site}")
    if ld.get("website_from") and site != "not found":
        lines.append(f"(from {ld['website_from']})")
    if not code:
        lines.append("Call code coming, GoHighLevel did not answer. Texting it when it does.")
    elif phone == "not found":
        lines.append(f"No phone found, so {code} cannot dial yet.")
    else:
        lines.append(f"Reply \"{code}\" to call.")
    return "\n".join(lines)


if __name__ == "__main__":  # python -m surven_outreach.email_leads meta-check 3   (cloud task "meta-check")
    import sys
    import traceback
    if sys.argv[1:2] == ["meta-check"]:
        from .day7 import build as _day7
        _day7.save = lambda s: None  # a test: nothing is saved
        items = [json.loads(l) for l in open(D / "alerts_sent.jsonl", encoding="utf-8") if l.strip()]
        for it in items[-int((sys.argv[2:3] or ["3"])[0]):]:
            print("=" * 40, it["id"], flush=True)
            try:
                print(check_meta(it["id"], _facts(it["id"]), it.get("lead") or {}), flush=True)
            except Exception:
                traceback.print_exc()
        _day7.close()
