"""Care-home cold email campaign (Sept 2026).

Separate from the old med-spa queue: its own sqlite file and its own per-prospect
emails. Every email body is written in advance for that one prospect and stored
in data/carehome/queue.json, so this runner never generates copy. It only decides
what is due, sends it inside the prospect's local morning window, threads the
follow-ups onto email 1, and stops a thread the moment the prospect replies,
bounces, or opts out.

Commands (run from C:\\dev\\surven-outreach with the venv python):
    python -m surven_outreach.carehome load      # import/refresh queue.json into the db
    python -m surven_outreach.carehome status    # print what is sent / due / stopped
    python -m surven_outreach.carehome run       # scan replies, then send what is due now
    python -m surven_outreach.carehome run --dry # show what would send, send nothing
"""

import argparse
import email
import imaplib
import json
import time
import sqlite3
import sys
from datetime import date, datetime, timedelta
from email.utils import format_datetime, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_config
from .notify import notify_reply
from .reply_text import is_opt_out, new_text, sender_name
from .sender import SmtpSender, build_message
from .threading_headers import new_message_id, reply_headers

ROOT = Path(__file__).resolve().parent.parent
import os
QUEUE = Path(os.getenv("CAREHOME_QUEUE", ROOT / "data" / "carehome" / "queue.json"))
DB = Path(os.getenv("CAREHOME_DB", ROOT / "data" / "carehome" / "carehome.db"))
LOG = Path(os.getenv("CAREHOME_LOG", ROOT / "data" / "carehome" / "run.log"))
TEST_IGNORE_TIMING = os.getenv("CAREHOME_IGNORE_TIMING") == "1"  # test harness only

# step -> days after email 1. v2 (2026-09-23): 6 emails, Gong's 6-7 sweet spot, breakup on day 21.
OFFSETS = {1: 0, 2: 3, 3: 7, 4: 10, 5: 14, 6: 21}
LAST_STEP = 6
# Owners answer early morning (Nevrus principle 14). Send only between 7:00 and 9:59 local.
WINDOW = (7, 10)
MAX_NEW_PER_DAY = 20   # Joey 9/22: volume up; 20 new/day keeps the primary domain safe
MAX_TOTAL_PER_DAY = 45
# Joey 9/25: owners skim by sender name and open company names, skip people they don't know.
FROM_NAME = "Surven Occupancy"
# 9/29: runs in GitHub Actions (CAREHOME_CLOUD=1) every 10 minutes, so each sender sends 1 per run there (its own
# sends land ~10 min apart, GitHub's start-time jitter adds more randomness). On the PC it was 8 per 30-min run.
CLOUD = os.getenv("CAREHOME_CLOUD") == "1"
MAX_PER_RUN = 1 if CLOUD else 8  # per sender per run. 9/29: was 5 total, which capped a 3-hour window at ~30 sends
# 9/29: 10x via 8 cheap .xyz sender domains on the same ImprovMX account (sender_domains.py sets them up).
# Every home keeps ONE sender for its whole thread; new homes go to whichever ready sender has the most room.
# A new domain only sends once ImprovMX sees its DNS as valid, then ramps (brand-new domains that blast get
# filtered). surven.ai keeps its old 20 new / 45 total. ImprovMX Premium allows 400/day across all of them.
PRIMARY = "joey@surven.ai"
SENDERS_FILE = ROOT / "data" / "carehome" / "senders.json"
RAMP = [(3, 8), (7, 15), (14, 25), (10 ** 6, 40)]  # (days since ready, max sends/day incl. follow-ups)
# 10/15 (Joey: "I don't want to burn these domains or burn the leads"): survenoccupancy.com sat in Gmail spam for
# about its first 10 days, so the 7 .com names bought 10/15 send no cold email before this date, and after it only
# once their warm-up mail reaches the inbox 3 days running (warmup.py). The ramp starts on the release day.
HOLD_UNTIL = {f"joey@{d}": "2026-10-25" for d in ("getsurven.com", "trysurven.com", "survenhq.com", "joinsurven.com",
                                                    "meetsurven.com", "gosurven.com", "usesurven.com")}
