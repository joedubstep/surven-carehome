import sqlite3
from datetime import date, timedelta

OFFSETS = {1: 1, 2: 3, 3: 6, 4: 10, 5: 14}  # step -> day-offset from first contact

SCHEMA = """
CREATE TABLE IF NOT EXISTS prospects (
  email TEXT PRIMARY KEY,
  business TEXT, first_name TEXT, category TEXT, city TEXT, website TEXT, about TEXT,
  personal_line TEXT,
  status TEXT NOT NULL DEFAULT 'queued',       -- queued|active|replied|bounced|opted_out|completed|skipped
  current_step INTEGER NOT NULL DEFAULT 0,      -- 0..5
  thread_message_id TEXT, thread_subject TEXT,
  next_touch_date TEXT, first_contact_date TEXT, last_touch_date TEXT,
  rank INTEGER, personalized INTEGER, replied_at TEXT
);
CREATE TABLE IF NOT EXISTS sends (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT NOT NULL, step INTEGER NOT NULL,
  subject TEXT, body TEXT, message_id TEXT,
  sent_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'sent'
);
CREATE TABLE IF NOT EXISTS suppression (
  email TEXT PRIMARY KEY, reason TEXT, added_at TEXT
);
CREATE TABLE IF NOT EXISTS notified (
  message_id TEXT PRIMARY KEY, kind TEXT, at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sends_sent_at ON sends(sent_at);
CREATE INDEX IF NOT EXISTS idx_prospects_status ON prospects(status);
"""


def connect(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init_db(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    # migrations for existing DBs (ADD COLUMN is idempotent-guarded)
    cols = {r["name"] for r in con.execute("PRAGMA table_info(prospects)")}
    if "personalized" not in cols:
        con.execute("ALTER TABLE prospects ADD COLUMN personalized INTEGER")
    if "replied_at" not in cols:
        con.execute("ALTER TABLE prospects ADD COLUMN replied_at TEXT")
    con.commit()


def import_queue(con, rows: list[dict]) -> int:
    cur = con.executemany(
        """INSERT INTO prospects (email, business, first_name, category, city, website, about, rank)
           VALUES (:email,:business,:first_name,:category,:city,:website,:about,:rank)
           ON CONFLICT(email) DO NOTHING""",
        [{**r, "rank": r.get("personalization_score", 0)} for r in rows],
    )
    con.commit()
    return cur.rowcount


def next_offset(step: int):
    if step >= 5:
        return None
    return OFFSETS[step + 1] - OFFSETS[step]


def record_send(con, email, step, subject, body, message_id, sent_on: date):
    con.execute(
        "INSERT INTO sends (email,step,subject,body,message_id,sent_at) VALUES (?,?,?,?,?,?)",
        (email, step, subject, body, message_id, sent_on.isoformat()),
    )
    row = con.execute("SELECT first_contact_date FROM prospects WHERE email=?", (email,)).fetchone()
    first = (row["first_contact_date"] if row else None) or sent_on.isoformat()
    delta = next_offset(step)
    nxt = (sent_on + timedelta(days=delta)).isoformat() if delta else None
    status = "completed" if step >= 5 else "active"
    con.execute(
        """UPDATE prospects SET current_step=?, status=?, thread_message_id=COALESCE(thread_message_id,?),
           thread_subject=COALESCE(thread_subject,?), first_contact_date=?, last_touch_date=?, next_touch_date=?
           WHERE email=?""",
        (step, status, message_id, subject, first, sent_on.isoformat(), nxt, email),
    )
    con.commit()


def mark(con, email, status, reason: str = "", added_at: str | None = None):
    when = added_at or date.today().isoformat()
    con.execute("UPDATE prospects SET status=?, next_touch_date=NULL WHERE email=?", (status, email))
    if status == "replied":
        con.execute("UPDATE prospects SET replied_at=? WHERE email=?", (when, email))
    if status in ("replied", "bounced", "opted_out"):
        con.execute(
            "INSERT OR IGNORE INTO suppression (email,reason,added_at) VALUES (?,?,?)",
            (email, reason or status, when),
        )
    con.commit()


def due_followups(con, today: date) -> list:
    return con.execute(
        """SELECT * FROM prospects WHERE status='active'
           AND current_step BETWEEN 1 AND 4
           AND next_touch_date IS NOT NULL AND next_touch_date<=?
           ORDER BY rank DESC""",
        (today.isoformat(),),
    ).fetchall()


def was_notified(con, message_id) -> bool:
    return con.execute("SELECT 1 FROM notified WHERE message_id=?", (message_id,)).fetchone() is not None


def mark_notified(con, message_id, kind, at: str | None = None):
    con.execute("INSERT OR IGNORE INTO notified (message_id, kind, at) VALUES (?,?,?)",
                (message_id, kind, at or date.today().isoformat()))
    con.commit()
