from datetime import date

from surven_outreach.db import connect, init_db, import_queue, record_send
from surven_outreach.reply_scan import classify_and_mark


class FakeGmail:
    def __init__(self, inbound, bounces, autos=None):
        self._in = inbound
        self._b = bounces
        self._auto = set(autos or [])

    def has_inbound_from(self, email):
        return email in self._in

    def has_bounce_for(self, email):
        return email in self._b

    def is_auto_reply_from(self, email):
        return email in self._auto

    def body_text_from(self, email):
        return self._in.get(email, "")


def _seed(tmp):
    db = connect(str(tmp / "t.db")); init_db(db)
    import_queue(db, [{"email": e, "business": "B", "first_name": "X", "category": "c", "city": "C",
                       "website": "", "about": "", "personalization_score": 1}
                      for e in ["r@x.com", "b@x.com", "u@x.com", "s@x.com"]])
    for e in ["r@x.com", "b@x.com", "u@x.com", "s@x.com"]:
        record_send(db, e, 1, "s", "b", f"<{e}>", date(2026, 6, 1))
    return db


def test_classify_marks_reply_bounce_optout(tmp_path):
    db = _seed(tmp_path)
    g = FakeGmail(inbound={"r@x.com": "yes interested", "u@x.com": "please unsubscribe"},
                  bounces={"b@x.com"})
    res = classify_and_mark(db, g)

    def st(e):
        return db.execute("SELECT status FROM prospects WHERE email=?", (e,)).fetchone()[0]

    assert st("r@x.com") == "replied"
    assert st("b@x.com") == "bounced"
    assert st("u@x.com") == "opted_out"
    assert st("s@x.com") == "active"     # untouched
    assert res["replied"] == ["r@x.com"]
    # all stopped prospects are now suppressed
    assert db.execute("SELECT COUNT(*) FROM suppression").fetchone()[0] == 3


def test_reply_after_sequence_completed_is_caught(tmp_path):
    # prospect got all 5 touches (E5 breakup) -> record_send marked it 'completed'.
    # A reply that lands AFTER the final email must STILL be caught (this is exactly
    # when "not interested" / "actually, yes" replies to the breakup arrive).
    db = _seed(tmp_path)
    db.execute("UPDATE prospects SET status='completed', current_step=5, last_touch_date=? "
               "WHERE email='s@x.com'", (date.today().isoformat(),))
    db.commit()
    g = FakeGmail(inbound={"s@x.com": "Not interested"}, bounces=set())
    res = classify_and_mark(db, g)
    assert db.execute("SELECT status FROM prospects WHERE email='s@x.com'").fetchone()[0] == "opted_out"
    assert res["opted_out"] == ["s@x.com"]


def test_auto_reply_does_not_count_as_reply(tmp_path):
    db = _seed(tmp_path)
    g = FakeGmail(inbound={"r@x.com": "Out of office until Monday"}, bounces=set(), autos={"r@x.com"})
    res = classify_and_mark(db, g)
    # auto-responder must NOT stop the sequence or trigger an alert
    assert db.execute("SELECT status FROM prospects WHERE email='r@x.com'").fetchone()[0] == "active"
    assert res["auto_skipped"] == ["r@x.com"]
    assert res["replied"] == []
