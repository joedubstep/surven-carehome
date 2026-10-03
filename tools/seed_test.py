"""Inbox placement test (10/3): send the real first email of one queued home from each sender to test inboxes
we can read (Joey's Gmail, survengeo, mail-tester). Goes through the same cloud path as the real sends.
Changes no campaign state. Usage: python tools/seed_test.py addr1 [addr2 ...]
addr=sender sends to addr from that one sender only (mail-tester scores one email per address)."""
import json
import os
import random
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from email.utils import format_datetime  # noqa: E402

from surven_outreach import carehome as ch  # noqa: E402
from surven_outreach.config import load_config  # noqa: E402
from surven_outreach.sender import SmtpSender, build_message  # noqa: E402
from surven_outreach.threading_headers import new_message_id  # noqa: E402

to = [a.split("=") + [None] if "=" not in a else a.split("=", 1) for a in sys.argv[1:] if "@" in a]
to = [(t[0], t[1]) for t in to]
cfg = load_config()
senders = ch.load_senders(cfg)
queue = ch.load_queue()
con = ch.connect()
pid = next(r["id"] for r in con.execute("SELECT id FROM prospects WHERE status='queued' ORDER BY rank DESC")
           if r["id"] in queue)
em = queue[pid]["emails"]["1"]
print("using the email-1 copy of", pid)
for addr, s in senders.items():
    smtp = SmtpSender(cfg.smtp_host, cfg.smtp_port, addr, s["password"])
    for rcpt, only in to:
        if only and only != addr:
            continue
        mid = new_message_id(addr.split("@")[1], seed=f"seed-{addr}-{rcpt}-{time.time()}")
        msg = build_message(ch.FROM_NAME, addr, rcpt, em["subject"], em["body"], mid,
                            {"Date": format_datetime(datetime.now().astimezone())})
        try:
            smtp.send(msg)
            print("SEED sent", addr, "->", rcpt)
        except Exception as ex:
            print("SEED failed", addr, "->", rcpt, repr(ex)[:160])
        time.sleep(random.uniform(3, 8))
