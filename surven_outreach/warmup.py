"""Warm-up for held sender domains (10/15, Joey: "I don't want to burn these domains or burn the leads").

A domain in carehome.HOLD_UNTIL sends no cold email until its date. Meanwhile, once a day, each one sends one real
first email (a queued home's copy) to our own inbox (survengeo), and this module takes it out of Gmail spam if it
landed there, then opens and stars it. So the domain has a short history of mail that was wanted when the hold ends.
State: data/carehome/warmup.json = {address: last warm-up day}. Runs from carehome.cmd_run, 9 AM to 5 PM ET."""
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


def held_today() -> list:
    meta = json.load(open(ch.SENDERS_FILE)) if ch.SENDERS_FILE.exists() else {}
    today = date.today().isoformat()
    return [a for a, until in ch.HOLD_UNTIL.items() if today < until and meta.get(a, {}).get("ready")]


def rescue(cfg, addrs) -> int:
    """Move our own warm-up mail out of Gmail spam into the inbox, then open and star it. -> number moved."""
    c = imaplib.IMAP4_SSL(cfg.imap_host, timeout=60)
    c.login(cfg.imap_user, cfg.imap_password)
    moved = 0
    try:
        c.select('"[Gmail]/Spam"')
        for a in addrs:
            _, d = c.uid("search", None, "FROM", a)
            for u in d[0].split():
                if c.uid("MOVE", u, "INBOX")[0] == "OK":  # Gmail: moving out of Spam = "Not spam"
                    moved += 1
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
    if not 9 <= now.hour < 17:
        return
    done = json.loads(STATE.read_text()) if STATE.exists() else {}
    due = [a for a in addrs if done.get(a) != now.date().isoformat()]
    from . import secrets_store
    pw = secrets_store.get("IMPROVMX_SENDERS")
    queue = ch.load_queue()
    copies = [p["emails"]["1"] for p in queue.values() if p.get("emails", {}).get("1", {}).get("body")]
    for a in due:
        em = random.choice(copies)
        msg = build_message(ch.FROM_NAME, a, cfg.imap_user, em["subject"], em["body"],
                            new_message_id(a.split("@")[1], seed=f"warmup-{a}-{time.time()}"),
                            {"Date": format_datetime(datetime.now().astimezone())})
        try:
            SmtpSender(cfg.smtp_host, cfg.smtp_port, a, pw[a]).send(msg)
            done[a] = now.date().isoformat()
            ch.log(f"warmup sent from {a}")
        except Exception as ex:
            ch.log(f"warmup send failed from {a}: {ex!r}")
        time.sleep(random.uniform(5, 20))
    STATE.write_text(json.dumps(done, indent=1))
    if not due and now.minute >= 10:  # nothing sent this run: look in spam about once an hour, not every run
        return
    time.sleep(60 if due else 0)  # give Gmail a moment to file what was just sent
    n = rescue(cfg, addrs)
    if n:
        ch.log(f"warmup: moved {n} out of Gmail spam")
