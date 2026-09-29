from . import db as dbm


def scan_bookings(con, client, notify_fn) -> list:
    """Push a one-time alert for each NEW Cal.com booking. Dedups by message-id so the
    30-min scan never re-alerts the same booking."""
    fresh = []
    for b in client.find_calcom_bookings():
        mid = b.get("message_id") or b.get("subject", "")
        if not mid or dbm.was_notified(con, mid):
            continue
        notify_fn(b["subject"])
        dbm.mark_notified(con, mid, "booking")
        fresh.append(b)
    return fresh
