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
  - website: the state list's website when it has one, else the replier's business email domain, but only after the
    page itself is fetched and names the facility, its city or its phone. Free mail (gmail...) gives no website.
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
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0"})
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read(400_000).decode("utf-8", "ignore")


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


def find_website(pid: str, replier_email: str, facility: str) -> str:
    f = _facts(pid)
    if f.get("website"):
        return f["website"]
    dom = (replier_email or "").split("@")[-1].lower()
    if not dom or dom in FREEMAIL:
        return ""
    for url in (f"https://{dom}", f"https://www.{dom}"):
        if site_names_facility(url, facility or f.get("facility", ""), f.get("city", ""), f.get("phone", "")):
            return url
    return ""


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
    website = item.get("website") if "website" in item else find_website(item["id"], email, item.get("facility", ""))
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
    return {"code": f"E{n}", "phone": phone, "website": website, "contact_id": cid}


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
    lines = [f"{head}: {code + '. ' if code else ''}{who}, {item.get('facility', '')}", f"\"{said}\""]
    lines += community_lines(_facts(item.get("id", "")))
    lines += [f"Phone: {phone}", f"Site: {site}"]
    if not code:
        lines.append("Call code coming, GoHighLevel did not answer. Texting it when it does.")
    elif phone == "not found":
        lines.append(f"No phone found, so {code} cannot dial yet.")
    else:
        lines.append(f"Reply \"{code}\" to call.")
    return "\n".join(lines)
