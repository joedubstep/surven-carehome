"""v3 sequence (Joey 10/23): 4 emails on days 1, 3, 7 and 14; day 7 is "pending" in the queue and built per home at
send time (day7/build.py); homes already in the old 6-email run keep the old schedule."""
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


def test_v3_day7_goes_on_day7_and_is_built_at_send():
    con = db()
    home(con, "h", 2, "2026-10-27", "2026-10-29")
    home(con, "early", 2, "2026-10-28", "2026-10-30")
    assert due(con, {"h": V3, "early": V3}) == {("h", 3)}


def test_pending_day7_dry_run_builds_nothing():
    con = db()
    home(con, "h", 2, "2026-10-27", "2026-10-29")
    r = con.execute("SELECT * FROM prospects").fetchone()
    assert ch.send_one(con, {}, None, r, V3, 3, dry=True) == "dry"


def test_day7_prep_picks_homes_due_by_tomorrow_without_a_fresh_check(monkeypatch):
    from datetime import date
    from surven_outreach.day7 import build as day7
    con = db()
    home(con, "tomorrow", 2, "2026-10-27", "2026-10-29")  # day 7 = 11/2
    home(con, "later", 2, "2026-10-30", "2026-11-01")
    home(con, "checked", 2, "2026-10-27", "2026-10-29")
    monkeypatch.setattr(day7, "load", lambda: {"checked": {"checked": "2026-10-31"}})
    got = ch.day7_due(con, {"tomorrow": V3, "later": V3, "checked": V3}, date(2026, 11, 1))
    assert [r["id"] for r, p in got] == ["tomorrow"]


def test_v3_ends_after_day14_and_old_homes_keep_six():
    con = db()
    home(con, "v3_done", 4, "2026-10-10", "2026-10-23")
    home(con, "old_mid", 4, "2026-10-19", "2026-10-29")
    assert due(con, {"v3_done": V3, "old_mid": OLD}) == {("old_mid", 5)}
    assert ch.last_step(V3) == 4 and ch.last_step(OLD) == 6


def test_day7_email_carries_the_picture_inline():
    from surven_outreach.sender import build_message
    msg = build_message("Joey", "joey@surven.ai", "a@b.com", "Re: s", "text", "<m@x>", {},
                        html='<p>hi</p><img src="cid:day7">', images=[("day7", b"\xff\xd8jpg", "jpeg")])
    parts = [x.get_content_type() for x in msg.walk()]
    assert parts[:2] == ["multipart/alternative", "text/plain"] and "multipart/related" in parts
    img = next(x for x in msg.walk() if x.get_content_type() == "image/jpeg")
    assert img["Content-ID"] == "<day7>" and img.get_content_disposition() == "inline"


def test_day7_html_has_footer_and_picture():
    from surven_outreach.day7.build import _html
    h = _html("Hi Ann, top.", "day7", "ad", 520, "Reply yes.\n\nJoey")
    assert 'src="cid:day7"' in h and "Reply STOP to opt out." in h and "surven.ai" in h
    assert h.index("top.") < h.index("cid:day7") < h.index("Reply yes.")


def test_sheet_reply_stages():
    from surven_outreach.carehome_sheet import reply_stage as st
    assert st("yes", 3, "v3") == "Yes to lead magnet"
    assert st("yes", 1, "v3") == "Warm reply"
    assert st("We have one bed open currently.", 1, "v3") == "Not a fit"
    assert st("We are full", 2, "v3") == "Not a fit"
    assert st("4 rooms", 1, "v3") == "Warm reply"
    assert st("Yes. Call Lynn at +1 (907) 575-7014", 5, "") == "Warm reply"  # phone digits are not a room count
