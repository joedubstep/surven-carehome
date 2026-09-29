from datetime import date

from surven_outreach.db import connect, init_db, import_queue
from surven_outreach.pipeline import prepare, send_batch
from surven_outreach.sender import FakeSender

CFG = type("C", (), {"max_new_per_day": 5, "max_total_per_day": 25, "send_days": {0, 1, 2, 3, 4, 5},
                     "sender_name": "Joseph", "sender_email": "joey@surven.ai"})


def test_send_batch_threads_followups_to_original(tmp_path):
    db = connect(str(tmp_path / "t.db")); init_db(db)
    import_queue(db, [{"email": "a@x.com", "business": "A", "first_name": "Al", "category": "roofing",
                       "city": "Newark", "website": "x.com", "about": "a", "personalization_score": 1}])
    fs = FakeSender()
    b1 = prepare(db, CFG, date(2026, 6, 1))
    for item in b1:
        item["personal_line"] = "saw your new site — clean"
    send_batch(db, CFG, fs, b1, today=date(2026, 6, 1))
    assert db.execute("SELECT current_step FROM prospects WHERE email='a@x.com'").fetchone()[0] == 1

    b2 = prepare(db, CFG, date(2026, 6, 3))   # day3 -> step2 due
    send_batch(db, CFG, fs, b2, today=date(2026, 6, 3))
    m1, m2 = fs.sent[0], fs.sent[-1]
    assert m2["In-Reply-To"] == m1["Message-ID"]      # threaded to the original
    assert m2["Subject"].startswith("Re:")
    assert m1["Subject"] == "quick q Al"              # first send carries the real subject


def test_send_batch_sends_personalized_first_email(tmp_path):
    db = connect(str(tmp_path / "t.db")); init_db(db)
    import_queue(db, [{"email": "a@x.com", "business": "A", "first_name": "Al", "category": "roofing",
                       "city": "Newark", "website": "x.com", "about": "a", "personalization_score": 1}])
    fs = FakeSender()
    b1 = prepare(db, CFG, date(2026, 6, 1))
    for item in b1:
        item["personal_line"] = "saw your new site — clean"
    send_batch(db, CFG, fs, b1, today=date(2026, 6, 1))
    body = fs.sent[0].get_content()
    assert "hey Al," in body
    assert "saw your new site" in body
    assert body.rstrip().endswith("thanks,\nJoseph")
