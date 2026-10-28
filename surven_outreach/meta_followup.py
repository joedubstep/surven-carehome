"""Meta ads answers the cloud could not get (10/28, Joey: "We must always check").

Meta refuses the Ad Library to GitHub's cloud machines most of the time ("Rate limit exceeded", checked 10/28 with the
cloud task "meta-check"), so a reply text often goes out with "Meta ads: still checking, I'll text it". Joey's PC gets
answers. This runs there (Windows task "Surven-Meta-Followup", every 10 minutes): it reads the cloud's reply alerts
from the state branch (read only), checks Meta for every reply still waiting (email_leads.check_meta, the same check
as the text), and texts Joey the answer. Each reply is texted once (data/carehome/meta_followups.json, kept on this PC
only). Run by hand: python -m surven_outreach.meta_followup
"""
import json
import subprocess
from datetime import datetime, timedelta
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


def cloud_alerts() -> list:
    """The cloud's reply alerts, sent and still in flight, from the state branch."""
    subprocess.run(["git", "-C", str(REPO), "fetch", "-q", "state", "state"], check=True, timeout=180,
                   capture_output=True)
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


if __name__ == "__main__":
    try:
        run()
    except Exception as ex:
        log(f"error: {ex!r}"[:500])
        raise
