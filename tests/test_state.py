from datetime import date

from surven_outreach.db import (
    connect, init_db, import_queue, record_send, mark, due_followups, next_offset,
)


def _seed(tmp):
    db = connect(str(tmp / "t.db")); init_db(db)
    import_queue(db, [{"email": "a@x.com", "business": "A", "first_name": "Al", "category": "Roofing",
                       "city": "Newark", "website": "x.com", "about": "abc", "personalization_score": 5}])
    return db


def test_next_offset_cadence():
    assert next_offset(1) == 2   # day1 -> day3
    assert next_offset(2) == 3   # day3 -> day6
    assert next_offset(4) == 4   # day10 -> day14
    assert next_offset(5) is None


def test_record_send_advances_and_sets_next(tmp_path):
    db = _seed(tmp_path)
    record_send(db, "a@x.com", step=1, subject="quick q", body="...",
                message_id="<m1@surven.ai>", sent_on=date(2026, 6, 1))
    p = db.execute("SELECT * FROM prospects WHERE email='a@x.com'").fetchone()
    assert p["current_step"] == 1 and p["status"] == "active"
    assert p["thread_message_id"] == "<m1@surven.ai>"
    assert p["next_touch_date"] == "2026-06-03"   # +2 days


def test_due_followups_only_when_date_reached(tmp_path):
    db = _seed(tmp_path)
    record_send(db, "a@x.com", 1, "s", "b", "<m1>", date(2026, 6, 1))
    assert [r["email"] for r in due_followups(db, date(2026, 6, 2))] == []
    assert [r["email"] for r in due_followups(db, date(2026, 6, 3))] == ["a@x.com"]


def test_mark_reply_stops_sequence(tmp_path):
    db = _seed(tmp_path)
    record_send(db, "a@x.com", 1, "s", "b", "<m1>", date(2026, 6, 1))
    mark(db, "a@x.com", "replied")
    assert db.execute("SELECT status FROM prospects WHERE email='a@x.com'").fetchone()[0] == "replied"
    assert due_followups(db, date(2026, 6, 10)) == []   # never resurfaces
    # suppression row written
    assert db.execute("SELECT COUNT(*) FROM suppression WHERE email='a@x.com'").fetchone()[0] == 1
