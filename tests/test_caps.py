from datetime import date

from surven_outreach.db import connect, init_db, record_send
from surven_outreach.caps import can_send_today, new_slots_remaining, total_slots_remaining

CFG = type("C", (), {"max_new_per_day": 5, "max_total_per_day": 25, "send_days": {0, 1, 2, 3, 4, 5}})


def test_sunday_blocked_saturday_allowed():
    assert can_send_today(CFG, date(2026, 5, 31)) is False   # Sunday
    assert can_send_today(CFG, date(2026, 5, 30)) is True     # Saturday


def test_slots_decrement_with_sends(tmp_path):
    db = connect(str(tmp_path / "t.db")); init_db(db)
    for i in range(3):
        db.execute("INSERT INTO prospects(email) VALUES (?)", (f"{i}@x.com",))
        db.commit()
        record_send(db, f"{i}@x.com", 1, "s", "b", f"<m{i}>", date(2026, 6, 1))
    assert new_slots_remaining(db, CFG, date(2026, 6, 1)) == 2      # 5 - 3 new
    assert total_slots_remaining(db, CFG, date(2026, 6, 1)) == 22   # 25 - 3
