"""Check that a home's mailbox exists before its first email (Joey 10/24: "I do not want our fucking addresses to go
to spam. We're taking bounce rate very seriously"). Bounces so far: 48 of 784 homes (6.1%); state-list addresses
11.4%, directory listings 22%, and 2,704 of the homes still to email came from state lists.

Free and silent: for each address it asks the home's own mail server, over port 25, whether it would take mail for
that mailbox (RCPT TO), then hangs up before anything is sent. Runs in the cloud (this PC's internet provider blocks
port 25; GitHub's machines do not). Each address gets one verdict in data/carehome/email_verify.json:
  valid      the server said yes, and it says no to a made-up address at the same domain
  invalid    the server said that mailbox does not exist (it would bounce)
  catch_all  the server says yes to any address, so a yes proves nothing
  unknown    no clear answer (busy, greylisting, our checking machine is blocked); tried again next run
New homes get their first email only when valid (carehome.due_items)."""
from __future__ import annotations

import argparse
import json
import random
import re
import smtplib
import socket
import sqlite3
import string
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

D = Path(__file__).resolve().parent.parent / "data" / "carehome"
STATE = D / "email_verify.json"
HELO = "mail-check.surven.ai"
GONE = re.compile(r"5\.1\.[0-9]|5\.4\.1|user unknown|unknown user|no such user|does not exist|doesn't exist|not found|"
                  r"invalid recipient|recipient rejected|address rejected|no mailbox|mailbox unavailable|"
                  r"unrouteable|undeliverable|disabled|inactive|not a valid|no longer", re.I)
BLOCKED = re.compile(r"spamhaus|blocklist|blacklist|block list|black list|\brbl\b|reputation|policy|too many|\brate\b|"
                     r"try again|temporar|greylist|grey-list|banned|not permitted|helo|reverse",
                     re.I)
_host_locks: dict[str, threading.Semaphore] = defaultdict(lambda: threading.Semaphore(2))


def load() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=0, sort_keys=True), encoding="utf-8")


def mx_hosts(domain: str) -> list[str]:
    import dns.resolver
    try:
        ans = dns.resolver.resolve(domain, "MX", lifetime=10)
        hosts = [str(r.exchange).rstrip(".") for r in sorted(ans, key=lambda r: r.preference)]
        return [h for h in hosts if h] or [domain]
    except (dns.resolver.NXDOMAIN, dns.resolver.NoNameservers):
        return []
    except Exception:
        return [domain]  # no MX record: mail goes to the domain itself


def _talk(host: str, addrs: list[str], fake: str) -> dict[str, tuple[int, str]]:
    """One connection: RCPT for each address and for one made-up address. -> {address: (code, message)}"""
    out = {}
    with _host_locks[host]:
        s = smtplib.SMTP(timeout=25, local_hostname=HELO)
        try:
            s.connect(host, 25)
            s.ehlo_or_helo_if_needed()
            code, msg = s.mail("")
            if code >= 400:
                return {a: (code, msg.decode(errors="ignore")) for a in addrs + [fake]}
            for a in addrs + [fake]:
                code, msg = s.rcpt(a)
                out[a] = (code, msg.decode(errors="ignore")[:160])
        finally:
            try:
                s.quit()
            except Exception:
                pass
        time.sleep(1.0)
    return out


def verdict(code: int, msg: str, fake_ok: bool) -> str:
    if code in (250, 251):
        return "catch_all" if fake_ok else "valid"
    if code >= 500 and GONE.search(msg) and not BLOCKED.search(msg):
        return "invalid"
    if code in (550, 551, 553) and not BLOCKED.search(msg):
        return "invalid"
    return "unknown"


def check_domain(domain: str, addrs: list[str]) -> dict[str, dict]:
    today = date.today().isoformat()
    hosts = mx_hosts(domain)
    if not hosts:
        return {a: {"status": "invalid", "code": 0, "msg": "domain has no mail server", "checked": today} for a in addrs}
    fake = "".join(random.choices(string.ascii_lowercase + string.digits, k=14)) + "@" + domain
    last_err = ""
    for host in hosts[:2]:
        try:
            got = _talk(host, addrs, fake)
        except (socket.timeout, TimeoutError, OSError, smtplib.SMTPException) as ex:
            last_err = f"{type(ex).__name__}: {ex}"[:160]
            continue
        fc, fm = got.get(fake, (0, ""))
        fake_ok = fc in (250, 251)
        return {a: {"status": verdict(*got.get(a, (0, "")), fake_ok), "code": got.get(a, (0, ""))[0],
                    "msg": got.get(a, (0, ""))[1], "mx": host, "checked": today} for a in addrs}
    return {a: {"status": "unknown", "code": 0, "msg": last_err or "no answer", "checked": today} for a in addrs}


def to_check(con, recheck_days: int = 45) -> list[str]:
    """Addresses of homes not emailed yet: never checked, an unknown from an earlier day, or an old check."""
    done = load()
    today = date.today()
    out = []
    for (email,) in con.execute("SELECT email FROM prospects WHERE status='queued' AND email != ''"):
        e = email.strip().lower()
        v = done.get(e)
        if v:
            age = (today - date.fromisoformat(v["checked"])).days
            if v["status"] == "unknown" and age < 1:
                continue
            if v["status"] != "unknown" and age < recheck_days:
                continue
        out.append(e)
    return sorted(set(out))


def run(limit: int = 4000, minutes: int = 45, workers: int = 8) -> Counter:
    con = sqlite3.connect(D / "carehome.db")
    addrs = to_check(con)[:limit]
    by_domain: dict[str, list[str]] = defaultdict(list)
    for a in addrs:
        by_domain[a.split("@")[-1]].append(a)
    stop_at = time.time() + minutes * 60
    results = load()
    lock = threading.Lock()
    counts: Counter = Counter()

    def one(item):
        dom, group = item
        if time.time() > stop_at:
            return
        for i in range(0, len(group), 20):  # at most 20 addresses a connection
            got = check_domain(dom, group[i:i + 20])
            with lock:
                results.update(got)
                counts.update(v["status"] for v in got.values())

    items = sorted(by_domain.items(), key=lambda kv: -len(kv[1]))
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(one, items))
    save(results)
    print(f"{datetime.now().isoformat(timespec='seconds')} mailbox check: {sum(counts.values())} of {len(addrs)} "
          f"addresses checked: {dict(counts)}", flush=True)
    return counts


def summary() -> Counter:
    return Counter(v["status"] for v in load().values())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--minutes", type=int, default=45)
    a = ap.parse_args()
    run(a.limit, a.minutes)
    print("all verdicts so far:", dict(summary()))
