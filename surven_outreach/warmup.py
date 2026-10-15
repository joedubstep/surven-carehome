"""Warm-up for held sender domains (10/15, Joey: "I don't want to burn these domains or burn the leads").

A domain in carehome.HOLD_UNTIL sends no cold email until its date. Meanwhile, once a day, each one sends one real
first email (a queued home's copy) to our own inbox (survengeo). This module checks where it landed, takes it out of
Gmail spam if it landed there, then opens and stars it, so the domain builds a short history of wanted mail.

Release is automatic and needs no one (Joey 10/15: "are you actually going to do that every day?"): from its hold
date, a domain starts cold email only after its warm-up mail reached the inbox, not spam, 3 days in a row. Until then
the hold simply carries on. Everything runs inside the cloud campaign run (carehome.cmd_run), 8 AM to 8 PM ET.
State: data/carehome/warmup.json = {"sent": {addr: day}, "spam": {addr: {day: true if it hit spam}},
"released": {addr: day}}."""
import imaplib
import json
import random
import time
from datetime import date, datetime
from email.utils import format_datetime
from zoneinfo import ZoneInfo

from . import carehome as ch
from .sender import SmtpSender, build_message
from .threading_headers import new_message_id

STATE = ch.ROOT / "data" / "carehome" / "warmup.json"
CLEAN_DAYS = 3  # warm-up mail in the inbox this many days running before a domain sends cold email


def load() -> dict:
    s = json.loads(STATE.read_text()) if STATE.exists() else {}
    if s and "sent" not in s:  # first version kept only {addr: last sent day}
        s = {"sent": s}
    for k in ("sent", "spam", "released"):
        s.setdefault(k, {})
    return s


def release_day(addr: str, s: dict = None):
    """The day a held address may start cold email, or None while it is still held. Records a new release."""
    s = s or load()
    if addr in s["released"]:
        return s["released"][addr]
    today = date.today().isoformat()
    if today < ch.HOLD_UNTIL[addr]:
        return None
    days = sorted(s["spam"].get(addr, {}).items())[-CLEAN_DAYS:]
    if len(days) == CLEAN_DAYS and not any(hit for _, hit in days):
        s["released"][addr] = today
        STATE.write_text(json.dumps(s, indent=1))
        ch.log(f"warmup: {addr} released for cold email, inbox {CLEAN_DAYS} days running")
        return today
    return None


def held_today() -> list:
    meta = json.load(open(ch.SENDERS_FILE)) if ch.SENDERS_FILE.exists() else {}
    s = load()
    return [a for a in ch.HOLD_UNTIL if meta.get(a, {}).get("ready") and release_day(a, s) is None]


def rescue(cfg, addrs) -> dict:
    """Move our own warm-up mail out of Gmail spam into the inbox, then open and star it. -> {addr: moved}."""
    c = imaplib.IMAP4_SSL(cfg.imap_host, timeout=60)
    c.login(cfg.imap_user, cfg.imap_password)
    moved = {a: 0 for a in addrs}
    try:
        c.select('"[Gmail]/Spam"')
        for a in addrs:
            _, d = c.uid("search", None, "FROM", a)
            for u in d[0].split():
                if c.uid("MOVE", u, "INBOX")[0] == "OK":  # Gmail: moving out of Spam = "Not spam"
                    moved[a] += 1
        c.select("INBOX")
        for a in addrs:
            _, d = c.uid("search", None, "FROM", a, "UNFLAGGED")
            for u in d[0].split():
                c.uid("STORE", u, "+FLAGS", r"(\Seen \Flagged)")
    finally:
        c.logout()
    return moved


def run(cfg) -> None:
    addrs = held_today()
    if not addrs:
        return
    now = datetime.now(ZoneInfo("America/New_York"))
    if not 8 <= now.hour < 20:
        return
    s = load()
    today = now.date().isoformat()
    due = [a for a in addrs if s["sent"].get(a) != today]
    if due:
        from . import secrets_store
        pw = secrets_store.get("IMPROVMX_SENDERS")
        copies = [p["emails"]["1"] for p in ch.load_queue().values() if p.get("emails", {}).get("1", {}).get("body")]
        for a in due:
            em = random.choice(copies)
            msg = build_message(ch.FROM_NAME, a, cfg.imap_user, em["subject"], em["body"],
                                new_message_id(a.split("@")[1], seed=f"warmup-{a}-{time.time()}"),
                                {"Date": format_datetime(datetime.now().astimezone())})
            try:
                SmtpSender(cfg.smtp_host, cfg.smtp_port, a, pw[a]).send(msg)
                s["sent"][a] = today
                ch.log(f"warmup sent from {a}")
            except Exception as ex:
                ch.log(f"warmup send failed from {a}: {ex!r}")
            time.sleep(random.uniform(5, 20))
        STATE.write_text(json.dumps(s, indent=1))
        time.sleep(60)  # give Gmail a moment to file what was just sent
    elif now.minute >= 10:  # nothing sent this run: look in spam about once an hour, not every run
        return
    moved = rescue(cfg, addrs)
    s = load()
    for a in addrs:
        if s["sent"].get(a) == today:  # a day counts once today's warm-up was sent; any spam that day marks it
            s["spam"].setdefault(a, {})[today] = s["spam"].get(a, {}).get(today, False) or moved[a] > 0
    STATE.write_text(json.dumps(s, indent=1))
    if any(moved.values()):
        ch.log(f"warmup: moved out of Gmail spam: {', '.join(a for a, n in moved.items() if n)}")
