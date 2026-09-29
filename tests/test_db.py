from surven_outreach.db import connect, init_db, import_queue


def test_init_creates_tables(tmp_path):
    db = connect(str(tmp_path / "t.db"))
    init_db(db)
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"prospects", "sends", "suppression"} <= names


def _row(email):
    return {"email": email, "business": "A", "first_name": "Al", "category": "Roofing",
            "city": "Newark", "website": "x.com", "about": "abc", "personalization_score": 5}


def test_import_is_idempotent_and_preserves_state(tmp_path):
    db = connect(str(tmp_path / "t.db")); init_db(db)
    rows = [_row("a@x.com")]
    import_queue(db, rows)
    import_queue(db, rows)  # second import must not duplicate
    db.execute("UPDATE prospects SET status='active' WHERE email='a@x.com'")
    db.commit()
    import_queue(db, rows)  # third import must preserve existing state
    n = db.execute("SELECT COUNT(*) FROM prospects").fetchone()[0]
    status = db.execute("SELECT status FROM prospects WHERE email='a@x.com'").fetchone()[0]
    assert n == 1 and status == "active"
