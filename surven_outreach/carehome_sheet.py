"""Care-home list as a live Google Sheet (Joey 9/29: "a Google Sheet that has all 5,000 homes... keep track of the
contacts' statuses too"). Joey made the sheet himself ("Care home email list", owner joedubstep312) from the CSV.
  adopt: one time. Adds Status / Emails sent / Last email / Notes (yours) columns at the front, drops the old static
         Campaign column, appends campaign homes missing from the CSV, formats the tab.
  sync:  rewrites Status / Emails sent / Last email from carehome.db, matched by email. Runs nightly from
         carehome_stats.py. Finds columns by header name, so Joey can sort, filter or move columns. Never touches
         Notes (yours) or anything else.
Auth: google_auth.py (spreadsheets scope). Sheet id lives in data/carehome/sheet_map.json."""
import json, sqlite3, sys, urllib.parse, urllib.request
from pathlib import Path
from .google_auth import access_token

D = Path(__file__).resolve().parent.parent / "data" / "carehome"
MAP = D / "sheet_map.json"
BASE = "https://sheets.googleapis.com/v4/spreadsheets/"
FRONT = ["Status", "Emails sent", "Last email", "Notes (yours)"]
TAB = "Homes"


def api(method, url, body=None, tok=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=120).read() or b"{}")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Google API {e.code}: {e.read()[:400].decode(errors='ignore')}")


# 10/24, Joey: the sheet shows each home's stage. Day N = the day of the 4-email run (days 1, 3, 7, 14); homes still on
# the old 6-email run show the nearest stage. Replies are sorted by what they said.
DAY = {1: "Day 1 emailed", 2: "Day 3 emailed", 3: "Day 7 emailed", 4: "Day 14 emailed"}
OLD_DAY = {1: "Day 1 emailed", 2: "Day 3 emailed", 3: "Day 7 emailed", 4: "Day 7 emailed", 5: "Day 14 emailed",
           6: "Day 14 emailed"}
STAGE_COLORS = [  # (label, background, white bold text?)
    ("Day 1 emailed", "#E3F0FC", False), ("Day 3 emailed", "#CFE3F8", False), ("Day 7 emailed", "#B6D3F2", False),
    ("Day 14 emailed", "#9C9C9C", False), ("Opted out", "#E6E6E6", False), ("Bounced", "#F5C6C6", False),
    ("Warm reply", "#B7E1B7", False), ("Not a fit", "#F9DDB5", False), ("Yes to lead magnet", "#3E9E4F", True),
]
WORD_NUM = {"zero": 0, "no": 0, "none": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
            "eight": 8, "nine": 9, "ten": 10, "a couple": 2, "a few": 3, "several": 4}


def reply_stage(said: str, step: int, seq: str) -> str:
    """What a reply means for the campaign: a yes to the free offer, a room count, or a plain reply. The offer is the
    day 7 gift on the 4-email run; on the old run every email but 4 and 5 offered the one-page plan."""
    import re
    s = (said or "").lower()
    s = re.sub(r"\[cid:[^\]]*\]|<[^>]*>|https?://\S+|\S+@\S+|\+?\d[\d\-. ()]{6,}\d", " ", s)  # signatures, phones
    offer = step >= 3 if seq == "v3" else step in (1, 2, 3, 6)
    if offer and re.search(r"\b(yes|yeah|yep|sure|ok|okay|please|interested|send)\b", s):
        return "Yes to lead magnet"
    if re.search(r"\b(we are full|we're full|fully occupied|no (open )?(rooms|beds|vacanc\w*|openings))\b|\bfull\b", s):
        return "Not a fit"
    m = re.search(r"\b(\d{1,3})\b", s)
    n = int(m.group(1)) if m else next((v for k, v in WORD_NUM.items() if re.search(rf"\b{k}\b", s)), None)
    if n is not None:
        return "Warm reply" if n >= 3 else "Not a fit"
    return "Warm reply"


