from datetime import date

from surven_outreach.db import connect, init_db, import_queue, record_send
from surven_outreach.pipeline import prepare

CFG = type("C", (), {"max_new_per_day": 5, "max_total_per_day": 25, "send_days": {0, 1, 2, 3, 4, 5}})


def _seed(tmp, n):
    db = connect(str(tmp / "t.db")); init_db(db)
    import_queue(db, [{"email": f"{i}@x.com", "business": f"B{i}", "first_name": "X", "category": "roofing",
                       "city": "Newark", "website": "x.com", "about": "a", "personalization_score": n - i}
                      for i in range(n)])
    return db


def test_prepare_picks_new_within_cap_and_due_followups(tmp_path):
    db = _seed(tmp_path, 10)
    record_send(db, "0@x.com", 1, "s", "b", "<m0>", date(2026, 6, 1))  # due step-2 on 6-03
    batch = prepare(db, CFG, today=date(2026, 6, 3))
    new = [b for b in batch if b["step"] == 1]
    followups = [b for b in batch if b["step"] > 1]
    assert len(new) == 5                                   # capped at max_new
    assert [f["email"] for f in followups] == ["0@x.com"]  # due step-2
    assert new[0]["needs_personal_line"] is True
    assert followups[0]["needs_personal_line"] is False


def test_prepare_empty_on_sunday(tmp_path):
    db = _seed(tmp_path, 3)
    assert prepare(db, CFG, today=date(2026, 5, 31)) == []  # Sunday
