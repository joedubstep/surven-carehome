"""A/B test report (10/15, Joey: "set up an A/B test, two different versions"). Reads the cloud database copy and
puts each home in version A or B by the email 1 it actually got (B's email 1 carries the Aline 57% line), so a home
sent email 1 just before a queue change is still counted right. Counts only homes first emailed on or after START.
Usage: python tools/cloud_state.py pull && python tools/ab_report.py"""
import collections
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "carehome", "carehome.db")
START = sys.argv[1] if len(sys.argv) > 1 else "2026-10-16"
B_MARK = "Aline's Q2 2026 report found 57%"

con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row
t = collections.defaultdict(collections.Counter)
for r in con.execute("SELECT p.status, s.body FROM sends s JOIN prospects p ON p.id = s.prospect_id "
                     "WHERE s.step = 1 AND s.sent_at >= ?", (START,)):
    arm = "B" if B_MARK in (r["body"] or "") else "A"
    t[arm]["homes"] += 1
    t[arm][r["status"]] += 1
print(f"A/B since {START}: replied = any human reply (read each one by hand for yes vs no)")
for arm in "AB":
    c = t[arm]
    n = c["homes"] or 1
    print(f"  {arm}: {c['homes']} homes | replied {c['replied']} ({c['replied'] / n:.1%}) | opted out {c['opted_out']} "
          f"({c['opted_out'] / n:.1%}) | bounced {c['bounced']}")
print("Read it at about 500 homes a side. Below that, one reply swings the result.")