def status_by_email():
    """email -> (stage, emails sent, last email date) from carehome.db."""
    con = sqlite3.connect(D / "carehome.db"); con.row_factory = sqlite3.Row
    try:
        seq = {x["id"]: x.get("seq", "") for x in json.load(open(D / "queue.json", encoding="utf-8"))}
    except (OSError, ValueError):
        seq = {}
    out = {}
    for r in con.execute("SELECT * FROM prospects"):
        st, step = r["status"], r["current_step"] or 0
        v3 = seq.get(r["id"]) == "v3"
        if st in ("active", "completed"):
            label = (DAY if v3 else OLD_DAY).get(step, "Day 14 emailed")
        elif st == "replied":
            said = (r["stopped_reason"] or "").split(":", 1)[-1]
            label = reply_stage(said, step, "v3" if v3 else "")
        else:
            label = {"queued": "Not emailed yet", "bounced": "Bounced", "opted_out": "Opted out",
                     "skipped": "Skipped"}.get(st, st.title())
        out[(r["email"] or "").strip().lower()] = [label, step, r["last_sent_on"] or ""]
    try:  # 10/24: a home whose address was replaced (enrich_mail.py) still shows on its sheet row (the old address)
        for old, rec in json.load(open(D / "email_enrich.json", encoding="utf-8")).items():
            if rec.get("new") and rec["new"] in out and old not in out:
                out[old] = out[rec["new"]]
    except (OSError, ValueError):
        pass
    return out


