"""Daily campaign stats -> Notion (Joey 9/26: "track conversions... reply rate, bounce rate... automatically
updates every day"). No Claude, no paid tools: carehome.db + GoHighLevel API + Notion API.

Writes two Notion databases on the campaign report page:
  "Campaign Stats (daily)"  one row per day since the first send, recomputed every run (history stays right)
  "Campaign Events"         one row per reply, opt-out, bounce, call held, meeting booked, client won
Then refreshes every prospect row (build/notion_sync.py).

Calls and meetings come from GoHighLevel: any call of 60s+ to a campaign home's phone (data/carehome/phones.json)
after its first email counts as "call held"; appointments on those contacts count as "meeting booked".
Clients won (and anything GHL can't see) go in data/carehome/funnel_events.json by hand.

Run: python -m surven_outreach.carehome_stats   (the Windows task "Surven-CareHome-Stats" runs it nightly)
"""
import json, re, sqlite3, subprocess, sys, time, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "data" / "carehome"
DB = D / "carehome.db"
MAP = D / "stats_notion_map.json"
PHONES = D / "phones.json"
MANUAL = D / "funnel_events.json"
LOG = D / "stats.log"
PIA = r"C:\Users\evann\Desktop\PIA\PIAs.txt"
REPORT_PAGE = "3e4e1c3d79d8814497e6cbe44dfb0743"  # "Cold Email Campaign" report page in Notion
LOC = "EeadFhXhDOJ1DWE4ArhK"
START = date(2026, 9, 23)
TYPES = ["Reply", "Opt-out", "Bounce", "Call held", "Meeting booked", "Client won"]


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


from . import secrets_store  # 9/29: PIA files on Joey's PC, GitHub secret in the cloud
NTOK = secrets_store.get("NOTION_TOKEN")
GTOK = secrets_store.get("GHL_PIT")


def notion(method, path, body=None):
    req = urllib.request.Request("https://api.notion.com/v1" + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + NTOK, "Notion-Version": "2022-06-28",
                                          "Content-Type": "application/json"})
    for a in range(4):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=60).read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 * (a + 1))
                continue
            log(f"notion {method} {path} HTTP {e.code} {e.read()[:300]!r}")
            return None


def ghl(path, params=None, version="2021-04-15"):
    url = "https://services.leadconnectorhq.com" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + GTOK, "Version": version,
                                               "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except Exception as ex:
        return {"error": repr(ex)}


def rt(s):
    s = str(s or "")
    return [{"type": "text", "text": {"content": s[i:i + 1900]}} for i in range(0, min(len(s), 1900 * 5), 1900)]


def ensure_dbs(m):
    num = {"number": {"format": "number"}}
    pct = {"number": {"format": "percent"}}
    if "daily_db" not in m:
        props = {"Day": {"title": {}}, "Date": {"date": {}}}
        for k in ["New homes emailed", "Follow-ups sent", "Emails sent", "Homes emailed to date", "Not emailed yet",
                  "Replies", "Replies to date", "Positive replies to date", "Opt-outs to date", "Bounces",
                  "Bounces to date", "Calls held to date", "Meetings booked to date", "Clients to date"]:
            props[k] = num
        for k in ["Reply rate", "Positive reply rate", "Bounce rate", "Call rate (of replies)"]:
            props[k] = pct
        props["Updated"] = {"rich_text": {}}
        r = notion("POST", "/databases", {"parent": {"type": "page_id", "page_id": REPORT_PAGE},
                                          "title": rt("Campaign Stats (daily)"), "properties": props})
        m["daily_db"] = r["id"]
    if "events_db" not in m:
        props = {"Event": {"title": {}}, "Date": {"date": {}},
                 "Type": {"select": {"options": [{"name": t} for t in TYPES]}},
                 "Home": {"rich_text": {}}, "Email": {"email": {}}, "After email #": num,
                 "What they said": {"rich_text": {}}, "Details": {"rich_text": {}}, "Home row": {"url": {}}}
        r = notion("POST", "/databases", {"parent": {"type": "page_id", "page_id": REPORT_PAGE},
                                          "title": rt("Campaign Events (replies, bounces, calls, meetings)"), "properties": props})
        m["events_db"] = r["id"]
    m.setdefault("days", {})
    m.setdefault("events", {})


