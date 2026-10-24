"""v3 sequence (Joey 10/23): 4 emails on days 1, 3, 7 and 14; day 7 waits while it is still "pending"; homes already
in the old 6-email run keep the old schedule."""
import sqlite3
from datetime import datetime, timezone

from surven_outreach import carehome as ch

NOON_ET = datetime(2026, 11, 2, 13, 0, tzinfo=timezone.utc)  # 8 AM Eastern, inside the send window
SENDERS = {ch.PRIMARY: {"cap_total": 50, "cap_new": 50}}


def db():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(ch.SCHEMA)
    con.execute("ALTER TABLE prospects ADD COLUMN sender TEXT")  # added by connect() on the live db
    con.execute("ALTER TABLE sends ADD COLUMN sender TEXT")
    return con


def home(con, pid, step, first, last, status="active"):
    con.execute("INSERT INTO prospects (id,email,facility,first_name,tz,status,current_step,first_sent_on,last_sent_on,sender)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)", (pid, f"{pid}@x.com", pid, "", "America/New_York", status, step, first,
                                                   last, ch.PRIMARY))


V3 = {"seq": "v3", "emails": {"1": {"subject": "s", "body": "b"}, "2": {"body": "b"}, "3": {"pending": True},
                              "4": {"body": "b"}}}
OLD = {"emails": {str(i): {"body": "b"} for i in range(1, 7)}}


def due(con, queue):
    return {(r["id"], step) for r, p, step, a in ch.due_items(con, queue, NOON_ET, SENDERS)}


def test_v3_day3_goes_two_days_after_day1():
    con = db()
    home(con, "on_time", 1, "2026-10-31", "2026-10-31")
    home(con, "too_soon", 1, "2026-11-01", "2026-11-01")
    assert due(con, {"on_time": V3, "too_soon": V3}) == {("on_time", 2)}


def test_v3_day7_waits_while_pending_then_goes():
    con = db()
    home(con, "h", 2, "2026-10-27", "2026-10-29")
    assert due(con, {"h": V3}) == set()
    ready = {"seq": "v3", "emails": {**V3["emails"], "3": {"body": "the ad"}}}
    assert due(con, {"h": ready}) == {("h", 3)}


def test_v3_ends_after_day14_and_old_homes_keep_six():
    con = db()
    home(con, "v3_done", 4, "2026-10-10", "2026-10-23")
    home(con, "old_mid", 4, "2026-10-19", "2026-10-29")
    assert due(con, {"v3_done": V3, "old_mid": OLD}) == {("old_mid", 5)}
    assert ch.last_step(V3) == 4 and ch.last_step(OLD) == 6