def set_stage_colors():
    """Replace the Homes tab's color rules with one per stage (exact match, so "Day 1" never colors "Day 14")."""
    sid = json.load(open(MAP))["id"]; tok = access_token()
    meta = api("GET", BASE + f"{sid}?fields=sheets(properties(sheetId,title),conditionalFormats)", tok=tok)
    sh = next(s for s in meta["sheets"] if s["properties"]["title"] == TAB)
    gid, n_old = sh["properties"]["sheetId"], len(sh.get("conditionalFormats", []))
    head = sheet_values(sid, tok, f"{TAB}!1:1")[0]
    col = head.index("Status")
    rng = {"sheetId": gid, "startRowIndex": 1, "startColumnIndex": col, "endColumnIndex": col + 1}

    def rgb(h):
        return {"red": int(h[1:3], 16) / 255, "green": int(h[3:5], 16) / 255, "blue": int(h[5:7], 16) / 255}
    reqs = [{"deleteConditionalFormatRule": {"sheetId": gid, "index": 0}} for _ in range(n_old)]
    for i, (label, bg, bold) in enumerate(STAGE_COLORS):
        fmt = {"backgroundColor": rgb(bg)}
        if bold:
            fmt["textFormat"] = {"bold": True, "foregroundColor": rgb("#FFFFFF")}
        reqs.append({"addConditionalFormatRule": {"index": i, "rule": {"ranges": [rng], "booleanRule": {
            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": label}]}, "format": fmt}}}})
    api("POST", BASE + f"{sid}:batchUpdate", {"requests": reqs}, tok)
    print(f"sheet: {n_old} old color rules replaced with {len(STAGE_COLORS)} stage colors")


def col_letter(i):
    s = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        s = chr(65 + rem) + s
    return s


def sheet_values(sid, tok, rng):
    return api("GET", BASE + f"{sid}/values/{urllib.parse.quote(rng)}", tok=tok).get("values", [])


def adopt(sid):
    tok = access_token()
    meta = api("GET", BASE + f"{sid}?fields=sheets(properties)", tok=tok)
    sp = meta["sheets"][0]["properties"]; gid, title = sp["sheetId"], sp["title"]
    head = sheet_values(sid, tok, f"'{title}'!1:1")[0]
    if head[0] == "Status":
        raise SystemExit("already adopted")
    camp = head.index("Campaign") if "Campaign" in head else None
    reqs = [{"insertDimension": {"range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 4}, "inheritFromBefore": False}}]
    if camp is not None:  # static snapshot column, replaced by the live Status column
        reqs.append({"deleteDimension": {"range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": camp + 4, "endIndex": camp + 5}}})
    reqs.append({"updateSheetProperties": {"properties": {"sheetId": gid, "title": TAB, "gridProperties": {"frozenRowCount": 1}},
                                           "fields": "title,gridProperties.frozenRowCount"}})
    api("POST", BASE + f"{sid}:batchUpdate", {"requests": reqs}, tok)
    api("PUT", BASE + f"{sid}/values/{TAB}!A1:D1?valueInputOption=RAW", {"values": [FRONT]}, tok)

    # campaign homes that were never in the CSV (the first 100: hand research + 9/22 bulk list)
    head = sheet_values(sid, tok, f"{TAB}!1:1")[0]
    ei = head.index("Email")
    have = {(r[0] if r else "").strip().lower() for r in sheet_values(sid, tok, f"{TAB}!{col_letter(ei)}:{col_letter(ei)}")}
    P = json.load(open(D / "build/prospects.json", encoding="utf-8"))
    add = []
    for p in P:
        if (p.get("email") or "").strip().lower() in have or not p.get("email"):
            continue
        parts = (p.get("address") or "").split(";")[0].split(",")
        row = {h: "" for h in head}
        row.update({"Facility": p["facility"], "State": p.get("state", ""), "City": parts[-2].strip().title() if len(parts) >= 3 else "",
                    "Address": p.get("address", ""), "Beds": p.get("beds") or "", "Tier": "Campaign (first 100)",
                    "Contact": p.get("contact_name", ""), "Email": p["email"], "Email check": p.get("email_confidence", ""),
                    "Flags": "; ".join(p.get("flags") or []), "Source": (p.get("sources") or [p.get("website", "")])[0]})
        add.append([row[h] for h in head])
    if add:
        api("POST", BASE + f"{sid}/values/{TAB}!A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS", {"values": add}, tok)

    n = len(sheet_values(sid, tok, f"{TAB}!{col_letter(ei)}:{col_letter(ei)}"))
    status_rng = {"sheetId": gid, "startRowIndex": 1, "endRowIndex": n, "startColumnIndex": 0, "endColumnIndex": 1}

    def color(text, rgb):
        return {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [status_rng], "booleanRule": {
            "condition": {"type": "TEXT_CONTAINS", "values": [{"userEnteredValue": text}]},
            "format": {"backgroundColor": {"red": rgb[0], "green": rgb[1], "blue": rgb[2]}}}}}}

    def width(a, b, px):
        return {"updateDimensionProperties": {"range": {"sheetId": gid, "dimension": "COLUMNS", "startIndex": a, "endIndex": b},
                                              "properties": {"pixelSize": px}, "fields": "pixelSize"}}

    fi = head.index("Facility")
    api("POST", BASE + f"{sid}:batchUpdate", {"requests": [
        {"repeatCell": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                        "cell": {"userEnteredFormat": {"textFormat": {"bold": True}, "backgroundColor": {"red": 0.93, "green": 0.93, "blue": 0.93}}},
                        "fields": "userEnteredFormat(textFormat,backgroundColor)"}},
        {"setBasicFilter": {"filter": {"range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": n, "startColumnIndex": 0, "endColumnIndex": len(head)}}}},
        width(0, 1, 150), width(1, 3, 95), width(3, 4, 220), width(fi, fi + 1, 260), width(ei, ei + 1, 240),
        color("Not emailed", (1, 1, 1)), color("Queued", (1.0, 0.95, 0.8)), color("Emailed", (0.85, 0.91, 0.98)),
        color("All 6 sent", (0.9, 0.9, 0.9)), color("Opted out", (0.9, 0.9, 0.9)), color("Bounced", (0.96, 0.78, 0.78)),
        color("Replied", (0.72, 0.88, 0.72)),
    ]}, tok)
    url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
    json.dump({"id": sid, "url": url}, open(MAP, "w"), indent=1)
    print("adopted", url, "| added", len(add), "campaign homes | rows", n - 1)
    sync()


SMALL_TAB = "Under 10 beds"


def split_small(min_beds=10):
    """Joey 9/29: the Homes tab holds only 10+ bed homes. Moves every row under min_beds (Notes included) to the
    'Under 10 beds' tab, which is kept as history and not synced."""
    m = json.load(open(MAP)); sid = m["id"]; tok = access_token()
    rows = sheet_values(sid, tok, f"{TAB}!A1:ZZ")
    head, body = rows[0], rows[1:]
    bi = head.index("Beds")

    def beds(r):
        try:
            return float(r[bi]) if len(r) > bi and str(r[bi]).strip() else None
        except ValueError:
            return None
    small = [r for r in body if (beds(r) or 0) < min_beds]
    big = [r for r in body if (beds(r) or 0) >= min_beds]
    meta = api("GET", BASE + f"{sid}?fields=sheets(properties)", tok=tok)
    tabs = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta["sheets"]}
    gid = tabs[TAB]
    if SMALL_TAB not in tabs:
        api("POST", BASE + f"{sid}:batchUpdate", {"requests": [{"addSheet": {"properties": {"title": SMALL_TAB, "index": 1}}}]}, tok)
        api("PUT", BASE + f"{sid}/values/{urllib.parse.quote(SMALL_TAB)}!A1?valueInputOption=RAW", {"values": [head]}, tok)
    if small:
        api("POST", BASE + f"{sid}/values/{urllib.parse.quote(SMALL_TAB)}!A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS",
            {"values": small}, tok)
    api("POST", BASE + f"{sid}/values/{TAB}!A2:ZZ:clear", {}, tok)
    for i in range(0, len(big), 2000):
        api("PUT", BASE + f"{sid}/values/{TAB}!A{i + 2}?valueInputOption=RAW", {"values": big[i:i + 2000]}, tok)
    api("POST", BASE + f"{sid}:batchUpdate", {"requests": [{"setBasicFilter": {"filter": {"range": {
        "sheetId": gid, "startRowIndex": 0, "endRowIndex": len(big) + 1, "startColumnIndex": 0, "endColumnIndex": len(head)}}}}]}, tok)
    print(f"sheet: {len(big)} homes with {min_beds}+ beds stay in {TAB}; moved {len(small)} to '{SMALL_TAB}'")


def append_homes(recs):
    """Add new homes (candidates.json record shape) to the bottom of Homes, skipping emails already in the sheet."""
    m = json.load(open(MAP)); sid = m["id"]; tok = access_token()
    head = sheet_values(sid, tok, f"{TAB}!1:1")[0]
    ei = head.index("Email")
    have = {(r[0] if r else "").strip().lower() for r in sheet_values(sid, tok, f"{TAB}!{col_letter(ei)}:{col_letter(ei)}")}
    try:  # 10/23: Joey had the under-40 homes and this tab deleted (backup in data/carehome/archive_2026-10-23_under40)
        have |= {(r[0] if r else "").strip().lower() for r in sheet_values(sid, tok, f"'{SMALL_TAB}'!{col_letter(ei)}:{col_letter(ei)}")}
    except RuntimeError:
        pass
    out = []
    for r in recs:
        if r["email"].strip().lower() in have:
            continue
        row = {h: "" for h in head}
        row.update({"Facility": r.get("directory_name") or r["facility"], "State": r["state"], "City": r.get("city", ""),
                    "Address": r.get("address", ""), "Beds": r["beds"], "Open beds": "" if r.get("open_beds") is None else r["open_beds"],
                    "Census": "" if r.get("census") is None else r["census"], "Census date": r.get("census_date") or "",
                    "Tier": r["tier"], "Contact": r.get("contact", ""), "Email": r["email"], "Email check": r["email_check"],
                    "Licensee": r.get("licensee", ""), "Licensed since": r.get("license_first_date", ""),
                    "Flags": "; ".join(r.get("flags") or []), "Source": (r.get("sources") or [r.get("url", "")])[0]})
        out.append([row[h] for h in head])
        have.add(r["email"].strip().lower())
    for i in range(0, len(out), 2000):
        api("POST", BASE + f"{sid}/values/{TAB}!A1:append?valueInputOption=RAW&insertDataOption=INSERT_ROWS", {"values": out[i:i + 2000]}, tok)
    print("sheet: appended", len(out), "homes")
    return len(out)


def sync():
    """Rewrite Status / Emails sent / Last email for every row, matched by the row's Email."""
    if not MAP.exists():
        return print("sheet: not set up")
    sid = json.load(open(MAP))["id"]; tok = access_token()
    head = sheet_values(sid, tok, f"{TAB}!1:1")[0]
    si, ei = head.index("Status"), head.index("Email")
    if head[si + 1:si + 3] != ["Emails sent", "Last email"]:
        raise RuntimeError("sheet: Status, Emails sent and Last email must stay side by side")
    S = status_by_email()
    emails = sheet_values(sid, tok, f"{TAB}!{col_letter(ei)}2:{col_letter(ei)}")
    vals = [S.get((r[0] if r else "").strip().lower(), ["Not emailed yet", 0, ""]) if r and r[0] else ["", "", ""] for r in emails]
    a, c = col_letter(si), col_letter(si + 2)
    api("PUT", BASE + f"{sid}/values/{TAB}!{a}2:{c}{len(vals) + 1}?valueInputOption=RAW", {"values": vals}, tok)
    print("sheet: synced", len(vals), "rows;", sum(1 for v in vals if v[0] not in ("Not emailed yet", "")), "in the campaign")


if __name__ == "__main__":
    if sys.argv[1:2] == ["adopt"]:
        adopt(sys.argv[2])
    elif sys.argv[1:2] == ["colors"]:
        set_stage_colors()
    else:
        sync()
