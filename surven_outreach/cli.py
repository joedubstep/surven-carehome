import argparse
import json
from datetime import date

from . import db as dbm
from .caps import total_slots_remaining
from .clean import apply_suppression, clean_prospects, load_suppression
from .bookings import scan_bookings
from .config import load_config
from .notify import notify_booking, notify_reply
from .pipeline import prepare, send_batch
from .reply_scan import ImapClient, classify_and_mark
from .sender import SmtpSender


def cmd_clean(cfg, con):
    rows = clean_prospects(cfg.source_csv)
    rows = apply_suppression(rows, load_suppression(cfg.suppression_csv))
    n = dbm.import_queue(con, rows)
    print(f"cleaned={len(rows)} imported_new={n}")


def cmd_scan(cfg, con):
    client = ImapClient(cfg.imap_host, cfg.imap_user, cfg.imap_password)
    res = classify_and_mark(con, client)
    print(f"replied={len(res['replied'])} bounced={len(res['bounced'])} "
          f"opted_out={len(res['opted_out'])} auto_skipped={len(res.get('auto_skipped', []))}")
    for e in res["replied"]:
        row = con.execute("SELECT business, first_name FROM prospects WHERE email=?", (e,)).fetchone()
        business = row["business"] if row else e
        first = row["first_name"] if row else ""
        notify_reply(cfg, business, first)   # phone + desktop alert, genuine replies only
        print("HOT REPLY (reply within the hour):", first, "@", business, f"<{e}>")
    # Cal.com booking alerts (reads the personal inbox where booking emails land)
    if cfg.imap_bookings_user and cfg.imap_bookings_password:
        bclient = ImapClient(cfg.imap_host, cfg.imap_bookings_user, cfg.imap_bookings_password)
        booked = scan_bookings(con, bclient, lambda s: notify_booking(cfg, s))
        print(f"new_bookings={len(booked)}")
        for b in booked:
            print("NEW BOOKING:", b["subject"])


def cmd_prepare(cfg, con):
    today = date.today()
    batch = prepare(con, cfg, today)
    path = f"data/batch-{today.isoformat()}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(batch, f, indent=2, default=str)
    new = sum(1 for b in batch if b["needs_personal_line"])
    print(f"prepared={len(batch)} new_needing_personal_line={new} -> {path}")


def cmd_send(cfg, con, batch_path):
    with open(batch_path, encoding="utf-8") as f:
        batch = json.load(f)
    today = date.today()
    slots = total_slots_remaining(con, cfg, today)
    if len(batch) > slots:
        print(f"cap guard: trimming batch {len(batch)} -> {slots}")
        batch = batch[:slots]
    sender = SmtpSender(cfg.smtp_host, cfg.smtp_port, cfg.smtp_user, cfg.smtp_password)
    res = send_batch(con, cfg, sender, batch, today)
    sent = sum(1 for r in res if r.get("sent"))
    bounced = [r["email"] for r in res if r.get("bounced")]
    failed = [r["email"] for r in res if r.get("failed")]
    print(f"sent={sent}")
    if bounced:
        print(f"bounced/suppressed={len(bounced)}: {', '.join(bounced)}")
    if failed:
        print(f"failed/will-retry={len(failed)}: {', '.join(failed)}")


def cmd_mark(cfg, con, email, status):
    dbm.mark(con, email, status)
    print(f"marked {email} {status}")


def main(argv=None):
    cfg = load_config()
    con = dbm.connect(cfg.db_path)
    dbm.init_db(con)
    p = argparse.ArgumentParser(prog="surven-outreach")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("clean")
    sub.add_parser("scan")
    sub.add_parser("prepare")
    s_send = sub.add_parser("send")
    s_send.add_argument("--batch", required=True)
    s_mark = sub.add_parser("mark")
    s_mark.add_argument("email")
    s_mark.add_argument("status")
    args = p.parse_args(argv)
    if args.cmd == "clean":
        cmd_clean(cfg, con)
    elif args.cmd == "scan":
        cmd_scan(cfg, con)
    elif args.cmd == "prepare":
        cmd_prepare(cfg, con)
    elif args.cmd == "send":
        cmd_send(cfg, con, args.batch)
    elif args.cmd == "mark":
        cmd_mark(cfg, con, args.email, args.status)


if __name__ == "__main__":
    main()