RUN_BUDGET = 27 * 60   # seconds; a run's last send starts before the next 30-min run
# 9/29: GitHub can start scheduled runs late or drop them when busy. A cloud run that comes 20+ minutes after the
# last one (or after 7 AM ET) catches up: up to 6 sends per sender, 2-5 min apart, so late runs don't cut the day.
LAST_RUN_FILE = ROOT / "data" / "carehome" / "last_run.json"
TITLE_NAMES = {"lvn", "rn", "lpn", "cna", "bsn", "msn", "np", "md", "dr", "mr", "mrs", "ms", "owner", "admin",
               "administrator", "manager", "director"}
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com", "icloud.com", "msn.com", "live.com", "comcast.net"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
  id TEXT PRIMARY KEY, email TEXT NOT NULL, facility TEXT, first_name TEXT, tz TEXT,
  rank INTEGER DEFAULT 0, status TEXT NOT NULL DEFAULT 'queued',
  current_step INTEGER NOT NULL DEFAULT 0, thread_message_id TEXT, thread_subject TEXT,
  first_sent_on TEXT, last_sent_on TEXT, stopped_reason TEXT, stopped_on TEXT
);
CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT, prospect_id TEXT NOT NULL, step INTEGER NOT NULL,
  email TEXT, subject TEXT, body TEXT, message_id TEXT, sent_at TEXT NOT NULL,
  UNIQUE(prospect_id, step)
);
"""


def log(msg: str) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    for t in ("prospects", "sends"):  # 9/29 multi-sender: which address a thread goes out from
        if "sender" not in [c[1] for c in con.execute(f"PRAGMA table_info({t})")]:
            con.execute(f"ALTER TABLE {t} ADD COLUMN sender TEXT")
    con.execute("UPDATE prospects SET sender=? WHERE sender IS NULL AND current_step>0", (PRIMARY,))
    con.execute("UPDATE sends SET sender=? WHERE sender IS NULL", (PRIMARY,))
    con.commit()
    return con


def load_senders(cfg) -> dict:
    """address -> {password, cap_total, cap_new}. Only senders that are ready today."""
    from . import secrets_store
    meta = json.load(open(SENDERS_FILE)) if SENDERS_FILE.exists() else {}
    pw = secrets_store.get("IMPROVMX_SENDERS")
    out = {PRIMARY: {"password": cfg.smtp_password, "cap_total": MAX_TOTAL_PER_DAY, "cap_new": MAX_NEW_PER_DAY}}
    for addr, m in meta.items():
        # 10/15 (Joey: "stop the .xyz email so we don't burn any contacts"): .xyz lands in Gmail spam, so .xyz
        # addresses send nothing, not even follow-ups. Their open threads wait (a thread never switches sender)
        # until they are restarted from a .com address.
        if addr == PRIMARY or not m.get("ready") or addr not in pw or addr.endswith(".xyz"):
            continue
        start = date.fromisoformat(m["ready_on"])
        if addr in HOLD_UNTIL:  # held to warm up: cold email starts only once warmup.py releases it
            from . import warmup
            released = warmup.release_day(addr)
            if released is None:
                continue
            start = max(start, date.fromisoformat(released))
        age = (date.today() - start).days
        cap = next(c for d, c in RAMP if age < d)
        out[addr] = {"password": pw[addr], "cap_total": cap, "cap_new": cap}
    return out


def load_queue() -> dict:
    return {p["id"]: p for p in json.load(open(QUEUE, encoding="utf-8"))}


def cmd_load(con) -> None:
    q = load_queue()
    added = 0
    for pid, p in q.items():
        cur = con.execute(
            "INSERT OR IGNORE INTO prospects (id,email,facility,first_name,tz,rank) VALUES (?,?,?,?,?,?)",
            (pid, p["email"], p["facility"], p.get("first_name", ""), p["tz"], p.get("rank", 0)),
        )
        added += cur.rowcount
        # keep rank/email fresh for rows not yet contacted
        con.execute("UPDATE prospects SET rank=?, email=?, tz=? WHERE id=? AND current_step=0",
                    (p.get("rank", 0), p["email"], p["tz"], pid))
    con.commit()
    log(f"load: {len(q)} in queue.json, {added} new")


def stop(con, pid: str, status: str, reason: str) -> None:
    con.execute("UPDATE prospects SET status=?, stopped_reason=?, stopped_on=? WHERE id=?",
                (status, reason, date.today().isoformat(), pid))
    con.commit()


def _imap(cfg):
    c = imaplib.IMAP4_SSL(cfg.imap_host, timeout=45)
    c.login(cfg.imap_user, cfg.imap_password)
    c.select("INBOX", readonly=True)
    return c


def _msgs_after(c, criteria: list, after: datetime) -> list:
    """Messages matching criteria that arrived AFTER our first send (older mail never counts)."""
    since = (after - timedelta(days=1)).strftime("%d-%b-%Y")
    _, data = c.search(None, *criteria, "SINCE", since)
    out = []
    for i in (data[0].split() if data and data[0] else []):
        _, dd = c.fetch(i, "(INTERNALDATE)")
        when = imaplib.Internaldate2tuple(dd[0] if isinstance(dd[0], bytes) else dd[0][0])
        if not when or datetime.fromtimestamp(time.mktime(when)).astimezone() <= after:
            continue
        _, md = c.fetch(i, "(BODY.PEEK[])")
        out.append(email.message_from_bytes(md[0][1]))
    return out


def _is_auto(m) -> bool:
    auto = (m.get("Auto-Submitted", "") or "").lower()
    if auto and auto != "no":
        return True
    if (m.get("Precedence", "") or "").lower() in ("auto_reply", "bulk", "junk") or m.get("X-Autoreply"):
        return True
    subj = (m.get("Subject", "") or "").lower()
    return any(k in subj for k in ("out of office", "automatic reply", "auto-reply", "autoreply", "away from", "vacation"))


SENT_BOX = '"[Gmail]/Sent Mail"'


def save_to_sent(cfg, msg, when: datetime) -> None:
    """Put a copy in Gmail's Sent tab. Mail goes out through ImprovMX, so Gmail never sees it
    otherwise. Same Message-ID, so a prospect's reply threads under the original. Best effort."""
    try:
        c = imaplib.IMAP4_SSL(cfg.imap_host, timeout=45)
        c.login(cfg.imap_user, cfg.imap_password)
        try:
            c.select(SENT_BOX, readonly=True)
            _, hit = c.search(None, "HEADER", "Message-ID", f'"{msg["Message-ID"]}"')
            if not (hit and hit[0]):
                c.append(SENT_BOX, r"(\Seen)", imaplib.Time2Internaldate(when), msg.as_bytes())
        finally:
            c.logout()
    except Exception as ex:
        log(f"save to Sent failed {msg['To']}: {ex!r}")