def ghl_events(con, prospects):
    """Calls of 60s+ and appointments on campaign homes, after each home's first email."""
    out = []
    try:
        phones = json.load(open(PHONES, encoding="utf-8"))
    except Exception:
        return out
    for p in prospects:
        if not p["first_sent_on"]:
            continue
        first = datetime.fromisoformat(p["first_sent_on"]).replace(tzinfo=timezone.utc) - timedelta(days=1)
        best, contacts = None, set()
        for ph in phones.get(p["id"], []):
            r = ghl("/conversations/search", {"locationId": LOC, "query": ph, "limit": 5})
            for c in r.get("conversations", []):
                if re.sub(r"\D", "", c.get("phone") or "")[-10:] != ph:
                    continue
                contacts.add(c.get("contactId"))
                msgs = ghl(f"/conversations/{c['id']}/messages", {"type": "TYPE_CALL", "limit": 100}).get("messages", {})
                msgs = msgs.get("messages", []) if isinstance(msgs, dict) else msgs
                for msg in msgs:
                    dur = ((msg.get("meta") or {}).get("call") or {}).get("duration") or 0
                    when = datetime.fromisoformat(msg["dateAdded"].replace("Z", "+00:00"))
                    if dur >= 60 and when >= first and (best is None or when < best[0]):
                        best = (when, dur, msg.get("direction"))
            time.sleep(0.15)
        if best:
            local = best[0].astimezone().date().isoformat()
            out.append({"key": f"{p['id']}|Call held", "id": p["id"], "type": "Call held", "date": local,
                        "details": f"First call of 60s+: {best[1] // 60} min {best[1] % 60} s, {best[2]}, via GoHighLevel"})
        for cid in contacts:
            ap = ghl(f"/contacts/{cid}/appointments", version="2021-07-28")
            for a in ap.get("events", []) or []:
                st = (a.get("startTime") or "")[:10]
                if st:
                    out.append({"key": f"{p['id']}|Meeting booked|{a.get('id')}", "id": p["id"], "type": "Meeting booked",
                                "date": st, "details": f"{a.get('title') or 'Appointment'} ({a.get('appointmentStatus') or a.get('status') or ''})"})
    return out


