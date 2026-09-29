import smtplib
from datetime import date

from . import db as dbm
from .caps import can_send_today, new_slots_remaining, total_slots_remaining
from .sender import build_message
from .templates import render
from .threading_headers import new_message_id, reply_headers


def prepare(con, cfg, today: date) -> list[dict]:
    if not can_send_today(cfg, today):
        return []
    batch: list[dict] = []
    # 1) due follow-ups first (consume total cap, not the new-prospect cap)
    for row in dbm.due_followups(con, today):
        step = row["current_step"] + 1
        batch.append({"email": row["email"], "step": step,
                      "needs_personal_line": False, "prospect": dict(row)})
    # 2) new prospects up to the smaller of the new cap and remaining total cap
    n_new = min(new_slots_remaining(con, cfg, today),
                max(0, total_slots_remaining(con, cfg, today) - len(batch)))
    new_rows = con.execute(
        "SELECT * FROM prospects WHERE status='queued' ORDER BY rank DESC LIMIT ?", (n_new,)
    ).fetchall()
    for row in new_rows:
        batch.append({"email": row["email"], "step": 1,
                      "needs_personal_line": True, "prospect": dict(row)})
    return batch


def send_batch(con, cfg, sender, batch: list[dict], today: date) -> list[dict]:
    results = []
    domain = cfg.sender_email.split("@")[-1]
    for item in batch:
        p = {**item["prospect"], "personal_line": item.get("personal_line", "")}
        step = item["step"]
        rendered = render(step, p)
        if step == 1:
            mid = new_message_id(domain, seed=f'{p["email"]}-1')
            extra: dict = {}
            subject = rendered["subject"]
        else:
            mid = new_message_id(domain, seed=f'{p["email"]}-{step}')
            extra = reply_headers(p["thread_message_id"], p["thread_subject"])
            subject = extra["Subject"]
        msg = build_message(cfg.sender_name, cfg.sender_email, p["email"],
                            subject, rendered["body"], mid, extra)
        # one bad recipient must not abort the whole batch: suppress + continue
        try:
            sender.send(msg)
        except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
            dbm.mark(con, p["email"], "bounced", f"smtp refused at send: {e!r}"[:300])
            results.append({"email": p["email"], "step": step, "bounced": True})
            continue
        except (smtplib.SMTPException, OSError) as e:
            # transient send failure after retries (connection drop, timeout): do NOT bounce.
            # leave prospect untouched (queued/active) so it retries on the next run.
            results.append({"email": p["email"], "step": step, "failed": True, "error": str(e)[:200]})
            continue
        dbm.record_send(con, p["email"], step, subject, rendered["body"], mid, today)
        if step == 1:
            con.execute("UPDATE prospects SET personalized=? WHERE email=?",
                        (1 if (item.get("personal_line") or "").strip() else 0, p["email"]))
            con.commit()
        results.append({"email": p["email"], "step": step, "message_id": mid, "sent": True})
    return results