def cmd_backfill_sent(con) -> None:
    """Copy every email already sent into Gmail's Sent tab (skips ones already there)."""
    cfg = load_config()
    rows = con.execute("SELECT * FROM sends ORDER BY sent_at").fetchall()
    for s in rows:
        extra = {"Date": format_datetime(datetime.fromisoformat(s["sent_at"]))}
        if s["step"] > 1:
            p = con.execute("SELECT thread_message_id, thread_subject FROM prospects WHERE id=?",
                            (s["prospect_id"],)).fetchone()
            extra.update(reply_headers(p["thread_message_id"], p["thread_subject"]))
        msg = build_message(FROM_NAME, s["sender"] or PRIMARY, s["email"], s["subject"], s["body"], s["message_id"], extra)
        save_to_sent(cfg, msg, datetime.fromisoformat(s["sent_at"]))
    log(f"backfill-sent: {len(rows)} checked")


# Reply alerts for Joey's phone. The Windows task only queues them here; a Claude desktop
# scheduled task ("Surven reply alerts") runs `alerts`, sends each line as a Claude push
# notification, then runs `alerts --ack`. Unacked alerts are shown again next run.
ALERTS_PENDING = LOG.parent / "alerts_pending.jsonl"
ALERTS_INFLIGHT = LOG.parent / "alerts_inflight.jsonl"
ALERTS_SENT = LOG.parent / "alerts_sent.jsonl"


