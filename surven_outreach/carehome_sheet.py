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


def status_by_email():
    """email -> (status, emails sent, last email date) from carehome.db."""
    con = sqlite3.connect(D / "carehome.db"); con.row_factory = sqlite3.Row
    out = {}
    for r in con.execute("SELECT * FROM prospects"):
        st, step = r["status"], r["current_step"] or 0
        label = {"queued": "Queued", "replied": "Replied", "bounced": "Bounced", "opted_out": "Opted out",
                 "completed": "All 6 sent, no reply", "skipped": "Skipped"}.get(st, st.title())
        if st == "active":
            label = f"Emailed {step} of 6"
        out[(r["email"] or "").strip().lower()] = [label, step, r["last_sent_on"] or ""]
    return out


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
    else:
        sync()
