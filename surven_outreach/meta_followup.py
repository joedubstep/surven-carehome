"""Meta ads answers the cloud could not get (10/28, Joey: "We must always check").

Meta refuses the Ad Library to GitHub's cloud machines most of the time ("Rate limit exceeded", checked 10/28 with the
cloud task "meta-check"), so a reply text often goes out with "Meta ads: still checking, I'll text it". Joey's PC gets
answers. This runs there (Windows task "Surven-Meta-Followup", every 10 minutes): it reads the cloud's reply alerts
from the state branch (read only), checks Meta for every reply still waiting (email_leads.check_meta, the same check
as the text), and texts Joey the answer. Each reply is texted once (data/carehome/meta_followups.json, kept on this PC
only).

Day 7 emails (10/28, Joey: "move the day 7 check to my PC too"): the cloud's nightly Ad Library check is refused the
same way, so this also checks every home whose day 7 is due within 2 days (day7.build.prep, the same check and photo
pick) and pushes the answers to the state branch as data/carehome/day7_pc.json. The cloud's send runs read that file,
so the email goes out with the right picture. One run at a time (meta_followup.lock), since a batch of 100 homes takes
longer than 10 minutes. Run by hand: python -m surven_outreach.meta_followup
"""
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import carehome as ch
from . import email_leads as el

REPO = Path(__file__).resolve().parent.parent
D = REPO / "data" / "carehome"
DONE = D / "meta_followups.json"
LOG = D / "meta_followup.log"


def log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')} {msg}\n")


def fetch() -> None:
    subprocess.run(["git", "-C", str(REPO), "fetch", "-q", "state", "state"], check=True, timeout=180,
                   capture_output=True)


def state_file(name: str) -> bytes:
    """One file as the cloud last saved it (call fetch first)."""
    return subprocess.run(["git", "-C", str(REPO), "show", f"state/state:data/carehome/{name}"], check=True,
                          capture_output=True, timeout=60).stdout


def cloud_alerts() -> list:
    """The cloud's reply alerts, sent and still in flight, from the state branch."""
    fetch()
    out = []
    for name in ("alerts_sent.jsonl", "alerts_inflight.jsonl"):
        r = subprocess.run(["git", "-C", str(REPO), "show", f"state/state:data/carehome/{name}"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out += [json.loads(l) for l in r.stdout.splitlines() if l.strip()]
    return out


def waiting(alerts: list, done: dict, now: datetime) -> list:
    """Replies whose text said Meta was still being checked, from the last 3 days, not answered yet. One per home."""
    out = {}
    for it in alerts:
        if (it.get("meta_later") and it.get("lead") and it["id"] not in done
                and datetime.fromisoformat(it["at"]) > now - timedelta(days=3)):
            out[it["id"]] = it
    return list(out.values())


def followup_text(it: dict, lines: list) -> str:
    code = (it.get("lead") or {}).get("code", "")
    who = el.short_name(it.get("from_name", ""), it.get("first_name") or "Owner")
    return "\n".join([f"{code + '. ' if code else ''}{who}, {it.get('facility', '')}"] + lines)


def run() -> None:
    now = datetime.now(ZoneInfo("America/New_York")).replace(tzinfo=None)
    if now.hour >= ch.QUIET[0] or now.hour < ch.QUIET[1]:
        return
    try:
        done = json.loads(DONE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        done = {}
    items = waiting(cloud_alerts(), done, now)
    try:
        for it in items:
            lines = el.meta_line(it["id"], el._facts(it["id"]), it.get("lead"))
            if lines == [el.META_LATER]:
                log(f"{it['id']}: Meta did not answer here either, next try in 10 minutes")
                continue
            if lines and not ch._ghl_sms(followup_text(it, lines)):
                log(f"{it['id']}: text failed, next try in 10 minutes")
                continue
            done[it["id"]] = {"at": now.isoformat(timespec="seconds"), "lines": lines}
            DONE.write_text(json.dumps(done, indent=1), encoding="utf-8")
            log(f"{it['id']}: texted {lines[:1]}")
    finally:
        if items:
            from .day7 import build as day7
            day7.close()


def push() -> None:
    """The PC's Ad Library checks to the state branch, where the cloud's send runs pull them."""
    if (D / "day7_pc.json").exists():
        subprocess.run([sys.executable, str(REPO / "tools" / "cloud_state.py"), "push-local", "day7_pc.json"],
                       cwd=REPO, check=True, capture_output=True, timeout=300)


def day7_checks(within: int = 2, minutes: int = 50) -> None:
    """Ad Library check + photo pick for every home whose day 7 is due within `within` days and has no fresh check."""
    from .day7 import build as day7
    from .day7.adlib import RateLimited
    fetch()
    day7.STATE.write_bytes(state_file("day7.json"))  # the cloud's own checks, so none is done twice
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "carehome.db"
        db.write_bytes(state_file("carehome.db"))
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            items = ch.day7_due(con, ch.load_queue(), date.today(), within=within)
        finally:
            con.close()
    if not items:
        return
    log(f"day 7: {len(items)} home(s) to check")
    n, stop_at = Counter(), time.time() + minutes * 60
    try:
        for i, (r, p) in enumerate(items, 1):
            if time.time() > stop_at:
                log(f"day 7: {minutes} minutes used, the rest next run")
                break
            try:
                rec = day7.prep(r, p)
                n[rec.get("ads") or "error"] += 1
            except RateLimited:
                log("day 7: Meta is rate limiting this PC, next try in 10 minutes")
                break
            except Exception as ex:
                n["error"] += 1
                log(f"day 7 {r['id']}: {ex!r}"[:300])
            if i % 10 == 0:
                push()
            time.sleep(2)
    finally:
        if n:
            push()
        log(f"day 7 done: {dict(n) or 'nothing checked'}")


def lock():
    """Held for the whole run; a second copy started by the task 10 minutes later sees it and quits."""
    f = open(D / "meta_followup.lock", "a+")
    f.seek(0)
    try:
        import msvcrt
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    except ImportError:
        import fcntl
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return f


def main() -> None:
    try:
        held = lock()
    except OSError:
        return  # the last run is still going
    from .day7 import build as day7
    day7.OUT = day7.PC_STATE
    try:
        try:
            run()
        except Exception as ex:  # a reply problem must not stop the day 7 checks
            log(f"replies error: {ex!r}"[:500])
        day7_checks()
    finally:
        day7.close()
        held.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        log(f"error: {ex!r}"[:500])
        raise