def queue_alert(r, status: str, said: str, from_name: str = "", from_addr: str = "", again: bool = False) -> None:
    """The whole reply goes to Joey (reply_text.py says why), with who really wrote it. When someone other than the
    person we emailed answers (a coworker, a new manager), the alert names both. 10/6: a reply also carries the facts
    email_leads.py needs to make the replier an E code lead, and the text is built when it goes out."""
    emailed = r["first_name"] or ""
    other = bool(from_addr) and from_addr.lower() != (r["email"] or "").lower()
    who = from_name or emailed or "Someone"
    if other and emailed and emailed.lower() not in who.lower():
        who += f" (we emailed {emailed})"
    said = said.strip()
    if len(said) > 900:
        said = said[:900].rsplit(" ", 1)[0] + "..."
    msg = (f"Opt-out: {who} at {r['facility']}. Thread stopped. They wrote:\n\"{said}\"" if status == "opted_out"
           else f"Cold email reply: {who} at {r['facility']} wrote:\n\"{said}\"\nCall them now.")
    rec = {"at": datetime.now().isoformat(timespec="seconds"), "id": r["id"], "email": r["email"], "msg": msg[:1400],
           "status": status, "said": said, "from_name": from_name, "from_email": from_addr or r["email"],
           "first_name": r["first_name"] or "", "facility": r["facility"], "again": again}
    with open(ALERTS_PENDING, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def cmd_alerts(ack: bool, quiet: bool = False) -> None:
    if ack:
        if ALERTS_INFLIGHT.exists():
            with open(ALERTS_SENT, "a", encoding="utf-8") as f:
                f.write(ALERTS_INFLIGHT.read_text(encoding="utf-8"))
            ALERTS_INFLIGHT.write_text("", encoding="utf-8")  # emptied, never removed (see below)
        return
    if ALERTS_PENDING.exists():
        tmp = ALERTS_PENDING.with_suffix(".moving")
        os.replace(ALERTS_PENDING, tmp)  # atomic, so a scan writing at the same moment starts a fresh file
        with open(ALERTS_INFLIGHT, "a", encoding="utf-8") as f:
            f.write(tmp.read_text(encoding="utf-8"))
        tmp.unlink()
        # 10/6: the cloud's state branch never deletes a file (tools/cloud_state.py keeps what it does not get), so a
        # removed queue file would come back next run with old alerts in it. Empty it instead of removing it.
        ALERTS_PENDING.touch()
    if ALERTS_INFLIGHT.exists() and not quiet:
        for line in ALERTS_INFLIGHT.read_text(encoding="utf-8").splitlines():
            if line.strip():
                print(json.loads(line)["msg"])


# 2026-09-23: Claude scheduled runs can't push to Joey's phone (Remote Control is off for unattended
# runs), so alerts go out as a GHL text from the Surven HQ number to Joey's own contact instead.
GHL_JOEY_CONTACT = "ITgRAUyt3KqdPbQB5cUo"  # Surven HQ, Joey's own contact (Nevrus context.md SMS target)
PIA_FILE = r"C:\Users\evann\Desktop\PIA\PIAs.txt"
QUIET = (22, 7)  # no texts from 10 PM to 7 AM Eastern; they wait for the morning


def _ghl_sms(text: str) -> bool:
    import subprocess
    from . import secrets_store
    tok = secrets_store.get("GHL_PIT")
    body = json.dumps({"type": "SMS", "contactId": GHL_JOEY_CONTACT, "message": text})
    r = subprocess.run(["curl", "-s", "-X", "POST", "https://services.leadconnectorhq.com/conversations/messages",
                        "-H", "Authorization: Bearer " + tok, "-H", "Version: 2021-04-15",
                        "-H", "Content-Type: application/json", "--data-binary", "@-"],
                       input=body, capture_output=True, text=True, timeout=30)
    try:
        return bool(json.loads(r.stdout).get("messageId"))
    except Exception:
        log(f"sms alert failed: {r.stdout[:200]!r}")
        return False


def send_alert_texts() -> None:
    """Work every queued reply alert, one at a time, each kept until it is fully done (10/6):
    1. a reply becomes an E code lead in GoHighLevel (email_leads.prepare), at any hour, retried every run until it works;
    2. Joey gets the text (what they wrote, phone, website, the E code), not between 10 PM and 7 AM Eastern;
    3. if GoHighLevel was down when the text went, a short "E3 is ready" text follows once the lead exists.
    A text that went out is never sent again; nothing is dropped until all of it is done."""
    if not (ALERTS_PENDING.exists() or ALERTS_INFLIGHT.exists()):
        return
    cmd_alerts(ack=False, quiet=True)  # moves pending into inflight
    items = [json.loads(l) for l in ALERTS_INFLIGHT.read_text(encoding="utf-8").splitlines() if l.strip()]
    hour = datetime.now(ZoneInfo("America/New_York")).hour
    night = hour >= QUIET[0] or hour < QUIET[1]
    from . import email_leads
    done, keep = [], []
    for it in items:
        lead_kind = it.get("status") == "replied" and "said" in it
        if lead_kind and not it.get("lead"):
            try:
                it["lead"] = email_leads.prepare(it)
                it["noted"] = True
                log(f"email lead {it['id']}: {it['lead']['code']} phone={'yes' if it['lead']['phone'] else 'no'} site={'yes' if it['lead']['website'] else 'no'}")
            except Exception as ex:
                it["tries"] = it.get("tries", 0) + 1
                log(f"email lead {it['id']} not made yet (try {it['tries']}): {ex!r}"[:300])
        if not night and not it.get("texted"):
            msg = email_leads.alert_text(it, it.get("lead")) if lead_kind else it["msg"]
            if _ghl_sms(msg):
                it["texted"], it["texted_code"] = True, bool(it.get("lead"))
        if not night and it.get("texted") and it.get("lead") and not it.get("texted_code"):
            ld = it["lead"]
            who = email_leads.short_name(it.get("from_name", ""), it.get("first_name") or "Owner")
            if _ghl_sms(f"{ld['code']}. {who}, {it.get('facility', '')} is ready. Reply \"{ld['code']}\" to call."):
                it["texted_code"] = True
        finished = it.get("texted") and (not lead_kind or (it.get("lead") and it.get("texted_code")))
        (done if finished else keep).append(it)
    if done:
        with open(ALERTS_SENT, "a", encoding="utf-8") as f:
            f.writelines(json.dumps(it) + "\n" for it in done)
        log(f"texted Joey {len(done)} reply alert(s)")
    # Written even when empty (never removed): see cmd_alerts on why a removed file would come back.
    ALERTS_INFLIGHT.write_text("".join(json.dumps(it) + "\n" for it in keep), encoding="utf-8")


SCAN_STATE = LOG.parent / "scan_state.json"
BOUNCE_FROM = ("mailer-daemon", "postmaster", "bounces.improvmx.net")  # 9/25: ImprovMX bounces come from the last one
EMAIL_RE = __import__("re").compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")


def scan_replies(con, cfg) -> None:
    """9/29 incremental scan: reads only inbox mail that arrived since the last run (by IMAP UID) and matches it to a
    thread by In-Reply-To/References, then exact address, then the business domain when only one live thread uses it.
    Same outcomes as before: bounce -> 'bounced'; human reply -> 'replied' or 'opted_out', alert + text to Joey."""
    from email.utils import parseaddr, parsedate_to_datetime
    rows = {r["id"]: r for r in con.execute("SELECT * FROM prospects WHERE status IN ('active','completed','replied')")}
    first = {pid: datetime.fromisoformat(t) for pid, t in
             con.execute("SELECT prospect_id, MIN(sent_at) FROM sends GROUP BY prospect_id") if pid in rows}
    by_mid = {(m or "").strip("<> ").lower(): pid for pid, m in con.execute("SELECT prospect_id, message_id FROM sends") if pid in first}
    by_email, by_dom = {}, {}
    for pid in first:
        e = rows[pid]["email"].lower()
        by_email[e] = pid
        d = e.split("@")[-1]
        if d not in FREEMAIL:
            by_dom.setdefault(d, []).append(pid)
    state = json.load(open(SCAN_STATE)) if SCAN_STATE.exists() else {}
    if "uidvalidity" in state:  # old one-folder format = the inbox
        state = {"INBOX": state}
    c = imaplib.IMAP4_SSL(cfg.imap_host, timeout=60)
    c.login(cfg.imap_user, cfg.imap_password)
    try:
        # 10/3: Gmail put 13 ImprovMX bounce notices in Spam, so the scan missed them. Read Spam too.
        for box in ("INBOX", "[Gmail]/Spam"):
            _scan_box(c, box, state, con, cfg, rows, first, by_mid, by_email, by_dom)
        json.dump(state, open(SCAN_STATE, "w"))
    finally:
        try:
            c.logout()
        except Exception:
            pass


def _first_reply_mail(r, when) -> bool:
    """For threads stopped before 10/7, when Message-IDs were not kept yet: mail from the day it was marked replied
    or earlier is the first reply, not a new one."""
    return bool(r["stopped_on"]) and r["stopped_on"][:10] < "2026-10-07" and when.date().isoformat() <= r["stopped_on"][:10]


def _scan_box(c, box, state, con, cfg, rows, first, by_mid, by_email, by_dom) -> None:
    import re
    st = state.setdefault(box, {})
    c.select(f'"{box}"', readonly=True)
    uv = (c.untagged_responses.get("UIDVALIDITY") or [b"0"])[0].decode()
    if st.get("uidvalidity") == uv and st.get("last_uid"):
        _, d = c.uid("search", None, f"UID {int(st['last_uid']) + 1}:*")
    else:  # first run (or Gmail reset UIDs): everything since the campaign started
        _, d = c.uid("search", None, "SINCE", "22-Sep-2026")
    uids = [int(u) for u in (d[0].split() if d and d[0] else [])]
    uids = [u for u in uids if u > int(st.get("last_uid") or 0) or st.get("uidvalidity") != uv]
    stopped = 0
    for i in range(0, len(uids), 200):
        chunk = uids[i:i + 200]
        _, data = c.uid("fetch", ",".join(map(str, chunk)),
                        "(UID INTERNALDATE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID IN-REPLY-TO REFERENCES AUTO-SUBMITTED PRECEDENCE X-AUTOREPLY)])")
        for part in data:
            if not isinstance(part, tuple):
                continue
            meta = part[0].decode(errors="ignore")
            uid = int(re.search(r"UID (\d+)", meta).group(1))
            when = imaplib.Internaldate2tuple(part[0])
            when = datetime.fromtimestamp(time.mktime(when)).astimezone() if when else None
            h = email.message_from_bytes(part[1])
            sender = parseaddr(h.get("From", ""))[1].lower()
            if not sender or when is None:
                continue
            try:
                if any(b in sender for b in BOUNCE_FROM):
                    _, body = c.uid("fetch", str(uid), "(BODY.PEEK[])")
                    text = email.message_from_bytes(body[0][1]).as_string().lower()
                    for e in set(EMAIL_RE.findall(text)):
                        pid = by_email.get(e)
                        if pid and pid in first and when > first[pid] and rows[pid]["status"] in ("active", "completed"):
                            stop(con, pid, "bounced", "bounce notice in inbox" if box == "INBOX" else "bounce notice in spam folder")
                            log(f"stop {pid}: bounced")
                            stopped += 1
                    continue
                refs = re.findall(r"<([^>]+)>", (h.get("In-Reply-To", "") or "") + " " + (h.get("References", "") or ""))
                pid = next((by_mid[x.lower()] for x in refs if x.lower() in by_mid), None) or by_email.get(sender)
                if not pid:
                    cands = by_dom.get(sender.split("@")[-1], [])
                    pid = cands[0] if len(cands) == 1 else None  # a coworker at the same business (owner forwards it)
                if not pid or pid not in first or when <= first[pid] or _is_auto(h):
                    continue
                _, body = c.uid("fetch", str(uid), "(BODY.PEEK[])")
                full = email.message_from_bytes(body[0][1])
                said = new_text(full)
                status = "opted_out" if is_opt_out(said) else "replied"
                r = rows[pid]
                # Each email alerts once, by its Message-ID, even if the scan's place is ever lost and old mail is read
                # again (that would otherwise send every first reply back to Joey as "again").
                mid = (h.get("Message-ID", "") or f"uid{uid}").strip()
                seen = state.setdefault("alerted", [])
                if mid in seen or (r["status"] == "replied" and _first_reply_mail(r, when)):
                    continue
                seen.append(mid)
                del seen[:-500]
                if r["status"] == "replied" and status == "replied":  # 10/6: they wrote again; Joey hears it, same E code
                    queue_alert(r, status, said, sender_name(full), sender, again=True)
                    log(f"{pid}: replied again")
                    continue
                stop(con, pid, status, f"reply in inbox: {said[:300]}")
                log(f"stop {pid}: {status}")
                stopped += 1
                notify_reply(cfg, r["facility"], r["first_name"], "(opt-out)" if status == "opted_out" else "")
                queue_alert(r, status, said, sender_name(full), sender)
                rows[pid] = con.execute("SELECT * FROM prospects WHERE id=?", (pid,)).fetchone()
            except Exception as ex:  # one bad message must not block the rest
                log(f"scan error uid {uid}: {ex!r}")
    if uids:
        st.update({"uidvalidity": uv, "last_uid": max(uids)})
    log(f"scan {box}: {len(uids)} new message(s), {stopped} thread(s) stopped")


def scan_replies_full(con, cfg) -> None:
    """The old per-home scan (kept for a manual re-check: `carehome rescan`)."""
    rows = con.execute("SELECT * FROM prospects WHERE status IN ('active','completed')").fetchall()
    if not rows:
        return
    c = _imap(cfg)
    try:
        for r in rows:
            e = r["email"]
            first = con.execute("SELECT MIN(sent_at) FROM sends WHERE prospect_id=?", (r["id"],)).fetchone()[0]
            if not first:
                continue
            after = datetime.fromisoformat(first)
            try:
                # 9/25: ImprovMX sends its own bounce notices from no-reply@bounces.improvmx.net
                bounces = [m for s in ("mailer-daemon", "postmaster", "bounces.improvmx.net")
                           for m in _msgs_after(c, ["FROM", s, "BODY", f'"{e}"'], after)]
                if bounces:
                    stop(con, r["id"], "bounced", "bounce notice in inbox")
                    log(f"stop {r['id']}: bounced")
                    continue
                # a reply from a coworker at the same business domain also counts (owner forwards it)
                dom = e.split("@")[-1].lower()
                who = e if dom in FREEMAIL else "@" + dom
                human = [m for m in _msgs_after(c, ["FROM", f'"{who}"'], after) if not _is_auto(m)]
                if not human:
                    continue
                said = new_text(human[-1])
                status = "opted_out" if is_opt_out(said) else "replied"
                stop(con, r["id"], status, f"reply in inbox: {said[:300]}")
                log(f"stop {r['id']}: {status}")
                # alert Joey on every human reply, opt-outs included, so nothing sits unread
                notify_reply(cfg, r["facility"], r["first_name"], "(opt-out)" if status == "opted_out" else "")
                queue_alert(r, status, said, sender_name(human[-1]), parseaddr(human[-1].get("From", ""))[1])
            except Exception as ex:  # one bad lookup must not block the rest
                log(f"scan error {r['id']}: {ex!r}")
    finally:
        try:
            c.logout()
        except Exception:
            pass


def cloud_per_run(now: datetime) -> int:
    """Sends allowed per sender this cloud run: 1 when runs come every 10 min, more when GitHub ran late."""
    try:
        last = datetime.fromisoformat(json.load(open(LAST_RUN_FILE))["at"])
    except Exception:
        last = None
    day_start = now.astimezone(ZoneInfo("America/New_York")).replace(hour=WINDOW[0], minute=0, second=0, microsecond=0)
    since = max(last, day_start) if last else day_start
    gap = (now - since).total_seconds() / 60
    return max(1, min(6, int((gap + 5) // 10)))


def due_items(con, queue: dict, now_utc: datetime, senders: dict, per_run: int = MAX_PER_RUN) -> list:
    """-> [(row, queue entry, step, sender address)]. Follow-ups go out from the home's own sender first;
    new homes then fill whichever ready sender has the most room left today."""
    used = {a: {"total": 0, "new": 0, "run": 0} for a in senders}
    for s in con.execute("SELECT step, sender FROM sends WHERE substr(sent_at,1,10)=?",
                         (now_utc.astimezone().date().isoformat(),)):
        u = used.get(s["sender"] or PRIMARY)
        if u:
            u["total"] += 1
            u["new"] += s["step"] == 1

    def room(a, new):
        u, c = used[a], senders[a]
        return u["total"] < c["cap_total"] and u["run"] < per_run and (not new or u["new"] < c["cap_new"])

    follow, fresh = [], []
    for r in con.execute("SELECT * FROM prospects WHERE status IN ('queued','active') ORDER BY rank DESC, id"):
        p = queue.get(r["id"])
        if not p:
            continue
        local = now_utc.astimezone(ZoneInfo(r["tz"]))
        if not TEST_IGNORE_TIMING and not (WINDOW[0] <= local.hour < WINDOW[1]):
            continue
        step = r["current_step"] + 1
        if step > LAST_STEP:
            continue
        if step == 1:
            if r["status"] != "queued":
                continue
            fresh.append((r, p, step))
        else:
            first = date.fromisoformat(r["first_sent_on"])
            if not TEST_IGNORE_TIMING and local.date() < first + timedelta(days=OFFSETS[step]):
                continue
            if not TEST_IGNORE_TIMING and r["last_sent_on"] == local.date().isoformat():
                continue
            follow.append((r, p, step))
    items = []
    for r, p, step in follow:
        a = r["sender"] or PRIMARY
        if a in senders and room(a, False):  # a thread never switches sender
            items.append((r, p, step, a))
            used[a]["total"] += 1; used[a]["run"] += 1
    for r, p, step in fresh:
        if r["sender"]:  # 9/30: pinned after ImprovMX's spam filter refused it from a .xyz address
            if r["sender"] in senders and room(r["sender"], True):
                a = r["sender"]
                items.append((r, p, step, a))
                used[a]["total"] += 1; used[a]["new"] += 1; used[a]["run"] += 1
            continue
        open_ = [a for a in senders if room(a, True)]
        if not open_:
            break
        a = max(open_, key=lambda x: senders[x]["cap_total"] - used[x]["total"])
        items.append((r, p, step, a))
        used[a]["total"] += 1; used[a]["new"] += 1; used[a]["run"] += 1
    return items


def send_one(con, cfg, sender, r, p, step, dry: bool, from_addr: str = PRIMARY) -> None:
    em = dict(p["emails"][str(step)])
    # 10/4: some state lists put a title in the name field ("LVN", "Ms"), so Greens Haven got "Hey Lvn,". Drop it.
    if (r["first_name"] or "").strip().lower() in TITLE_NAMES:
        import re
        em["body"] = re.sub(rf"^(Hey|Hi|Hello) {re.escape(r['first_name'].strip())},", "Hi,", em["body"], flags=re.I)
    domain = from_addr.split("@")[-1]
    mid = new_message_id(domain, seed=f"carehome-{r['id']}-{step}")
    if step == 1:
        subject, extra = em["subject"], {}
    else:
        extra = reply_headers(r["thread_message_id"], r["thread_subject"])
        subject = extra["Subject"]
    if dry:
        log(f"DRY would send {r['id']} step {step} from {from_addr} to {r['email']} subj={subject!r}")
        return "dry"
    msg = build_message(FROM_NAME, from_addr, r["email"], subject, em["body"], mid,
                        {**extra, "Date": format_datetime(datetime.now().astimezone())})
    try:
        sender.send(msg)
    except Exception as ex:
        if ex.__class__.__name__ in ("SMTPRecipientsRefused", "SMTPSenderRefused"):
            stop(con, r["id"], "bounced", f"refused at send: {ex!r}"[:200])
            log(f"stop {r['id']}: refused at send")
            return "bounced"
        # 9/30: some GitHub machines' IPs are on Spamhaus, so ImprovMX refuses the whole connection
        if ex.__class__.__name__ == "SMTPHeloError" or "black listed" in str(ex):
            log(f"send blocked {r['id']}: this cloud machine's IP is on a blocklist, stopping this run")
            return "blocked"
        if "considered as SPAM" in str(ex) and step == 1 and from_addr != PRIMARY:
            con.execute("UPDATE prospects SET sender=? WHERE id=?", (PRIMARY, r["id"]))
            con.commit()
            log(f"send failed {r['id']}: ImprovMX spam filter refused it from {from_addr}, will go from {PRIMARY}")
            return "spam"
        log(f"send failed {r['id']} step {step}: {ex!r} (will retry next run)")
        return "failed"
    now = datetime.now().astimezone()
    local_day = now.astimezone(ZoneInfo(r["tz"])).date().isoformat()
    con.execute("INSERT INTO sends (prospect_id,step,email,subject,body,message_id,sent_at,sender) VALUES (?,?,?,?,?,?,?,?)",
                (r["id"], step, r["email"], subject, em["body"], mid, now.isoformat(), from_addr))
    con.execute(
        """UPDATE prospects SET current_step=?, status=?, last_sent_on=?,
           first_sent_on=COALESCE(first_sent_on,?), thread_message_id=COALESCE(thread_message_id,?),
           thread_subject=COALESCE(thread_subject,?), sender=COALESCE(sender,?) WHERE id=?""",
        (step, "completed" if step == LAST_STEP else "active", local_day, local_day, mid, subject, from_addr, r["id"]),
    )
    con.commit()
    log(f"SENT {r['id']} step {step} from {from_addr} to {r['email']}")
    save_to_sent(cfg, msg, now)
    return "sent"


def cmd_run(con, dry: bool) -> None:
    cfg = load_config()
    queue = load_queue()
    if not dry:
        scan_replies(con, cfg)
        try:
            send_alert_texts()
        except Exception as ex:  # an alert problem must never block the sends
            log(f"alert texts error: {ex!r}")
    try:  # new sender domains switch on by themselves the first run after their DNS goes live
        from . import sender_domains
        sender_domains.check(quiet=True)
    except Exception as ex:
        log(f"sender check error: {ex!r}")
    if not dry:
        try:  # 10/15: held domains send a little mail to our own inbox each day so the wait builds a history
            from . import warmup
            warmup.run(cfg)
        except Exception as ex:  # warm-up must never block the campaign
            log(f"warmup error: {ex!r}")
    senders = load_senders(cfg)
    now = datetime.now().astimezone()
    per_run = cloud_per_run(now) if CLOUD and not dry else MAX_PER_RUN
    if per_run > 1 and CLOUD:
        log(f"run: GitHub ran late, catching up with up to {per_run} per sender")
    items = due_items(con, queue, now, senders, per_run)
    blocked = False
    if not items:
        log("run: nothing due in any send window")
    else:
        import random
        smtp = {a: SmtpSender(cfg.smtp_host, cfg.smtp_port, a, s["password"]) for a, s in senders.items()}
        # Joey 9/24: never on the hour or seconds apart. Each sender's sends look typed by hand (first 1-4 min
        # after the run starts, then 2-5 min apart); different senders run side by side. Whatever doesn't fit in
        # RUN_BUDGET waits for the next run.
        plan, clock = [], {}
        for it in items:
            a = it[3]
            if CLOUD and a not in clock:  # a sender's first send; different senders go out 5-25 s apart
                t = max(clock.values(), default=0) + random.uniform(5, 25)
            elif CLOUD:  # catch-up runs only: that sender's next one 2-5 min later
                t = clock[a] + random.uniform(120, 300)
            else:
                t = clock.get(a, 0) + (random.uniform(60, 240) if a not in clock else random.uniform(120, 300))
            if t > RUN_BUDGET:
                continue
            clock[a] = t
            plan.append((t, it))
        plan.sort(key=lambda x: x[0])
        start = time.monotonic()
        for t, (r, p, step, a) in plan:
            if not dry and not TEST_IGNORE_TIMING:
                time.sleep(max(0.0, start + t - time.monotonic()))
            if send_one(con, cfg, smtp[a], r, p, step, dry, a) == "blocked":
                blocked = True
                break
    if CLOUD and not dry and not blocked:  # a blocked run doesn't count, so the next run catches up
        LAST_RUN_FILE.write_text(json.dumps({"at": now.isoformat()}))
    if not dry:
        try:  # 9/29: Joey's Google Sheet shows each home's status within 30 minutes (carehome_sheet.py)
            from . import carehome_sheet
            carehome_sheet.sync()
        except BaseException as ex:  # a sheet problem must never block the campaign
            log(f"sheet sync error: {ex!r}")


def cmd_status(con) -> None:
    for r in con.execute("SELECT * FROM prospects ORDER BY rank DESC, id"):
        print(f"{r['id']:<32} {r['status']:<10} step {r['current_step']}  first {r['first_sent_on'] or '-'}  "
              f"last {r['last_sent_on'] or '-'}  {r['stopped_reason'] or ''}")
    n = con.execute("SELECT COUNT(*) FROM sends").fetchone()[0]
    print(f"total sends: {n}")


def main(argv=None) -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(prog="carehome")
    ap.add_argument("cmd", choices=["load", "run", "status", "backfill-sent", "alerts", "alert-texts", "rescan"])
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--ack", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "alerts":
        cmd_alerts(a.ack)
        return
    if a.cmd == "alert-texts":
        send_alert_texts()
        return
    con = connect()
    if a.cmd == "load":
        cmd_load(con)
    elif a.cmd == "backfill-sent":
        cmd_backfill_sent(con)
    elif a.cmd == "run":
        cmd_load(con)
        cmd_run(con, a.dry)
    elif a.cmd == "rescan":
        scan_replies_full(con, load_config())
    else:
        cmd_status(con)


if __name__ == "__main__":
    main()