def main():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    prospects = [dict(r) for r in con.execute("select * from prospects")]
    P = {p["id"]: p for p in prospects}
    sends = [dict(r) for r in con.execute("select * from sends")]
    total_homes = len(prospects)
    m = json.load(open(MAP)) if MAP.exists() else {}
    ensure_dbs(m)
    json.dump(m, open(MAP, "w"), indent=1)
    rowmap = json.load(open(ROOT / "data" / "carehome" / "build" / "notion_map.json"))

    # ---- events
    ev = []
    for p in prospects:
        typ = {"replied": "Reply", "opted_out": "Opt-out", "bounced": "Bounce"}.get(p["status"])
        if typ:
            ev.append({"key": f"{p['id']}|{typ}", "id": p["id"], "type": typ, "date": p["stopped_on"],
                       "after": p["current_step"], "details": p["stopped_reason"]})
    ev += ghl_events(con, prospects)
    try:
        ev += [dict(e, key=e.get("key") or f"{e['id']}|{e['type']}|{e['date']}") for e in json.load(open(MANUAL, encoding="utf-8"))]
    except FileNotFoundError:
        pass
    except Exception as ex:
        log(f"manual events file error: {ex!r}")
    try:
        said = {json.loads(l)["id"]: json.loads(l)["msg"] for l in open(D / "alerts_sent.jsonl", encoding="utf-8") if l.strip()}
    except Exception:
        said = {}
    for e in ev:
        p = P.get(e["id"], {})
        props = {"Event": {"title": rt(f"{e['type']}: {p.get('facility', e['id'])}")},
                 "Date": {"date": {"start": e["date"]}}, "Type": {"select": {"name": e["type"]}},
                 "Home": {"rich_text": rt(p.get("facility", e["id"]))},
                 "Details": {"rich_text": rt(e.get("details", ""))}}
        if p.get("email"):
            props["Email"] = {"email": p["email"]}
        if e.get("after"):
            props["After email #"] = {"number": e["after"]}
        if e["type"] in ("Reply", "Opt-out") and said.get(e["id"]):
            props["What they said"] = {"rich_text": rt(said[e["id"]])}
        if rowmap.get(e["id"]):
            props["Home row"] = {"url": "https://www.notion.so/" + rowmap[e["id"]].replace("-", "")}
        if e["key"] in m["events"]:
            notion("PATCH", "/pages/" + m["events"][e["key"]], {"properties": props})
        else:
            r = notion("POST", "/pages", {"parent": {"database_id": m["events_db"]}, "properties": props})
            if r:
                m["events"][e["key"]] = r["id"]
    json.dump(m, open(MAP, "w"), indent=1)

    # ---- daily rows, recomputed from day one every run
    first_send = {}
    for s in sends:
        if s["step"] == 1:
            first_send[s["prospect_id"]] = s["sent_at"][:10]
    today = date.today()
    d = START
    while d <= today:
        ds = d.isoformat()
        day_sends = [s for s in sends if s["sent_at"][:10] == ds]
        homes = sum(1 for v in first_send.values() if v <= ds)
        cnt = lambda t, same=False: sum(1 for e in ev if e["type"] == t and (e["date"] == ds if same else e["date"] <= ds))
        replies, opts, bounces = cnt("Reply"), cnt("Opt-out"), cnt("Bounce")
        calls = len({e["id"] for e in ev if e["type"] == "Call held" and e["date"] <= ds})
        props = {"Day": {"title": rt(d.strftime("%a %b %d, %Y"))}, "Date": {"date": {"start": ds}},
                 "New homes emailed": {"number": sum(1 for s in day_sends if s["step"] == 1)},
                 "Follow-ups sent": {"number": sum(1 for s in day_sends if s["step"] > 1)},
                 "Emails sent": {"number": len(day_sends)},
                 "Homes emailed to date": {"number": homes}, "Not emailed yet": {"number": total_homes - homes},
                 "Replies": {"number": cnt("Reply", True) + cnt("Opt-out", True)},
                 "Replies to date": {"number": replies + opts}, "Positive replies to date": {"number": replies},
                 "Opt-outs to date": {"number": opts}, "Bounces": {"number": cnt("Bounce", True)},
                 "Bounces to date": {"number": bounces}, "Calls held to date": {"number": calls},
                 "Meetings booked to date": {"number": len({e["id"] for e in ev if e["type"] == "Meeting booked" and e["date"] <= ds})},
                 "Clients to date": {"number": len({e["id"] for e in ev if e["type"] == "Client won" and e["date"] <= ds})},
                 "Reply rate": {"number": round((replies + opts) / homes, 4) if homes else 0},
                 "Positive reply rate": {"number": round(replies / homes, 4) if homes else 0},
                 "Bounce rate": {"number": round(bounces / homes, 4) if homes else 0},
                 "Call rate (of replies)": {"number": round(calls / replies, 4) if replies else 0},
                 "Updated": {"rich_text": rt(datetime.now().strftime("%Y-%m-%d %I:%M %p ET"))}}
        if ds in m["days"]:
            notion("PATCH", "/pages/" + m["days"][ds], {"properties": props})
        else:
            r = notion("POST", "/pages", {"parent": {"database_id": m["daily_db"]}, "properties": props})
            if r:
                m["days"][ds] = r["id"]
        d += timedelta(days=1)
    json.dump(m, open(MAP, "w"), indent=1)
    log(f"stats: {len(m['days'])} days, {len(ev)} events, homes emailed {len(first_send)}")

    # ---- refresh every prospect row (campaign state per home)
    r = subprocess.run([sys.executable, "notion_sync.py"], cwd=str(D / "build"), capture_output=True, text=True, timeout=1800)
    log("prospect rows: " + (r.stdout.strip().splitlines() or ["?"])[-1])
    try:  # 9/29: the live Google Sheet of every home (carehome_sheet.py) gets the same statuses
        from . import carehome_sheet
        carehome_sheet.sync()
    except BaseException as ex:  # a sheet failure must never break the stats run
        log(f"sheet sync failed: {ex!r}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
